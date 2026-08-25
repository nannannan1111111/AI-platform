(() => {
  const TOKEN_KEY = 'creative_studio_access_token';
  const route = window.location.pathname.match(/^\/workspace\/canvases\/([^/]+)\/(classic|smart)$/);
  if (!route) {
    window.SaaSCanvasGateway = Object.freeze({ active: false });
    return;
  }

  const canvasId = decodeURIComponent(route[1]);
  const token = window.sessionStorage.getItem(TOKEN_KEY);
  const nativeFetch = window.fetch.bind(window);
  const snapshots = new Map();
  const canvasListUrl = '/workspace/canvases';
  const generationTasksUrl = '/workspace/generations';
  const blockedLegacyExternalPaths = new Set([
    '/generate',
    '/api/image-task-query',
    '/api/cloud-video/upload',
    '/api/canvas-video',
  ]);
  const blockedLegacyExternalPrefixes = [
    '/api/runninghub/',
    '/api/midjourney/',
    '/api/angle/',
    '/api/ms/',
    '/api/canvas-image-tasks/',
  ];
  const blockedLegacyLocalDataPaths = new Set([
    '/api/canvas-assets/download',
    '/api/smart-canvas/prompt-templates',
    '/api/canvases/trash',
  ]);
  const blockedLegacyLocalDataPrefixes = [
    '/api/asset-library',
  ];
  let imageCatalogPromise = null;
  const mediaPreviewUrls = new Set();
  const thumbnailMediaUrls = new Map();
  const thumbnailMediaLoads = new Map();
  const thumbnailQueue = [];
  let thumbnailActive = 0;
  const originalMediaUrls = new Map();
  const originalMediaLoads = new Map();
  const completedGenerationTaskNotices = new Set();

  window.SaaSCanvasGateway = Object.freeze({
    active: true,
    canvasId,
    canvasListUrl,
    generationTasksUrl,
    streamGenerationTask,
    newGenerationTaskId,
    previewMedia,
    loadOriginalMedia,
    cachedOriginalMediaUrl(value) {
      return originalMediaUrls.get(mediaIdFromValue(value)) || '';
    },
    editorUrl(id, kind) {
      const encodedId = encodeURIComponent(id);
      // 经典画布已停止提供新入口；旧调用方仍可传 kind，但统一落到智能编辑器。
      return `/workspace/canvases/${encodedId}/smart?id=${encodedId}`;
    },
  });

  if (!token) {
    window.location.replace('/login');
    return;
  }

  function authorizedOptions(options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', `Bearer ${token}`);
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    return { ...options, headers };
  }

  async function saasFetch(path, options = {}) {
    const { timeoutMs = 15_000, ...requestOptions } = options;
    const controller = new AbortController();
    const timeoutHandle = timeoutMs > 0
      ? (window.setTimeout || globalThis.setTimeout)(() => controller.abort(), timeoutMs)
      : 0;
    const externalSignal = requestOptions.signal;
    const abortFromCaller = () => controller.abort(externalSignal.reason);
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort(externalSignal.reason);
      else externalSignal.addEventListener('abort', abortFromCaller, { once: true });
    }
    let response;
    try {
      response = await nativeFetch(path, authorizedOptions({ ...requestOptions, signal: controller.signal }));
    } catch (error) {
      if (controller.signal.aborted && !externalSignal?.aborted) {
        throw new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒），请稍后重试`);
      }
      throw error;
    } finally {
      if (timeoutHandle) (window.clearTimeout || globalThis.clearTimeout)(timeoutHandle);
      externalSignal?.removeEventListener('abort', abortFromCaller);
    }
    if (response.status === 401) {
      window.sessionStorage.removeItem(TOKEN_KEY);
      window.location.replace('/login');
    }
    return response;
  }

  async function streamGenerationTask(taskId, onMedia = null, onTask = null) {
    const response = await saasFetch(
      `/api/v1/generation-tasks/${encodeURIComponent(taskId)}/events`,
      { headers: { Accept: 'text/event-stream' }, timeoutMs: 0 },
    );
    if (!response.ok || !response.body) throw new Error('任务状态连接不可用');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    let terminalTask = null;
    let mediaItemCount = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
        const events = buffered.split(/\r?\n\r?\n/);
        buffered = events.pop() || '';
        for (const event of events) {
          const eventName = event.split(/\r?\n/)
            .find(line => line.startsWith('event:'))?.slice(6).trim() || 'message';
          const data = event.split(/\r?\n/)
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
            .join('\n');
          if (!data) continue;
          const payload = JSON.parse(data);
          if (eventName === 'media') {
            mediaItemCount += Array.isArray(payload) ? payload.length : 1;
            if (onMedia) await onMedia(payload);
            continue;
          }
          const task = payload;
          if (onTask) await onTask(task);
          if (['succeeded', 'failed', 'cancelled'].includes(task.status)) terminalTask = task;
        }
        if (terminalTask) {
          if (terminalTask.status !== 'succeeded') return terminalTask;
          const delivered = Math.max(0, Number(terminalTask.delivered_quantity || 0));
          if (!delivered || mediaItemCount >= delivered || done) return terminalTask;
        }
        if (done) throw new Error('任务状态连接提前关闭');
      }
    } finally {
      reader.releaseLock();
    }
  }

  function timestamp(value) {
    const parsed = Date.parse(value || '');
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function legacyCanvas(value) {
    snapshots.set(value.canvas_id, value);
    return {
      ...(value.document || {}),
      id: value.canvas_id,
      canvas_id: value.canvas_id,
      title: value.title,
      kind: value.kind,
      version: value.version,
      created_at: timestamp(value.created_at),
      updated_at: timestamp(value.updated_at),
      project: 'default',
    };
  }

  function generationTaskIds(node) {
    return [...new Set([
      ...(Array.isArray(node?.saasGenerationTaskIds) ? node.saasGenerationTaskIds : []),
      node?.lastGenerationTaskId,
    ].map(value => String(value || '').trim()).filter(Boolean))];
  }

  function documentGenerationTaskIds(document) {
    return new Set((Array.isArray(document?.nodes) ? document.nodes : []).flatMap(generationTaskIds));
  }

  function dismissedGenerationTaskIds(document) {
    return new Set((Array.isArray(document?.dismissedGenerationTaskIds)
      ? document.dismissedGenerationTaskIds
      : []).map(value => String(value || '').trim()).filter(Boolean));
  }

  function rememberDeletedGenerationNodes(previousDocument, nextDocument) {
    const previousTaskIds = documentGenerationTaskIds(previousDocument);
    const currentTaskIds = documentGenerationTaskIds(nextDocument);
    const dismissedTaskIds = new Set([
      ...dismissedGenerationTaskIds(previousDocument),
      ...dismissedGenerationTaskIds(nextDocument),
    ]);
    previousTaskIds.forEach(taskId => {
      if (!currentTaskIds.has(taskId)) dismissedTaskIds.add(taskId);
    });
    currentTaskIds.forEach(taskId => dismissedTaskIds.delete(taskId));
    if (dismissedTaskIds.size) {
      nextDocument.dismissedGenerationTaskIds = [...dismissedTaskIds].sort();
    } else {
      delete nextDocument.dismissedGenerationTaskIds;
    }
    return nextDocument;
  }

  function recoveryNodeId(prefix, taskId) {
    const safeTaskId = String(taskId || '').replace(/[^a-zA-Z0-9_-]/g, '-');
    return `saas-${prefix}-${safeTaskId}`;
  }

  function recoveryPosition(nodes, offset = 0) {
    const positioned = (Array.isArray(nodes) ? nodes : []).filter(node => Number.isFinite(Number(node?.x)));
    const right = positioned.reduce(
      (maximum, node) => Math.max(maximum, Number(node.x) + Number(node.w || 320)),
      0,
    );
    return { x: right + 120, y: 120 + offset * 220 };
  }

  async function canvasGenerationTaskList(canvasId, suffix) {
    try {
      const response = await saasFetch(
        `/api/v1/canvases/${encodeURIComponent(canvasId)}/generation-tasks/${suffix}`,
      );
      if (!response.ok) return [];
      const tasks = await response.json();
      return Array.isArray(tasks) ? tasks : [];
    } catch (_) {
      return [];
    }
  }

  async function recoverUntrackedCanvasTasks(value) {
    const document = value?.document;
    if (!document || typeof document !== 'object') return;
    if (!Array.isArray(document.nodes)) document.nodes = [];
    if (!Array.isArray(document.connections)) document.connections = [];
    const [recentTasks, activeTasks] = await Promise.all([
      canvasGenerationTaskList(value.canvas_id, 'recent?limit=20'),
      canvasGenerationTaskList(value.canvas_id, 'active'),
    ]);
    const tasks = [...new Map(
      [...recentTasks, ...activeTasks]
        .filter(task => task?.task_id)
        .map(task => [task.task_id, task]),
    ).values()];
    const knownTaskIds = documentGenerationTaskIds(document);
    const dismissedTaskIds = dismissedGenerationTaskIds(document);
    const recoverable = tasks
      .filter(task => ['queued', 'running', 'succeeded'].includes(task?.status))
      .filter(task => (
        task?.task_id
        && !knownTaskIds.has(task.task_id)
        && !dismissedTaskIds.has(task.task_id)
      ));
    recoverable.forEach((task, index) => {
      const position = recoveryPosition(document.nodes, index);
      if (value.kind === 'smart') {
        document.nodes.push({
          id: recoveryNodeId('recovered-result', task.task_id),
          type: 'smart-image',
          title: '恢复的生成任务',
          x: position.x,
          y: position.y,
          images: [],
          saasGenerationTaskIds: [task.task_id],
          lastGenerationTaskId: task.task_id,
          ...(task.status === 'queued' || task.status === 'running' ? {
            pendingTasks: [{
              taskId: task.task_id,
              kind: 'image',
              quantity: Math.max(1, Number(task.quantity) || 1),
            }],
            pending: Math.max(1, Number(task.quantity) || 1),
            running: task.status === 'running',
            queued: task.status === 'queued',
          } : {}),
          created_at: timestamp(task.created_at) || Date.now(),
        });
        return;
      }
      const sourceId = recoveryNodeId('recovered-source', task.task_id);
      const outputId = recoveryNodeId('recovered-output', task.task_id);
      document.nodes.push({
        id: sourceId,
        type: 'api',
        title: '恢复的生成任务',
        x: position.x,
        y: position.y,
        lastGenerationTaskId: task.task_id,
      });
      document.nodes.push({
        id: outputId,
        type: 'output',
        title: '恢复的生成结果',
        x: position.x + 440,
        y: position.y,
        images: [],
      });
      document.connections.push({ from: sourceId, to: outputId });
    });
  }

  function mediaContentUrl(mediaId) {
    return `/api/v1/media/${encodeURIComponent(mediaId)}/content`;
  }

  function mediaThumbnailUrl(mediaId, size = 512) {
    const width = Math.max(64, Math.min(2048, Number(size) || 512));
    return `/api/v1/media/${encodeURIComponent(mediaId)}/thumbnail?size=${width}`;
  }

  function mediaIdFromValue(value) {
    if (value && typeof value === 'object' && value.media_id) return String(value.media_id);
    const raw = typeof value === 'string' ? value : value?.url || '';
    const match = String(raw).match(/^\/api\/v1\/media\/([^/]+)\/content(?:[?#]|$)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  async function loadThumbnailMedia(mediaId, size = 512) {
    const cacheKey = `${mediaId}:${Math.max(64, Math.min(2048, Number(size) || 512))}`;
    if (thumbnailMediaUrls.has(cacheKey)) return thumbnailMediaUrls.get(cacheKey);
    if (thumbnailMediaLoads.has(cacheKey)) return thumbnailMediaLoads.get(cacheKey);
    const task = new Promise((resolve, reject) => {
      thumbnailQueue.push({ mediaId, size, cacheKey, resolve, reject });
      pumpThumbnailQueue();
    }).finally(() => thumbnailMediaLoads.delete(cacheKey));
    thumbnailMediaLoads.set(cacheKey, task);
    return task;
  }

  function pumpThumbnailQueue() {
    while (thumbnailActive < 8 && thumbnailQueue.length) {
      const item = thumbnailQueue.shift();
      thumbnailActive += 1;
      (async () => {
        const response = await saasFetch(mediaThumbnailUrl(item.mediaId, item.size));
        if (!response.ok) throw new Error('缩略图加载失败');
        const previewUrl = URL.createObjectURL(await response.blob());
        mediaPreviewUrls.add(previewUrl);
        thumbnailMediaUrls.set(item.cacheKey, previewUrl);
        return previewUrl;
      })().then(item.resolve, item.reject).finally(() => {
        thumbnailActive -= 1;
        pumpThumbnailQueue();
      });
    }
  }

  async function loadOriginalMedia(value) {
    const mediaId = mediaIdFromValue(value);
    if (!mediaId) return typeof value === 'string' ? value : value?.url || '';
    if (originalMediaUrls.has(mediaId)) return originalMediaUrls.get(mediaId);
    if (originalMediaLoads.has(mediaId)) return originalMediaLoads.get(mediaId);
    const task = (async () => {
      const response = await saasFetch(mediaContentUrl(mediaId));
      if (!response.ok) throw new Error('原图加载失败');
      const originalUrl = URL.createObjectURL(await response.blob());
      mediaPreviewUrls.add(originalUrl);
      originalMediaUrls.set(mediaId, originalUrl);
      return originalUrl;
    })().finally(() => originalMediaLoads.delete(mediaId));
    originalMediaLoads.set(mediaId, task);
    return task;
  }

  function sameDeliveredMedia(item, media) {
    return item && typeof item === 'object' && (
      item.media_id === media.media_id
      || item.url === mediaContentUrl(media.media_id)
    );
  }

  async function previewMedia(media) {
    if (media?.kind !== 'image' || !['temporary', 'persistent'].includes(media?.state)) return null;
    let previewUrl;
    try {
      previewUrl = await loadThumbnailMedia(media.media_id);
    } catch (_) {
      // A thumbnail can briefly lag behind the committed media row. Keep the
      // authenticated content URL so the result remains displayable; later
      // canvas hydration will retry the thumbnail without losing the result.
      previewUrl = '';
    }
    return {
      url: mediaContentUrl(media.media_id),
      ...(previewUrl ? { thumbnail: previewUrl } : {}),
      media_id: media.media_id,
      generationTaskId: media.task_id,
      kind: 'image',
      mime_type: media.mime_type,
      mediaState: media.state,
      expires_at: media.expires_at,
      name: `generation-${media.media_id}`,
    };
  }

  async function restoreCanvasMediaPreviews(value) {
    if (Array.isArray(value)) {
      await Promise.all(value.map(restoreCanvasMediaPreviews));
      return;
    }
    if (!value || typeof value !== 'object') return;
    if (value.media_id && (!value.thumbnail || !String(value.thumbnail).startsWith('blob:'))) {
      try {
        value.thumbnail = await loadThumbnailMedia(value.media_id);
        value.url = mediaContentUrl(value.media_id);
      } catch (_) {
        // Keep the stable authenticated path when a preview cannot be hydrated yet.
      }
    }
    await Promise.all(Object.values(value).map(restoreCanvasMediaPreviews));
  }

  async function generationTaskResult(taskId) {
    let delayMs = 800;
    for (let attempt = 0; attempt < 12; attempt += 1) {
      if (attempt > 0) {
        await new Promise(resolve => (window.setTimeout || globalThis.setTimeout)(resolve, delayMs));
        delayMs = Math.min(3000, Math.round(delayMs * 1.35));
      }
      const taskResponse = await saasFetch(`/api/v1/generation-tasks/${encodeURIComponent(taskId)}`);
      if (!taskResponse.ok) return { task: null, media: [] };
      const task = await taskResponse.json();
      if (task?.status !== 'succeeded') return { task, media: [] };
      const mediaResponse = await saasFetch(
        `/api/v1/generation-tasks/${encodeURIComponent(taskId)}/media`,
      );
      if (!mediaResponse.ok) continue;
      const mediaPayload = await mediaResponse.json();
      const media = (await Promise.all(
        (Array.isArray(mediaPayload) ? mediaPayload : []).map(previewMedia),
      )).filter(Boolean);
      const delivered = Math.max(0, Number(task.delivered_quantity || 0));
      if (delivered > 0 && media.length < delivered) continue;
      return { task, media };
    }
    const taskResponse = await saasFetch(`/api/v1/generation-tasks/${encodeURIComponent(taskId)}`);
    if (!taskResponse.ok) return { task: null, media: [] };
    return { task: await taskResponse.json(), media: [] };
  }

  async function mapWithConcurrency(values, limit, mapper) {
    const results = new Array(values.length);
    let nextIndex = 0;
    async function worker() {
      while (true) {
        const index = nextIndex++;
        if (index >= values.length) return;
        results[index] = await mapper(values[index], index);
      }
    }
    await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker));
    return results;
  }

  function replaceTaskMedia(items, taskIds, media) {
    const taskIdSet = new Set(taskIds);
    const current = (Array.isArray(items) ? items : []).filter(item => (
      !item || typeof item !== 'object' || !taskIdSet.has(item.generationTaskId)
    ));
    media.forEach(item => {
      const duplicate = current.some(existing => sameDeliveredMedia(existing, item));
      if (!duplicate) current.push(item);
    });
    return current;
  }

  async function restoreGenerationResults(value) {
    const document = value?.document;
    const nodes = Array.isArray(document?.nodes) ? document.nodes : [];
    const nodesWithTasks = nodes.map(node => ({ node, taskIds: generationTaskIds(node) }))
      .filter(item => item.taskIds.length);
    const allTaskIds = [...new Set(nodesWithTasks.flatMap(item => item.taskIds))];
    const resultsByTask = new Map(await mapWithConcurrency(allTaskIds, 6, async taskId => {
      try {
        return [taskId, { resolved: true, ...await generationTaskResult(taskId) }];
      } catch (_) {
        return [taskId, { resolved: false, task: null, media: [] }];
      }
    }));

    nodesWithTasks.forEach(({ node, taskIds }) => {
      const resolvedTaskIds = taskIds.filter(taskId => resultsByTask.get(taskId)?.resolved);
      const media = resolvedTaskIds.flatMap(taskId => resultsByTask.get(taskId)?.media || []);
      if (value.kind === 'smart') {
        node.images = replaceTaskMedia(node.images, resolvedTaskIds, media);
        const activeTasks = resolvedTaskIds
          .map(taskId => resultsByTask.get(taskId)?.task)
          .filter(task => task && ['queued', 'running'].includes(task.status));
        if (activeTasks.length) {
          node.pendingTasks = activeTasks.map(task => ({
            taskId: task.task_id,
            kind: 'image',
            quantity: Math.max(1, Number(task.quantity) || 1),
          }));
          node.pending = activeTasks.reduce(
            (total, task) => total + Math.max(1, Number(task.quantity) || 1),
            0,
          );
          node.running = activeTasks.some(task => task.status === 'running');
          node.queued = activeTasks.every(task => task.status === 'queued');
        }
        if (media.length) {
          node.outputKind = 'image';
          node.title = (node.images || []).length > 1 ? 'Group' : 'Image';
          if (!activeTasks.length) {
            node.pending = 0;
            node.running = false;
            node.queued = false;
            delete node.pendingTasks;
          }
        }
        return;
      }
      const activeTasks = resolvedTaskIds
        .map(taskId => resultsByTask.get(taskId)?.task)
        .filter(task => task && ['queued', 'running'].includes(task.status));
      if (activeTasks.length) {
        node.runStatus = activeTasks.some(task => task.status === 'running') ? 'running' : 'queued';
        node.running = activeTasks.some(task => task.status === 'running');
      }
      node.generatedOutputs = replaceTaskMedia(node.generatedOutputs, resolvedTaskIds, media);
      if (media.length && !activeTasks.length) {
        node.runStatus = 'done';
        node.running = false;
      }
      (Array.isArray(document.connections) ? document.connections : [])
        .filter(connection => connection?.from === node.id)
        .map(connection => nodes.find(candidate => candidate?.id === connection.to))
        .filter(candidate => candidate?.type === 'output')
        .forEach(output => {
          output.images = replaceTaskMedia(output.images, resolvedTaskIds, media);
          const resolvedIds = new Set(resolvedTaskIds);
          const pending = (Array.isArray(output._pending) ? output._pending : [])
            .filter(item => !resolvedIds.has(item?.canvasTaskId));
          activeTasks.forEach(task => pending.push({
            id: `saas-pending-${task.task_id}`,
            canvasTaskId: task.task_id,
            canvasTaskType: 'online-image',
            startedAt: timestamp(task.created_at) || Date.now(),
            run: { node: { id: node.id }, request: { task_id: task.task_id }, refs: [] },
          }));
          output._pending = pending;
        });
    });
  }

  async function legacyCanvasTaskStatus(taskId) {
    try {
      const result = await generationTaskResult(taskId);
      if (!result.task) return localJsonResponse({ detail: '任务不存在' }, 404);
      if (result.task.status === 'succeeded') {
        if (!completedGenerationTaskNotices.has(taskId)) {
          completedGenerationTaskNotices.add(taskId);
          showGenerationTaskNotice(
            taskId,
            result.task.partial_delivery
              ? (result.task.completion_message || `上游仅完成 ${result.task.delivered_quantity || 0}/${result.task.quantity || 0} 张`)
              : '生成完成',
          );
        }
        return localJsonResponse({
          task_id: taskId,
          status: 'succeeded',
          partial_delivery: Boolean(result.task.partial_delivery),
          completion_message: result.task.completion_message || null,
          result: { images: result.media, image_items: result.media },
        }, 200);
      }
      if (['failed', 'cancelled'].includes(result.task.status)) {
        return localJsonResponse({
          task_id: taskId,
          status: 'failed',
          error: result.task.failure_message || '图片生成失败，请重新提交任务。',
        }, 200);
      }
      return localJsonResponse(result.task, 200);
    } catch (_) {
      return localJsonResponse({ detail: '任务状态暂时不可用' }, 503);
    }
  }

  function stableCanvasValue(value) {
    if (Array.isArray(value)) return value.map(stableCanvasValue);
    if (!value || typeof value !== 'object') return value;
    const stable = Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, stableCanvasValue(item)]),
    );
    if (stable.media_id) {
      stable.url = mediaContentUrl(stable.media_id);
      if (typeof stable.thumbnail === 'string' && stable.thumbnail.startsWith('blob:')) delete stable.thumbnail;
    }
    return stable;
  }

  window.addEventListener('pagehide', () => {
    mediaPreviewUrls.forEach(previewUrl => URL.revokeObjectURL(previewUrl));
    mediaPreviewUrls.clear();
    thumbnailMediaUrls.clear();
    originalMediaUrls.clear();
  }, { once: true });

  function jsonResponse(source, payload, status = source.status) {
    const headers = new Headers(source.headers);
    headers.set('Content-Type', 'application/json');
    return new Response(JSON.stringify(payload), {
      status,
      statusText: source.statusText,
      headers,
    });
  }

  async function requestBody(input, options) {
    const body = options?.body;
    if (typeof body === 'string') return JSON.parse(body || '{}');
    if (input instanceof Request) return input.clone().json().catch(() => ({}));
    return {};
  }

  function localJsonResponse(payload, status) {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function isBlockedLegacyExternalRequest(pathname) {
    return blockedLegacyExternalPaths.has(pathname)
      || blockedLegacyExternalPrefixes.some(prefix => pathname.startsWith(prefix));
  }

  function isBlockedLegacyLocalDataRequest(pathname) {
    return blockedLegacyLocalDataPaths.has(pathname)
      || blockedLegacyLocalDataPrefixes.some(prefix => (
        pathname === prefix || pathname.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`)
      ))
      || /^\/api\/canvases\/[^/]+\/(meta|restore|purge)$/.test(pathname);
  }

  async function imageCatalog() {
    if (!imageCatalogPromise) {
      imageCatalogPromise = saasFetch('/api/v1/image-models').then(async response => {
        if (!response.ok) throw new Error('暂时无法加载平台模型目录');
        return response.json();
      }).catch(error => {
        imageCatalogPromise = null;
        throw error;
      });
    }
    return imageCatalogPromise;
  }

  async function safeLegacyConfig() {
    let catalog = { data: [] };
    let llmProviders = [];
    try {
      catalog = await imageCatalog();
    } catch (_) {
      // 目录暂时不可用时仍返回无敏感字段的兼容投影，避免回退旧配置来源。
    }
    try {
      const response = await saasFetch('/api/v1/llm-providers');
      if (response.ok) llmProviders = await response.json();
    } catch (_) {
      // 用户未配置 LLM 时保持为空，绝不回退到平台生图 Provider。
    }
    const imageModels = [...new Set((catalog?.data || []).flatMap(model => (
      (model.output_specs || [])
        .filter(spec => spec.status === 'available')
        .map(spec => `${String(model.logical_model || '').trim()}|||${String(spec.output_spec || '').trim()}`)
    )).filter(value => !value.startsWith('|||') && !value.endsWith('|||')))];
    return localJsonResponse({
      image_models: imageModels,
      chat_models: [...new Set(llmProviders.flatMap(provider => provider.models || []))],
      video_models: [],
      ms_chat_models: [],
      api_providers: [{
        id: 'saas-platform',
        name: '平台模型',
        protocol: 'saas',
        enabled: true,
        has_key: false,
        has_wallet_key: false,
        image_models: imageModels,
        chat_models: [],
        video_models: [],
        rh_apps: [],
        rh_workflows: [],
      }, ...llmProviders.filter(provider => provider.enabled !== false).map(provider => ({
        id: provider.code,
        name: provider.display_name,
        protocol: 'openai',
        enabled: true,
        has_key: Boolean(provider.has_key),
        image_models: [],
        chat_models: provider.models || [],
        video_models: [],
        rh_apps: [],
        rh_workflows: [],
      }))],
    }, 200);
  }

  function availableGenerationTarget(catalog, payload) {
    const availableModels = (catalog?.data || []).map(model => ({
      logical_model: String(model.logical_model || ''),
      output_specs: (model.output_specs || []).filter(spec => spec.status === 'available'),
    })).filter(model => model.logical_model && model.output_specs.length);
    const requestedValue = String(payload.logical_model || payload.model || '').trim();
    const [requestedLogicalModel, requestedOutputSpec = ''] = requestedValue.split('|||');
    const requestedModel = requestedLogicalModel.toLocaleLowerCase();
    const selectedModel = availableModels.find(
      model => model.logical_model.toLocaleLowerCase() === requestedModel,
    ) || availableModels[0];
    if (!selectedModel) throw new Error('平台当前没有可用的图片模型');
    const requestedSpec = String(
      requestedOutputSpec || payload.output_spec || payload.resolution || payload.quality || '',
    ).trim().toLocaleLowerCase();
    const selectedSpec = selectedModel.output_specs.find(
      spec => String(spec.output_spec || '').toLocaleLowerCase() === requestedSpec,
    ) || selectedModel.output_specs[0];
    return {
      logical_model: selectedModel.logical_model,
      output_spec: selectedSpec.output_spec,
    };
  }

  function normalizedOpenAIImageParameters(payload) {
    const supportedSizes = {
      '1024x1024': '1:1',
      '1536x1024': '3:2',
      '1024x1536': '2:3',
      '2048x1152': '16:9',
      '2048x2048': '1:1',
    };
    const requestedQuality = String(payload.quality || 'auto').trim().toLocaleLowerCase();
    const quality = ['auto', 'low', 'medium', 'high'].includes(requestedQuality)
      ? requestedQuality
      : 'auto';
    const requestedSize = String(payload.size || '').trim().toLocaleLowerCase().replaceAll('×', 'x');
    const customDimensions = requestedSize.match(/^(\d+)\s*x\s*(\d+)$/i);
    if (customDimensions && String(payload.resolution || '').trim().toLocaleLowerCase() === 'custom') {
      const width = Number(customDimensions[1]);
      const height = Number(customDimensions[2]);
      if ([width, height].every(value => Number.isInteger(value) && value >= 256 && value <= 8192 && value % 16 === 0)) {
        return { size: `${width}x${height}`, aspectRatio: 'custom', quality };
      }
      throw new Error('自定义像素尺寸不对，请修改！');
    }
    if (supportedSizes[requestedSize]) {
      return { size: requestedSize, aspectRatio: supportedSizes[requestedSize], quality };
    }

    const dimensions = requestedSize.match(/^(\d+)\s*x\s*(\d+)$/i);
    const requestedAspectRatio = String(payload.aspect_ratio || '').trim();
    let aspectRatio = ['1:1', '3:2', '2:3', '16:9'].includes(requestedAspectRatio)
      ? requestedAspectRatio
      : '';
    if (!aspectRatio && dimensions) {
      const width = Number(dimensions[1]);
      const height = Number(dimensions[2]);
      const ratio = width / height;
      if (Math.abs(ratio - 1) < 0.01) aspectRatio = '1:1';
      else if (Math.abs(ratio - 1.5) < 0.01) aspectRatio = '3:2';
      else if (Math.abs(ratio - (2 / 3)) < 0.01) aspectRatio = '2:3';
      else if (Math.abs(ratio - (16 / 9)) < 0.01) aspectRatio = '16:9';
    }
    if (!aspectRatio) {
      throw new Error('当前 SaaS 生图仅支持 1:1、3:2、2:3 和 16:9 尺寸');
    }
    const sizeByAspectRatio = {
      '1:1': dimensions && Math.max(Number(dimensions[1]), Number(dimensions[2])) > 1024
        ? '2048x2048'
        : '1024x1024',
      '3:2': '1536x1024',
      '2:3': '1024x1536',
      '16:9': '2048x1152',
    };
    return { size: sizeByAspectRatio[aspectRatio], aspectRatio, quality };
  }

  function canvasGenerationParameters(payload) {
    const resolutionTier = String(
      payload.resolution_tier || payload.resolution || payload.output_spec || '',
    ).trim().toLocaleLowerCase();
    const aspectRatio = String(payload.aspect_ratio || '').trim();
    const outputFormat = String(payload.output_format || 'png').trim().toLocaleLowerCase();
    if (resolutionTier === 'custom') {
      const size = String(payload.size || '').trim().toLocaleLowerCase().replaceAll('×', 'x');
      const dimensions = size.match(/^(\d+)x(\d+)$/);
      const width = Number(dimensions?.[1]);
      const height = Number(dimensions?.[2]);
      if (!dimensions || ![width, height].every(value => Number.isInteger(value) && value >= 256 && value <= 8192 && value % 16 === 0)) {
        throw new Error('自定义像素尺寸不对，请修改！');
      }
      if (!['png', 'jpeg', 'webp'].includes(outputFormat)) {
        throw new Error('当前 SaaS 生图不支持该输出格式');
      }
      return {
        aspect_ratio: 'custom',
        quality: 'auto',
        size: `${width}x${height}`,
        output_format: outputFormat,
        ...(payload.input_fidelity ? { input_fidelity: String(payload.input_fidelity).trim().toLocaleLowerCase() } : {}),
      };
    }
    if (['1k', '2k', '4k'].includes(resolutionTier)) {
      if (!['1:1', '4:3', '16:9', '3:4', '9:16'].includes(aspectRatio)) {
        throw new Error('当前 SaaS 生图不支持该输出比例');
      }
      if (!['png', 'jpeg', 'webp'].includes(outputFormat)) {
        throw new Error('当前 SaaS 生图不支持该输出格式');
      }
      const sizeByResolutionAndAspect = {
        '1k|1:1': '1024x1024', '1k|4:3': '1024x768', '1k|16:9': '1280x720',
        '1k|3:4': '768x1024', '1k|9:16': '720x1280',
        '2k|1:1': '2048x2048', '2k|4:3': '2048x1536', '2k|16:9': '2048x1152',
        '2k|3:4': '1536x2048', '2k|9:16': '1152x2048',
        '4k|1:1': '2880x2880', '4k|4:3': '3264x2448', '4k|16:9': '3840x2160',
        '4k|3:4': '2448x3264', '4k|9:16': '2160x3840',
      };
      return {
        aspect_ratio: aspectRatio,
        quality: 'auto',
        size: sizeByResolutionAndAspect[`${resolutionTier}|${aspectRatio}`],
        resolution_tier: resolutionTier,
        output_format: outputFormat,
        ...(payload.input_fidelity ? { input_fidelity: String(payload.input_fidelity).trim().toLocaleLowerCase() } : {}),
      };
    }
    const normalized = normalizedOpenAIImageParameters(payload);
    return {
      aspect_ratio: normalized.aspectRatio,
      size: normalized.size,
      quality: normalized.quality,
      ...(payload.output_format ? { output_format: outputFormat } : {}),
      ...(payload.input_fidelity ? { input_fidelity: String(payload.input_fidelity).trim().toLocaleLowerCase() } : {}),
    };
  }

  async function accountReferenceInputs(payload) {
    const inputs = Array.isArray(payload.reference_images) ? payload.reference_images : [];
    if (!inputs.length) return { reference_media_ids: [], mask_media_id: '' };
    if (inputs.length > 3) throw new Error('普通参考图和蒙版合计最多 3 张');
    const normalized = inputs.map(input => ({
      mediaId: String(input?.media_id || '').trim(),
      role: String(input?.role || '').trim().toLocaleLowerCase(),
    }));
    if (normalized.some(input => !input.mediaId)) {
      throw new Error('画布参考图必须先保存到账户媒体');
    }
    if (new Set(normalized.map(input => input.mediaId)).size !== normalized.length) {
      throw new Error('普通参考图和蒙版不能复用同一媒体');
    }
    const masks = normalized.filter(input => input.role === 'mask');
    const images = normalized.filter(input => input.role !== 'mask');
    if (masks.length > 1 || (masks.length && !images.length)) {
      throw new Error('蒙版必须且只能伴随至少一张普通参考图');
    }
    const converted = new Map(await Promise.all(normalized.map(async input => {
      const response = await saasFetch(
        `/api/v1/media/${encodeURIComponent(input.mediaId)}/use-as-reference`,
        { method: 'POST' },
      );
      if (!response.ok) throw new Error('画布参考图当前不可用于生成');
      const reference = await response.json();
      const mediaId = String(reference.media_id || '').trim();
      if (!mediaId) throw new Error('画布参考图转换失败');
      return [input.mediaId, mediaId];
    })));
    return {
      reference_media_ids: images.map(input => converted.get(input.mediaId)),
      mask_media_id: masks.length ? converted.get(masks[0].mediaId) : '',
    };
  }

  function newGenerationTaskId() {
    const id = window.crypto?.randomUUID?.()
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `canvas-${id}`;
  }

  function hideGenerationTaskNotice() {
    const notice = document.getElementById('saas-generation-task-notice');
    if (!notice) return;
    window.clearTimeout(showGenerationTaskNotice._timer);
    notice.remove();
  }

  function showGenerationTaskNotice(taskId, message = '生成任务已提交') {
    hideGenerationTaskNotice();
    let notice = document.getElementById('saas-generation-task-notice');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'saas-generation-task-notice';
      notice.setAttribute('role', 'status');
      notice.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:100000;min-width:260px;padding:14px 42px 14px 16px;border-radius:10px;background:#172033;color:#fff;box-shadow:0 8px 28px #0005;font:14px/1.5 system-ui,sans-serif';
      document.body.appendChild(notice);
    }
    notice.textContent = `${message}：${taskId}`;
    const link = document.createElement('a');
    link.href = generationTasksUrl;
    link.textContent = '打开生成任务';
    link.style.cssText = 'margin-left:10px;color:#8ec5ff;text-decoration:underline';
    notice.appendChild(link);
    const close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭提示');
    close.textContent = '×';
    close.style.cssText = 'position:absolute;top:6px;right:8px;width:26px;height:26px;padding:0;border:0;background:transparent;color:#cbd5e1;font:20px/26px system-ui;cursor:pointer';
    close.addEventListener?.('click', hideGenerationTaskNotice);
    notice.appendChild(close);
    showGenerationTaskNotice._timer = (window.setTimeout || setTimeout)(hideGenerationTaskNotice, 2000);
  }

  async function submitImageGeneration(input, options) {
    const legacyPayload = { ...await requestBody(input, options) };
    delete legacyPayload.provider_id;
    delete legacyPayload.provider;
    delete legacyPayload.route_id;
    delete legacyPayload.api_key;
    delete legacyPayload.base_url;
    delete legacyPayload.endpoint;
    try {
      const target = availableGenerationTarget(await imageCatalog(), legacyPayload);
      const quantity = Math.max(1, Math.min(5, Math.trunc(Number(legacyPayload.quantity) || 1)));
      const imageParameters = canvasGenerationParameters(legacyPayload);
      const referenceInputs = await accountReferenceInputs(legacyPayload);
      const response = await saasFetch('/api/v1/generation-tasks', {
        method: 'POST',
        body: JSON.stringify({
          task_id: String(legacyPayload.task_id || '').trim() || newGenerationTaskId(),
          canvas_id: canvasId,
          logical_model: target.logical_model,
          output_spec: target.output_spec,
          quantity,
          prompt: String(legacyPayload.prompt || ''),
          params: imageParameters,
          reference_media_ids: referenceInputs.reference_media_ids,
          mask_media_id: referenceInputs.mask_media_id,
        }),
      });
      if (!response.ok) return response;
      const task = await response.json();
      showGenerationTaskNotice(task.task_id);
      return jsonResponse(response, {
        ...task,
        saas_generation_task: true,
        status_url: generationTasksUrl,
      });
    } catch (error) {
      return localJsonResponse({ detail: error?.message || '暂时无法提交生成任务' }, 503);
    }
  }

  async function uploadCanvasImages(input, options) {
    let source = options?.body;
    if (!(source instanceof FormData) && input instanceof Request) {
      source = await input.clone().formData().catch(() => null);
    }
    if (!(source instanceof FormData)) {
      return localJsonResponse({ detail: '图片上传请求无效' }, 422);
    }
    const safe = new FormData();
    const supportedMimeTypes = new Set(['image/png', 'image/jpeg', 'image/webp']);
    for (const value of source.values()) {
      if (value instanceof File && supportedMimeTypes.has(String(value.type || '').toLowerCase())) {
        safe.append('files', value, value.name || 'canvas-image');
      }
    }
    if (![...safe.values()].length) {
      return localJsonResponse({ detail: '画布只支持 PNG、JPEG、WebP 图片' }, 422);
    }
    const response = await saasFetch(
      `/api/v1/canvases/${encodeURIComponent(canvasId)}/media`,
      { method: 'POST', body: safe },
    );
    if (!response.ok) return response;
    const body = await response.json();
    const files = await Promise.all((body.files || []).map(async file => {
      const previewUrl = await loadThumbnailMedia(file.media_id);
      return {
        ...file,
        url: mediaContentUrl(file.media_id),
        thumbnail: previewUrl,
        mediaState: 'persistent',
      };
    }));
    return jsonResponse(response, { files });
  }

  async function exportCanvasWorkflow(input, options) {
    const body = await requestBody(input, options);
    return saasFetch(
      `/api/v1/canvases/${encodeURIComponent(canvasId)}/workflows/export`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  }

  async function importCanvasWorkflow(input, options) {
    let body = options?.body;
    if (!(body instanceof FormData) && input instanceof Request) {
      body = await input.clone().formData().catch(() => null);
    }
    if (!(body instanceof FormData)) {
      return localJsonResponse({ detail: '请选择本地 JSON 或 ZIP 工作流文件' }, 422);
    }
    const response = await saasFetch(
      `/api/v1/canvases/${encodeURIComponent(canvasId)}/workflows/import`,
      { method: 'POST', body },
    );
    if (!response.ok) return response;
    const workflow = await response.json();
    await restoreCanvasMediaPreviews(workflow);
    return jsonResponse(response, workflow);
  }

  function canvasDocument(snapshot, body) {
    const document = { ...(snapshot?.document || {}), ...body };
    [
      'title', 'id', 'canvas_id', 'user_id', 'account_space_id', 'kind', 'version',
      'created_at', 'updated_at', 'base_updated_at', 'client_id', 'project',
    ].forEach(key => delete document[key]);
    return stableCanvasValue(document);
  }

  async function loadCanvas(id) {
    const response = await saasFetch(`/api/v1/canvases/${encodeURIComponent(id)}`);
    if (!response.ok) return response;
    const value = await response.json();
    await recoverUntrackedCanvasTasks(value);
    await restoreGenerationResults(value);
    await restoreCanvasMediaPreviews(value.document);
    return jsonResponse(response, { canvas: legacyCanvas(value) });
  }

  async function saveCanvas(id, input, options) {
    let snapshot = snapshots.get(id);
    if (!snapshot) {
      const current = await saasFetch(`/api/v1/canvases/${encodeURIComponent(id)}`);
      if (!current.ok) return current;
      snapshot = await current.json();
      snapshots.set(id, snapshot);
    }
    const body = await requestBody(input, options);
    const document = rememberDeletedGenerationNodes(
      snapshot.document,
      canvasDocument(snapshot, body),
    );
    const response = await saasFetch(`/api/v1/canvases/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({
        expected_version: snapshot.version,
        title: body.title ?? snapshot.title,
        document,
      }),
    });
    if (response.status === 409) {
      const latest = await saasFetch(`/api/v1/canvases/${encodeURIComponent(id)}`);
      if (!latest.ok) return response;
      const latestCanvas = legacyCanvas(await latest.json());
      return jsonResponse(response, {
        detail: {
          code: 'CanvasVersionConflict',
          message: '画布版本冲突',
          canvas: latestCanvas,
          updated_at: latestCanvas.updated_at,
        },
      });
    }
    if (!response.ok) return response;
    const saved = await response.json();
    return jsonResponse(response, { canvas: legacyCanvas(saved) });
  }

  async function listCanvases() {
    const response = await saasFetch('/api/v1/canvases');
    if (!response.ok) return response;
    const values = await response.json();
    return jsonResponse(response, { canvases: values.map(legacyCanvas) });
  }

  async function createCanvas(input, options) {
    const body = await requestBody(input, options);
    const response = await saasFetch('/api/v1/canvases', {
      method: 'POST',
      body: JSON.stringify({ title: body.title, kind: 'smart' }),
    });
    if (!response.ok) return response;
    return jsonResponse(response, { canvas: legacyCanvas(await response.json()) });
  }

  window.fetch = async (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.origin);
    if (url.origin !== window.location.origin) return nativeFetch(input, options);
    const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (
      method === 'GET'
      && (
        /^\/api\/v1\/(media|reference-media)\/[^/]+\/content$/.test(url.pathname)
        || /^\/api\/v1\/media\/[^/]+\/thumbnail$/.test(url.pathname)
      )
    ) {
      return saasFetch(`${url.pathname}${url.search}`, options);
    }
    if (method === 'POST' && url.pathname === '/api/v1/media/archive') {
      return saasFetch(`${url.pathname}${url.search}`, options);
    }
    if (method === 'POST' && url.pathname === '/api/canvas-workflows/export') {
      return exportCanvasWorkflow(input, options);
    }
    if (method === 'POST' && url.pathname === '/api/canvas-workflows/import') {
      return importCanvasWorkflow(input, options);
    }
    if (url.pathname === '/api/prompt-libraries' || url.pathname.startsWith('/api/prompt-libraries/')) {
      return saasFetch(`/api/v1${url.pathname.slice(4)}${url.search}`, options);
    }
    if (url.pathname === '/api/canvas-llm' && method === 'POST') {
      return saasFetch('/api/v1/canvas-llm', options);
    }
    const legacyTaskStatus = url.pathname.match(/^\/api\/canvas-image-tasks\/([^/]+)$/);
    if (legacyTaskStatus && method === 'GET') {
      return legacyCanvasTaskStatus(decodeURIComponent(legacyTaskStatus[1]));
    }
    if (isBlockedLegacyExternalRequest(url.pathname)) {
      return localJsonResponse({ detail: '该能力尚未安全接入 SaaS' }, 409);
    }
    if (isBlockedLegacyLocalDataRequest(url.pathname)) {
      return localJsonResponse({ detail: '旧本地数据不属于当前 SaaS 账户' }, 409);
    }
    if (url.pathname === '/api/config') {
      if (method !== 'GET') return localJsonResponse({ detail: '请求方法不受支持' }, 405);
      return safeLegacyConfig();
    }
    if (url.pathname === '/api/canvases') {
      if (method === 'GET') return listCanvases();
      if (method === 'POST') return createCanvas(input, options);
    }
    if (url.pathname === '/api/canvas-image-tasks' && method === 'POST') {
      return submitImageGeneration(input, options);
    }
    if (url.pathname === '/api/online-image' && method === 'POST') {
      return submitImageGeneration(input, options);
    }
    if (url.pathname === '/api/ai/upload' && method === 'POST') {
      return uploadCanvasImages(input, options);
    }
    const item = url.pathname.match(/^\/api\/canvases\/([^/]+)$/);
    if (item) {
      const id = decodeURIComponent(item[1]);
      if (method === 'GET') return loadCanvas(id);
      if (method === 'PUT') return saveCanvas(id, input, options);
      if (method === 'DELETE') {
        return saasFetch(
          `/api/v1/canvases/${encodeURIComponent(id)}${url.search}`,
          { method: 'DELETE' },
        );
      }
    }
    const touch = url.pathname.match(/^\/api\/canvases\/([^/]+)\/touch$/);
    if (touch && method === 'POST') {
      const id = decodeURIComponent(touch[1]);
      const snapshot = snapshots.get(id);
      if (snapshot) return new Response(JSON.stringify({ canvas: legacyCanvas(snapshot) }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
      return loadCanvas(id);
    }
    return nativeFetch(input, options);
  };
})();

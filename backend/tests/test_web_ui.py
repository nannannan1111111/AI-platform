import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app


def test_account_and_wallet_pages_use_the_python_saas_web_shell() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    account = client.get("/workspace/account")
    wallet = client.get("/workspace/wallet")

    assert account.status_code == 200
    assert wallet.status_code == 200
    assert 'id="app"' in account.text
    assert 'id="app"' in wallet.text


def test_wallet_explains_unavailable_payment_when_a_recharge_package_is_clicked() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert script.status_code == 200
    assert "支付途径尚未开放，可联系管理员人工充值" in script.text
    assert "admin_grant: '人工充值'" in script.text
    assert "page_size=20" in script.text
    assert "data-ledger-page" in script.text
    assert "上一页" in script.text
    assert "下一页" in script.text
    assert "total_entries" in script.text


def test_python_saas_web_shell_exposes_user_model_catalog_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/models")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/web-assets/app.js?v=page-load-timeout-6" in page.text
    assert "/web-assets/styles.css?v=sidebar-scroll-1" in page.text
    assert "/api/v1/image-models" in script.text
    assert "逻辑模型" in script.text
    assert "暂不可用" in script.text


def test_topbar_places_available_storage_before_credit_balance() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text
    shell_start = script.index("function shell")
    shell_end = script.index("function loadingPage", shell_start)
    shell_source = script[shell_start:shell_end]
    storage_position = shell_source.index("可用容量")
    balance_position = shell_source.index("可用额度")
    assert storage_position < balance_position


def test_topbar_places_announcement_and_support_after_balance() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text
    styles = client.get("/web-assets/styles.css").text
    shell_start = script.index("function shell(")
    shell_end = script.index("async function authenticatedPlatformContentImage", shell_start)
    shell_source = script[shell_start:shell_end]

    balance_position = shell_source.index("可用额度")
    announcement_position = shell_source.index('data-platform-content="announcement"')
    support_position = shell_source.index('data-platform-content="support"')
    assert balance_position < announcement_position < support_position
    assert "/api/v1/platform-content" in script
    assert "平台公告" in script
    assert "联系客服" in script
    assert "navigationItem('公告与客服', '/admin/platform-content'" in script
    assert "/api/v1/admin/platform-content" in script
    assert "state.user.storage_allowance.available_bytes" in shell_source
    assert ".storage-pill" in styles


def _rolled_back_test_data_pages_show_layout_skeletons_instead_of_account_loading_message() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text
    styles = client.get("/web-assets/styles.css").text

    loading_start = script.index("function loadingPage")
    loading_end = script.index("async function ensureAccountSummary", loading_start)
    loading_source = script[loading_start:loading_end]
    assert "正在读取账户数据" not in loading_source
    assert "page-loading-skeleton" in loading_source
    assert "page-skeleton-card" in loading_source
    assert 'aria-busy="true"' in loading_source
    assert ".page-skeleton-grid" in styles
    assert "page-skeleton-shimmer" in styles


def _rolled_back_test_stale_page_responses_cannot_replace_the_current_route() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text

    shell_start = script.index("function shell")
    shell_end = script.index("function loadingPage", shell_start)
    shell_source = script[shell_start:shell_end]
    assert "const expectedRoute = shellRouteByTitle[title]" in shell_source
    assert "if (expectedRoute && state.route !== expectedRoute) return" in shell_source
    assert "'生成任务': '/workspace/generations'" in script
    assert "'模型路由': '/admin/model-routing'" in script


def _rolled_back_test_account_summary_uses_stale_data_while_refreshing_in_background() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text

    refresh_start = script.index("async function refreshAccountSummary")
    refresh_end = script.index("async function detectAdminProviders", refresh_start)
    source = script[refresh_start:refresh_end]
    assert "if (state.accountSummaryPromise) return state.accountSummaryPromise" in source
    assert "if (state.accountSummaryLoaded && state.user && state.balance)" in source
    assert "void refreshAccountSummary({ includeProviders: true }).catch(() => {})" in source
    assert source.index("void refreshAccountSummary") < source.index("return;", source.index("void refreshAccountSummary"))


def _rolled_back_test_multi_request_admin_pages_load_independent_details_in_parallel() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text

    runninghub_start = script.index("async function adminRunningHubCapabilitiesPage")
    runninghub_end = script.index("async function adminRoutingPage", runninghub_start)
    runninghub_source = script[runninghub_start:runninghub_end]
    assert "const [schemaEntries, priceEntries] = await Promise.all([" in runninghub_source

    routing_start = runninghub_end
    routing_source = script[routing_start:]
    assert "const [policyEntries, healthEntries] = await Promise.all([" in routing_source


def _rolled_back_test_common_page_data_is_prefetched_cached_and_request_deduplicated() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text

    cache_start = script.index("async function cachedPageApi")
    cache_end = script.index("function prefetchPageData", cache_start)
    cache_source = script[cache_start:cache_end]
    assert "state.pageDataCache.get(path)" in cache_source
    assert "state.pageDataRequests.get(path)" in cache_source
    assert "return cached.value" in cache_source

    prefetch_start = cache_end
    prefetch_end = script.index("const shellRouteByTitle", prefetch_start)
    prefetch_source = script[prefetch_start:prefetch_end]
    assert "requestIdleCallback" in prefetch_source
    for path in (
        "/api/v1/image-models",
        "/api/v1/canvases",
        "/api/v1/credits/ledger",
        "/api/v1/admin/image-model-routes",
    ):
        assert path in prefetch_source

    assert "cachedPageApi('/api/v1/recharge-packages', [])" in script
    assert "cachedPageApi('/api/v1/admin/users', [])" in script


def test_workspace_only_publishes_the_smart_canvas_product_surface() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/canvases")
    classic = client.get("/workspace/canvases/canvas-1/classic?id=canvas-1")
    smart = client.get("/workspace/canvases/canvas-2/smart?id=canvas-2")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert classic.status_code == 200
    assert smart.status_code == 200
    assert "'/workspace/canvases'" in script.text
    assert "'/api/v1/canvases'" in script.text
    assert "kind: 'smart'" in script.text
    assert '<option value="classic">' not in script.text
    assert "canvas.kind === 'smart'" in script.text
    assert "/web-assets/saas-canvas-gateway.js" in classic.text
    assert "entry=smart-canvas.js" in classic.text
    assert 'src="/static/js/canvas.js' not in classic.text
    assert classic.text.index("/web-assets/saas-canvas-gateway.js") < classic.text.index(
        "/static/js/media-legacy-entry.js"
    )
    assert "/web-assets/saas-canvas-gateway.js" in smart.text
    assert "/static/js/media-legacy-entry.js" in smart.text
    assert "entry=smart-canvas.js" in smart.text
    assert smart.text.index("/web-assets/saas-canvas-gateway.js") < smart.text.index("/static/js/media-legacy-entry.js")
    assert "entry=smart-canvas.js" in smart.text


def test_legacy_and_smart_routes_keep_the_static_assets_available() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    classic = client.get("/workspace/canvases/canvas-1/classic?id=canvas-1")
    smart = client.get("/workspace/canvases/canvas-2/smart?id=canvas-2")
    classic_script = client.get("/static/js/canvas.js")
    smart_script = client.get("/static/js/smart-canvas.js")

    assert classic.status_code == 200
    assert smart.status_code == 200
    assert classic_script.status_code == 200
    assert smart_script.status_code == 200


def test_canvas_editor_navigation_only_targets_current_saas_routes() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    classic_script = client.get("/static/js/canvas.js").text
    smart_script = client.get("/static/js/smart-canvas.js").text

    assert "'/workspace/canvases'" in classic_script
    assert "'/workspace/canvases'" in smart_script
    assert "/workspace/canvases/${encodeURIComponent(id)}/smart" in classic_script
    assert "/static/canvas-list.html" not in classic_script
    assert "/static/canvas-list.html" not in smart_script
    assert "/static/smart-canvas.html" not in classic_script


def test_saas_canvas_gateway_maps_legacy_editor_persistence_to_versioned_canvases() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert gateway.status_code == 200
    assert "creative_studio_access_token" in gateway.text
    assert "Authorization" in gateway.text
    assert "/api/v1/canvases" in gateway.text
    assert "expected_version" in gateway.text
    assert "document" in gateway.text
    assert "CanvasVersionConflict" in gateway.text


def test_saas_canvas_gateway_submits_one_safe_generation_task_and_records_it_for_observation() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")

    assert gateway.status_code == 200
    assert "/api/v1/image-models" in gateway.text
    assert "/api/v1/generation-tasks" in gateway.text
    assert "delete legacyPayload.provider_id" in gateway.text
    assert "quantity" in gateway.text
    assert "normalizedOpenAIImageParameters" in gateway.text
    assert "params: imageParameters" in gateway.text
    assert "canvasGenerationParameters" in gateway.text
    assert "quality: 'auto'" in gateway.text
    assert "2048x1152" in gateway.text
    assert "1024x1536" in gateway.text
    assert "/workspace/generations" in gateway.text
    assert "saas_generation_task" in classic.text
    assert "saas_generation_task" in smart.text
    assert "task_id:taskId" in classic.text
    assert "quantity:count" in smart.text
    assert "newGenerationTaskId" in gateway.text
    assert "String(legacyPayload.task_id || '').trim() || newGenerationTaskId()" in gateway.text
    assert "prepareSaaSGenerationSubmission" in classic.text
    assert "prepareSaaSGenerationSubmission" in smart.text
    assert "completeSaaSGenerationSubmission" in classic.text
    assert "rememberSaaSGenerationSubmission" in smart.text
    assert "requestedValue.split('|||')" in gateway.text
    assert "`${String(model.logical_model || '').trim()}|||${String(spec.output_spec || '').trim()}`" in gateway.text
    assert "(window.setTimeout || setTimeout)(hideGenerationTaskNotice, 2000)" in gateway.text
    assert "aria-label', '关闭提示'" in gateway.text
    assert "result.task.partial_delivery" in gateway.text
    assert "上游仅完成 ${result.task.delivered_quantity || 0}/${result.task.quantity || 0} 张" in gateway.text
    assert "canvasGenerationParameters(legacyPayload)" in gateway.text


def test_saas_canvas_gateway_preserves_the_exact_selected_model_specification() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function availableGenerationTarget');
const end = source.indexOf('function normalizedOpenAIImageParameters', start);
eval(source.slice(start, end));
const catalog = {data: [{logical_model: 'gpt-image-2', output_specs: [
  {output_spec: '1K', status: 'available'},
  {output_spec: '满血4k', status: 'available'},
]}]};
assert.deepEqual(
  availableGenerationTarget(catalog, {model: 'gpt-image-2|||满血4k', resolution: '1k'}),
  {logical_model: 'gpt-image-2', output_spec: '满血4k'},
);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_keeps_a_saas_submission_visible_and_observed() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function completeSaaSGenerationSubmission');
const end = source.indexOf('function extractUpstreamTaskId', start);
const node = {id: 'source-1', runStatus: '', running: true};
const out = {id: 'output-1', type: 'output', images: [], _pending: []};
const polls = [];
global.window = {SaaSCanvasGateway: {active: true}};
const refreshRunNodes = () => {};
const scheduleSave = () => {};
const saveCanvas = async () => {};
const pollCanvasImageTask = taskId => { polls.push(taskId); };
eval(source.slice(start, end));
(async () => {
  const handled = await completeSaaSGenerationSubmission(node, out, [{
    saas_generation_task: true, task_id: 'task-queued', status: 'queued',
  }]);
  assert.equal(handled, true);
  assert.equal(node.lastGenerationTaskId, 'task-queued');
  assert.deepEqual(node.saasGenerationTaskIds, ['task-queued']);
  assert.equal(node.runStatus, 'queued');
  assert.equal(out._pending.length, 1);
  assert.equal(out._pending[0].canvasTaskId, 'task-queued');
  assert.equal(out._pending[0].canvasTaskType, 'online-image');
  assert.equal(out._pending[0].run.node.id, 'source-1');
  assert.deepEqual(polls, ['task-queued']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_keeps_a_saas_submission_visible_and_observed() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function rememberSaaSGenerationSubmission');
const end = source.indexOf('async function runRunningHubGeneration', start);
const resumed = [];
global.window = {SaaSCanvasGateway: {active: true}};
const render = () => {};
const scheduleSave = () => {};
const resumeSmartPendingNode = node => { resumed.push(node.id); };
const smartPendingTasks = node => (node.pendingTasks || []).filter(task => task?.taskId);
const smartPendingTaskQuantity = task => Math.max(1, Math.trunc(Number(task?.quantity) || 1));
eval(source.slice(start, end));
const node = {id: 'result-1', pending: 3, running: true, queued: false};
const handled = rememberSaaSGenerationSubmission(node, {
  saas_generation_task: true, taskIds: ['task-queued'], count: 3,
});
assert.equal(handled, true);
assert.equal(node.lastGenerationTaskId, 'task-queued');
assert.deepEqual(node.saasGenerationTaskIds, ['task-queued']);
  assert.deepEqual(node.pendingTasks, [{taskId: 'task-queued', kind: 'image', quantity: 3}]);
assert.equal(node.pending, 3);
assert.equal(node.queued, true);
assert.equal(node.running, false);
assert.deepEqual(resumed, ['result-1']);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_observes_live_task_status_and_bounds_recovery_concurrency() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")

    assert smart.status_code == 200
    source = smart.text
    assert "applySmartTaskStatus" in source
    assert "appendSmartTaskMedia" in source
    assert "liveStatusHint" in source
    assert "SMART_TASK_RESUME_CONCURRENCY = 6" in source
    assert "withSmartTaskResumeSlot" in source
    assert "onTask: snapshot => applySmartTaskStatus" in source
    assert "onMedia: items => appendSmartTaskMedia" in source
    assert "window.SaaSCanvasGateway.previewMedia(item)" in source
    assert "/api/v1/generation-tasks/${encodeURIComponent(taskId)}" in source
    assert "task-failed-cell" in source


def test_classic_canvas_persists_the_task_binding_before_submission() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function prepareSaaSGenerationSubmission');
const end = source.indexOf('async function createCanvasImageTask', start);
const events = [];
global.window = {SaaSCanvasGateway: {active: true, newGenerationTaskId() { return 'task-reserved'; }}};
const scheduleSave = () => { events.push('scheduled'); };
const flushCanvasSave = async () => { events.push('saved'); };
eval(source.slice(start, end));
(async () => {
  const node = {id: 'source-1', saasGenerationTaskIds: ['task-older'], lastGenerationTaskId: 'task-older'};
  const taskId = await prepareSaaSGenerationSubmission(node);
  assert.equal(taskId, 'task-reserved');
  assert.deepEqual(node.saasGenerationTaskIds, ['task-older', 'task-reserved']);
  assert.equal(node.lastGenerationTaskId, 'task-reserved');
  assert.deepEqual(events, ['scheduled', 'saved']);
  await rollbackPreparedSaaSGenerationSubmission(node, taskId);
  assert.deepEqual(node.saasGenerationTaskIds, ['task-older']);
  assert.equal(node.lastGenerationTaskId, 'task-older');
  assert.deepEqual(events, ['scheduled', 'saved', 'scheduled', 'saved']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_flush_waits_for_an_active_save_before_persisting_task_binding() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function waitForCanvasSaveIdle');
const end = source.indexOf('async function loadConfig', start);
let savingCanvasNow = true;
let saveCanvasAgain = false;
let canvasSaveIdleWaiters = [];
let saveTimer = setTimeout(() => {}, 10000);
let localCanvasDirty = false;
const saves = [];
const saveCanvas = async () => { saves.push('persisted'); return true; };
eval(source.slice(start, end));
(async () => {
  let finished = false;
  const flushing = flushCanvasSave().then(() => { finished = true; });
  await Promise.resolve();
  assert.equal(finished, false);
  assert.deepEqual(saves, []);
  assert.equal(saveCanvasAgain, true);
  savingCanvasNow = false;
  const waiters = canvasSaveIdleWaiters;
  canvasSaveIdleWaiters = [];
  waiters.forEach(resolve => resolve());
  await flushing;
  assert.equal(finished, true);
  assert.deepEqual(saves, ['persisted']);
  assert.equal(localCanvasDirty, true);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_persists_the_task_binding_before_submission() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function prepareSaaSGenerationSubmission');
const end = source.indexOf('async function runApiGeneration', start);
const events = [];
global.window = {SaaSCanvasGateway: {active: true, newGenerationTaskId() { return 'task-reserved'; }}};
const scheduleSave = () => { events.push('scheduled'); };
const saveCanvas = async () => { events.push('saved'); };
eval(source.slice(start, end));
(async () => {
  const node = {id: 'result-1'};
  const taskId = await prepareSaaSGenerationSubmission(node);
  assert.equal(taskId, 'task-reserved');
  assert.deepEqual(node.saasGenerationTaskIds, ['task-reserved']);
  assert.equal(node.lastGenerationTaskId, 'task-reserved');
  assert.deepEqual(events, ['scheduled', 'saved']);
  await rollbackPreparedSaaSGenerationSubmission(node, taskId);
  assert.equal('saasGenerationTaskIds' in node, false);
  assert.equal('lastGenerationTaskId' in node, false);
  assert.deepEqual(events, ['scheduled', 'saved', 'scheduled', 'saved']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_restores_delivered_images_through_authenticated_media_content() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert gateway.status_code == 200
    assert "/api/v1/generation-tasks/${encodeURIComponent(taskId)}" in gateway.text
    assert "/api/v1/generation-tasks/${encodeURIComponent(taskId)}/media" in gateway.text
    assert "/api/v1/media/${encodeURIComponent(mediaId)}/content" in gateway.text
    assert "/api/v1/media/${encodeURIComponent(mediaId)}/thumbnail?size=${width}" in gateway.text
    assert "loadOriginalMedia" in gateway.text
    assert "URL.createObjectURL" in gateway.text
    assert "generatedOutputs" in gateway.text
    assert "media_id" in gateway.text
    assert "object_key" not in gateway.text
    assert "setInterval" not in gateway.text


def test_saas_canvas_gateway_restores_classic_results_and_persists_only_stable_media_refs() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const calls = [];
let savedBody = null;
const canvasValue = {
  canvas_id: 'canvas-1', title: 'Classic', kind: 'classic', version: 1,
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  document: {
    nodes: [
      {id: 'source-1', type: 'api', lastGenerationTaskId: 'task-1'},
      {id: 'output-1', type: 'output', images: []},
      {
        id: 'source-unavailable', type: 'api', lastGenerationTaskId: 'task-unavailable',
        generatedOutputs: [{
          media_id: 'media-existing', generationTaskId: 'task-unavailable',
          url: '/api/v1/media/media-existing/content',
        }],
      },
    ],
    connections: [{from: 'source-1', to: 'output-1'}],
  },
};
function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {'Content-Type': 'application/json'},
  });
}
async function nativeFetch(input, options = {}) {
  const path = String(input);
  const headers = new Headers(options.headers || {});
  calls.push({path, authorization: headers.get('Authorization')});
  if (path === '/api/v1/canvases/canvas-1' && options.method === 'PUT') {
    savedBody = JSON.parse(options.body);
    return json({...canvasValue, version: 2, document: savedBody.document});
  }
  if (path === '/api/v1/canvases/canvas-1') return json(canvasValue);
  if (path === '/api/v1/generation-tasks/task-1') {
    return json({task_id: 'task-1', status: 'succeeded'});
  }
  if (path === '/api/v1/generation-tasks/task-unavailable') {
    throw new Error('temporary result catalog failure');
  }
  if (path === '/api/v1/generation-tasks/task-1/media') {
    return json([{
      media_id: 'media-1', task_id: 'task-1', kind: 'image', mime_type: 'image/png',
      state: 'temporary', expires_at: '2026-08-10T00:00:00Z',
    }]);
  }
  if (path === '/api/v1/media/media-1/thumbnail?size=512') {
    return new Response(new Uint8Array([137, 80, 78, 71]), {
      status: 200,
      headers: {'Content-Type': 'image/webp'},
    });
  }
  if (path === '/api/v1/media/media-1/content') {
    return new Response(new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), {
      status: 200,
      headers: {'Content-Type': 'image/png'},
    });
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-1/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch,
  addEventListener() {},
  crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:authenticated-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-1');
  assert.equal(response.status, 200);
  const canvas = (await response.json()).canvas;
  const sourceNode = canvas.nodes.find(node => node.id === 'source-1');
  const outputNode = canvas.nodes.find(node => node.id === 'output-1');
  const unavailableSource = canvas.nodes.find(node => node.id === 'source-unavailable');
  assert.deepEqual(sourceNode.generatedOutputs.map(item => [item.media_id, item.url, item.thumbnail]), [
    ['media-1', '/api/v1/media/media-1/content', 'blob:authenticated-preview'],
  ]);
  assert.deepEqual(outputNode.images.map(item => [item.media_id, item.url, item.thumbnail]), [
    ['media-1', '/api/v1/media/media-1/content', 'blob:authenticated-preview'],
  ]);
  assert.equal(unavailableSource.generatedOutputs[0].media_id, 'media-existing');
  assert.equal(calls.filter(call => call.path === '/api/v1/media/media-1/thumbnail?size=512').length, 1);
  assert.equal(calls.filter(call => call.path.endsWith('/content')).length, 0);
  const [firstOriginal, secondOriginal] = await Promise.all([
    window.SaaSCanvasGateway.loadOriginalMedia(sourceNode.generatedOutputs[0]),
    window.SaaSCanvasGateway.loadOriginalMedia(sourceNode.generatedOutputs[0]),
  ]);
  assert.equal(firstOriginal, 'blob:authenticated-preview');
  assert.equal(secondOriginal, firstOriginal);
  assert.equal(calls.filter(call => call.path.endsWith('/content')).length, 1);
  assert.ok(calls.filter(call => call.path.startsWith('/api/v1/')).every(
    call => call.authorization === 'Bearer account-token',
  ));
  await window.fetch('/api/canvases/canvas-1', {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(canvas),
  });
  const storedSource = savedBody.document.nodes.find(node => node.id === 'source-1');
  const storedOutput = savedBody.document.nodes.find(node => node.id === 'output-1');
  assert.equal(storedSource.generatedOutputs[0].url, '/api/v1/media/media-1/content');
  assert.equal(storedOutput.images[0].url, '/api/v1/media/media-1/content');
  assert.equal(JSON.stringify(savedBody).includes('blob:authenticated-preview'), false);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_restores_smart_canvas_result_nodes_without_polling() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  document: {nodes: [{
    id: 'result-1', type: 'smart-image', saasGenerationTaskIds: ['task-1'],
    lastGenerationTaskId: 'task-1', pending: 2, running: true, queued: true,
    pendingTasks: [{taskId: 'legacy-task'}],
    images: [{url: '/assets/reference.png', name: 'reference'}],
  }]},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart') return json(canvasValue);
  if (path === '/api/v1/generation-tasks/task-1') return json({task_id: 'task-1', status: 'succeeded'});
  if (path === '/api/v1/generation-tasks/task-1/media') return json([
    {media_id: 'media-1', task_id: 'task-1', kind: 'image', mime_type: 'image/png', state: 'temporary'},
    {media_id: 'media-2', task_id: 'task-1', kind: 'image', mime_type: 'image/webp', state: 'persistent'},
  ]);
  if (path.startsWith('/api/v1/media/') && path.includes('/thumbnail?size=512')) {
    return new Response(new Uint8Array([1, 2, 3]), {status: 200, headers: {'Content-Type': 'image/png'}});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
let preview = 0;
URL.createObjectURL = () => `blob:smart-preview-${++preview}`;
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-smart');
  const node = (await response.json()).canvas.nodes[0];
  assert.deepEqual(node.images.map(item => item.media_id || item.url), [
    '/assets/reference.png', 'media-1', 'media-2',
  ]);
  assert.equal(node.outputKind, 'image');
  assert.equal(node.title, 'Group');
  assert.equal(node.pending, 0);
  assert.equal(node.running, false);
  assert.equal(node.queued, false);
  assert.equal(Object.hasOwn(node, 'pendingTasks'), false);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_rehydrates_running_smart_tasks_and_exposes_legacy_status_reads() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {nodes: [{
    id: 'result-1', type: 'smart-image', saasGenerationTaskIds: ['task-running'],
    lastGenerationTaskId: 'task-running', images: [],
  }]},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart') return json(canvasValue);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/recent?limit=20') {
    return json([{task_id: 'task-running', status: 'running', quantity: 2}]);
  }
  if (path === '/api/v1/generation-tasks/task-running') {
    return json({task_id: 'task-running', status: 'running', quantity: 2});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:smart-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-smart');
  const node = (await response.json()).canvas.nodes[0];
  assert.equal(node.pending, 2);
  assert.equal(node.running, true);
  assert.equal(node.queued, false);
  assert.deepEqual(node.pendingTasks, [{taskId: 'task-running', kind: 'image', quantity: 2}]);

  const legacyStatus = await window.fetch('/api/canvas-image-tasks/task-running');
  assert.equal(legacyStatus.status, 200);
  assert.deepEqual(await legacyStatus.json(), {
    task_id: 'task-running', status: 'running', quantity: 2,
  });
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_keeps_running_smart_task_when_another_task_completed() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {nodes: [{
    id: 'result-1', type: 'smart-image',
    saasGenerationTaskIds: ['task-complete', 'task-running'],
    lastGenerationTaskId: 'task-running', pending: 4, running: true, queued: false,
    pendingTasks: [
      {taskId: 'task-complete', kind: 'image', quantity: 1},
      {taskId: 'task-running', kind: 'image', quantity: 3},
    ],
    images: [],
  }]},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart') return json(canvasValue);
  if (path === '/api/v1/generation-tasks/task-complete') {
    return json({task_id: 'task-complete', status: 'succeeded', quantity: 1});
  }
  if (path === '/api/v1/generation-tasks/task-complete/media') {
    return json([{
      media_id: 'media-complete', task_id: 'task-complete', kind: 'image',
      mime_type: 'image/png', state: 'persistent',
    }]);
  }
  if (path === '/api/v1/generation-tasks/task-running') {
    return json({task_id: 'task-running', status: 'running', quantity: 3});
  }
  if (path === '/api/v1/media/media-complete/thumbnail?size=512') {
    return new Response(new Uint8Array([1, 2, 3]), {status: 200, headers: {'Content-Type': 'image/png'}});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:smart-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-smart');
  const node = (await response.json()).canvas.nodes[0];
  assert.deepEqual(node.images.map(item => item.media_id), ['media-complete']);
  assert.equal(node.pending, 3);
  assert.equal(node.running, true);
  assert.equal(node.queued, false);
  assert.deepEqual(node.pendingTasks, [{taskId: 'task-running', kind: 'image', quantity: 3}]);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_rehydrates_running_classic_tasks_into_output_nodes() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-classic', title: 'Classic', kind: 'classic', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {
    nodes: [
      {id: 'source-1', type: 'api', lastGenerationTaskId: 'task-running'},
      {id: 'output-1', type: 'output', images: []},
    ],
    connections: [{from: 'source-1', to: 'output-1'}],
  },
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-classic') return json(canvasValue);
  if (path === '/api/v1/generation-tasks/task-running') {
    return json({task_id: 'task-running', status: 'running', quantity: 1});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-classic/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:classic-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-classic');
  const canvas = (await response.json()).canvas;
  const sourceNode = canvas.nodes.find(node => node.id === 'source-1');
  const outputNode = canvas.nodes.find(node => node.id === 'output-1');
  assert.equal(sourceNode.runStatus, 'running');
  assert.equal(outputNode._pending.length, 1);
  assert.equal(outputNode._pending[0].canvasTaskId, 'task-running');
  assert.equal(outputNode._pending[0].canvasTaskType, 'online-image');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_keeps_only_running_classic_placeholder_after_mixed_restore() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-classic', title: 'Classic', kind: 'classic', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {
    nodes: [
      {
        id: 'source-1', type: 'api',
        saasGenerationTaskIds: ['task-complete', 'task-running'],
        lastGenerationTaskId: 'task-running', runStatus: 'running', running: true,
      },
      {
        id: 'output-1', type: 'output', images: [],
        _pending: [
          {id: 'old-complete', canvasTaskId: 'task-complete'},
          {id: 'old-running', canvasTaskId: 'task-running'},
        ],
      },
    ],
    connections: [{from: 'source-1', to: 'output-1'}],
  },
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-classic') return json(canvasValue);
  if (path === '/api/v1/generation-tasks/task-complete') {
    return json({task_id: 'task-complete', status: 'succeeded', quantity: 1});
  }
  if (path === '/api/v1/generation-tasks/task-complete/media') {
    return json([{
      media_id: 'media-complete', task_id: 'task-complete', kind: 'image',
      mime_type: 'image/png', state: 'persistent',
    }]);
  }
  if (path === '/api/v1/generation-tasks/task-running') {
    return json({task_id: 'task-running', status: 'running', quantity: 1});
  }
  if (path === '/api/v1/media/media-complete/thumbnail?size=512') {
    return new Response(new Uint8Array([1, 2, 3]), {status: 200, headers: {'Content-Type': 'image/png'}});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-classic/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:classic-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-classic');
  const canvas = (await response.json()).canvas;
  const sourceNode = canvas.nodes.find(node => node.id === 'source-1');
  const outputNode = canvas.nodes.find(node => node.id === 'output-1');
  assert.equal(sourceNode.runStatus, 'running');
  assert.equal(sourceNode.running, true);
  assert.deepEqual(outputNode.images.map(item => item.media_id), ['media-complete']);
  assert.deepEqual(outputNode._pending.map(item => item.canvasTaskId), ['task-running']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_recovers_untracked_canvas_tasks_after_early_page_exit() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {nodes: []},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart') return json(canvasValue);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/recent?limit=20') {
    return json([{task_id: 'task-orphan', status: 'running', quantity: 1, created_at: '2026-08-11T00:01:00Z'}]);
  }
  if (path === '/api/v1/generation-tasks/task-orphan') {
    return json({task_id: 'task-orphan', status: 'running', quantity: 1, created_at: '2026-08-11T00:01:00Z'});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:smart-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-smart');
  const canvas = (await response.json()).canvas;
  assert.equal(canvas.nodes.length, 1);
  assert.equal(canvas.nodes[0].type, 'smart-image');
  assert.equal(canvas.nodes[0].lastGenerationTaskId, 'task-orphan');
  assert.deepEqual(canvas.nodes[0].pendingTasks, [{taskId: 'task-orphan', kind: 'image', quantity: 1}]);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_recovers_active_tasks_even_when_recent_history_omits_them() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const canvasValue = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {nodes: []},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart') return json(canvasValue);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/recent?limit=20') return json([]);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/active') {
    return json([{task_id: 'task-active', status: 'queued', quantity: 1}]);
  }
  if (path === '/api/v1/generation-tasks/task-active') {
    return json({task_id: 'task-active', status: 'queued', quantity: 1});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:smart-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-smart');
  const canvas = (await response.json()).canvas;
  assert.equal(canvas.nodes.length, 1);
  assert.equal(canvas.nodes[0].lastGenerationTaskId, 'task-active');
  assert.equal(canvas.nodes[0].pending, 1);
  assert.deepEqual(canvas.nodes[0].pendingTasks, [{taskId: 'task-active', kind: 'image', quantity: 1}]);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_does_not_restore_a_generation_node_deleted_and_saved_by_the_user() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
let storedCanvas = {
  canvas_id: 'canvas-smart', title: 'Smart', kind: 'smart', version: 3,
  created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z',
  document: {
    nodes: [{
      id: 'generated-node', type: 'smart-image', lastGenerationTaskId: 'task-active',
      saasGenerationTaskIds: ['task-active'], pendingTasks: [{taskId: 'task-active', kind: 'image'}],
      pending: 1, queued: true,
    }],
    connections: [],
  },
};
let savedBody = null;
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input, options = {}) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-smart' && options.method === 'PUT') {
    savedBody = JSON.parse(options.body);
    storedCanvas = {...storedCanvas, version: storedCanvas.version + 1, document: savedBody.document};
    return json(storedCanvas);
  }
  if (path === '/api/v1/canvases/canvas-smart') return json(storedCanvas);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/recent?limit=20') return json([]);
  if (path === '/api/v1/canvases/canvas-smart/generation-tasks/active') {
    return json([{task_id: 'task-active', status: 'queued', quantity: 1}]);
  }
  if (path === '/api/v1/generation-tasks/task-active') {
    return json({task_id: 'task-active', status: 'queued', quantity: 1});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-smart/smart', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:smart-preview';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const opened = await window.fetch('/api/canvases/canvas-smart');
  const canvas = (await opened.json()).canvas;
  assert.equal(canvas.nodes.length, 1);
  canvas.nodes = [];
  canvas.connections = [];
  const saved = await window.fetch('/api/canvases/canvas-smart', {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(canvas),
  });
  assert.equal(saved.status, 200);
  assert.deepEqual(savedBody.document.dismissedGenerationTaskIds, ['task-active']);
  const reopened = await window.fetch('/api/canvases/canvas-smart');
  const reopenedCanvas = (await reopened.json()).canvas;
  assert.deepEqual(reopenedCanvas.nodes, []);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_continues_observing_rehydrated_saas_tasks_until_completion() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function pollSmartCanvasTask');
const end = source.indexOf('function finalizeSmartPendingTask', start);
const functionSource = source.slice(start, end);
const activeSmartTaskPolls = new Map();
const tr = value => value;
class ImageTaskRecoverSignal extends Error {}
let reads = 0;
global.window = {SaaSCanvasGateway: {active: true}};
global.setTimeout = callback => { callback(); return 1; };
global.fetch = async path => {
  if (path === '/api/v1/generation-tasks/task-running/media') {
    return new Response(JSON.stringify([{media_id: 'media-1'}]), {status: 200});
  }
  assert.equal(path, '/api/v1/generation-tasks/task-running');
  reads += 1;
  const payload = reads === 1
    ? {task_id: 'task-running', status: 'running'}
    : {task_id: 'task-running', status: 'succeeded'};
  return new Response(JSON.stringify(payload), {status: 200});
};
eval(functionSource);
(async () => {
  const result = await pollSmartCanvasTask('task-running');
  assert.equal(reads, 2);
  assert.deepEqual(result.images, [{media_id: 'media-1'}]);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_continues_observing_rehydrated_saas_tasks_until_completion() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function pollCanvasImageTask');
const end = source.indexOf('async function waitCanvasImageTaskResult', start);
const functionSource = source.slice(start, end);
const activeCanvasTaskPolls = new Set();
const tr = value => value;
const sleep = async () => {};
const findPendingTask = () => ({pending: {canvasTaskId: 'task-running'}});
const cascadeTargetIdFromOptions = () => '';
const ensureCascadeActive = () => {};
const cascadeBackendRestartMessage = () => 'backend restarted';
const responseErrorMessage = async response => response.text();
const normalizeCanvasTaskError = error => error.message;
const isCascadeAbortError = () => false;
let reads = 0;
let completedTaskId = '';
const completeCanvasImageTask = taskId => { completedTaskId = taskId; };
const failCanvasImageTask = () => { throw new Error('task should not fail'); };
global.window = {SaaSCanvasGateway: {active: true}};
const cascadeFetch = async path => {
  assert.equal(path, '/api/canvas-image-tasks/task-running');
  reads += 1;
  const payload = reads === 1
    ? {task_id: 'task-running', status: 'running'}
    : {task_id: 'task-running', status: 'succeeded', result: {images: [{media_id: 'media-1'}]}};
  return new Response(JSON.stringify(payload), {status: 200});
};
eval(functionSource);
(async () => {
  const status = await pollCanvasImageTask('task-running');
  assert.equal(status, 'succeeded');
  assert.equal(reads, 2);
  assert.equal(completedTaskId, 'task-running');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_enables_retention_for_saas_media_identified_by_media_id() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function smartNodeToolbarHtml');
const end = source.indexOf('function duplicateSmartNodeMediaToCanvas', start);
const functionSource = source.slice(start, end);
const imageForDisplay = value => value;
const mediaKindForItem = () => 'image';
const escapeAttr = String;
const escapeHtml = String;
const smartNodeToolbarImageIndex = () => 0;
global.window = {SaaSCanvasGateway: {active: true}};
eval(functionSource);
const html = smartNodeToolbarHtml({
  id: 'node-1', type: 'smart-image',
  images: [{url: 'blob:authenticated-preview', media_id: 'media-1', mediaState: 'temporary'}],
});
const actionIndex = html.indexOf('data-smart-node-action="keep"');
const tagStart = html.lastIndexOf('<button', actionIndex);
const tagEnd = html.indexOf('>', actionIndex);
const keepButton = html.slice(tagStart, tagEnd + 1);
assert.ok(actionIndex >= 0, 'retention action should be rendered');
assert.equal(keepButton.includes('disabled'), false, 'SaaS temporary media should be retainable');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_enables_retention_for_saas_media_identified_by_media_id() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function openImageNodeMenu');
const end = source.indexOf('function openImageNodePreview', start);
const helperStart = source.indexOf('function isRetainableCanvasMedia');
const helperEnd = source.indexOf('async function promoteCanvasMedia', helperStart);
const functionSource = `${source.slice(helperStart, helperEnd)}\n${source.slice(start, end)}`;
const nodes = [{
  id: 'node-1', type: 'image', url: 'blob:authenticated-preview',
  media_id: 'media-1', mediaState: 'temporary',
}];
const closeCreateMenu = () => {};
const mediaKindForNode = () => 'image';
const isMissingAssetUrl = () => false;
const outputUrlValue = item => typeof item === 'string' ? item : item?.url || '';
const escapeAttr = String;
const refreshIcons = () => {};
const closeImageNodeMenu = () => {};
const openImageNodePreview = () => {};
const openImageEditor = () => {};
const keepImageNodeMedia = async () => {};
const pickImageForNode = () => {};
const imageNodeMenu = {
  innerHTML: '', style: {}, classList: {add() {}}, querySelector() { return {}; },
};
global.window = {SaaSCanvasGateway: {active: true}};
eval(functionSource);
openImageNodeMenu('node-1', 10, 10);
assert.ok(imageNodeMenu.innerHTML.includes('data-image-keep="node-1"'), 'retention action should be rendered');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_edit_outputs_keep_the_new_persistent_media_identity() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function canvasUploadedMediaFields');
const end = source.indexOf('function renderCropBox', start);
assert.ok(start >= 0, 'classic canvas media identity behavior is missing');
const nodes = [];
const selected = {clear() {}, add() {}};
const uid = prefix => `${prefix}-1`;
const imageEditorOutputPoint = (node, offsetY = 0) => ({x: node.x + node.w + 36, y: node.y + offsetY});
global.window = {SaaSCanvasGateway: {active: true}};
eval(source.slice(start, end));
const uploaded = {
  url: 'blob:edited-preview', name: 'edited.png', kind: 'image',
  media_id: 'media-edited', mime_type: 'image/png', mediaState: 'persistent',
};
const created = addGeneratedImageNode(uploaded, {x: 10, y: 20, w: 200}, 'edited');
assert.equal(created.media_id, 'media-edited');
assert.equal(created.mime_type, 'image/png');
assert.equal(created.mediaState, 'persistent');
const replaced = {
  url: 'blob:original-preview', name: 'original.png', media_id: 'media-original',
  mime_type: 'image/png', mediaState: 'persistent',
};
replaceCanvasImageNodeMedia(replaced, uploaded);
assert.equal(replaced.url, 'blob:edited-preview');
assert.equal(replaced.media_id, 'media-edited');
assert.equal(replaced.mime_type, 'image/png');
assert.equal(replaced.mediaState, 'persistent');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_generator_keeps_original_media_and_separate_mask_in_an_edit_chain() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function generatedImageRefs');
const end = source.indexOf('function orderedSources', start);
const CANVAS_MEDIA_OUTPUT_TYPES = ['generator','midjourney','msgen','video','rh'];
const canvasUploadedMediaFields = item => ({
  ...(item?.media_id ? {media_id: item.media_id} : {}),
  ...(item?.mime_type ? {mime_type: item.mime_type} : {}),
  ...(item?.mediaState ? {mediaState: item.mediaState} : {}),
});
const outputUrlValue = item => typeof item === 'string' ? item : item?.url || '';
const outputImageName = () => 'output.png';
const mediaKindForOutputItem = () => 'image';
const mediaKindForNode = () => 'image';
const mediaKindForRef = () => 'image';
const tr = value => value;
const trf = value => value;
const loopContext = null;
const connections = [
  {from: 'original', to: 'mask'},
  {from: 'mask', to: 'generator'},
];
const nodes = [
  {id: 'original', type: 'image', url: 'blob:original', name: 'original.png', media_id: 'media-original', mime_type: 'image/png'},
  {id: 'mask', type: 'image', url: 'blob:mask', name: 'mask.png', role: 'mask', media_id: 'media-mask', mime_type: 'image/png'},
  {id: 'generator', type: 'generator'},
];
eval(source.slice(start, end));
const refs = generatorSources(nodes[2]).flatMap(item => item.refs || []);
assert.deepEqual(refs.map(ref => [ref.media_id, ref.role]), [
  ['media-original', ''],
  ['media-mask', 'mask'],
]);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_dragged_output_keeps_its_persistent_media_identity() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function canvasOutputMediaDragValue');
const end = source.indexOf('async function downloadUrl', start);
assert.ok(start >= 0, 'classic output drag media behavior is missing');
const nodes = [];
const uid = prefix => `${prefix}-1`;
const ensureCanvas = () => true;
const mediaKindForRef = () => 'image';
const defaultPoint = () => ({x: 0, y: 0});
const outputImageName = () => 'edited.png';
const render = () => {};
const scheduleSave = () => {};
const canvasUploadedMediaFields = item => ({
  media_id: item.media_id, mime_type: item.mime_type, mediaState: item.mediaState,
});
eval(source.slice(start, end));
const dragged = canvasOutputMediaDragValue({
  url: 'blob:edited-preview', name: 'edited.png', kind: 'image',
  media_id: 'media-edited', mime_type: 'image/png', mediaState: 'persistent',
  run: {internal: 'must not be copied'},
});
assert.deepEqual(dragged, {
  url: 'blob:edited-preview', name: 'edited.png', kind: 'image',
  media_id: 'media-edited', mime_type: 'image/png', mediaState: 'persistent',
});
createImageCardFromOutput(dragged, {x: 20, y: 30});
assert.equal(nodes.length, 1);
assert.equal(nodes[0].url, 'blob:edited-preview');
assert.equal(nodes[0].media_id, 'media-edited');
assert.equal(nodes[0].mediaState, 'persistent');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_saas_delivery_keeps_media_identity_in_source_and_output_nodes() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const mergeStart = source.indexOf('function mergeGeneratedOutputs');
const mergeEnd = source.indexOf('function pendingById', mergeStart);
const appendStart = source.indexOf('function appendOutputImages(');
const appendEnd = source.indexOf('function outputCompareUrlFor', appendStart);
const outputUrlValue = item => typeof item === 'string' ? item : item?.url || '';
const mediaKindForOutputItem = () => 'image';
const isVideoUrl = () => false;
const syncConnectedOutputsFromGenerated = () => {};
const canvasUploadedMediaFields = item => ({
  media_id: item.media_id, mime_type: item.mime_type, mediaState: item.mediaState,
});
eval(source.slice(mergeStart, mergeEnd));
eval(source.slice(appendStart, appendEnd));
const delivered = {
  url: 'blob:delivered-preview', name: 'result.png', kind: 'image',
  media_id: 'media-1', mime_type: 'image/png', mediaState: 'temporary',
};
const sourceNode = {id: 'source-1', type: 'generator', generatedOutputs: []};
mergeGeneratedOutputs(sourceNode, [delivered]);
assert.equal(sourceNode.generatedOutputs[0].media_id, 'media-1');
const outputNode = {id: 'output-1', type: 'output', images: []};
appendOutputImages(outputNode, [delivered]);
assert.equal(outputNode.images[0].media_id, 'media-1');
assert.equal(outputNode.images[0].mime_type, 'image/png');
assert.equal(outputNode.images[0].mediaState, 'temporary');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_edit_replacement_uses_the_new_persistent_media_identity() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function smartCanvasUploadedMediaFields');
const end = source.indexOf('function applyOutpaintSizeToSmartParams', start);
assert.ok(start >= 0, 'smart canvas media identity behavior is missing');
const node = {id: 'node-1', images: [{
  url: 'blob:original-preview', name: 'original.png', kind: 'image',
  media_id: 'media-original', mime_type: 'image/png', mediaState: 'persistent',
}]};
const currentEditImage = () => ({node, index: 0});
const mediaKindForItem = () => 'image';
let selectedId = '';
let selectedImage = {nodeId: '', index: -1};
global.window = {SaaSCanvasGateway: {active: true}};
eval(source.slice(start, end));
const changed = replaceEditedImage({
  url: 'blob:edited-preview', name: 'edited.webp', kind: 'image',
  media_id: 'media-edited', mime_type: 'image/webp', mediaState: 'persistent',
});
assert.equal(changed, true);
assert.equal(node.images[0].url, 'blob:edited-preview');
assert.equal(node.images[0].media_id, 'media-edited');
assert.equal(node.images[0].mime_type, 'image/webp');
assert.equal(node.images[0].mediaState, 'persistent');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_prompt_request_preserves_mask_role_and_media_identity() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function buildPromptRequest');
const end = source.indexOf('function outgoingConnectionsFor', start);
const collectPromptParts = () => [];
const originalPromptTextFromParts = () => '';
const blockedInputRefKeys = () => new Set();
const defaultReferenceImagesFor = () => [
  {url: 'blob:source', name: 'source.png', media_id: 'media-source', mime_type: 'image/png'},
  {url: 'blob:mask', name: 'mask.png', role: 'mask', media_id: 'media-mask', mime_type: 'image/png'},
];
const uniqueReferenceImages = value => value;
const inputRefKey = item => item.media_id || item.url;
const isSmartGroupNode = () => false;
const textForNode = () => '';
const inputPromptTextFor = () => 'replace the selected area';
const mediaKindForItem = () => 'image';
const rhDefaultPromptSuggestion = () => '';
const tr = value => value;
const settings = {engine: 'api'};
const smartLoopContext = null;
const SMART_REFERENCE_IMAGE_MAX = 3;
eval(source.slice(start, end));
const request = buildPromptRequest({id: 'node-1'});
assert.deepEqual(request.refs.map(ref => [ref.media_id, ref.role]), [
  ['media-source', 'image_1'],
  ['media-mask', 'mask'],
]);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_saas_delivery_keeps_media_identity_in_the_result_node() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function finalizeSmartPendingTask');
const end = source.indexOf('async function resumeSmartPendingNode', start);
const smartPendingTasks = node => (node.pendingTasks || []).filter(task => task?.taskId);
const smartPendingTaskQuantity = task => Math.max(1, Math.trunc(Number(task?.quantity) || 1));
const resultMediaUrls = value => value;
const cleanHistoryImages = value => value;
const copyMediaSizeFields = (source, target) => target;
const stripImageGenerationMeta = value => value;
const smartCanvasUploadedMediaFields = item => ({
  media_id: item.media_id, mime_type: item.mime_type, mediaState: item.mediaState,
});
const mediaNodeDefaultScale = () => 1;
const nowMs = () => 1000;
const MEDIA_NODE_DEFAULT_SCALE = 1;
const MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE = 1;
const MEDIA_GROUP_DEFAULT_SCALE = 1;
eval(source.slice(start, end));
const node = {
  id: 'result-1', pending: 1, pendingTasks: [{taskId: 'task-1', kind: 'image'}],
  images: [], runStartedAt: 900,
};
finalizeSmartPendingTask(node, 'task-1', [{
  url: 'blob:delivered-preview', name: 'result.png', kind: 'image',
  media_id: 'media-1', mime_type: 'image/png', mediaState: 'temporary',
}], 'image');
assert.equal(node.images.length, 1);
assert.equal(node.images[0].media_id, 'media-1');
assert.equal(node.images[0].mime_type, 'image/png');
assert.equal(node.images[0].mediaState, 'temporary');
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_locally_blocks_unreleased_external_execution() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert gateway.status_code == 200
    assert "isBlockedLegacyExternalRequest" in gateway.text
    assert "'/api/canvas-video'" in gateway.text
    assert "'/api/canvas-llm'" not in gateway.text.split("const blockedLegacyExternalPrefixes")[0]
    assert "saasFetch('/api/v1/canvas-llm'" in gateway.text
    assert "'/api/runninghub/'" in gateway.text
    assert "该能力尚未安全接入 SaaS" in gateway.text
    assert gateway.text.index("isBlockedLegacyExternalRequest(url.pathname)") < gateway.text.rindex(
        "return nativeFetch(input, options)"
    )


def test_saas_canvas_gateway_maps_legacy_image_uploads_to_account_owned_canvas_media() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert "blockedLegacyExternalPrefixes" in gateway.text
    for path in (
        "'/generate'",
        "'/api/image-task-query'",
        "'/api/cloud-video/upload'",
        "'/api/midjourney/'",
        "'/api/angle/'",
        "'/api/ms/'",
        "'/api/canvas-image-tasks/'",
    ):
        assert path in gateway.text
    assert "url.pathname === '/api/canvas-image-tasks' && method === 'POST'" in gateway.text
    assert "url.pathname === '/api/online-image' && method === 'POST'" in gateway.text
    assert "submitImageGeneration(input, options)" in gateway.text
    assert "url.pathname === '/api/ai/upload' && method === 'POST'" in gateway.text
    assert "/api/v1/canvases/${encodeURIComponent(canvasId)}/media" in gateway.text
    assert "uploadCanvasImages(input, options)" in gateway.text
    assert "image/png" in gateway.text
    assert "image/jpeg" in gateway.text
    assert "image/webp" in gateway.text
    assert "media_id" in gateway.text
    assert "mediaState: 'persistent'" in gateway.text
    assert "URL.createObjectURL" in gateway.text


def test_saas_canvas_gateway_converts_canvas_source_and_mask_to_separate_account_references() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const calls = [];
let generationBody = null;
function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input, options = {}) {
  const path = String(input);
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/v1/image-models') return json({data: [{
    logical_model: 'gpt-image-2', output_specs: [{output_spec: '4k', status: 'available'}],
  }]});
  if (path === '/api/v1/media/canvas-source/use-as-reference') {
    assert.equal(options.method, 'POST');
    return json({media_id: 'reference-source'}, 201);
  }
  if (path === '/api/v1/media/canvas-mask/use-as-reference') {
    assert.equal(options.method, 'POST');
    return json({media_id: 'reference-mask'}, 201);
  }
  if (path === '/api/v1/generation-tasks') {
    generationBody = JSON.parse(options.body);
    return json({task_id: generationBody.task_id, status: 'queued'}, 201);
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-1/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {
  getElementById() { return null; },
  body: {appendChild() {}},
  createElement() { return {setAttribute() {}, appendChild() {}, style: {}, textContent: ''}; },
};
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvas-image-tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      model: 'gpt-image-2', prompt: 'replace the dog with a cat', resolution: '4k',
      aspect_ratio: '1:1', output_format: 'webp',
      reference_images: [
        {media_id: 'canvas-source', role: 'image_1', url: 'blob:must-not-leak'},
        {media_id: 'canvas-mask', role: 'mask', url: 'blob:must-not-leak'},
      ],
    }),
  });
  assert.equal(response.status, 201);
  assert.deepEqual(generationBody.reference_media_ids, ['reference-source']);
  assert.equal(generationBody.mask_media_id, 'reference-mask');
      assert.deepEqual(generationBody.params, {
        aspect_ratio: '1:1', quality: 'auto', size: '2880x2880', resolution_tier: '4k', output_format: 'webp',
      });
  assert.equal(JSON.stringify(generationBody).includes('blob:'), false);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saas_canvas_gateway_connects_the_classic_online_image_api_to_saas_generation() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
let generationBody = null;
function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input, options = {}) {
  const path = String(input);
  if (path === '/api/v1/image-models') return json({data: [{
    logical_model: 'gpt-image-2', output_specs: [{output_spec: '4k', status: 'available'}],
  }]});
  if (path === '/api/v1/generation-tasks') {
    generationBody = JSON.parse(options.body);
    return json({task_id: generationBody.task_id, status: 'queued'}, 202);
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-1/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
  setTimeout() { return 1; }, clearTimeout() {},
};
global.document = {
  getElementById() { return null; },
  body: {appendChild() {}},
  createElement() { return {setAttribute() {}, addEventListener() {}, appendChild() {}, style: {}, textContent: ''}; },
};
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/online-image', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      model: 'gpt-image-2|||4k', prompt: 'classic canvas request', quantity: 2,
      resolution: '4k', aspect_ratio: '1:1',
    }),
  });
  assert.equal(response.status, 202);
  assert.equal(generationBody.canvas_id, 'canvas-1');
  assert.equal(generationBody.quantity, 2);
  assert.equal(generationBody.logical_model, 'gpt-image-2');
  assert.equal(generationBody.output_spec, '4k');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_classic_canvas_uses_the_async_task_path_instead_of_reading_online_image_immediately() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")

    assert classic.status_code == 200
    run_start = classic.text.index("async function runGenerator(genId")
    run_end = classic.text.index("async function runGeneratorLegacy", run_start)
    active_run = classic.text[run_start:run_end]
    assert "createCanvasImageTasks(payload, count" in active_run
    assert "completeSaaSGenerationSubmission(gen, out, taskInfos)" in active_run
    assert "fetch('/api/online-image'" not in active_run


def test_smart_canvas_clears_a_multi_image_saas_task_as_one_completed_batch() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function smartPendingTasks');
const end = source.indexOf('async function resumeSmartPendingNode', start);
const resultMediaUrls = value => value;
const cleanHistoryImages = value => value;
const copyMediaSizeFields = (source, target) => target;
const stripImageGenerationMeta = value => value;
const smartCanvasUploadedMediaFields = () => ({});
const mediaNodeDefaultScale = () => 1;
const nowMs = () => 1000;
const MEDIA_NODE_DEFAULT_SCALE = 1;
const MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE = 1;
const MEDIA_GROUP_DEFAULT_SCALE = 1;
eval(source.slice(start, end));
const node = {
  id: 'result-1', pending: 4,
  pendingTasks: [{taskId: 'task-batch', kind: 'image', quantity: 4}],
  images: [], runStartedAt: 900,
};
finalizeSmartPendingTask(node, 'task-batch', [
  {url: 'blob:1'}, {url: 'blob:2'}, {url: 'blob:3'}, {url: 'blob:4'},
]);
assert.equal(node.pending, 0);
assert.equal('pendingTasks' in node, false);
assert.equal(node.images.length, 4);
assert.equal(node.running, false);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_restores_pending_count_from_task_quantities() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")

    assert smart.status_code == 200
    assert "tasks.reduce((total, task) => total + smartPendingTaskQuantity(task), 0)" in smart.text
    assert "n.pending = Math.max(pendingQuantity" in smart.text


def test_saas_canvas_gateway_maps_permanent_canvas_deletion_and_authenticated_media_reads() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const calls = [];
async function nativeFetch(input, options = {}) {
  const headers = new Headers(options.headers || {});
  calls.push({path: String(input), method: options.method || 'GET', authorization: headers.get('Authorization')});
  return new Response(null, {status: options.method === 'DELETE' ? 204 : 200});
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-1/classic', origin: 'http://test', replace() {}},
  sessionStorage: {getItem() { return 'account-token'; }, removeItem() {}},
  fetch: nativeFetch, addEventListener() {}, crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const deleted = await window.fetch('/api/canvases/canvas-1?confirm_running_tasks=true', {method: 'DELETE'});
  assert.equal(deleted.status, 204);
  const media = await window.fetch('/api/v1/media/media-1/content');
  assert.equal(media.status, 200);
  assert.deepEqual(calls, [
    {path: '/api/v1/canvases/canvas-1?confirm_running_tasks=true', method: 'DELETE', authorization: 'Bearer account-token'},
    {path: '/api/v1/media/media-1/content', method: 'GET', authorization: 'Bearer account-token'},
  ]);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_canvas_downloads_fetch_media_bytes_instead_of_calling_the_removed_proxy() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")

    assert "/api/download-output" not in classic.text
    assert "/api/download-output" not in smart.text
    assert "await fetch(raw)" in classic.text
    assert "await fetch(item.url)" in smart.text


def test_canvas_group_downloads_use_the_account_isolated_media_archive_in_saas() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")

    assert "url.pathname === '/api/v1/media/archive'" in gateway.text
    assert "/api/v1/media/archive" in classic.text
    assert "/api/v1/media/archive" in smart.text
    assert "media_ids" in classic.text
    assert "media_ids" in smart.text


def test_saas_canvas_gateway_blocks_unmigrated_local_runtime_data() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert "isBlockedLegacyLocalDataRequest" in gateway.text
    assert "blockedLegacyLocalDataPrefixes" in gateway.text
    for path in (
        "'/api/asset-library'",
        "'/api/prompt-libraries'",
        "'/api/canvas-assets/download'",
        "'/api/smart-canvas/prompt-templates'",
        "'/api/canvases/trash'",
    ):
        assert path in gateway.text
    assert "旧本地数据不属于当前 SaaS 账户" in gateway.text
    assert "(meta|restore|purge)" in gateway.text
    assert gateway.text.index("isBlockedLegacyLocalDataRequest(url.pathname)") < gateway.text.rindex(
        "return nativeFetch(input, options)"
    )


def test_saas_smart_canvas_maps_local_workflow_transfer_to_account_scoped_endpoints() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    page = client.get("/workspace/canvases/canvas-1/smart?id=canvas-1")

    assert "exportCanvasWorkflow" in gateway.text
    assert "importCanvasWorkflow" in gateway.text
    assert "/workflows/export`" in gateway.text
    assert "/workflows/import`" in gateway.text
    assert "url.pathname === '/api/canvas-workflows/export'" in gateway.text
    assert "url.pathname === '/api/canvas-workflows/import'" in gateway.text
    assert "await restoreCanvasMediaPreviews(workflow)" in gateway.text
    assert "'/api/canvas-workflows/'" not in gateway.text
    assert "ZIP 内图片会持久导入当前画布，并立即占用存储容量" in page.text
    assert "/web-assets/saas-canvas-gateway.js?v=canvas-generation-delivery-5" in page.text


def test_saas_canvas_gateway_projects_legacy_config_from_the_safe_model_catalog() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    assert gateway.status_code == 200
    assert "safeLegacyConfig" in gateway.text
    assert "url.pathname === '/api/config'" in gateway.text
    assert "await imageCatalog()" in gateway.text
    assert "api_providers" in gateway.text
    assert "id: 'saas-platform'" in gateway.text
    assert "name: '平台模型'" in gateway.text
    assert "rh_apps: []" in gateway.text
    assert "rh_workflows: []" in gateway.text
    assert gateway.text.index("url.pathname === '/api/config'") < gateway.text.rindex(
        "return nativeFetch(input, options)"
    )


def test_saas_canvas_runninghub_nodes_hide_legacy_platform_controls_without_rewriting_data() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")

    assert classic.status_code == 200
    assert smart.status_code == 200
    notice = "节点数据已保留，等待平台管理员安全发布该能力"
    assert notice in classic.text
    assert notice in smart.text
    classic_render = classic.text.index("function renderRhBody(node)")
    classic_guard = classic.text.index("window.SaaSCanvasGateway?.active", classic_render)
    classic_legacy_controls = classic.text.index("ensureRhNodeSelection(node)", classic_render)
    assert classic_guard < classic_legacy_controls
    smart_render = smart.text.index("function renderRunningHubParams()")
    smart_guard = smart.text.index("window.SaaSCanvasGateway?.active", smart_render)
    smart_legacy_controls = smart.text.index("selectedRunningHubRef()", smart_render)
    assert smart_guard < smart_legacy_controls


def test_saas_canvas_video_controls_stay_blocked_and_llm_uses_account_configuration() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")

    assert "尚未配置 LLM。" in classic.text
    assert "视频节点数据已保留，等待平台管理员安全发布该能力" in classic.text
    assert "尚未配置 LLM。" in smart.text
    assert "视频配置已保留，等待平台管理员安全发布该能力" in smart.text
    classic_llm = classic.text.index("function renderLLMBody(node)")
    assert classic.text.index("window.SaaSCanvasGateway?.active", classic_llm) < classic.text.index(
        "resolveChatProviderId", classic_llm
    )
    classic_video = classic.text.index("function renderVideoBody(node)")
    assert classic.text.index("window.SaaSCanvasGateway?.active", classic_video) < classic.text.index(
        "sanitizeVideoNodeProviderModel", classic_video
    )
    smart_video = smart.text.index("function renderApiVideoParams()")
    assert smart.text.index("window.SaaSCanvasGateway?.active", smart_video) < smart.text.index(
        "videoApiProviders", smart_video
    )
    smart_llm = smart.text.index("function promptNodeBodyHtml(node)")
    assert smart.text.index("window.SaaSCanvasGateway?.active", smart_llm) < smart.text.index(
        "resolveChatProviderId", smart_llm
    )


def test_saas_canvas_editor_does_not_publish_other_legacy_pages() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    assert client.get("/static/js/canvas.js").status_code == 200
    assert client.get("/static/api-settings.html").status_code == 404


def test_canvas_workspace_publishes_safe_create_and_delete_mutations() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert "data-canvas-rename" not in script.text
    assert "data-canvas-delete" in script.text
    assert "confirm_running_tasks=true" in script.text
    assert "kind: 'smart'" in script.text
    assert '<option value="classic">' not in script.text


def test_python_saas_web_shell_exposes_prompt_only_asset_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/assets")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/personal-assets" not in script.text
    assert "媒体资产" not in script.text
    assert "工作流管理" not in script.text
    assert "画布资产" not in script.text
    assert "本地素材" not in script.text
    assert "提示词库" in script.text
    assert "/api/v1/prompt-libraries" in script.text


def test_smart_canvas_restores_generation_log_context() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    page = client.get("/workspace/canvases/test-canvas/smart")
    script = client.get("/static/js/smart-canvas.js")

    assert page.status_code == 200
    assert "entryVersion=smart-task-status-2" in page.text
    assert "rememberSmartPendingLog" in script.text
    assert "restoredSmartPendingLogContext" in script.text
    assert "pendingLogStartedAt" in script.text


def test_canvas_generation_logs_expire_after_24_hours() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic_page = client.get("/workspace/canvases/classic-log/classic")
    smart_page = client.get("/workspace/canvases/smart-log/smart")
    classic_script = client.get("/static/js/canvas.js")
    smart_script = client.get("/static/js/smart-canvas.js")

    assert "entryVersion=smart-task-status-2" in classic_page.text
    assert "entryVersion=smart-task-status-2" in smart_page.text
    assert "GENERATION_LOG_RETENTION_MS = 24 * 60 * 60 * 1000" in classic_script.text
    assert "SMART_GENERATION_LOG_RETENTION_MS = 24 * 60 * 60 * 1000" in smart_script.text
    assert "pruneGenerationLogs" in classic_script.text
    assert "pruneSmartGenerationLogs" in smart_script.text
    assert "smartLogToggle?.addEventListener('click'" in smart_script.text
    assert "window.openSmartCanvasLog = openSmartCanvasLog" in smart_script.text


def test_python_saas_web_shell_exposes_recent_generation_task_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/generations")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/generation-tasks/recent?limit=100" in script.text
    assert "最近生成任务" in script.text
    assert "最近24小时内生成的结果" in script.text
    assert "任务来源" in script.text
    assert "文生图" in script.text
    assert "智能画布" in script.text
    assert "经典画布" in script.text
    assert "data-generation-view" in script.text
    assert "冻结额度" in script.text
    assert "已删除画布" in script.text
    assert "generation-history-page" in script.text
    assert "generation-history-table" in script.text
    assert ".generation-history-table { overflow: visible; }" in client.get("/web-assets/styles.css").text
    generation_page_start = script.text.index("async function workspaceGenerationsPage")
    generation_page_end = script.text.index("function providersTable", generation_page_start)
    generation_page_source = script.text[generation_page_start:generation_page_end]
    assert "/api/v1/canvases/${encodeURIComponent(canvas.canvas_id)}/generation-tasks/recent" not in generation_page_source


def test_python_saas_web_shell_exposes_top_level_image_generation_with_references() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/images")
    script = client.get("/web-assets/app.js")
    styles = client.get("/web-assets/styles.css")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "navigationItem('图片生成', '/workspace/images'" in script.text
    assert "data-image-generation-form" in script.text
    assert "image-workbench" in script.text
    assert "image-workbench-sidebar" in script.text
    assert "image-workbench-results" in script.text
    assert "模型来源" in script.text
    assert "来源地址、路由和凭据由管理员维护" not in script.text
    assert "创作内容" in script.text
    assert "image-chat-composer" in script.text
    assert "描述你想生成的画面" not in script.text
    assert "自定义像素尺寸不对，请修改！" in script.text
    assert "宽高必须是 16 的倍数，范围 256–8192 像素" in script.text
    assert "name=\"custom_width\"" in script.text
    assert "resolutionSelect.disabled = isCustom" in script.text
    assert ".image-chat-input .image-generate-button" in styles.text
    assert ".page.image-workbench-page" in styles.text
    assert ".image-workbench:not(.image-workbench-inpainting) [data-reference-list]" in styles.text
    assert "padding-top:7px; display:flex; align-items:flex-start" in styles.text
    assert "referenceHint=1" in page.text
    assert "grid-template-rows:auto minmax(min-content,1fr) minmax(min-content,1.2fr)" in styles.text
    assert "grid-template-columns:repeat(var(--reference-grid-columns,3),var(--reference-thumbnail-size,104px))" in styles.text
    assert "aspect-ratio:1" in styles.text
    assert "layoutPolish=7" in page.text
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in styles.text
    assert "[...state.imageSessionEntries].reverse()" in script.text
    assert "[...(entry.media || [])].reverse()" in script.text
    assert "body.scrollTop = 0" in script.text
    assert "customSize.classList.toggle('is-active', isCustom)" in script.text
    assert "input.value = presetDimensions[index]" in script.text
    assert "input.dataset.customValue || input.value" in script.text
    assert "customWidthInput?.dataset.customValue" in script.text
    assert 'aria-disabled="${aspectRatio === \'custom\' ? \'false\' : \'true\'}"' in script.text
    assert "输出设置" in script.text
    assert "尚未生成图片" in script.text
    assert "/api/v1/image-models" in script.text
    assert "/api/v1/reference-media" in script.text
    assert "/api/v1/reference-media/content" in script.text
    assert "'X-Reference-Filename': encodeURIComponent(file.name || 'reference-image')" in script.text
    assert "else if (options.body && !headers.has('Content-Type'))" in script.text
    assert "/api/v1/reference-media/recent" in script.text
    assert "data-reference-delete" in script.text
    assert "image-reference-thumbnail" in styles.text
    assert "image-reference-notice" in script.text
    assert ".image-reference-dropzone .image-reference-notice" in styles.text
    assert "reference_media_ids" in script.text
    assert "'超时退款'" in script.text
    assert "'上游明确失败'" in script.text
    assert "个最近任务未完成" in script.text
    image_page_start = script.text.index("function workspaceImagesPage")
    image_page_end = script.text.index("const generationTaskStatusLabel", image_page_start)
    image_page_source = script.text[image_page_start:image_page_end]
    assert '<label>操作</label>' not in image_page_source
    assert '<select name="operation">' not in image_page_source
    assert "state.referenceMediaEntries.length ? 'edit' : 'generate'" in image_page_source
    assert "values.get('input_fidelity') || 'auto'" in image_page_source
    assert "referenceEdit=1" in page.text
    assert '<div class="field image-fidelity-field" data-image-fidelity-field>' in image_page_source
    assert "fidelityField.hidden = false" in image_page_source
    assert "grid-template-rows:auto minmax(min-content,1fr) minmax(min-content,1.2fr)" in styles.text
    assert "width:100%; grid-column:1; justify-self:stretch" in styles.text
    assert "fidelityLayout=2" in page.text
    assert "referenceLimits=4" in page.text
    assert "data-max-reference-images" in script.text
    assert "syncImageReferenceLimit(currentForm)" in image_page_source
    assert "--reference-list-height" in image_page_source
    assert "imageReferenceListHeight()" in script.text
    assert "function imageReferenceGridLayout" in script.text
    assert "return 112" in script.text
    assert "height:112px; min-height:112px; max-height:112px" in styles.text
    assert "--reference-thumbnail-size" in styles.text
    assert "data-reference-expand" in script.text
    assert "openImageLightbox(await loadOriginalMediaUrl(media)" in script.text
    assert "container._referenceExpandBound" in script.text
    assert "container.addEventListener('click'" in script.text
    assert "cursor:zoom-in" in styles.text
    assert "当前模型最多上传" in script.text
    assert 'value="inpaint"' in script.text
    assert 'name="input_fidelity"' in script.text
    assert "mask_media_id" in script.text
    assert "transparent" not in script.text
    assert "涂抹区域将被重绘" in script.text
    assert "painted ? 0 : 255" in client.get("/static/js/smart-canvas.js").text
    assert 'name="resolution_tier"' in script.text
    assert 'name="aspect_ratio"' in script.text
    assert 'name="output_format"' in script.text
    assert "次请求）" in script.text
    assert "次流式请求" not in script.text
    for value, label in (
        ("1k", "1K"),
        ("2k", "2K"),
        ("4k", "4K"),
        ("1:1", "1:1"),
        ("4:3", "4:3"),
        ("16:9", "16:9"),
        ("3:4", "3:4"),
        ("9:16", "9:16"),
        ("png", "PNG"),
        ("jpeg", "JPEG"),
        ("webp", "WEBP"),
    ):
        assert f'value="{value}"' in script.text
        assert f">{label}</option>" in script.text
    assert "resolution_tier: values.get('resolution_tier')" in script.text
    assert "output_format: values.get('output_format')" in script.text
    assert "files.length > limit" in script.text
    assert "/api/v1/generation-tasks" in script.text
    assert "/api/v1/media/${encodeURIComponent(media.media_id)}/content" in script.text
    assert "/api/v1/media/archive" in script.text
    assert "data-image-download" in script.text
    assert "data-image-download-all" in script.text
    assert "data-image-clear" in script.text
    assert "data-image-lightbox" in script.text
    assert "data-image-details" in script.text
    assert "data-image-use-reference" in script.text
    assert "data-image-reuse-prompt" in script.text
    assert "/use-as-reference" in script.text
    assert "参数详情" in script.text
    assert "作为参考图" in script.text
    assert "复用提示词" in script.text
    assert "data-image-lightbox-stage" in script.text
    assert "data-image-zoom-in" in script.text
    assert "data-image-zoom-out" in script.text
    assert ".image-lightbox-stage" in styles.text
    assert "touch-action: none" in styles.text
    assert "结果保存24小时，但占用个人存储空间，不用请及时删除" in script.text
    assert "/api/v1/generation-tasks/recent?limit=100" in script.text
    assert "task.canvas_id === null" in script.text
    assert "item.state === 'temporary'" in script.text
    assert "method: 'DELETE'" in script.text
    assert "data-image-delete" in script.text
    assert "个人存储空间不足 10MB，请清理后再生成" in script.text
    assert "data-image-save" not in script.text
    assert "window.localStorage" in script.text
    assert "image-generation-pending" in script.text
    assert "setInterval" not in script.text
    assert "/api/v1/generation-tasks/${encodeURIComponent(taskId)}/events" in script.text
    assert "Accept: 'text/event-stream'" in script.text
    assert ".image-result-actions { flex: 0 0 auto;" in styles.text
    assert ".image-result-actions button { white-space: nowrap; }" in styles.text


def test_canvas_custom_pixel_sizes_are_validated_before_generation() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    classic = client.get("/static/js/canvas.js")
    smart = client.get("/static/js/smart-canvas.js")
    gateway = client.get("/web-assets/saas-canvas-gateway.js")

    for source in (classic.text, smart.text, gateway.text):
        assert "自定义像素尺寸不对，请修改！" in source
        assert "value >= 256 && value <= 8192 && value % 16 === 0" in source
    assert "宽高必须是 16 的倍数，范围 256–8192 像素" in classic.text
    assert "宽高必须是 16 的倍数，范围 256–8192 像素" in smart.text
    assert "qualitySelect.disabled = node.resolution === 'custom'" in classic.text
    assert "settings.resolution === 'custom'" in smart.text
    assert "['1k','2k','4k','custom'].includes(gen.resolution)" in classic.text
    assert "['1k','2k','4k','custom'].includes(runSettings.resolution)" in smart.text


def test_python_saas_web_shell_exposes_separate_inpainting_page_below_image_generation() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/workspace/inpainting")
    script = client.get("/web-assets/app.js")
    styles = client.get("/web-assets/styles.css")
    index = client.get("/web-assets/index.html")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    image_navigation = "navigationItem('图片生成', '/workspace/images'"
    inpainting_navigation = "navigationItem('局部重绘', '/workspace/inpainting'"
    canvas_navigation = "navigationItem('无限画布', '/workspace/canvases'"
    assert script.text.index(image_navigation) < script.text.index(inpainting_navigation) < script.text.index(canvas_navigation)
    assert "function workspaceInpaintingPage()" in script.text
    assert "workspaceImagesPage('inpaint')" in script.text
    assert "if (state.route === '/workspace/inpainting') return workspaceInpaintingPage();" in script.text
    assert '<input name="operation" type="hidden" value="inpaint">' in script.text
    assert "局部重绘模式" in script.text
    assert "待编辑原图（第 1 张）" in script.text
    assert "打开遮罩编辑器" in script.text
    assert "涂抹局部重绘区域" in script.text
    assert "无需上传遮罩图片" in script.text
    assert 'input name="mask"' not in script.text
    assert "data-mask-editor-canvas" in script.text
    assert "data-mask-editor-brush" in script.text
    assert "data-mask-editor-clear" in script.text
    assert "data-mask-editor-undo" in script.text
    assert "maskPixels.data[offset + 3] = selected ? 0 : 255" in script.text
    editor_start = script.text.index("function imageMaskEditorHTML")
    page_start = script.text.index("function workspaceImagesPage", editor_start)
    editor_source = script.text[editor_start:page_start]
    assert 'type="file"' not in editor_source
    assert "maskCanvas.width = canvas.width" in editor_source
    assert "maskCanvas.height = canvas.height" in editor_source
    assert "new File([blob], 'inpainting-mask.png', { type: 'image/png' })" in editor_source
    assert 'name="input_fidelity"' in script.text
    assert ".image-inpainting-mode" in styles.text
    assert "inpaintingPage=1" in index.text
    assert "maskEditor=1" in index.text


def test_top_level_image_workspace_renders_before_restoring_heavy_history() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    page_start = script.text.index("function workspaceImagesPage")
    page_end = script.text.index("const generationTaskStatusLabel", page_start)
    page_source = script.text[page_start:page_end]

    assert "loadingPage('图片生成')" not in page_source
    assert "shell(inpaintingPage ? '局部重绘' : '图片生成'" in page_source
    assert "void Promise.all([" in page_source
    assert "正在加载可用图片模型…" in page_source
    shell_index = page_source.index("shell(inpaintingPage ? '局部重绘' : '图片生成'")
    assert shell_index < page_source.index("ensureAccountSummary()")
    assert shell_index < page_source.index("restoreRecentImageResults()")
    assert shell_index < page_source.index("restoreRecentReferenceMedia()")


def test_top_level_image_workspace_restores_queued_tasks_as_visible_pending_cards() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const htmlStart = source.indexOf('function imageSessionResultsHTML');
const htmlEnd = source.indexOf('function sessionMedia', htmlStart);
const restoreStart = source.indexOf('async function restoreRecentImageResults');
const restoreEnd = source.indexOf('async function completeImageSessionEntry', restoreStart);
const state = {imageSessionEntries: [], previewUrls: []};
const clearImagePreviewUrls = () => {};
const authenticatedMediaObjectUrl = async () => { throw new Error('queued tasks have no media yet'); };
const observed = [];
const observeImageSessionTask = entry => { observed.push(entry.taskId); };
const optionalApi = async path => {
  assert.equal(path, '/api/v1/generation-tasks/recent?limit=100');
  return [{
    task_id: 'task-queued', canvas_id: null, status: 'queued', quantity: 2,
    logical_model: 'gpt-image-2', prompt: '排队中的提示词',
    params: {resolution_tier: '4k', aspect_ratio: '1:1', output_format: 'png'},
    created_at: '2026-08-11T00:00:00Z',
  }];
};
const escapeHTML = value => String(value ?? '');
const formatCredits = String;
const formatDate = String;
eval(source.slice(htmlStart, htmlEnd));
eval(source.slice(restoreStart, restoreEnd));
(async () => {
  await restoreRecentImageResults();
  assert.equal(state.imageSessionEntries.length, 1);
  assert.deepEqual(state.imageSessionEntries[0], {
    taskId: 'task-queued', status: 'pending', taskStatus: 'queued', quantity: 2, startNumber: 1,
    logicalModel: 'gpt-image-2', prompt: '排队中的提示词',
    params: {resolution_tier: '4k', aspect_ratio: '1:1', output_format: 'png'},
    createdAt: '2026-08-11T00:00:00Z', media: [],
  });
  const html = imageSessionResultsHTML();
  assert.equal((html.match(/image-generation-pending/g) || []).length, 2);
  assert.ok(html.includes('请求 1 正在排队'));
  assert.ok(html.includes('请求 2 正在排队'));
  assert.deepEqual(observed, ['task-queued']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_top_level_image_workspace_observes_a_restored_task_until_delivery() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function loadImageSessionMedia');
const end = source.indexOf('function bindImageSessionActions', start);
assert.ok(start >= 0, 'image task observation behavior is missing');
const state = {
  route: '/workspace/images', imageSessionEntries: [], previewUrls: [], user: null,
};
const entry = {
  taskId: 'task-queued', status: 'pending', quantity: 1, startNumber: 4,
  logicalModel: 'gpt-image-2', prompt: '继续观察', params: {}, media: [],
};
state.imageSessionEntries.push(entry);
let taskReads = 0;
let renders = 0;
let mediaReads = 0;
const notices = [];
const api = async path => {
  if (path === '/api/v1/generation-tasks/task-queued') {
    taskReads += 1;
    return taskReads === 1
      ? {task_id: 'task-queued', status: 'running'}
      : {task_id: 'task-queued', status: 'succeeded', quantity: 1, delivered_quantity: 1, created_at: '2026-08-11T00:01:00Z'};
  }
  if (path === '/api/v1/generation-tasks/task-queued/media') {
    mediaReads += 1;
    return mediaReads === 1 ? [] : [
      {media_id: 'media-1', mime_type: 'image/png', state: 'temporary'},
    ];
  }
  if (path === '/api/v1/auth/me') return {user_id: 'user-1'};
  throw new Error(`unexpected request: ${path}`);
};
const authenticatedMediaObjectUrl = async media => `blob:${media.media_id}`;
const renderImageSessionResults = () => { renders += 1; };
const toast = message => { notices.push(message); };
global.window = {setTimeout(callback) { callback(); return 1; }};
eval(source.slice(start, end));
(async () => {
  await observeImageSessionTask(entry);
  assert.equal(taskReads, 2);
  assert.equal(mediaReads, 2, 'image page must wait for committed media visibility');
  assert.equal(entry.status, 'succeeded');
  assert.equal(entry.media.length, 1);
  assert.equal(entry.media[0].sessionNumber, 4);
  assert.equal(entry.media[0].previewUrl, 'blob:media-1');
  assert.deepEqual(state.user, {user_id: 'user-1'});
  assert.ok(renders >= 1);
  assert.deepEqual(notices, ['已生成 1 张图片']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_top_level_image_submission_keeps_a_queued_response_visible() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function continueImageSessionEntry');
const end = source.indexOf('function bindImageSessionActions', start);
assert.ok(start >= 0, 'queued submission continuation behavior is missing');
const observed = [];
const observeImageSessionTask = entry => { observed.push(entry.taskId); };
const completeImageSessionEntry = async () => { throw new Error('queued task is not complete'); };
const renderImageSessionResults = () => {};
eval(source.slice(start, end));
(async () => {
  const entry = {status: 'pending', taskId: '', createdAt: '', media: []};
  await continueImageSessionEntry(entry, {
    task_id: 'task-queued', status: 'queued', created_at: '2026-08-11T00:00:00Z',
  });
  assert.equal(entry.status, 'pending');
  assert.equal(entry.taskId, 'task-queued');
  assert.equal(entry.createdAt, '2026-08-11T00:00:00Z');
  assert.deepEqual(observed, ['task-queued']);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_generation_task_page_keeps_failed_tasks_visible_with_a_persistent_notice() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")
    styles = client.get("/web-assets/styles.css")

    assert "/api/v1/generation-tasks/recent?limit=100" in script.text
    assert "failure_message" in script.text
    assert "最近生成任务" in script.text
    assert "生成失败" in script.text
    assert ".failure-notice" in styles.text


def test_generation_task_page_lists_safe_result_availability_without_content_urls() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")
    styles = client.get("/web-assets/styles.css")

    assert "/generation-tasks/${encodeURIComponent(task.task_id)}/media" in script.text
    assert "已交付" in script.text
    assert "临时可用至" in script.text
    assert "已保留" in script.text
    assert "已过期" in script.text
    assert "已释放" in script.text
    assert "generation-viewer-backdrop" in script.text
    assert "data-generation-view-close" in script.text
    assert ".generation-viewer-backdrop" in styles.text
    assert ".generation-viewer-grid" in styles.text
    assert "content_url" not in script.text


def test_image_workspace_falls_back_to_bounded_polling_when_sse_disconnects() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function loadImageSessionMedia');
const end = source.indexOf('function bindImageSessionActions', start);
const entry = {taskId:'task-sse-drop', status:'pending', quantity:1, startNumber:1, media:[]};
const state = {imageTaskObservers:new Set(), user:null};
let streamCalls = 0;
let taskReads = 0;
const streamGenerationTask = async () => { streamCalls += 1; throw new Error('connection dropped'); };
const api = async path => {
  if(path === '/api/v1/generation-tasks/task-sse-drop') {
    taskReads += 1;
    return {task_id:'task-sse-drop', status:'succeeded', delivered_quantity:1, created_at:'2026-08-24T00:00:00Z'};
  }
  if(path === '/api/v1/generation-tasks/task-sse-drop/media') return [{media_id:'media-1'}];
  if(path === '/api/v1/auth/me') return {user_id:'user-1'};
  throw new Error(`unexpected request: ${path}`);
};
const authenticatedMediaObjectUrl = async media => `blob:${media.media_id}`;
const renderImageSessionResults = () => {};
const notices = [];
const toast = message => notices.push(message);
global.window = {setTimeout(callback) { callback(); return 1; }};
eval(source.slice(start, end));
(async () => {
  await observeImageSessionTask(entry);
  assert.equal(streamCalls, 1);
  assert.equal(taskReads, 1);
  assert.equal(entry.status, 'succeeded');
  assert.deepEqual(entry.media.map(item => item.media_id), ['media-1']);
  assert.deepEqual(notices, ['已生成 1 张图片']);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_smart_canvas_edit_media_keeps_thumbnail_and_crop_creates_a_new_node() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function smartCanvasUploadedMediaFields');
const end = source.indexOf('async function applyImageOutpaint', start);
assert.ok(start >= 0 && end > start, 'smart canvas edit helpers are missing');
const node = {id: 'source', type: 'smart-image', x: 10, y: 20, images: [{url: '/api/v1/media/original/content', media_id: 'media-original'}]};
const image = node.images[0];
let cropState = {nodeId: 'source', x: 10, y: 20, w: 60, h: 50};
const created = [];
global.window = {SaaSCanvasGateway: {active: true}};
global.document = {getElementById(id) { assert.equal(id, 'cropImage'); return {
  naturalWidth: 100, naturalHeight: 80, clientWidth: 100, clientHeight: 80,
}; }};
global.document.createElement = tag => ({width: 0, height: 0, getContext() { return {drawImage() {}}; }, toBlob(cb) { cb({}); }});
const currentEditImage = () => ({node, image, index: 0});
const uploadCroppedBlob = async () => ({url: '/api/v1/media/crop/content', name: 'crop.png', media_id: 'media-crop', thumbnail: 'blob:crop-thumb', mime_type: 'image/png', mediaState: 'persistent'});
const imageLayout = () => ({width: 200});
const nodeScale = () => 1;
const createNode = (x, y, images) => { created.push({x, y, images}); return {id: 'crop'}; };
const closeImageEditor = () => {};
const render = () => {};
const scheduleSave = () => {};
eval(source.slice(start, end));
assert.equal(smartCanvasUploadedMediaFields({media_id: 'm', thumbnail: 'blob:t'}).thumbnail, 'blob:t');
(async () => {
  await applyImageCrop();
  assert.equal(created.length, 1, 'crop should create a separate image node');
  assert.equal(node.images[0].media_id, 'media-original', 'source image must remain unchanged');
  assert.equal(created[0].images[0].media_id, 'media-crop');
  assert.equal(created[0].images[0].thumbnail, 'blob:crop-thumb');
})();
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_classic_canvas_crop_creates_a_separate_image_node() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    classic = client.get("/static/js/canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function applyImageCrop');
const end = source.indexOf('async function applyImageOutpaint', start);
assert.ok(start >= 0 && end > start, 'classic crop helper is missing');
const node = {id: 'source', type: 'image', x: 10, y: 20, w: 200, url: 'blob:original', name: 'original.png'};
let cropState = {nodeId: 'source', x: 10, y: 20, w: 60, h: 50};
const created = [];
const nodes = [node];
global.document = {getElementById(id) { assert.equal(id, 'cropImage'); return {naturalWidth: 100, naturalHeight: 80, clientWidth: 100, clientHeight: 80}; }, createElement() { return {width: 0, height: 0, getContext() { return {drawImage() {}}; }, toBlob(cb) { cb({}); }}; }};
const uploadCroppedBlob = async () => ({url: 'blob:crop', name: 'crop.png', media_id: 'media-crop', thumbnail: 'blob:crop-thumb'});
const addGeneratedImageNode = (file, sourceNode, suffix, offsetY, extra) => { created.push({file, sourceNode, suffix, offsetY, extra}); };
const closeImageEditor = () => {};
const render = () => {};
const scheduleSave = () => {};
eval(source.slice(start, end));
(async () => {
  await applyImageCrop();
  assert.equal(created.length, 1);
  assert.equal(created[0].sourceNode, node);
  assert.equal(created[0].suffix, 'crop');
  assert.equal(node.url, 'blob:original');
})();
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=classic.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_smart_canvas_reconciles_media_after_terminal_sse_event() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    smart = client.get("/static/js/smart-canvas.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function pollSmartCanvasTask');
const end = source.indexOf('function finalizeSmartPendingTask', start);
const activeSmartTaskPolls = new Map();
const tr = value => value;
class ImageTaskRecoverSignal extends Error {}
const delivered = [];
let mediaReads = 0;
const terminal = {task_id:'task-terminal-first', status:'succeeded', delivered_quantity:1};
global.window = {SaaSCanvasGateway: {
  active:true,
  async streamGenerationTask(_taskId, onMedia, onTask) {
    await onMedia([]);
    await onTask(terminal);
    return terminal;
  },
  async previewMedia(item) { return {...item, url:`blob:${item.media_id}`}; },
}};
global.fetch = async path => {
  if(path === '/api/v1/generation-tasks/task-terminal-first') return new Response(JSON.stringify(terminal), {status:200});
  if(path === '/api/v1/generation-tasks/task-terminal-first/media') {
    mediaReads += 1;
    return new Response(JSON.stringify(mediaReads === 1 ? [] : [{media_id:'media-1'}]), {status:200});
  }
  throw new Error(`unexpected request: ${path}`);
};
global.setTimeout = callback => { callback(); return 1; };
eval(source.slice(start, end));
(async () => {
  const result = await pollSmartCanvasTask('task-terminal-first', {onMedia:items => delivered.push(...items)});
  assert.equal(mediaReads, 2, 'successful task must wait for media visibility');
  assert.deepEqual(result.images.map(item => item.media_id), ['media-1']);
  assert.deepEqual(delivered.map(item => item.media_id), ['media-1']);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=smart.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_csp_bridge_binds_actions_directly_inside_propagation_boundaries() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    bridge = client.get("/static/js/csp-event-bridge.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
function element(attributes, dataset={}) {
  return {
    dataset,
    listeners:{},
    getAttribute(name) { return attributes[name] || null; },
    hasAttribute(name) { return Object.hasOwn(attributes, name); },
    addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); },
  };
}
const closeButton = element({'data-csp-click':'closeSmartWorkflowTransferModal'});
const boundary = element({}, {cspStopPropagation:'true'});
global.document = {querySelectorAll() { return [closeButton, boundary]; }};
let closed = 0;
global.closeSmartWorkflowTransferModal = () => { closed += 1; };
eval(source);
let stopped = 0;
closeButton.listeners.click[0]({stopPropagation() { stopped += 1; }});
boundary.listeners.click[0]({stopPropagation() { stopped += 1; }});
assert.equal(closed, 1);
assert.equal(stopped, 1);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=bridge.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_canvas_creation_form_is_smart_only_without_a_kind_selector() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text

    assert 'id="canvas-kind"' not in script
    assert "canvas-kind-hint" not in script
    assert "kind: 'smart'" in script
    assert "object_key" not in script


def test_generation_task_page_refreshes_with_read_only_requests() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert "data-generation-refresh" in script.text
    assert "刷新状态" in script.text
    assert "workspaceGenerationsPage" in script.text
    assert "/generation-tasks/${encodeURIComponent(task.task_id)}/reconcile" not in script.text
    assert "setInterval" not in script.text


def test_generation_task_page_can_clear_terminal_history_and_uses_wide_layout() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js").text
    styles = client.get("/web-assets/styles.css").text
    page = client.get("/workspace/generations").text

    assert "data-generation-clear-history" in script
    assert "api('/api/v1/generation-tasks/history', { method: 'DELETE' })" in script
    assert "任务记录、额度流水和生成图片不会被删除" in script
    assert "'generation-history-page'" in script
    assert ".page.generation-history-page" in styles
    assert ".generation-history-table { overflow: visible; }" in styles
    assert "queueVersion=user-generation-queue-6" in page


def test_generation_task_page_exposes_read_only_view_actions_without_retry_or_delete() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert "data-generation-view" in script.text
    assert "data-generation-retry" not in script.text
    assert "重新尝试" not in script.text
    assert "/generation-tasks/${encodeURIComponent(taskId)}/retry" not in script.text
    assert "data-image-delete" in script.text  # 图片工作区删除仍保留，不属于任务查看器。
    assert "setInterval" not in script.text


def test_python_saas_web_shell_exposes_login_and_registration_entry_pages() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    assert client.get("/").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200


def test_canvas_list_uses_stable_creation_order_and_displays_canvas_type() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js").text
    styles = client.get("/web-assets/styles.css").text

    assert "Date.parse(right.created_at || '') - Date.parse(left.created_at || '')" in script
    assert "Date.parse(right.updated_at || '') - Date.parse(left.updated_at || '')" not in script
    assert "<th>画布类型</th>" in script
    assert "已停用（历史数据保留）" in script
    assert "历史画布不提供编辑入口" in script
    assert '<option value="classic">' not in script
    assert "<th>画布标识</th>" not in script
    assert "canvas-create-form" in script
    assert "canvas-preview-generating" in script
    assert "latestCanvasPreviewTask" in script
    assert "/api/v1/generation-tasks/recent?limit=100" in script
    assert "/generation-tasks/recent?limit=20" in script
    assert "/generation-tasks/${encodeURIComponent(task.task_id)}/media" in script
    assert "state.route === '/workspace/canvases'" in script
    assert ".canvas-create-form .canvas-title-field" in styles
    assert ".canvas-preview img" in styles


def test_canvas_preview_prefers_the_latest_active_or_successful_generation() -> None:
    script = TestClient(create_app(InMemoryAccountAccess())).get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function canvasPreviewHTML');
const end = source.indexOf('function clearCanvasPreviewUrls', start);
const escapeHTML = value => String(value);
eval(source.slice(start, end));
const tasks = [
  {task_id: 'old-success', canvas_id: 'canvas-1', status: 'succeeded', created_at: '2026-08-11T10:00:00Z'},
  {task_id: 'new-running', canvas_id: 'canvas-1', status: 'running', created_at: '2026-08-11T11:00:00Z'},
  {task_id: 'other', canvas_id: 'canvas-2', status: 'succeeded', created_at: '2026-08-11T12:00:00Z'},
];
assert.equal(latestCanvasPreviewTask('canvas-1', tasks).task_id, 'new-running');
assert.match(canvasPreviewHTML({status: 'generating'}), /canvas-preview-generating/);
assert.match(canvasPreviewHTML({status: 'ready', url: 'blob:preview'}), /blob:preview/);
assert.match(canvasPreviewHTML(null), /canvas-preview-empty/);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_running_multi_image_task_only_marks_the_current_image_as_generating() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function imageSessionResultsHTML');
const end = source.indexOf('function sessionMedia', start);
const state = {imageHistoryLoading: false, imageSessionEntries: [{
  taskId: 'task-running', status: 'pending', taskStatus: 'running', quantity: 4,
  startNumber: 1, logicalModel: 'gpt-image-2', media: [],
}]};
const escapeHTML = value => String(value ?? '');
const formatCredits = String;
const formatDate = String;
eval(source.slice(start, end));
const html = imageSessionResultsHTML();
assert.ok(html.includes('请求 1 正在生图'));
assert.ok(html.includes('请求 2 等待前一张完成'));
assert.ok(html.includes('请求 3 等待前一张完成'));
assert.ok(html.includes('请求 4 等待前一张完成'));
assert.equal((html.match(/请求 \d+ 正在生图/g) || []).length, 1);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_canvas_list_adds_export_after_delete_for_smart_canvases_only() -> None:
    script = TestClient(create_app(InMemoryAccountAccess())).get("/web-assets/app.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('function canvasPreviewHTML');
const end = source.indexOf('function canvasEditorUrl', start);
const escapeHTML = value => String(value ?? '');
const formatDate = value => String(value ?? '');
eval(source.slice(start, end));
const common = {version: 1, updated_at: '2026-08-12T00:00:00Z'};
const smart = canvasesTable([{...common, canvas_id: 'smart-1', title: 'Smart', kind: 'smart'}]);
const classic = canvasesTable([{...common, canvas_id: 'classic-1', title: 'Classic', kind: 'classic'}]);
assert.match(smart, /data-canvas-export="smart-1"/);
assert.ok(smart.indexOf('data-canvas-export') > smart.indexOf('data-canvas-delete'));
assert.equal(classic.includes('data-canvas-export'), false);
assert.equal(classic.includes('data-canvas-open'), false);
assert.match(classic, /已停用（历史数据保留）/);
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "exportSmartCanvasFromList" in script.text
    assert "/workflows/export`" in script.text
    assert "智能画布工作流已导出" in script.text


def test_python_saas_web_shell_exposes_administrator_model_routing_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/model-routing")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/providers" in script.text
    assert "/health-check" in script.text
    assert "/routing-policy" in script.text
    assert "每 24 小时" in script.text
    assert "5 分钟" not in script.text


def test_administrator_model_routing_page_explains_the_selection_flow_in_order() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js").text

    source_step = script.index("① API 来源")
    mapping_step = script.index("② 模型映射与路由")
    health_step = script.index("③ 健康检测与选路资格")
    price_step = script.index("④ 用户售价")
    policy_step = script.index("⑤ 选择策略")
    assert source_step < mapping_step < health_step < price_step < policy_step
    assert "可参与选路" in script
    assert "来源已停用" in script
    assert "路由已停用" in script
    assert "尚未完成健康检测" in script
    assert "优先级只在健康与性能指标相同时作为最后裁决" in script


def test_administrator_model_routing_page_supports_editing_and_irreversible_deletion() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js").text
    styles = client.get("/web-assets/styles.css").text

    assert "data-provider-edit" in script
    assert "data-provider-delete" in script
    assert "data-route-edit" in script
    assert "data-route-delete" in script
    assert "取消编辑" in script
    assert "永久删除 API 来源" in script
    assert "永久删除模型路由" in script
    assert "修改地址、Key 或模型映射后" in script
    assert "method: 'DELETE'" in script
    assert "admin-routing-page" in script
    assert "routing-health-table" in script
    assert ".page.admin-routing-page" in styles
    assert ".routing-health-table" in styles
    assert "overflow-x: visible" in styles


def test_python_saas_web_shell_exposes_administrator_provider_cost_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/provider-costs")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/image-model-routes" in script.text
    assert "/api/v1/admin/provider-cost-rates" in script.text
    assert "cost_per_image_yuan" in script.text
    assert "每张成本（元）" in script.text
    assert 'value="RMB"' in script.text
    assert "data-price-delete" in script.text
    assert "method: 'PUT'" in script.text
    assert "新成本立即生效并升级版本号" in script.text
    assert "/api/v1/admin/provider-cost-summary" in script.text
    assert "已提交尝试" in script.text
    assert "估算支出（元）" in script.text


def test_administrator_provider_cost_page_explains_accounting_scope_and_summary() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js").text

    assert "记录平台向上游采购每张图片的成本" in script
    assert "不会影响用户售价" in script
    assert "不会参与模型选路" in script
    assert "成本版本会固化到每次生成尝试" in script
    assert "缺少已生效成本版本时，任务保持排队且不会调用上游" in script
    assert "配置成本估算，不代表 Provider 最终账单" in script


def test_python_saas_web_shell_exposes_administrator_runninghub_capability_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/runninghub-capabilities")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/runninghub-capabilities" in script.text
    assert "RunningHub 能力目录" in script.text
    assert "内部 workflow ID" in script.text
    assert "用户目录不会返回" in script.text
    assert "不提供删除" in script.text
    admin_detection = script.text.index("async function detectAdminProviders()")
    account_page = script.text.index("async function accountPage()", admin_detection)
    runninghub_fallback = script.text.index("'/api/v1/admin/runninghub-capabilities'", admin_detection)
    assert admin_detection < runninghub_fallback < account_page


def test_administrator_runninghub_page_publishes_ordered_input_schema_versions_and_shows_history() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert script.status_code == 200
    assert "/input-schema-versions" in script.text
    assert 'name="input_key"' in script.text
    assert 'name="label"' in script.text
    assert 'name="kind"' in script.text
    assert 'name="required"' in script.text
    assert "发布新 schema 版本" in script.text
    assert "上移" in script.text
    assert "下移" in script.text
    assert "历史版本不可编辑或删除" in script.text


def test_administrator_runninghub_page_publishes_versioned_user_prices() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    script = client.get("/web-assets/app.js")

    assert script.status_code == 200
    assert "/price-versions" in script.text
    assert 'name="credits_per_run"' in script.text
    assert 'value="0.1000"' in script.text
    assert 'name="effective_from"' in script.text
    assert "发布用户价格版本" in script.text
    assert "每次能力使用" in script.text
    assert "价格历史不可编辑或删除" in script.text


def test_python_saas_web_shell_exposes_administrator_model_price_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/model-prices")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/model-prices" in script.text
    assert "/api/v1/admin/model-prices" in script.text
    assert "当前生效价格" in script.text
    assert "发布新价格版本" in script.text
    assert 'name="max_reference_images"' in script.text
    assert "最大上传参考图张数" in script.text
    assert "按逻辑模型与成品规格保存" in script.text
    assert "max_reference_images: Number(form.get('max_reference_images'))" in script.text


def test_python_saas_web_shell_exposes_administrator_recharge_package_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/recharge-packages")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/recharge-packages" in script.text
    assert "/api/v1/admin/recharge-packages" in script.text
    assert "当前可售充值包" in script.text
    assert "发布充值包版本" in script.text
    assert "换算率 = 到账额度 ÷ 支付金额" in script.text
    assert "额度/元" in script.text


def test_python_saas_web_shell_exposes_administrator_payment_settings_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/payment-settings")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/payment-settings" in script.text
    assert "submitPaymentCheckout(order.checkout)" in script.text
    assert "易支付网关" in script.text
    assert "/api/v1/admin/recharge-rate" in script.text
    assert "普通充值换算比例" in script.text
    assert "每 1 元兑换额度" in script.text
    assert "/api/v1/recharge-orders/direct" in script.text
    assert "自定义金额" in script.text


def test_python_saas_web_shell_exposes_administrator_user_management_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    vue_source = (
        Path(__file__).parents[2] / "frontend" / "admin" / "src" / "pages" / "UsersPage.vue"
    ).read_text(encoding="utf-8")

    page = client.get("/admin/users")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/users" in script.text
    assert "/api/v1/admin/users/by-email" in script.text
    assert "/api/v1/admin/user-activity?window=" in script.text
    assert "await api('/api/v1/admin/users')" not in script.text
    assert "/credit-grants" in script.text
    assert "/generation-limit" in script.text
    assert "execution_concurrency" in script.text
    assert "保存并发" in script.text
    assert "超出任务继续排队" in script.text
    assert "/recharge-records" in script.text
    assert "充值记录" in script.text
    assert "支付充值" in script.text
    assert "人工充值" in script.text
    assert "按邮箱设置用户" in script.text
    assert "用户用量统计" in script.text
    assert "近 7 天" in script.text
    assert "近 30 天" in script.text
    assert "全部时间" in script.text
    assert "消耗额度" in script.text
    assert "失败任务" in script.text
    assert "data-user-sort" in script.text
    assert "不会默认列出所有用户" in script.text
    assert "const credits = String(grantCredits.value).trim();" in vue_source
    assert "body: JSON.stringify({ credits, reason })" in vue_source


def test_python_saas_web_shell_exposes_administrator_generation_task_management_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/generation-tasks")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert "任务管理" in script.text
    assert "/api/v1/admin/generation-tasks/active" in script.text
    assert "/api/v1/admin/generation-tasks/${encodeURIComponent(button.dataset.adminTaskCancel)}/cancel" in script.text
    assert "取消并退款" in script.text
    assert "迟到结果不会交付" in script.text


def test_python_saas_web_shell_exposes_generation_deadline_setting() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/generation-capacity")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert "任务自动截止时间" in script.text
    assert "task_deadline_minutes" in script.text
    assert "Worker 第一次准备调用上游时开始计时" in script.text
    assert "当前超时判定" in script.text
    assert "SSE 心跳不会延长" in script.text


def test_python_saas_web_shell_exposes_administrator_storage_allowance_page() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/storage-allowance")
    script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert 'id="app"' in page.text
    assert "/api/v1/admin/storage-allowance" in script.text
    assert "统一存储额度" in script.text
    assert "不会删除已有媒体" in script.text
    assert "统一存储额度（MB）" in script.text
    assert "搜索用户" in script.text
    assert "单独设置用户额度" in script.text
    assert "/api/v1/admin/users/${encodeURIComponent(user.user_id)}/storage-allowance" in script.text
    assert "单独额度优先于统一额度" in script.text
    assert "limit_mb" in script.text
    assert "1_000_000" in script.text
    assert "10_000_000" in script.text
    assert "MiB" not in script.text
    assert "GiB" not in script.text


def test_python_saas_web_shell_mounts_vue_admin_and_workspace_pages() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    page = client.get("/admin/users")
    bundle = client.get("/web-assets/admin-vue/admin.js")
    shell_script = client.get("/web-assets/app.js")

    assert page.status_code == 200
    assert bundle.status_code == 200
    assert 'type="module"' in page.text
    assert "/web-assets/admin-vue/admin.js?v=admin-vue-8" in page.text
    assert "admin-vue-root" in shell_script.text
    assert "vueAdminRoutes" in shell_script.text
    assert "按邮箱设置用户" in bundle.text
    assert "当前生成任务" in bundle.text
    assert "单独设置用户额度" in bundle.text
    assert "图片生成容量" in bundle.text
    assert "邮件设置" in bundle.text
    assert "公告与客服" in bundle.text
    assert "模型路由与价格" in bundle.text
    assert "Provider 成本" in bundle.text
    assert "特惠充值包" in bundle.text
    assert "易支付网关" in bundle.text
    assert "RunningHub 能力目录" in bundle.text
    assert "导出 TXT" in bundle.text
    assert "redeem-codes-${new Date().toISOString().slice(0,10)}.txt" in bundle.text
    assert "个人账户" in bundle.text
    assert "额度账务记录" in bundle.text
    assert "最近生成任务" in bundle.text
    assert "图片模型目录" in bundle.text
    assert "提示词库" in bundle.text
    assert "我的 LLM Provider" in bundle.text
    assert "vueWorkspaceRoutes" in shell_script.text


def test_standalone_saas_does_not_ship_the_legacy_root_page() -> None:
    legacy_shell = Path(__file__).parents[2] / "static" / "index.html"

    assert not legacy_shell.exists()

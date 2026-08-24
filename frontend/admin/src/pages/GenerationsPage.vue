<script setup lang="ts">
import { onMounted, ref } from "vue";

import { errorMessage, formatCredits, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const clearing = ref(false);
const error = ref("");
const tasks = ref<JsonRecord[]>([]);
const viewerTask = ref<JsonRecord | null>(null);
const previews = ref<Array<{ media: JsonRecord; url: string }>>([]);
const statusLabels: Record<string, string> = { queued: "排队中", running: "生成中", succeeded: "已完成", failed: "生成失败", cancelled: "已取消" };

function mediaState(media: JsonRecord): string {
  if (media.state === "temporary") return `临时可用至 ${formatDate(media.expires_at)}`;
  if (media.state === "persistent") return "已保留";
  if (media.state === "expired") return "已过期";
  if (media.state === "released") return "已释放";
  return "状态未知";
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [canvases, loadedTasks] = await Promise.all([
      props.bridge.api("/api/v1/canvases").catch(() => []),
      props.bridge.api("/api/v1/generation-tasks/recent?limit=100").catch(() => []),
    ]);
    const canvasesById = new Map(canvases.map((canvas: JsonRecord) => [canvas.canvas_id, canvas]));
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const recent = loadedTasks.filter((task: JsonRecord) => new Date(task.created_at).getTime() >= cutoff);
    const resultEntries = await Promise.all(recent.filter((task: JsonRecord) => task.status === "succeeded").map(async (task: JsonRecord) => [task.task_id, await props.bridge.api(`/api/v1/generation-tasks/${encodeURIComponent(task.task_id)}/media`).catch(() => [])]));
    const resultsByTask = new Map(resultEntries);
    tasks.value = recent.map((task: JsonRecord) => {
      const canvas = task.canvas_id ? canvasesById.get(task.canvas_id) as JsonRecord | undefined : undefined;
      const source = !task.canvas_id ? "文生图" : !canvas ? "已删除画布" : `“${canvas.title || "未命名画布"}”-${canvas.kind === "smart" ? "智能画布" : "历史画布"}`;
      return { ...task, source_label: source, results: resultsByTask.get(task.task_id) || [] };
    });
  } catch (caught) { error.value = errorMessage(caught); }
  finally { loading.value = false; }
}

function resultSummary(task: JsonRecord): string {
  if (task.failure_message) return task.failure_message;
  if (task.status !== "succeeded") return "—";
  return `已交付 ${task.delivered_quantity ?? task.results?.length ?? 0} 项`;
}

async function openViewer(task: JsonRecord): Promise<void> {
  viewerTask.value = task;
  previews.value = [];
  const media = (task.results || []).filter((item: JsonRecord) => item.kind === "image" && ["temporary", "persistent"].includes(item.state));
  previews.value = (await Promise.all(media.map(async (item: JsonRecord) => {
    try { return { media: item, url: await props.bridge.authenticatedImage(`/api/v1/media/${encodeURIComponent(item.media_id)}/content`) }; }
    catch { return null; }
  }))).filter(Boolean) as Array<{ media: JsonRecord; url: string }>;
}

async function clearTerminalHistory(): Promise<void> {
  const confirmed = await props.bridge.confirm(
    "只会从最近任务列表隐藏已结束的任务；任务记录、额度流水和生成图片不会被删除。",
    "清除已结束记录",
    "确认清除",
  );
  if (!confirmed) return;
  clearing.value = true;
  try {
    const result = await props.bridge.api("/api/v1/generation-tasks/history", { method: "DELETE" });
    props.bridge.toast(`已清除 ${Number(result.cleared_tasks || 0)} 条已结束记录`);
    await load();
  } catch (caught) { props.bridge.toast(errorMessage(caught)); }
  finally { clearing.value = false; }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取生成任务…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else><div class="page-head"><div><h1>最近生成任务</h1><p>查看排队、生成及最近完成或失败的任务。</p></div><div class="row-actions"><button class="secondary-btn" :disabled="clearing || !tasks.some(task => ['succeeded', 'failed', 'cancelled'].includes(task.status))" @click="clearTerminalHistory">清除已结束记录</button><button class="secondary-btn" :disabled="clearing" @click="load">刷新状态</button></div></div><div v-if="tasks.some(task => task.status === 'failed')" class="failure-notice" role="alert"><strong>{{ tasks.filter(task => task.status === "failed").length }} 个最近任务生成失败</strong><span>{{ tasks.find(task => task.status === "failed")?.failure_message || "图片生成失败，请重新提交任务。" }}</span></div><section class="panel"><div class="section-head" style="margin-top:0"><div><h2>最近任务</h2><p>最近24小时内生成的结果；清除只会隐藏已结束记录，不会删除任务、额度流水或生成图片。</p></div></div><div v-if="!tasks.length" class="empty">当前账户空间还没有生成任务。</div><div v-else class="table-wrap generation-history-table"><table><thead><tr><th>任务来源</th><th>逻辑模型</th><th>成品规格</th><th>请求数量</th><th>提交时冻结额度</th><th>状态</th><th>结果提示</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.task_id"><td><strong>{{ task.source_label }}</strong></td><td>{{ task.logical_model }}</td><td>{{ task.output_spec }}</td><td>{{ task.quantity }}</td><td>{{ formatCredits(task.frozen_credits) }}</td><td><span class="status" :class="task.status">{{ statusLabels[task.status] || task.status }}</span></td><td><strong v-if="task.status === 'succeeded'">{{ resultSummary(task) }}</strong><span v-else>{{ resultSummary(task) }}</span></td><td>{{ formatDate(task.updated_at) }}</td><td><button class="text-btn" @click="openViewer(task)">查看</button></td></tr></tbody></table></div></section></template>
  <div v-if="viewerTask" class="generation-viewer-backdrop" @click.self="viewerTask = null"><section class="generation-viewer" role="dialog" aria-modal="true" aria-label="生成任务结果"><div class="generation-viewer-head"><div><h2>{{ viewerTask.source_label }}</h2><p>{{ statusLabels[viewerTask.status] || viewerTask.status }} · {{ formatDate(viewerTask.updated_at) }}</p></div><button type="button" aria-label="关闭" @click="viewerTask = null">×</button></div><div class="generation-viewer-grid"><figure v-for="(preview, index) in previews" :key="preview.media.media_id"><img :src="preview.url" :alt="`任务结果 ${index + 1}`"><figcaption>结果 {{ index + 1 }} · {{ mediaState(preview.media) }}</figcaption></figure><div v-if="!previews.length" class="empty">当前任务没有仍可查看的24小时结果。</div></div></section></div>
</template>

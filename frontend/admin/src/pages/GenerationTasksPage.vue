<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { errorMessage, formatCredits, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const error = ref("");
const tasks = ref<JsonRecord[]>([]);
const cancellingTaskId = ref("");
const queuedCount = computed(() => tasks.value.filter((task) => task.status === "queued").length);
const runningCount = computed(() => tasks.value.filter((task) => task.status === "running").length);
const statusLabels: Record<string, string> = { queued: "排队中", running: "生成中" };

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    tasks.value = await props.bridge.api("/api/v1/admin/generation-tasks/active");
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function cancelTask(task: JsonRecord): Promise<void> {
  const confirmed = await props.bridge.confirm(
    `确定取消 ${task.user_email || "未知用户"} 的任务，并退回 ${formatCredits(task.frozen_credits)} 冻结额度吗？此操作不能恢复。`,
    "确认取消并退款",
    "确认取消",
  );
  if (!confirmed) return;
  cancellingTaskId.value = task.task_id;
  try {
    await props.bridge.api(`/api/v1/admin/generation-tasks/${encodeURIComponent(task.task_id)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ account_space_id: task.account_space_id }),
    });
    props.bridge.toast("任务已取消，冻结额度已退回用户");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    cancellingTaskId.value = "";
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取活动任务…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>当前生成任务</h1><p>查看全站排队中和生成中的任务。取消后会释放全部冻结额度；迟到结果不会交付。</p></div><button class="secondary-btn" type="button" @click="load">刷新</button></div>
    <section class="grid three"><article class="stat-card"><span>活动任务</span><strong>{{ tasks.length }}</strong><small>排队中与生成中的任务总数</small></article><article class="stat-card"><span>排队中</span><strong>{{ queuedCount }}</strong><small>尚未开始 Provider 调用</small></article><article class="stat-card"><span>生成中</span><strong>{{ runningCount }}</strong><small>已经开始执行的任务</small></article></section>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>任务明细</h2><p>列表按任务提交时间从早到晚排列。</p></div></div>
      <div v-if="!tasks.length" class="empty">当前没有排队中或生成中的任务。</div>
      <div v-else class="table-wrap admin-generation-task-table"><table><thead><tr><th>用户</th><th>任务</th><th>模型 / 规格</th><th>数量</th><th>冻结额度</th><th>状态</th><th>提交 / 开始时间</th><th>操作</th></tr></thead><tbody>
        <tr v-for="task in tasks" :key="task.task_id">
          <td><strong>{{ task.user_email || "未知用户" }}</strong><br><span class="mono">{{ task.user_id }}</span></td>
          <td><span class="mono">{{ task.task_id }}</span><br><span class="muted">{{ String(task.prompt || "").slice(0, 60) || "—" }}</span></td>
          <td><strong>{{ task.logical_model }}</strong><br><span class="muted">{{ task.output_spec }}</span></td>
          <td>{{ Number(task.quantity) }}</td><td>{{ formatCredits(task.frozen_credits) }}</td>
          <td><span class="status" :class="task.status">{{ statusLabels[task.status] || task.status }}</span></td>
          <td>{{ formatDate(task.created_at) }}<template v-if="task.started_at"><br><span class="muted">开始：{{ formatDate(task.started_at) }}</span></template></td>
          <td><button class="danger-btn" type="button" :disabled="cancellingTaskId === task.task_id" @click="cancelTask(task)">{{ cancellingTaskId === task.task_id ? "取消中…" : "取消并退款" }}</button></td>
        </tr>
      </tbody></table></div>
    </section>
  </template>
</template>

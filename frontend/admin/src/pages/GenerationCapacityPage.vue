<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const capacity = reactive<JsonRecord>({});

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    Object.assign(capacity, await props.bridge.api("/api/v1/admin/generation-worker-capacity"));
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  try {
    Object.assign(capacity, await props.bridge.api("/api/v1/admin/generation-worker-capacity", {
      method: "PUT",
      body: JSON.stringify({
        enabled_workers: Number(capacity.enabled_workers),
        concurrency_per_worker: Number(capacity.concurrency_per_worker),
        global_active_image_limit: Number(capacity.global_active_image_limit),
        task_deadline_minutes: Number(capacity.task_deadline_minutes),
      }),
    }));
    props.bridge.toast("生成 Worker 容量已更新");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取生成容量…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>图片生成容量</h1><p>动态调整 Worker 执行能力和全站可积压的活动图片数量，无需重启服务。</p></div></div>
    <section class="grid three">
      <article class="stat-card"><span>已部署 Worker 上限</span><strong>{{ Number(capacity.deployed_worker_limit) }}</strong><small>超过此数量需要在服务器扩容容器</small></article>
      <article class="stat-card"><span>当前启用 Worker</span><strong>{{ Number(capacity.enabled_workers) }}</strong><small>其余已部署 Worker 保持待机</small></article>
      <article class="stat-card"><span>Worker 总执行容量</span><strong>{{ Number(capacity.total_concurrency) }}</strong><small>仍会受到上游共享并发池限制</small></article>
      <article class="stat-card"><span>全站活动图片占用</span><strong>{{ Number(capacity.active_image_units) }} / {{ Number(capacity.global_active_image_limit) }}</strong><small>排队 {{ Number(capacity.queued_image_units) }} · 正在生图 {{ Number(capacity.running_image_units) }}</small></article>
      <article class="stat-card"><span>任务自动截止时间</span><strong>{{ Number(capacity.task_deadline_minutes) }} 分钟</strong><small>{{ Number(capacity.task_deadline_minutes) * 60 }} 秒 · 排队时间不计入</small></article>
    </section>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>当前超时判定</h2><p>Worker 第一次准备调用上游时开始计时，达到 {{ Number(capacity.task_deadline_minutes) }} 分钟（{{ Number(capacity.task_deadline_minutes) * 60 }} 秒）即算任务超时。</p></div></div>
      <div class="grid three">
        <article class="stat-card"><span>排队阶段</span><strong>不计时</strong><small>只有取得用户和 Provider 执行槽后才开始</small></article>
        <article class="stat-card"><span>到达截止时间</span><strong>立即中止</strong><small>停止当前结果读取，也不再发送本批剩余图片请求</small></article>
        <article class="stat-card"><span>额度与迟到结果</span><strong>退回冻结额度</strong><small>任务标记失败；截止后到达的图片作废</small></article>
      </div>
    </section>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>调整容量</h2><p>降低容量不会中断正在调用上游的任务，只影响后续任务；设置会由所有 Worker 自动读取。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="save">
        <div class="field"><label>启用 Worker 数量</label><input v-model.number="capacity.enabled_workers" type="number" min="1" :max="Number(capacity.deployed_worker_limit)" required></div>
        <div class="field"><label>单 Worker 并发数</label><input v-model.number="capacity.concurrency_per_worker" type="number" min="1" max="50" required></div>
        <div class="field"><label>全站活动图片名额上限</label><input v-model.number="capacity.global_active_image_limit" type="number" min="1" max="100000" required><small>按 queued + running 的图片数量计算；一批 4 张占 4 个名额</small></div>
        <div class="field"><label>任务自动截止时间（分钟）</label><input v-model.number="capacity.task_deadline_minutes" type="number" min="1" max="120" required><small>Provider 的绝对请求超时应小于或等于此值；达到时限后失败退款，迟到结果作废</small></div>
        <button class="primary-btn" type="submit" :disabled="saving">{{ saving ? "保存中…" : "保存生成容量" }}</button>
      </form>
    </section>
  </template>
</template>

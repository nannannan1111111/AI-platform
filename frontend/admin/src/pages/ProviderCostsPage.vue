<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import { errorMessage, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const routes = ref<JsonRecord[]>([]);
const summaries = ref<JsonRecord[]>([]);
const versions = ref<JsonRecord[]>([]);
const selectedRouteId = ref("");
const form = reactive({ provider_currency: "RMB", cost_per_image_yuan: "" });
const currentVersion = computed(() => Math.max(0, ...versions.value.map(version => Number(version.version))));

async function loadVersions(): Promise<void> {
  versions.value = selectedRouteId.value
    ? await props.bridge.api(`/api/v1/admin/provider-cost-rates?route_id=${encodeURIComponent(selectedRouteId.value)}`)
    : [];
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [loadedRoutes, loadedSummaries] = await Promise.all([
      props.bridge.api("/api/v1/admin/image-model-routes"),
      props.bridge.api("/api/v1/admin/provider-cost-summary"),
    ]);
    routes.value = loadedRoutes;
    summaries.value = loadedSummaries;
    selectedRouteId.value = routes.value[0]?.route_id || "";
    await loadVersions();
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  if (!selectedRouteId.value) return props.bridge.toast("请选择模型路由");
  saving.value = true;
  try {
    await props.bridge.api(`/api/v1/admin/provider-cost-rates/${encodeURIComponent(selectedRouteId.value)}`, {
      method: "PUT",
      body: JSON.stringify({ provider_currency: String(form.provider_currency), cost_per_image_yuan: String(form.cost_per_image_yuan) }),
    });
    props.bridge.toast("Provider 当前成本已更新");
    form.cost_per_image_yuan = "";
    await Promise.all([loadVersions(), props.bridge.api("/api/v1/admin/provider-cost-summary").then(value => { summaries.value = value; })]);
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    saving.value = false;
  }
}

watch(selectedRouteId, () => { if (!loading.value) void loadVersions(); });
onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取 Provider 成本…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>Provider 成本</h1><p>记录平台向上游采购每张图片的成本，保留可审计的历史核算依据。</p></div></div>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>它如何生效</h2><p>成本版本固化到每次生成尝试。缺少已生效成本版本时，任务保持排队且不会调用上游。</p></div></div></section>
    <section class="panel">
      <div class="section-head" style="margin-top:0"><div><h2>设置当前成本</h2><p>新成本立即生效并升级版本号，旧版本保留用于审计。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="save">
        <div class="field span-two"><label>模型路由</label><select v-model="selectedRouteId" required :disabled="!routes.length"><option v-if="!routes.length" value="">请先创建模型路由</option><option v-for="route in routes" :key="route.route_id" :value="route.route_id">{{ route.logical_model }}/{{ route.output_spec }} · {{ route.provider_model_name }}</option></select></div>
        <div class="field"><label>Provider 计费币种</label><input v-model="form.provider_currency" minlength="3" maxlength="3" required></div>
        <div class="field"><label>每张成本（元）</label><input v-model="form.cost_per_image_yuan" type="number" min="0" step="0.01" required placeholder="0.12"></div>
        <button class="primary-btn" type="submit" :disabled="saving || !routes.length">{{ saving ? "保存中…" : "保存并升级版本" }}</button>
      </form>
    </section>
    <div class="section-head"><div><h2>Provider 支出估算</h2><p>每次重试分别计入，这是配置成本估算，不代表 Provider 最终账单。</p></div></div>
    <div v-if="!summaries.length" class="empty">尚无已提交上游的生成尝试成本。</div>
    <div v-else class="table-wrap"><table><thead><tr><th>Provider</th><th>逻辑模型</th><th>币种</th><th>已提交尝试</th><th>计费图片数</th><th>估算支出（元）</th></tr></thead><tbody><tr v-for="summary in summaries" :key="`${summary.provider_id}-${summary.logical_model}`"><td><strong>{{ summary.provider_display_name }}</strong><br><span class="mono">{{ summary.provider_id }}</span></td><td>{{ summary.logical_model }}</td><td>{{ summary.provider_currency }}</td><td>{{ summary.submitted_attempts }}</td><td>{{ summary.submitted_images }}</td><td>{{ (Number(summary.total_cost_cents || 0) / 100).toFixed(2) }}</td></tr></tbody></table></div>
    <div class="section-head"><div><h2>成本版本历史</h2><p>历史生成尝试仍引用当时固化的版本。</p></div></div>
    <div v-if="!versions.length" class="empty">所选模型路由尚未发布 Provider 成本版本。</div>
    <div v-else class="table-wrap"><table><thead><tr><th>版本</th><th>单张成本</th><th>状态</th><th>更新时间</th><th>版本标识</th></tr></thead><tbody><tr v-for="version in versions" :key="version.version_id"><td>v{{ version.version }}</td><td>{{ version.cost_per_image_yuan }} 元 {{ version.provider_currency }}</td><td><span class="status" :class="Number(version.version) === currentVersion ? 'healthy' : 'pending'">{{ Number(version.version) === currentVersion ? "当前" : "历史" }}</span></td><td>{{ formatDate(version.published_at) }}</td><td class="mono">{{ version.version_id }}</td></tr></tbody></table></div>
  </template>
</template>

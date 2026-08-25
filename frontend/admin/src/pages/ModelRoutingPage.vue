<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { errorMessage, formatCredits, formatDate, localDateTimeValue } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const providers = ref<JsonRecord[]>([]);
const routes = ref<JsonRecord[]>([]);
const prices = ref<JsonRecord[]>([]);
const healthByRoute = ref<Record<string, JsonRecord | null>>({});
const policies = reactive<Record<string, string>>({});
const providerForm = reactive<JsonRecord>({});
const routeForm = reactive<JsonRecord>({});
const priceForm = reactive({ spec_key: "", credits_per_result: "", effective_from: localDateTimeValue(new Date(Date.now() + 5 * 60_000)) });
const routeHealthLabels: Record<string, string> = { unknown: "未检测", healthy: "可用", degraded: "性能下降", unhealthy: "不可用" };

const routeSpecs = computed(() => [...new Map(routes.value.map(route => [`${route.logical_model}\u0000${route.output_spec}`, route])).values()]);
const providersById = computed(() => Object.fromEntries(providers.value.map(provider => [provider.provider_id, provider])));

function resetProviderForm(): void {
  Object.assign(providerForm, { provider_id: "", code: "", display_name: "", base_url: "", image_response_mode: "auto", concurrency_group: "", max_concurrency: 20, request_timeout_seconds: 600, api_key: "" });
}

function resetRouteForm(): void {
  Object.assign(routeForm, { route_id: "", provider_id: providers.value[0]?.provider_id || "", provider_model_name: "gpt-image-2", logical_model: "gpt-image-2", output_spec: "4k", compatibility_group: "gpt-image-2/4k/v1", priority: 100, max_reference_images: 3, enabled: false });
  syncReferenceLimit();
}

function specKey(item: JsonRecord): string {
  return `${item.logical_model}\u0000${item.output_spec}`;
}

function routeLabel(route: JsonRecord): string {
  return `${route.provider_model_name} · ${route.route_id}`;
}

function normalizedReferenceLimit(value: unknown): number {
  return Math.max(0, Math.min(16, Number(value) || 0));
}

function syncReferenceLimit(): void {
  if (routeForm.route_id) return;
  const existing = routes.value.find(route => route.logical_model === String(routeForm.logical_model || "").trim() && route.output_spec === String(routeForm.output_spec || "").trim());
  if (existing) routeForm.max_reference_images = normalizedReferenceLimit(existing.max_reference_images);
}

async function optionalApi(path: string): Promise<JsonRecord | null> {
  try { return await props.bridge.api(path); } catch { return null; }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [loadedProviders, loadedRoutes, loadedPrices] = await Promise.all([
      props.bridge.api("/api/v1/admin/providers"),
      props.bridge.api("/api/v1/admin/image-model-routes"),
      props.bridge.api("/api/v1/model-prices"),
    ]);
    providers.value = loadedProviders;
    routes.value = loadedRoutes;
    prices.value = loadedPrices;
    const [healthEntries, policyEntries] = await Promise.all([
      Promise.all(routes.value.map(async route => [route.route_id, await optionalApi(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}/health`)])),
      Promise.all(routeSpecs.value.map(async spec => [specKey(spec), await props.bridge.api(`/api/v1/admin/image-models/${encodeURIComponent(spec.logical_model)}/${encodeURIComponent(spec.output_spec)}/routing-policy`)])),
    ]);
    healthByRoute.value = Object.fromEntries(healthEntries);
    for (const [key, policy] of policyEntries) policies[key] = policy?.preferred_route_id || "";
    if (!providerForm.provider_id) resetProviderForm();
    if (!routeForm.provider_id) resetRouteForm();
    if (!priceForm.spec_key) priceForm.spec_key = routeSpecs.value[0] ? specKey(routeSpecs.value[0]) : "";
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function action(work: () => Promise<unknown>, success: string): Promise<void> {
  busy.value = true;
  try {
    await work();
    props.bridge.toast(success);
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    busy.value = false;
  }
}

async function saveProvider(): Promise<void> {
  const editing = Boolean(providerForm.provider_id);
  await action(async () => {
    if (editing) {
      const body: JsonRecord = { display_name: providerForm.display_name, base_url: providerForm.base_url, image_response_mode: providerForm.image_response_mode, concurrency_group: providerForm.concurrency_group, max_concurrency: Number(providerForm.max_concurrency), request_timeout_seconds: Number(providerForm.request_timeout_seconds) };
      if (providerForm.api_key) body.api_key = providerForm.api_key;
      await props.bridge.api(`/api/v1/admin/providers/${encodeURIComponent(providerForm.provider_id)}`, { method: "PATCH", body: JSON.stringify(body) });
    } else {
      if (!providerForm.api_key) throw new Error("创建 API 来源时必须填写 API Key");
      await props.bridge.api("/api/v1/admin/providers", { method: "POST", body: JSON.stringify({ ...providerForm, provider_id: undefined, protocol: "openai_compatible_images", max_concurrency: Number(providerForm.max_concurrency), request_timeout_seconds: Number(providerForm.request_timeout_seconds) }) });
    }
    resetProviderForm();
  }, editing ? "API 来源已更新，连接变化会要求路由重新检测" : "API 来源已保存");
}

function editProvider(provider: JsonRecord): void {
  Object.assign(providerForm, { ...provider, api_key: "", image_response_mode: provider.image_response_mode || "auto", concurrency_group: provider.concurrency_group || provider.code, max_concurrency: provider.max_concurrency || 20, request_timeout_seconds: provider.request_timeout_seconds || 600 });
  document.querySelector(".provider-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function deleteProvider(provider: JsonRecord): Promise<void> {
  if (!await props.bridge.confirm(`永久删除 API 来源「${provider.display_name}」？请先删除它的全部模型路由。此操作不可恢复。`, "确认永久删除", "永久删除")) return;
  await action(() => props.bridge.api(`/api/v1/admin/providers/${encodeURIComponent(provider.provider_id)}`, { method: "DELETE" }), "API 来源已永久删除");
}

async function saveRoute(): Promise<void> {
  const editing = Boolean(routeForm.route_id);
  await action(async () => {
    const editable = { provider_model_name: routeForm.provider_model_name, compatibility_group: routeForm.compatibility_group, priority: Number(routeForm.priority), max_reference_images: Number(routeForm.max_reference_images) };
    if (editing) await props.bridge.api(`/api/v1/admin/image-model-routes/${encodeURIComponent(routeForm.route_id)}`, { method: "PATCH", body: JSON.stringify(editable) });
    else await props.bridge.api("/api/v1/admin/image-model-routes", { method: "POST", body: JSON.stringify({ provider_id: routeForm.provider_id, logical_model: routeForm.logical_model, output_spec: routeForm.output_spec, ...editable }) });
    resetRouteForm();
  }, editing ? "模型路由已更新，请重新健康检测后启用" : "模型路由已创建");
}

function editRoute(route: JsonRecord): void {
  Object.assign(routeForm, { ...route, max_reference_images: normalizedReferenceLimit(route.max_reference_images) });
  document.querySelector(".route-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (route.enabled) props.bridge.toast("路由已启用，本次只能修改优先级；停用后才能修改模型映射");
}

async function deleteRoute(route: JsonRecord): Promise<void> {
  if (!await props.bridge.confirm(`永久删除模型路由「${route.provider_model_name}」？指定优先策略会恢复为自动选择，历史任务和成本仍保留。`, "确认永久删除", "永久删除")) return;
  await action(() => props.bridge.api(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}`, { method: "DELETE" }), "模型路由已永久删除");
}

function eligibility(route: JsonRecord): { label: string; css: string } {
  const provider = providersById.value[route.provider_id];
  const health = healthByRoute.value[route.route_id];
  if (!provider?.enabled) return { label: "来源已停用", css: "unknown" };
  if (!route.enabled) return { label: "路由已停用", css: "unknown" };
  if (!health) return { label: "尚未完成健康检测", css: "pending" };
  if (!health.available) return { label: "最近检测不可用", css: "unhealthy" };
  return { label: "可参与选路", css: "healthy" };
}

async function publishPrice(): Promise<void> {
  const spec = routeSpecs.value.find(item => specKey(item) === priceForm.spec_key);
  if (!spec) return props.bridge.toast("请先创建逻辑模型路由");
  const selected = new Date(priceForm.effective_from);
  if (Number.isNaN(selected.getTime())) return props.bridge.toast("请选择有效的生效时间");
  const effective = selected.getTime() <= Date.now() ? new Date(Date.now() + 5_000) : selected;
  await action(() => props.bridge.api("/api/v1/admin/model-prices", { method: "POST", body: JSON.stringify({ logical_model: spec.logical_model, output_spec: spec.output_spec, credits_per_result: String(priceForm.credits_per_result), effective_from: effective.toISOString() }) }), "模型价格版本已发布");
}

async function deletePrice(price: JsonRecord): Promise<void> {
  if (!await props.bridge.confirm(`删除价格「${price.logical_model}/${price.output_spec}」？该逻辑模型会从用户目录移除，历史任务仍保留原价格。`, "确认删除价格", "删除价格")) return;
  await action(() => props.bridge.api(`/api/v1/admin/model-prices/${encodeURIComponent(price.version_id)}`, { method: "DELETE" }), "模型价格已删除");
}

async function savePolicy(spec: JsonRecord): Promise<void> {
  const key = specKey(spec);
  const routeId = policies[key] || "";
  await action(() => props.bridge.api(`/api/v1/admin/image-models/${encodeURIComponent(spec.logical_model)}/${encodeURIComponent(spec.output_spec)}/routing-policy`, { method: "PUT", body: JSON.stringify({ mode: routeId ? "preferred" : "automatic", preferred_route_id: routeId }) }), "路由策略已保存");
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取模型路由…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>模型路由与价格</h1><p>依次配置来源、逻辑模型映射、健康资格、用户售价和选择策略。</p></div></div>
    <section class="panel provider-editor">
      <div class="section-head" style="margin-top:0"><div><h2>① API 来源</h2><p>API Key 为只写字段，保存后只显示不可逆短指纹。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveProvider">
        <div class="field"><label>来源代码</label><input v-model="providerForm.code" required :disabled="Boolean(providerForm.provider_id)" placeholder="source-a"></div>
        <div class="field"><label>显示名称</label><input v-model="providerForm.display_name" required placeholder="图片来源 A"></div>
        <div class="field span-two"><label>API 基础地址</label><input v-model="providerForm.base_url" type="url" required placeholder="https://example.com/v1"></div>
        <div class="field"><label>图片响应模式</label><select v-model="providerForm.image_response_mode"><option value="auto">自动兼容（推荐）</option><option value="sync_json">同步 JSON</option><option value="sse">SSE 流式</option><option value="async_task">异步 task_id</option></select></div>
        <div class="field"><label>上游账户共享组</label><input v-model="providerForm.concurrency_group" required placeholder="例如 originboost-main"></div>
        <div class="field"><label>账户共享并发数</label><input v-model.number="providerForm.max_concurrency" type="number" min="1" max="1000" required></div>
        <div class="field"><label>上游请求超时（秒）</label><input v-model.number="providerForm.request_timeout_seconds" type="number" min="60" max="1800" required><small>这是绝对时限，SSE 心跳不会延长；应小于或等于任务自动截止时间。</small></div>
        <div class="field span-two"><label>API Key（只写）</label><input v-model="providerForm.api_key" type="password" autocomplete="new-password" :required="!providerForm.provider_id" :placeholder="providerForm.provider_id ? '留空表示不轮换' : '保存后不可读取'"></div>
        <div class="row-actions"><button class="primary-btn" type="submit" :disabled="busy">{{ providerForm.provider_id ? "保存修改" : "保存来源" }}</button><button v-if="providerForm.provider_id" class="secondary-btn" type="button" @click="resetProviderForm">取消编辑</button></div>
      </form>
      <div class="section-head"><div><h3>已配置来源</h3><p>来源启用后，还需要路由启用且最近健康检测可用。</p></div></div>
      <div v-if="!providers.length" class="empty">尚未配置 API 来源。</div>
      <div v-else class="table-wrap"><table><thead><tr><th>来源</th><th>API 地址</th><th>传输</th><th>共享并发池</th><th>凭据指纹</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="provider in providers" :key="provider.provider_id"><td><strong>{{ provider.display_name }}</strong><br><span class="mono">{{ provider.code }}</span></td><td class="mono">{{ provider.base_url }}</td><td>{{ provider.image_response_mode || "auto" }}</td><td><span class="mono">{{ provider.concurrency_group || provider.code }}</span><br>{{ Number(provider.max_concurrency || 20) }} 并发 / {{ Number(provider.request_timeout_seconds || 600) }} 秒</td><td class="mono">{{ provider.key_fingerprint || "—" }}</td><td><span class="status" :class="provider.enabled ? 'healthy' : 'unknown'">{{ provider.enabled ? "已启用" : "已停用" }}</span></td><td><div class="row-actions"><button class="text-btn" @click="editProvider(provider)">编辑</button><button class="text-btn" @click="action(() => bridge.api(`/api/v1/admin/providers/${encodeURIComponent(provider.provider_id)}`, { method: 'PATCH', body: JSON.stringify({ enabled: !provider.enabled }) }), '来源状态已更新')">{{ provider.enabled ? "停用" : "启用" }}</button><button class="danger-btn" @click="deleteProvider(provider)">永久删除</button></div></td></tr></tbody></table></div>
    </section>
    <section class="panel admin-panel route-editor">
      <div class="section-head" style="margin-top:0"><div><h2>② 模型映射与路由</h2><p>新路由默认停用，必须先检测成功再启用。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveRoute">
        <div class="field"><label>API 来源</label><select v-model="routeForm.provider_id" required :disabled="Boolean(routeForm.route_id)"><option v-if="!providers.length" value="">请先添加 API 来源</option><option v-for="provider in providers" :key="provider.provider_id" :value="provider.provider_id">{{ provider.display_name }} · {{ provider.code }}</option></select></div>
        <div class="field"><label>上游模型名称</label><input v-model="routeForm.provider_model_name" required :disabled="Boolean(routeForm.route_id && routeForm.enabled)"></div>
        <div class="field"><label>逻辑模型</label><input v-model="routeForm.logical_model" required :disabled="Boolean(routeForm.route_id)" @change="syncReferenceLimit"></div>
        <div class="field"><label>成品规格</label><input v-model="routeForm.output_spec" required :disabled="Boolean(routeForm.route_id)" @change="syncReferenceLimit"></div>
        <div class="field"><label>兼容组</label><input v-model="routeForm.compatibility_group" required :disabled="Boolean(routeForm.route_id && routeForm.enabled)"></div>
        <div class="field"><label>优先级</label><input v-model.number="routeForm.priority" type="number" min="0" max="10000" required></div>
        <div class="field"><label>最大上传参考图张数</label><input v-model.number="routeForm.max_reference_images" type="number" min="0" max="16" step="1" required><small>gpt-image-2 当前建议不超过 3 张；多图是否成功仍受具体上游兼容性影响。</small></div>
        <div class="row-actions"><button class="primary-btn" type="submit" :disabled="busy || !providers.length">{{ routeForm.route_id ? "保存修改" : "创建路由" }}</button><button v-if="routeForm.route_id" class="secondary-btn" type="button" @click="resetRouteForm">取消编辑</button></div>
      </form>
    </section>
    <div class="section-head"><div><h2>③ 健康检测与选路资格</h2><p>来源、路由和最近健康检测同时可用，才可参与选路。</p></div></div>
    <div v-if="!routes.length" class="empty">尚未配置模型来源路由。</div>
    <div v-else class="table-wrap routing-health-table"><table><thead><tr><th>来源路由</th><th>参考图上限</th><th>启用状态</th><th>健康</th><th>可参与选路</th><th>EWMA / P95</th><th>成功率</th><th>优先级</th><th>最近检测</th><th>操作</th></tr></thead><tbody><tr v-for="route in routes" :key="route.route_id"><td><strong>{{ providersById[route.provider_id]?.display_name || route.provider_id }}</strong><br><span class="mono">{{ routeLabel(route) }}</span></td><td>{{ normalizedReferenceLimit(route.max_reference_images) }} 张</td><td>{{ providersById[route.provider_id]?.enabled ? "来源已启用" : "来源已停用" }}<br>{{ route.enabled ? "路由已启用" : "路由已停用" }}</td><td><span class="status" :class="healthByRoute[route.route_id]?.status || route.health_status || 'unknown'">{{ routeHealthLabels[healthByRoute[route.route_id]?.status || route.health_status || 'unknown'] || healthByRoute[route.route_id]?.status }}</span></td><td><span class="status" :class="eligibility(route).css">{{ eligibility(route).label }}</span></td><td>{{ healthByRoute[route.route_id] ? `${healthByRoute[route.route_id]?.ewma_latency_ms} / ${healthByRoute[route.route_id]?.p95_latency_ms} ms` : "—" }}</td><td>{{ healthByRoute[route.route_id] ? `${(Number(healthByRoute[route.route_id]?.success_rate) * 100).toFixed(1)}% · ${healthByRoute[route.route_id]?.sample_count} 次` : "—" }}</td><td>{{ route.priority }}</td><td>{{ formatDate(healthByRoute[route.route_id]?.checked_at) }}</td><td><div class="row-actions"><button class="text-btn" @click="action(() => bridge.api(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}/health-check`, { method: 'POST' }), '健康检测已完成')">检测</button><button class="text-btn" @click="editRoute(route)">编辑</button><button class="text-btn" @click="action(() => bridge.api(`/api/v1/admin/image-model-routes/${encodeURIComponent(route.route_id)}`, { method: 'PATCH', body: JSON.stringify({ enabled: !route.enabled }) }), '路由状态已更新')">{{ route.enabled ? "停用" : "启用" }}</button><button class="danger-btn" @click="deleteRoute(route)">永久删除</button></div></td></tr></tbody></table></div>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>④ 用户售价</h2><p>售价按“逻辑模型 + 成品规格”设置，与 Provider 成本独立。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="publishPrice">
        <div class="field span-two"><label>逻辑模型与成品规格</label><select v-model="priceForm.spec_key" :disabled="!routeSpecs.length"><option v-if="!routeSpecs.length" value="">请先创建模型路由</option><option v-for="spec in routeSpecs" :key="specKey(spec)" :value="specKey(spec)">{{ spec.logical_model }}/{{ spec.output_spec }}</option></select></div>
        <div class="field"><label>每张价格（额度）</label><input v-model="priceForm.credits_per_result" type="number" min="0.0001" step="0.0001" required placeholder="0.2000"></div>
        <div class="field"><label>生效时间</label><input v-model="priceForm.effective_from" type="datetime-local" required></div>
        <button class="primary-btn" type="submit" :disabled="busy || !routeSpecs.length">发布价格版本</button>
      </form>
      <div class="section-head"><div><h3>当前生效价格</h3><p>历史版本仍保留。</p></div></div>
      <div v-if="!prices.length" class="empty">当前没有已生效的模型价格。</div>
      <div v-else class="table-wrap"><table><thead><tr><th>逻辑模型</th><th>成品规格</th><th>每张价格</th><th>生效时间</th><th>发布时间</th><th>操作</th></tr></thead><tbody><tr v-for="price in prices" :key="price.version_id"><td>{{ price.logical_model }}</td><td>{{ price.output_spec }}</td><td>{{ formatCredits(price.credits_per_result) }} 额度</td><td>{{ formatDate(price.effective_from) }}</td><td>{{ formatDate(price.published_at) }}</td><td><button class="danger-btn" @click="deletePrice(price)">删除价格</button></td></tr></tbody></table></div>
    </section>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>⑤ 选择策略</h2><p>指定来源不可用时，会自动回退同兼容组的其他健康来源。</p></div></div>
      <div v-if="!routeSpecs.length" class="empty">创建路由后可设置选择策略。</div>
      <form v-for="spec in routeSpecs" v-else :key="specKey(spec)" class="admin-form-grid" @submit.prevent="savePolicy(spec)">
        <div class="field span-two"><label>{{ spec.logical_model }}/{{ spec.output_spec }} 来源策略</label><select v-model="policies[specKey(spec)]"><option value="">自动选择可用低延时来源</option><option v-for="route in routes.filter(item => item.logical_model === spec.logical_model && item.output_spec === spec.output_spec)" :key="route.route_id" :value="route.route_id">{{ routeLabel(route) }}</option></select></div>
        <button class="primary-btn" type="submit" :disabled="busy">保存策略</button>
      </form>
    </section>
  </template>
</template>

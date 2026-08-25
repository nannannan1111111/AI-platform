<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage, formatCredits, formatDate, localDateTimeValue } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const capabilities = ref<JsonRecord[]>([]);
const schemaHistories = ref<Record<string, JsonRecord[]>>({});
const priceHistories = ref<Record<string, JsonRecord[]>>({});
const schemaDrafts = reactive<Record<string, JsonRecord[]>>({});
const priceDrafts = reactive<Record<string, { credits_per_run: string; effective_from: string }>>({});
const form = reactive<JsonRecord>({ capability_id: "", name: "", workflow_id: "", input_capabilities: [], available: true });
const inputLabels: Record<string, string> = { text: "文本", image: "图片" };

function newInput(): JsonRecord {
  return { input_key: "", label: "", kind: "text", required: false };
}

function resetForm(): void {
  Object.assign(form, { capability_id: "", name: "", workflow_id: "", input_capabilities: [], available: true });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    capabilities.value = await props.bridge.api("/api/v1/admin/runninghub-capabilities");
    const [schemas, prices] = await Promise.all([
      Promise.all(capabilities.value.map(async item => [item.capability_id, await props.bridge.api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(item.capability_id)}/input-schema-versions`)])),
      Promise.all(capabilities.value.map(async item => [item.capability_id, await props.bridge.api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(item.capability_id)}/price-versions`)])),
    ]);
    schemaHistories.value = Object.fromEntries(schemas);
    priceHistories.value = Object.fromEntries(prices);
    for (const capability of capabilities.value) {
      const currentSchema = schemaHistories.value[capability.capability_id]?.at(-1);
      schemaDrafts[capability.capability_id] = currentSchema?.inputs?.map((input: JsonRecord) => ({ ...input })) || [newInput()];
      priceDrafts[capability.capability_id] ||= { credits_per_run: "0.1000", effective_from: localDateTimeValue(new Date()) };
    }
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

function editCapability(capability: JsonRecord): void {
  Object.assign(form, {
    capability_id: capability.capability_id,
    name: capability.name,
    workflow_id: capability.workflow_id,
    input_capabilities: [...(capability.input_capabilities || [])],
    available: capability.available,
  });
  document.querySelector(".runninghub-capability-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function toggleInputCapability(value: string, checked: boolean): void {
  const selected = new Set(form.input_capabilities || []);
  checked ? selected.add(value) : selected.delete(value);
  form.input_capabilities = [...selected];
}

async function saveCapability(): Promise<void> {
  saving.value = true;
  try {
    const hasSchema = Boolean(form.capability_id && schemaHistories.value[form.capability_id]?.length);
    const payload: JsonRecord = { name: form.name, workflow_id: form.workflow_id, available: Boolean(form.available) };
    if (!form.capability_id || !hasSchema) payload.input_capabilities = form.input_capabilities;
    await props.bridge.api(form.capability_id ? `/api/v1/admin/runninghub-capabilities/${encodeURIComponent(form.capability_id)}` : "/api/v1/admin/runninghub-capabilities", {
      method: form.capability_id ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    props.bridge.toast(form.capability_id ? "RunningHub 能力已更新" : "RunningHub 能力已发布");
    resetForm();
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    saving.value = false;
  }
}

async function toggleCapability(capability: JsonRecord): Promise<void> {
  try {
    await props.bridge.api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capability.capability_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ available: !capability.available }),
    });
    props.bridge.toast("RunningHub 能力状态已更新");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  }
}

function moveInput(capabilityId: string, index: number, offset: number): void {
  const inputs = schemaDrafts[capabilityId];
  const target = index + offset;
  if (!inputs || target < 0 || target >= inputs.length) return;
  [inputs[index], inputs[target]] = [inputs[target], inputs[index]];
}

async function publishSchema(capabilityId: string): Promise<void> {
  try {
    await props.bridge.api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capabilityId)}/input-schema-versions`, {
      method: "POST",
      body: JSON.stringify({ inputs: schemaDrafts[capabilityId] }),
    });
    props.bridge.toast("RunningHub 输入 schema 新版本已发布");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  }
}

async function publishPrice(capabilityId: string): Promise<void> {
  const draft = priceDrafts[capabilityId];
  try {
    await props.bridge.api(`/api/v1/admin/runninghub-capabilities/${encodeURIComponent(capabilityId)}/price-versions`, {
      method: "POST",
      body: JSON.stringify({ credits_per_run: String(draft.credits_per_run), effective_from: new Date(draft.effective_from).toISOString() }),
    });
    props.bridge.toast("RunningHub 用户价格新版本已发布");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  }
}

function priceStatus(versions: JsonRecord[], version: JsonRecord): { label: string; css: string } {
  const now = Date.now();
  const current = versions.filter(item => new Date(item.effective_from).getTime() <= now).sort((a, b) => new Date(b.effective_from).getTime() - new Date(a.effective_from).getTime())[0];
  if (current?.price_version_id === version.price_version_id) return { label: "当前生效", css: "healthy" };
  if (new Date(version.effective_from).getTime() > now) return { label: "未来生效", css: "pending" };
  return { label: "历史版本", css: "unknown" };
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取 RunningHub 能力…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>RunningHub 能力目录</h1><p>发布稳定的用户能力身份，并在平台内部绑定 RunningHub 工作流。</p></div></div>
    <section class="panel runninghub-capability-editor">
      <div class="section-head" style="margin-top:0"><div><h2>发布或编辑能力</h2><p>内部 workflow ID 仅供管理员使用；当前不提供删除，只能停用能力。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveCapability">
        <div class="field"><label>公开名称</label><input v-model="form.name" required maxlength="120" placeholder="商品摄影"></div>
        <div class="field"><label>内部 workflow ID</label><input v-model="form.workflow_id" required maxlength="255" autocomplete="off" placeholder="仅管理员可见"></div>
        <div class="field span-two"><label>粗粒度输入能力</label><div class="row-actions"><label v-for="kind in ['text', 'image']" :key="kind"><input type="checkbox" :checked="form.input_capabilities.includes(kind)" :disabled="Boolean(form.capability_id && schemaHistories[form.capability_id]?.length)" @change="toggleInputCapability(kind, ($event.target as HTMLInputElement).checked)"> {{ inputLabels[kind] }}</label></div><small v-if="form.capability_id && schemaHistories[form.capability_id]?.length">已发布 schema 后，粗粒度输入能力由 schema 固定。</small></div>
        <div class="field"><label><input v-model="form.available" type="checkbox"> 发布后立即可用</label></div>
        <div class="row-actions"><button class="primary-btn" type="submit" :disabled="saving">{{ saving ? "保存中…" : form.capability_id ? "保存修改" : "保存能力" }}</button><button class="secondary-btn" type="button" @click="resetForm">取消编辑</button></div>
      </form>
    </section>
    <div class="section-head"><div><h2>已发布能力</h2><p>公开能力 ID 保持稳定；停用项仍保留并显示为不可用。</p></div></div>
    <div v-if="!capabilities.length" class="empty">尚未发布 RunningHub 能力。用户目录当前为空。</div>
    <div v-else class="table-wrap"><table><thead><tr><th>公开能力</th><th>输入能力</th><th>内部 workflow ID</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="capability in capabilities" :key="capability.capability_id"><td><strong>{{ capability.name }}</strong><br><span class="mono">{{ capability.capability_id }}</span></td><td>{{ (capability.input_capabilities || []).map((value: string) => inputLabels[value] || value).join("、") || "无用户输入" }}</td><td class="mono">{{ capability.workflow_id }}</td><td><span class="status" :class="capability.available ? 'healthy' : 'unknown'">{{ capability.available ? "可用" : "已停用" }}</span></td><td><div class="row-actions"><button class="text-btn" @click="editCapability(capability)">编辑</button><button class="text-btn" @click="toggleCapability(capability)">{{ capability.available ? "停用" : "启用" }}</button></div></td></tr></tbody></table></div>
    <div v-if="capabilities.length" class="section-head"><div><h2>版本化输入 schema</h2><p>每次提交发布一个新版本，用户目录只读取最新版本。</p></div></div>
    <section v-for="capability in capabilities" :key="`schema-${capability.capability_id}`" class="panel">
      <div class="section-head" style="margin-top:0"><div><h3>{{ capability.name }}</h3><p><span class="mono">{{ capability.capability_id }}</span> · {{ schemaHistories[capability.capability_id]?.length ? `当前 v${schemaHistories[capability.capability_id].at(-1)?.version}` : "尚无 schema" }}</p></div></div>
      <form @submit.prevent="publishSchema(capability.capability_id)">
        <div class="table-wrap"><table><thead><tr><th>input_key</th><th>用户标签</th><th>类型</th><th>是否必填</th><th>顺序</th></tr></thead><tbody><tr v-for="(input, index) in schemaDrafts[capability.capability_id]" :key="index"><td><input v-model="input.input_key" required maxlength="64" pattern="[a-z][a-z0-9_]*" placeholder="prompt"></td><td><input v-model="input.label" required maxlength="120" placeholder="提示词"></td><td><select v-model="input.kind"><option value="text">文本</option><option value="image">图片</option></select></td><td><label><input v-model="input.required" type="checkbox"> 必填</label></td><td><div class="row-actions"><button class="text-btn" type="button" @click="moveInput(capability.capability_id, index, -1)">上移</button><button class="text-btn" type="button" @click="moveInput(capability.capability_id, index, 1)">下移</button><button class="text-btn" type="button" :disabled="schemaDrafts[capability.capability_id].length <= 1" @click="schemaDrafts[capability.capability_id].splice(index, 1)">移除</button></div></td></tr></tbody></table></div>
        <div class="row-actions" style="margin-top:14px"><button class="secondary-btn" type="button" @click="schemaDrafts[capability.capability_id].push(newInput())">添加输入</button><button class="primary-btn" type="submit">发布新 schema 版本</button></div>
      </form>
      <div class="section-head"><div><h4>版本历史</h4><p>历史版本不可编辑或删除。</p></div></div>
      <div v-if="!schemaHistories[capability.capability_id]?.length" class="empty">尚未发布输入 schema。</div>
      <div v-else class="table-wrap"><table><thead><tr><th>版本</th><th>输入顺序</th><th>发布时间</th></tr></thead><tbody><tr v-for="(version, index) in schemaHistories[capability.capability_id]" :key="version.schema_version_id"><td><strong>v{{ version.version }}</strong> <span v-if="index === schemaHistories[capability.capability_id].length - 1" class="status healthy">当前</span><br><span class="mono">{{ version.schema_version_id }}</span></td><td>{{ version.inputs.map((input: JsonRecord) => `${input.label} (${inputLabels[input.kind] || input.kind}${input.required ? '，必填' : '，选填'})`).join(" → ") }}</td><td>{{ formatDate(version.published_at) }}</td></tr></tbody></table></div>
    </section>
    <div v-if="capabilities.length" class="section-head"><div><h2>版本化用户价格</h2><p>价格按每次能力使用计费，历史不可编辑或删除。</p></div></div>
    <section v-for="capability in capabilities" :key="`price-${capability.capability_id}`" class="panel">
      <div class="section-head" style="margin-top:0"><div><h3>{{ capability.name }}</h3><p class="mono">{{ capability.capability_id }}</p></div></div>
      <form class="admin-form-grid" @submit.prevent="publishPrice(capability.capability_id)">
        <div class="field"><label>每次能力使用额度</label><input v-model="priceDrafts[capability.capability_id].credits_per_run" type="number" min="0.0001" step="0.0001" required></div>
        <div class="field"><label>生效时间</label><input v-model="priceDrafts[capability.capability_id].effective_from" type="datetime-local" required></div>
        <button class="primary-btn" type="submit">发布用户价格版本</button>
      </form>
      <div class="section-head"><div><h4>价格历史</h4><p>用户目录只显示当前生效价格。</p></div></div>
      <div v-if="!priceHistories[capability.capability_id]?.length" class="empty">尚未发布用户价格。</div>
      <div v-else class="table-wrap"><table><thead><tr><th>版本</th><th>每次能力使用</th><th>生效时间</th><th>发布时间</th><th>状态</th></tr></thead><tbody><tr v-for="version in priceHistories[capability.capability_id]" :key="version.price_version_id"><td><strong>v{{ version.version }}</strong><br><span class="mono">{{ version.price_version_id }}</span></td><td>{{ formatCredits(version.credits_per_run) }} 额度</td><td>{{ formatDate(version.effective_from) }}</td><td>{{ formatDate(version.published_at) }}</td><td><span class="status" :class="priceStatus(priceHistories[capability.capability_id], version).css">{{ priceStatus(priceHistories[capability.capability_id], version).label }}</span></td></tr></tbody></table></div>
    </section>
  </template>
</template>

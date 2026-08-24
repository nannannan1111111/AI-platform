<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const providers = ref<JsonRecord[]>([]);
const form = reactive<JsonRecord>({ id: "", code: "", display_name: "", base_url: "https://api.openai.com/v1", api_key: "", models_text: "", enabled: true });

function resetForm(): void {
  Object.assign(form, { id: "", code: "", display_name: "", base_url: "https://api.openai.com/v1", api_key: "", models_text: "", enabled: true });
}

async function load(): Promise<void> {
  loading.value = true;
  try { providers.value = await props.bridge.api("/api/v1/llm-providers"); }
  catch (caught) { error.value = errorMessage(caught); }
  finally { loading.value = false; }
}

function edit(provider: JsonRecord): void {
  Object.assign(form, { ...provider, api_key: "", models_text: (provider.models || []).join("\n") });
  document.querySelector(".llm-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function save(): Promise<void> {
  saving.value = true;
  try {
    const body = { code: form.code, display_name: form.display_name, base_url: form.base_url, api_key: form.api_key, models: String(form.models_text).split(/[\n,，]+/).map(value => value.trim()).filter(Boolean), enabled: Boolean(form.enabled) };
    await props.bridge.api(form.id ? `/api/v1/llm-providers/${encodeURIComponent(form.id)}` : "/api/v1/llm-providers", { method: form.id ? "PATCH" : "POST", body: JSON.stringify(body) });
    props.bridge.toast(form.id ? "LLM Provider 已更新" : "LLM Provider 已添加");
    resetForm();
    await load();
  } catch (caught) { props.bridge.toast(errorMessage(caught)); }
  finally { saving.value = false; }
}

async function remove(provider: JsonRecord): Promise<void> {
  if (!await props.bridge.confirm("确定删除这个 LLM Provider？删除后画布将无法继续使用该配置。", "确认删除", "确认删除")) return;
  try { await props.bridge.api(`/api/v1/llm-providers/${encodeURIComponent(provider.id)}`, { method: "DELETE" }); props.bridge.toast("LLM Provider 已删除"); await load(); }
  catch (caught) { props.bridge.toast(errorMessage(caught)); }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取 LLM 设置…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else><div class="page-head"><div><h1>LLM 设置</h1><p>配置您自己的文本模型 API，仅供当前账户的智能画布使用。生图 Provider 仍由平台统一管理。</p></div></div><section class="panel llm-editor"><div class="section-head" style="margin-top:0"><div><h2>{{ form.id ? "编辑 LLM Provider" : "添加 LLM Provider" }}</h2><p>支持 OpenAI-compatible /chat/completions。API Key 保存后不会返回明文。</p></div></div><form class="admin-form-grid" @submit.prevent="save"><div class="field"><label>Provider 代码</label><input v-model="form.code" required maxlength="64" placeholder="openai"></div><div class="field"><label>显示名称</label><input v-model="form.display_name" required maxlength="120" placeholder="我的 OpenAI"></div><div class="field span-two"><label>API 基础地址</label><input v-model="form.base_url" type="url" required placeholder="https://api.openai.com/v1"></div><div class="field span-two"><label>API Key（只写）</label><input v-model="form.api_key" type="password" autocomplete="new-password" :required="!form.id" :placeholder="form.id ? '留空则保留现有 Key' : '保存后不可读取'"></div><div class="field span-two"><label>文本模型（每行一个，也可用逗号分隔）</label><textarea v-model="form.models_text" rows="4" required placeholder="gpt-4.1-mini&#10;gpt-4o-mini"></textarea></div><div class="field"><label><input v-model="form.enabled" type="checkbox"> 启用此 Provider</label></div><div class="row-actions"><button class="primary-btn" type="submit" :disabled="saving">{{ saving ? "保存中…" : form.id ? "保存修改" : "添加 Provider" }}</button><button v-if="form.id" class="secondary-btn" type="button" @click="resetForm">取消编辑</button></div></form></section><div class="section-head"><div><h2>我的 LLM Provider</h2><p>这些配置按账户空间隔离，不会影响平台的图片生成路由。</p></div></div><div v-if="!providers.length" class="empty">尚未配置 LLM Provider。</div><div v-else class="llm-provider-grid"><article v-for="provider in providers" :key="provider.id" class="panel llm-provider-card"><div class="section-head" style="margin-top:0"><div><h2>{{ provider.display_name }}</h2><p>{{ provider.code }} · {{ provider.enabled ? "已启用" : "已停用" }}</p></div><span class="badge">Key ····{{ provider.key_fingerprint || "" }}</span></div><dl class="detail-list"><div class="detail-row"><dt>API 地址</dt><dd class="mono">{{ provider.base_url }}</dd></div><div class="detail-row"><dt>文本模型</dt><dd><span v-for="model in provider.models || []" :key="model" class="llm-model-chip">{{ model }}</span></dd></div></dl><div class="row-actions"><button class="secondary-btn" @click="edit(provider)">编辑</button><button class="danger-btn" @click="remove(provider)">删除</button></div></article></div></template>
</template>

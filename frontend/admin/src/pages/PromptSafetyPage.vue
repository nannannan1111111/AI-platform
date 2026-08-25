<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";
const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true); const saving = ref(false); const error = ref(""); const file = ref<HTMLInputElement | null>(null);
const settings = reactive<JsonRecord>({ enabled: true, prompt_check_enabled: true, keywords: [] });
async function load() { loading.value = true; try { Object.assign(settings, await props.bridge.api("/api/v1/admin/prompt-safety")); } catch (e) { error.value = errorMessage(e); } finally { loading.value = false; } }
async function save() { saving.value = true; try { await props.bridge.api("/api/v1/admin/prompt-safety", { method: "PUT", body: JSON.stringify({ enabled: Boolean(settings.enabled), prompt_check_enabled: Boolean(settings.prompt_check_enabled), keywords: String(settings.text || "").split(/\r?\n/) }) }); props.bridge.toast("违规关键词设置已保存"); await load(); } catch (e) { props.bridge.toast(errorMessage(e)); } finally { saving.value = false; } }
async function upload() { const selected = file.value?.files?.[0]; if (!selected) return; saving.value = true; try { Object.assign(settings, await props.bridge.api("/api/v1/admin/prompt-safety/upload", { method: "POST", body: (() => { const data = new FormData(); data.append("file", selected); return data; })() })); props.bridge.toast("关键词 TXT 已导入"); if (file.value) file.value.value = ""; } catch (e) { props.bridge.toast(errorMessage(e)); } finally { saving.value = false; } }
onMounted(async () => { await load(); settings.text = Array.isArray(settings.keywords) ? settings.keywords.join("\n") : ""; });
</script>
<template><div v-if="loading" class="loading">正在读取违规关键词设置…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else>
  <div class="page-head"><div><h1>违规关键词</h1><p>命中提示词后会在额度冻结和上游调用前阻止生图。</p></div></div>
  <section class="panel"><label class="checkbox-row"><input v-model="settings.enabled" type="checkbox">启用违规关键词功能</label><label class="checkbox-row"><input v-model="settings.prompt_check_enabled" type="checkbox">检查用户生图提示词</label>
    <div class="field"><label>关键词列表</label><textarea v-model="settings.text" rows="16" placeholder="每行一个关键词，空行忽略"></textarea><small>格式要求：UTF-8 纯文本，每行一个关键词，空行忽略，最多 10000 条。</small></div>
    <div class="row-actions"><button class="primary-btn" :disabled="saving" @click="save">保存设置</button><input ref="file" type="file" accept=".txt,text/plain" @change="upload"><span class="muted">上传 TXT 会替换当前列表</span></div>
  </section><section class="panel"><h2>当前关键词（{{ (settings.keywords || []).length }} 条）</h2><div class="keyword-list"><span v-for="(keyword, index) in settings.keywords || []" :key="`${keyword}-${index}`" class="badge">{{ keyword }}</span><div v-if="!(settings.keywords || []).length" class="empty">尚未配置关键词。</div></div></section>
</template></template>

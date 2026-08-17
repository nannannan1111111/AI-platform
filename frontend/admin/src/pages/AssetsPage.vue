<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const error = ref("");
const data = ref<JsonRecord>({ libraries: [] });
const libraryId = ref("system");
const category = ref("all");
const selectedId = ref("");
const query = ref("");
const editing = ref(false);
const creating = ref(false);
const newLibraryName = ref("");
const showLibraryCreator = ref(false);
const editor = reactive<JsonRecord>({ name: "", scene: "", category: "custom", positive: "", negative: "" });
const categoryNames: Record<string, string> = { view: "视角", storyboard: "分镜", character: "角色", product: "产品", lighting: "光影", custom: "我的" };
const libraries = computed<JsonRecord[]>(() => data.value.libraries || []);
const library = computed<JsonRecord | null>(() => libraries.value.find(item => item.id === libraryId.value) || libraries.value[0] || null);
const items = computed<JsonRecord[]>(() => (library.value?.items || []).filter((item: JsonRecord) => {
  if (category.value !== "all" && item.category !== category.value) return false;
  const needle = query.value.trim().toLowerCase();
  return !needle || [item.name, item.scene, item.positive, item.negative].join(" ").toLowerCase().includes(needle);
}));
const selected = computed<JsonRecord | null>(() => (library.value?.items || []).find((item: JsonRecord) => item.id === selectedId.value) || null);

async function load(): Promise<void> {
  loading.value = true;
  try {
    const response = await props.bridge.api("/api/v1/prompt-libraries");
    data.value = response.library || { libraries: [] };
    if (!libraries.value.some(item => item.id === libraryId.value)) libraryId.value = libraries.value[0]?.id || "";
    if (!items.value.some(item => item.id === selectedId.value)) selectedId.value = items.value[0]?.id || "";
  } catch (caught) { error.value = errorMessage(caught); }
  finally { loading.value = false; }
}

function chooseLibrary(id: string): void { libraryId.value = id; category.value = "all"; selectedId.value = ""; editing.value = false; creating.value = false; }
function chooseCategory(id: string): void { category.value = id; selectedId.value = ""; editing.value = false; creating.value = false; }
function chooseItem(id: string): void { selectedId.value = id; editing.value = false; creating.value = false; }
function startCreate(): void { Object.assign(editor, { name: "", scene: "", category: category.value === "all" ? "custom" : category.value, positive: "", negative: "" }); creating.value = true; editing.value = false; }
function startEdit(): void { if (!selected.value) return; Object.assign(editor, selected.value); editing.value = true; creating.value = false; }

async function createLibrary(): Promise<void> {
  if (!newLibraryName.value.trim()) return;
  try { const result = await props.bridge.api("/api/v1/prompt-libraries", { method: "POST", body: JSON.stringify({ name: newLibraryName.value.trim() }) }); libraryId.value = result.prompt_library.id; category.value = "all"; newLibraryName.value = ""; showLibraryCreator.value = false; await load(); }
  catch (caught) { props.bridge.toast(errorMessage(caught)); }
}

async function saveItem(): Promise<void> {
  const id = editing.value ? selected.value?.id : "";
  try {
    const result = await props.bridge.api(id ? `/api/v1/prompt-libraries/items/${encodeURIComponent(id)}` : "/api/v1/prompt-libraries/items", { method: id ? "PATCH" : "POST", body: JSON.stringify({ library_id: libraryId.value, name: editor.name, scene: editor.scene, category: editor.category || "custom", positive: editor.positive, negative: editor.negative, params: editor.params || {} }) });
    selectedId.value = result.item.id; editing.value = false; creating.value = false; await load();
  } catch (caught) { props.bridge.toast(errorMessage(caught)); }
}

async function removeItem(): Promise<void> {
  if (!selected.value || !await props.bridge.confirm("确认删除这条提示词吗？", "确认删除", "确认删除")) return;
  try { await props.bridge.api(`/api/v1/prompt-libraries/items/${encodeURIComponent(selected.value.id)}`, { method: "DELETE" }); selectedId.value = ""; await load(); }
  catch (caught) { props.bridge.toast(errorMessage(caught)); }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取提示词库…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else><div class="asset-workbench-head"><div><h1>提示词库</h1><p>按词库、分组、内容和预览管理提示词。</p></div><div class="asset-head-actions"><span class="asset-ready">准备就绪</span><button class="secondary-btn" @click="load">↻ 刷新</button></div></div><div class="prompt-workbench"><aside class="prompt-library-pane"><header><div><strong>提示词库</strong><small>可创建多个词库</small></div><button class="workbench-icon-btn" title="新建提示词库" @click="showLibraryCreator = !showLibraryCreator">＋</button></header><form v-if="showLibraryCreator" class="prompt-library-create" @submit.prevent="createLibrary"><input v-model="newLibraryName" placeholder="提示词库名称" required><button class="primary-btn" type="submit">创建</button></form><div class="prompt-tree"><div v-for="lib in libraries" :key="lib.id" class="prompt-tree-library"><button class="prompt-tree-root" :class="{ active: lib.id === library?.id }" @click="chooseLibrary(lib.id)"><span>⌁</span><strong>{{ lib.name }}</strong><em>{{ (lib.items || []).length }}</em></button><div v-if="lib.id === library?.id" class="prompt-tree-categories"><button :class="{ active: category === 'all' }" @click="chooseCategory('all')"><span>☷</span>全部提示词<em>{{ (lib.items || []).length }}</em></button><button v-for="itemCategory in lib.categories || []" :key="itemCategory.id" :class="{ active: category === itemCategory.id }" @click="chooseCategory(itemCategory.id)"><span>◇</span>{{ itemCategory.name }}<em>{{ (lib.items || []).filter((item: JsonRecord) => item.category === itemCategory.id).length }}</em></button></div></div></div></aside><section class="prompt-list-pane"><header><div><strong>{{ library?.name || "提示词库" }}</strong><small>共 {{ items.length }} 条提示词</small></div><div class="prompt-list-tools"><label>⌕<input v-model="query" placeholder="搜索名称、说明或正文"></label><button class="primary-btn" @click="startCreate">＋ 新增</button></div></header><div class="prompt-card-list"><button v-for="item in items" :key="item.id" class="prompt-list-card" :class="{ active: item.id === selected?.id }" @click="chooseItem(item.id)"><div><strong>{{ item.name }}</strong><span>{{ categoryNames[item.category] || item.category || "未分类" }}</span></div><p>{{ item.scene || "未填写用途说明" }}</p><article>{{ item.positive || "" }}</article></button><div v-if="!items.length" class="prompt-list-empty">当前分组暂无提示词</div></div></section><aside class="prompt-preview-pane"><template v-if="creating || editing"><div class="prompt-preview-head"><div><strong>{{ creating ? "新增提示词" : "编辑提示词" }}</strong><small>{{ creating ? `保存到 ${library?.name || '提示词库'}` : selected?.name }}</small></div></div><form class="prompt-workbench-editor" @submit.prevent="saveItem"><label><span>名称</span><input v-model="editor.name" required placeholder="提示词名称"></label><label><span>用途说明</span><textarea v-model="editor.scene" placeholder="说明适用场景和用途"></textarea></label><label><span>分类</span><select v-model="editor.category"><option v-for="itemCategory in library?.categories || []" :key="itemCategory.id" :value="itemCategory.id">{{ itemCategory.name }}</option></select></label><label class="prompt-editor-large"><span>正向提示词</span><textarea v-model="editor.positive" required placeholder="输入正向提示词"></textarea></label><label class="prompt-editor-large"><span>负向提示词</span><textarea v-model="editor.negative" placeholder="输入负向提示词"></textarea></label><div class="prompt-editor-actions"><button class="secondary-btn" type="button" @click="editing = false; creating = false">取消</button><button class="primary-btn" type="submit">{{ creating ? "创建提示词" : "保存修改" }}</button></div></form></template><template v-else-if="selected"><div class="prompt-preview-head"><div><strong>提示词预览</strong><small>{{ categoryNames[selected.category] || selected.category || "未分类" }}</small></div><div class="prompt-preview-actions"><button class="workbench-icon-btn" title="编辑" @click="startEdit">✎</button><button class="workbench-icon-btn danger" title="删除" @click="removeItem">♲</button></div></div><div class="prompt-preview-scroll"><h2>{{ selected.name }}</h2><p class="prompt-scene">{{ selected.scene || "未填写用途说明" }}</p><section class="prompt-copy-block"><header><strong>正向提示词</strong><span>{{ String(selected.positive || '').length }} 字符</span></header><div>{{ selected.positive || "" }}</div></section><section class="prompt-copy-block"><header><strong>负向提示词</strong><span>{{ String(selected.negative || '').length }} 字符</span></header><div>{{ selected.negative || "未设置" }}</div></section><section v-for="(value, key) in selected.params || {}" :key="key" class="prompt-param"><strong>{{ key }}</strong><span>{{ value }}</span></section></div></template><div v-else class="prompt-preview-empty"><span>⌁</span><strong>选择一条提示词查看详情</strong></div></aside></div></template>
</template>

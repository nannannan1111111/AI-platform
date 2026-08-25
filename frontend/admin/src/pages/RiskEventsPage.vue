<script setup lang="ts">
import { onMounted, ref } from "vue";
import { errorMessage, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";
const props = defineProps<{ bridge: AdminBridge }>(); const windowKey = ref("24h"); const loading = ref(true); const error = ref(""); const result = ref<JsonRecord>({ entries: [], page: 1, total_pages: 1, total_entries: 0 });
async function load() { loading.value = true; try { result.value = await props.bridge.api(`/api/v1/admin/risk-events?window=${windowKey.value}&page=1&page_size=50`); } catch (e) { error.value = errorMessage(e); } finally { loading.value = false; } }
onMounted(load);
</script>
<template><div v-if="loading" class="loading">正在读取运行风险日志…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else>
  <div class="page-head"><div><h1>运行风险日志</h1><p>仅显示脱敏风险事件，不包含完整提示词、Token 或密钥。</p></div><button class="secondary-btn" @click="load">刷新</button></div>
  <div class="segmented-control"><button v-for="item in [{v:'24h',l:'近24小时'},{v:'7d',l:'近7天'},{v:'30d',l:'近30天'},{v:'all',l:'全部'}]" :key="item.v" :class="{active:windowKey===item.v}" @click="windowKey=item.v;load()">{{ item.l }}</button></div>
  <section class="panel"><div v-if="!result.entries?.length" class="empty">当前窗口没有风险事件。</div><div v-else class="table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>级别</th><th>说明</th><th>计数</th></tr></thead><tbody><tr v-for="event in result.entries" :key="event.event_id"><td>{{ formatDate(event.occurred_at) }}</td><td>{{ event.kind }}</td><td><span class="status" :class="event.severity">{{ event.severity }}</span></td><td>{{ event.message }}</td><td>{{ event.count || "—" }}</td></tr></tbody></table></div></section>
</template></template>

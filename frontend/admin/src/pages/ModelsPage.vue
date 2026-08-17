<script setup lang="ts">
import { onMounted, ref } from "vue";

import { errorMessage, formatCredits } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const error = ref("");
const specifications = ref<JsonRecord[]>([]);
const availabilityLabels: Record<string, string> = { available: "可用", maintenance: "暂不可用" };

onMounted(async () => {
  try {
    const catalog = await props.bridge.api("/api/v1/image-models");
    specifications.value = (catalog.data || []).flatMap((model: JsonRecord) => (model.output_specs || []).map((spec: JsonRecord) => ({ logical_model: model.logical_model, ...spec })));
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div v-if="loading" class="loading">正在读取模型目录…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else><div class="page-head"><div><h1>图片模型目录</h1><p>查看平台发布的逻辑模型、成品规格、每张额度价格和当前可用状态。</p></div></div><section class="panel"><div class="section-head" style="margin-top:0"><div><h2>逻辑模型</h2><p>平台负责选择兼容来源；目录不会显示 API 来源、模型路由、Provider 成本或凭据。</p></div></div><div v-if="!specifications.length" class="empty">当前没有已发布的图片模型规格。</div><div v-else class="table-wrap"><table><thead><tr><th>逻辑模型</th><th>成品规格</th><th>参考图上限</th><th>每张价格</th><th>当前状态</th></tr></thead><tbody><tr v-for="spec in specifications" :key="`${spec.logical_model}-${spec.output_spec}`"><td><strong>{{ spec.logical_model }}</strong></td><td>{{ spec.output_spec }}</td><td>{{ Math.max(0, Math.min(16, Number(spec.max_reference_images) || 0)) }} 张</td><td>{{ formatCredits(spec.credits_per_result) }} 额度</td><td><span class="status" :class="spec.status">{{ availabilityLabels[spec.status] || spec.status }}</span></td></tr></tbody></table></div></section></template>
</template>

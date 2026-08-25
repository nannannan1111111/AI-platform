<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage, formatCredits, formatDate, localDateTimeValue } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const publishing = ref(false);
const error = ref("");
const packages = ref<JsonRecord[]>([]);
const form = reactive({ package_code: "", payment_cny: "", credits: "", effective_from: localDateTimeValue(new Date(Date.now() + 60_000)) });

function rate(item: JsonRecord): string {
  const payment = Number(item.payment_cny);
  const credits = Number(item.credits);
  return payment > 0 && Number.isFinite(credits) ? (credits / payment).toFixed(4).replace(/\.?0+$/, "") : "—";
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    packages.value = await props.bridge.api("/api/v1/recharge-packages");
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function publish(): Promise<void> {
  publishing.value = true;
  try {
    await props.bridge.api("/api/v1/admin/recharge-packages", {
      method: "POST",
      body: JSON.stringify({ ...form, payment_cny: String(form.payment_cny), credits: String(form.credits), effective_from: new Date(form.effective_from).toISOString() }),
    });
    props.bridge.toast("充值包版本已发布");
    form.payment_cny = "";
    form.credits = "";
    form.effective_from = localDateTimeValue(new Date(Date.now() + 60_000));
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    publishing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取充值包…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>特惠充值包</h1><p>配置独立于普通充值比例的特惠金额与赠送额度。</p></div></div>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>特惠规则</h2><p>每个特惠包独立决定比例：<strong>换算率 = 到账额度 ÷ 支付金额</strong>。</p></div></div></section>
    <section class="panel">
      <div class="section-head" style="margin-top:0"><div><h2>发布充值包版本</h2><p>沿用现有代码会发布新版本；已发布版本不能编辑或删除。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="publish">
        <div class="field span-two"><label>充值包代码</label><input v-model="form.package_code" list="recharge-package-codes" required placeholder="starter"><datalist id="recharge-package-codes"><option v-for="item in packages" :key="item.package_code" :value="item.package_code"></option></datalist></div>
        <div class="field"><label>支付金额（人民币）</label><input v-model="form.payment_cny" type="number" min="0.01" step="0.01" required placeholder="10.00"></div>
        <div class="field"><label>到账额度</label><input v-model="form.credits" type="number" min="0.0001" step="0.0001" required placeholder="10.0000"></div>
        <div class="field"><label>生效时间</label><input v-model="form.effective_from" type="datetime-local" required></div>
        <button class="primary-btn" type="submit" :disabled="publishing">{{ publishing ? "发布中…" : "发布充值包版本" }}</button>
      </form>
    </section>
    <div class="section-head"><div><h2>当前可售充值包</h2><p>显示每个充值包代码当前生效的版本。</p></div></div>
    <div v-if="!packages.length" class="empty">当前没有可售充值包。</div>
    <div v-else class="table-wrap"><table><thead><tr><th>充值包代码</th><th>支付金额</th><th>到账额度</th><th>换算率</th><th>生效时间</th><th>发布时间</th><th>版本标识</th></tr></thead><tbody><tr v-for="item in packages" :key="item.version_id"><td>{{ item.package_code }}</td><td>¥{{ item.payment_cny }}</td><td>{{ formatCredits(item.credits) }} 额度</td><td><strong>{{ rate(item) }}</strong> 额度/元</td><td>{{ formatDate(item.effective_from) }}</td><td>{{ formatDate(item.published_at) }}</td><td class="mono">{{ item.version_id }}</td></tr></tbody></table></div>
  </template>
</template>

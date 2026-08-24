<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { errorMessage, formatCredits, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const error = ref("");
const statement = ref<JsonRecord>({ entries: [], page: 1, total_pages: 1, total_entries: 0 });
const packages = ref<JsonRecord[]>([]);
const methods = ref<JsonRecord[]>([]);
const orders = ref<JsonRecord[]>([]);
const rate = ref<JsonRecord>({ credits_per_cny: "0", preset_payment_cny: [] });
const selectedProvider = ref("");
const customAmount = ref("20.00");
const busyId = ref("");
const redeemCode = ref("");
const redeeming = ref(false);
const balance = computed<JsonRecord>(() => props.bridge.currentBalance || {});
const orderLabels: Record<string, string> = { pending: "待支付", paid: "已到账", charged_back: "已拒付" };
const postingLabels: Record<string, string> = { recharge: "充值", admin_grant: "人工充值", reversal: "冲销", freeze: "冻结", settlement: "结算", release: "释放" };
const presets = computed(() => rate.value.preset_payment_cny || ["1.00", "2.00", "5.00", "10.00", "100.00"]);
const customPreview = computed(() => creditsFor(customAmount.value));

function creditsFor(payment: unknown): string {
  const credits = Number(payment) * Number(rate.value.credits_per_cny || 0);
  return Number.isFinite(credits) && credits > 0 ? formatCredits(credits) : "—";
}

async function load(page = 1): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [loadedStatement, loadedPackages, loadedMethods, loadedOrders, loadedRate] = await Promise.all([
      props.bridge.api(`/api/v1/credits/ledger?page=${page}&page_size=20`).catch(() => ({ entries: [], page: 1, total_pages: 1, total_entries: 0 })),
      props.bridge.api("/api/v1/recharge-packages").catch(() => []),
      props.bridge.api("/api/v1/payment-methods").catch(() => []),
      props.bridge.api("/api/v1/recharge-orders").catch(() => []),
      props.bridge.api("/api/v1/recharge-rate"),
    ]);
    statement.value = loadedStatement;
    packages.value = loadedPackages;
    methods.value = loadedMethods;
    orders.value = loadedOrders;
    rate.value = loadedRate;
    if (!methods.value.some(item => item.payment_provider === selectedProvider.value)) selectedProvider.value = methods.value[0]?.payment_provider || "";
  } catch (caught) { error.value = errorMessage(caught); }
  finally { loading.value = false; }
}

async function createOrder(path: string, body: JsonRecord, busy: string): Promise<void> {
  if (!selectedProvider.value) return props.bridge.toast("支付途径尚未开放，可联系管理员人工充值");
  busyId.value = busy;
  try {
    const order = await props.bridge.api(path, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ ...body, payment_provider: selectedProvider.value }) });
    props.bridge.checkout(order.checkout);
    props.bridge.toast(`充值订单已创建${order.credits ? `，支付成功将到账 ${formatCredits(order.credits)} 额度` : "，请在收银台完成支付"}`);
    await load();
  } catch (caught) { props.bridge.toast(errorMessage(caught)); }
  finally { busyId.value = ""; }
}
async function redeem(): Promise<void> {
  if (!redeemCode.value.trim()) return props.bridge.toast("请输入兑换码");
  redeeming.value = true;
  try { const result = await props.bridge.api("/api/v1/redeem-codes/redeem", { method: "POST", body: JSON.stringify({ code: redeemCode.value.trim() }) }); props.bridge.toast(`兑换成功，到账 ${formatCredits(result.credits)} 额度`); redeemCode.value = ""; await load(); }
  catch (caught) { props.bridge.toast(errorMessage(caught)); }
  finally { redeeming.value = false; }
}

onMounted(() => load());
</script>

<template>
  <div v-if="loading" class="loading">正在读取钱包…</div><div v-else-if="error" class="empty">{{ error }}</div><template v-else>
    <div class="page-head"><div><h1>钱包</h1><p>查看消费额度、支付途径、充值订单和不可改写的账务记录。</p></div></div>
    <section class="grid two"><article class="stat-card"><span>可用额度</span><strong>{{ formatCredits(balance.available_credits) }}</strong><small>充值取得的额度永久有效</small></article><article class="stat-card"><span>冻结额度</span><strong>{{ formatCredits(balance.frozen_credits) }}</strong><small>生成任务执行期间暂时占用</small></article></section>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>兑换码</h2><p>输入管理员发放的一次性兑换码，余额会立即到账。</p></div></div><form class="row-actions" @submit.prevent="redeem"><input v-model="redeemCode" placeholder="例如 PW-XXXXXXXX" maxlength="128" required><button class="primary-btn" type="submit" :disabled="redeeming">{{ redeeming ? "兑换中…" : "兑换余额" }}</button></form></section>
    <div class="section-head"><div><h2>支付途径</h2><p>页面只显示渠道名称，不保存或返回任何支付凭据。</p></div></div><div v-if="!methods.length" class="empty">暂未开放支付途径。平台配置后会显示在这里。</div><div v-else class="method-list"><button v-for="method in methods" :key="method.payment_provider" class="method" :class="{ active: selectedProvider === method.payment_provider }" @click="selectedProvider = method.payment_provider"><span class="method-mark">{{ String(method.display_name).slice(0, 1) }}</span><span>{{ method.display_name }}</span></button></div>
    <section class="panel"><div class="section-head" style="margin-top:0"><div><h2>普通充值</h2><p>当前全局比例：<strong>1 元 = {{ formatCredits(rate.credits_per_cny) }} 额度</strong>。</p></div></div><div class="package-grid"><article v-for="payment in presets" :key="payment" class="package-card"><h3>充值 {{ Number(payment) }} 元</h3><div class="price">¥{{ payment }}</div><p>到账 {{ creditsFor(payment) }} 额度</p><button class="primary-btn" :disabled="busyId === `direct-${payment}`" @click="createOrder('/api/v1/recharge-orders/direct', { payment_cny: String(payment) }, `direct-${payment}`)">立即充值</button></article><article class="package-card"><h3>自定义金额</h3><form @submit.prevent="createOrder('/api/v1/recharge-orders/direct', { payment_cny: customAmount }, 'custom')"><div class="field"><label>支付金额（元）</label><input v-model="customAmount" type="number" min="0.01" max="1000000" step="0.01" required></div><p>预计到账 <strong>{{ customPreview }}</strong> 额度</p><button class="primary-btn" type="submit" :disabled="busyId === 'custom'">按此金额充值</button></form></article></div></section>
    <div class="section-head"><div><h2>特惠充值包</h2><p>特惠包使用独立的支付金额和赠送额度。</p></div></div><div v-if="!packages.length" class="empty">当前没有可售特惠充值包。</div><div v-else class="package-grid"><article v-for="item in packages" :key="item.version_id" class="package-card"><h3>{{ item.package_code }}</h3><div class="price">¥{{ item.payment_cny }}</div><p>到账 {{ formatCredits(item.credits) }} 额度</p><button class="primary-btn" :disabled="busyId === item.version_id" @click="createOrder('/api/v1/recharge-orders', { package_version_id: item.version_id }, item.version_id)">创建充值订单</button></article></div>
    <div class="section-head"><div><h2>充值订单</h2><p>订单按创建时间从新到旧排列。</p></div></div><div v-if="!orders.length" class="empty">暂无充值订单。</div><div v-else class="table-wrap"><table><thead><tr><th>订单</th><th>充值包</th><th>支付金额</th><th>到账额度</th><th>支付途径</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="order in orders" :key="order.order_id"><td class="mono">{{ order.order_id }}</td><td>{{ order.package_code }}</td><td>¥{{ order.payment_cny }}</td><td>{{ formatCredits(order.credits) }}</td><td>{{ order.payment_provider }}</td><td><span class="status" :class="order.status">{{ orderLabels[order.status] || order.status }}</span></td><td>{{ formatDate(order.created_at) }}</td></tr></tbody></table></div>
    <div class="section-head"><div><h2>额度账务记录</h2><p>充值、冻结、结算、释放与冲销均以不可改写的记录表达。</p></div></div><div v-if="!statement.entries?.length" class="empty">暂无额度账务记录。</div><template v-else><div class="table-wrap"><table><thead><tr><th>类型</th><th>可用额度变动</th><th>冻结额度变动</th><th>变动后可用</th><th>引用</th><th>时间</th></tr></thead><tbody><tr v-for="entry in statement.entries" :key="entry.entry_id || `${entry.reference}-${entry.occurred_at}`"><td><span class="status" :class="entry.kind">{{ postingLabels[entry.kind] || entry.kind }}</span></td><td>{{ entry.delta_available_credits }}</td><td>{{ entry.delta_frozen_credits }}</td><td>{{ entry.available_credits_after }}</td><td class="mono">{{ entry.reference }}</td><td>{{ formatDate(entry.occurred_at) }}</td></tr></tbody></table></div><div class="row-actions" style="justify-content:center;margin-top:16px"><button class="secondary-btn" :disabled="Number(statement.page) <= 1" @click="load(Number(statement.page) - 1)">上一页</button><span>第 {{ statement.page }} / {{ statement.total_pages }} 页 · 共 {{ statement.total_entries }} 条</span><button class="secondary-btn" :disabled="Number(statement.page) >= Number(statement.total_pages)" @click="load(Number(statement.page) + 1)">下一页</button></div></template>
  </template>
</template>

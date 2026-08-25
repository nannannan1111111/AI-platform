<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const savingRate = ref(false);
const savingSettings = ref(false);
const error = ref("");
const settings = reactive<JsonRecord>({ methods: [] });
const rechargeRate = reactive<JsonRecord>({ credits_per_cny: "1.0000" });
const methodsText = ref("");
const merchantKey = ref("");

const statusText = computed(() => settings.enabled && settings.configured ? "已启用" : settings.configured ? "已配置，未启用" : "尚未完成配置");
const keyStatus = computed(() => settings.merchant_key_configured ? "商户密钥已安全保存；留空表示保留" : "尚未保存商户密钥");
const notifyUrl = computed(() => `${settings.public_base_url || "公开站点地址"}/api/v1/payments/epay/notify`);

function paymentMethodsText(methods: JsonRecord[]): string {
  const configured = methods?.length ? methods : [
    { payment_provider: "alipay", display_name: "支付宝" },
    { payment_provider: "wxpay", display_name: "微信支付" },
  ];
  return configured.map(method => `${method.payment_provider}|${method.display_name}`).join("\n");
}

function parseMethods(value: string): JsonRecord[] {
  return value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const separator = line.indexOf("|");
    if (separator < 1 || separator === line.length - 1) throw new Error("支付方式必须使用“标识|显示名称”格式");
    return { payment_provider: line.slice(0, separator).trim(), display_name: line.slice(separator + 1).trim() };
  });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [loadedSettings, loadedRate] = await Promise.all([
      props.bridge.api("/api/v1/admin/payment-settings"),
      props.bridge.api("/api/v1/admin/recharge-rate"),
    ]);
    Object.assign(settings, loadedSettings);
    if (!settings.public_base_url && window.location.protocol === "https:") settings.public_base_url = window.location.origin;
    Object.assign(rechargeRate, loadedRate);
    methodsText.value = paymentMethodsText(settings.methods || []);
    merchantKey.value = "";
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function saveRate(): Promise<void> {
  savingRate.value = true;
  try {
    Object.assign(rechargeRate, await props.bridge.api("/api/v1/admin/recharge-rate", {
      method: "PUT",
      body: JSON.stringify({ credits_per_cny: String(rechargeRate.credits_per_cny) }),
    }));
    props.bridge.toast("普通充值比例已保存");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    savingRate.value = false;
  }
}

async function saveSettings(): Promise<void> {
  savingSettings.value = true;
  try {
    const updated = await props.bridge.api("/api/v1/admin/payment-settings", {
      method: "PUT",
      body: JSON.stringify({
        enabled: Boolean(settings.enabled),
        gateway_url: settings.gateway_url,
        public_base_url: settings.public_base_url,
        merchant_id: settings.merchant_id,
        merchant_key: merchantKey.value,
        methods: parseMethods(methodsText.value),
      }),
    });
    Object.assign(settings, updated);
    methodsText.value = paymentMethodsText(settings.methods || []);
    merchantKey.value = "";
    props.bridge.toast("支付设置已保存");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    savingSettings.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取支付设置…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>支付设置</h1><p>对照 New API 的易支付兼容协议，统一配置支付宝、微信等支付方式。</p></div><span class="status" :class="settings.enabled && settings.configured ? 'healthy' : 'unknown'">{{ statusText }}</span></div>
    <section class="panel">
      <div class="section-head" style="margin-top:0"><div><h2>普通充值换算比例</h2><p>普通充值统一按这里的比例换算，特惠充值包不使用此比例。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveRate">
        <div class="field"><label>每 1 元兑换额度</label><input v-model="rechargeRate.credits_per_cny" type="number" min="0.0001" max="1000000" step="0.0001" required></div>
        <div class="field"><label>用户端显示</label><input :value="`1 元 = ${rechargeRate.credits_per_cny} 额度`" disabled></div>
        <button class="primary-btn" type="submit" :disabled="savingRate">{{ savingRate ? "保存中…" : "保存普通充值比例" }}</button>
      </form>
    </section>
    <section class="panel">
      <div class="section-head" style="margin-top:0"><div><h2>易支付网关</h2><p>商户密钥保存在受控密钥目录，不写入数据库、不返回浏览器。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveSettings">
        <div class="field span-two"><label><input v-model="settings.enabled" type="checkbox"> 启用在线支付</label><small>关闭后用户端不显示支付方式，已保存配置会保留。</small></div>
        <div class="field span-two"><label>网关基础地址</label><input v-model="settings.gateway_url" type="url" required placeholder="https://pay.example.com"><small>系统会自动提交到 /submit.php。</small></div>
        <div class="field span-two"><label>公开站点地址</label><input v-model="settings.public_base_url" type="url" required placeholder="https://studio.example.com"><small>网关回调地址：{{ notifyUrl }}</small></div>
        <div class="field"><label>商户 ID（PID）</label><input v-model="settings.merchant_id" required autocomplete="off" placeholder="1000"></div>
        <div class="field"><label>商户密钥</label><input v-model="merchantKey" type="password" autocomplete="new-password" :placeholder="keyStatus"><small>{{ keyStatus }}</small></div>
        <div class="field span-two"><label>支付方式</label><textarea v-model="methodsText" rows="4" required placeholder="alipay|支付宝&#10;wxpay|微信支付"></textarea><small>每行一种，格式为“网关方式标识|用户显示名称”。</small></div>
        <button class="primary-btn" type="submit" :disabled="savingSettings">{{ savingSettings ? "保存中…" : "保存支付设置" }}</button>
      </form>
    </section>
  </template>
</template>

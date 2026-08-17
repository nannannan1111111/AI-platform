<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import { errorMessage, formatBytes, formatCredits } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const user = computed<JsonRecord>(() => props.bridge.currentUser || {});
const balance = computed<JsonRecord>(() => props.bridge.currentBalance || {});
const storage = computed<JsonRecord>(() => user.value.storage_allowance || { limit_bytes: 0, used_bytes: 0, available_bytes: 0 });
const percentage = computed(() => {
  const limit = Math.max(Number(storage.value.limit_bytes) || 0, 0);
  const used = Math.max(Number(storage.value.used_bytes) || 0, 0);
  return limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
});
const initials = computed(() => String(user.value.email || "U").slice(0, 1).toUpperCase());
const resending = ref(false);
const savingPassword = ref(false);
const password = reactive({ current_password: "", new_password: "", confirm_password: "" });

async function resendVerification(): Promise<void> {
  resending.value = true;
  try {
    await props.bridge.api("/api/v1/auth/email-verification", { method: "POST" });
    props.bridge.toast("验证邮件已发送，请检查收件箱");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    resending.value = false;
  }
}

async function changePassword(): Promise<void> {
  if (password.new_password !== password.confirm_password) return props.bridge.toast("两次输入的新密码不一致");
  savingPassword.value = true;
  try {
    await props.bridge.api("/api/v1/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: password.current_password, new_password: password.new_password }),
    });
    props.bridge.invalidateSession("密码已修改，请使用新密码重新登录");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
    savingPassword.value = false;
  }
}
</script>

<template>
  <div class="page-head"><div><h1>个人账户</h1><p>查看身份信息、账户归属与存储额度使用情况。</p></div></div>
  <section class="profile-grid">
    <article class="panel profile-card"><div class="avatar">{{ initials }}</div><h2>{{ user.email }}</h2><p>个人创作者账户</p><span class="badge" :class="{ warning: !user.email_verified }">{{ user.email_verified ? "邮箱已验证" : "邮箱待验证" }}</span><button v-if="!user.email_verified" class="secondary-btn profile-action" type="button" :disabled="resending" @click="resendVerification">{{ resending ? "发送中…" : "重新发送验证邮件" }}</button></article>
    <article class="panel"><div class="section-head" style="margin-top:0"><div><h2>账户信息</h2><p>这些标识用于隔离您的创作资料和账务数据。</p></div></div><dl class="detail-list"><div class="detail-row"><dt>登录邮箱</dt><dd>{{ user.email }}</dd></div><div class="detail-row"><dt>邮箱状态</dt><dd>{{ user.email_verified ? "已验证" : "待验证" }}</dd></div><div class="detail-row"><dt>用户标识</dt><dd class="mono">{{ user.user_id }}</dd></div><div class="detail-row"><dt>账户空间标识</dt><dd class="mono">{{ user.account_space_id }}</dd></div></dl></article>
  </section>
  <div class="section-head"><div><h2>账户安全</h2><p>修改密码后，所有设备上的登录会话都会立即失效。</p></div></div>
  <section class="panel security-panel"><form class="security-form" @submit.prevent="changePassword"><div class="field"><label>当前密码</label><input v-model="password.current_password" type="password" autocomplete="current-password" required></div><div class="field"><label>新密码</label><input v-model="password.new_password" type="password" minlength="12" autocomplete="new-password" required placeholder="至少 12 个字符"></div><div class="field"><label>确认新密码</label><input v-model="password.confirm_password" type="password" minlength="12" autocomplete="new-password" required></div><button class="primary-btn" type="submit" :disabled="savingPassword">{{ savingPassword ? "修改中…" : "修改密码" }}</button></form></section>
  <div class="section-head"><div><h2>存储空间</h2><p>仅持久媒体计入，账户内相同内容按哈希去重。</p></div></div>
  <section class="grid three"><article class="stat-card"><span>存储额度上限</span><strong>{{ formatBytes(storage.limit_bytes) }}</strong><small>由平台管理员配置</small></article><article class="stat-card"><span>持久媒体已用</span><strong>{{ formatBytes(storage.used_bytes) }}</strong><small>临时、过期和已释放媒体不计入</small></article><article class="stat-card"><span>存储额度剩余</span><strong>{{ formatBytes(storage.available_bytes) }}</strong><div class="progress" aria-label="存储额度使用率"><span :style="{ width: `${percentage.toFixed(2)}%` }"></span></div></article></section>
  <div class="section-head"><div><h2>消费额度</h2><p>消费额度与存储额度相互独立。</p></div><button class="text-btn" @click="bridge.navigate('/workspace/wallet')">查看钱包明细</button></div>
  <section class="grid two"><article class="stat-card"><span>可用额度</span><strong>{{ formatCredits(balance.available_credits) }}</strong></article><article class="stat-card"><span>冻结额度</span><strong>{{ formatCredits(balance.frozen_credits) }}</strong></article></section>
</template>

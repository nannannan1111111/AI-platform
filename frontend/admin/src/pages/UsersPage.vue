<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { errorMessage, formatCredits, formatDate } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const error = ref("");
const windowValue = ref("7d");
const sortKey = ref("consumed_credits");
const sortDirection = ref<"asc" | "desc">("desc");
const users = ref<JsonRecord[]>([]);
const email = ref("");
const selectedUser = ref<JsonRecord | null>(null);
const rechargeRecords = ref<JsonRecord[]>([]);
const recordsLoaded = ref(false);
const searching = ref(false);
const savingConcurrency = ref(false);
const grantingCredits = ref(false);
const concurrency = ref(2);
const grantCredits = ref("");
const grantReason = ref("");
const typeLabels: Record<string, string> = { payment_recharge: "支付充值", admin_recharge: "人工充值", reversal: "充值冲正" };
const recordStatusLabels: Record<string, string> = { posted: "已入账", reversed: "已冲正" };

const sortedUsers = computed(() => [...users.value].sort((left, right) => {
  const value = (user: JsonRecord): string | number => {
    if (sortKey.value === "email") return String(user.email).toLowerCase();
    if (sortKey.value === "registered_at") return new Date(user.registered_at).getTime();
    return Number(user[sortKey.value] || 0);
  };
  const a = value(left);
  const b = value(right);
  const result = typeof a === "string" && typeof b === "string" ? a.localeCompare(b) : Number(a) - Number(b);
  return sortDirection.value === "asc" ? result : -result;
}));

function sortLabel(label: string, key: string): string {
  if (sortKey.value !== key) return `${label} ↕`;
  return `${label}${sortDirection.value === "asc" ? " ↑" : " ↓"}`;
}

function changeSort(key: string): void {
  sortDirection.value = sortKey.value === key && sortDirection.value === "desc" ? "asc" : "desc";
  sortKey.value = key;
}

async function loadActivity(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    users.value = await props.bridge.api(`/api/v1/admin/user-activity?window=${encodeURIComponent(windowValue.value)}`);
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function selectWindow(value: string): Promise<void> {
  windowValue.value = value;
  await loadActivity();
}

async function searchUser(): Promise<void> {
  searching.value = true;
  try {
    const normalized = email.value.trim().toLowerCase();
    selectedUser.value = await props.bridge.api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(normalized)}`);
    email.value = selectedUser.value?.email ?? normalized;
    concurrency.value = Number(selectedUser.value?.generation_execution_concurrency || 2);
    rechargeRecords.value = [];
    recordsLoaded.value = false;
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    searching.value = false;
  }
}

async function refreshSelected(): Promise<void> {
  if (!selectedUser.value) return;
  selectedUser.value = await props.bridge.api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(selectedUser.value.email)}`);
  concurrency.value = Number(selectedUser.value?.generation_execution_concurrency || 2);
}

async function saveConcurrency(): Promise<void> {
  if (!selectedUser.value) return;
  savingConcurrency.value = true;
  try {
    await props.bridge.api(`/api/v1/admin/users/${encodeURIComponent(selectedUser.value.user_id)}/generation-limit`, {
      method: "PUT",
      body: JSON.stringify({ execution_concurrency: Number(concurrency.value) }),
    });
    await refreshSelected();
    props.bridge.toast("用户执行并发已更新");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    savingConcurrency.value = false;
  }
}

async function grant(): Promise<void> {
  if (!selectedUser.value) return;
  const credits = String(grantCredits.value).trim();
  const reason = grantReason.value.trim();
  if (!credits || !reason) {
    props.bridge.toast("请填写充值额度和充值原因");
    return;
  }
  grantingCredits.value = true;
  try {
    await props.bridge.api(`/api/v1/admin/users/${encodeURIComponent(selectedUser.value.user_id)}/credit-grants`, {
      method: "POST",
      headers: { "Idempotency-Key": window.crypto.randomUUID() },
      body: JSON.stringify({ credits, reason }),
    });
    grantCredits.value = "";
    grantReason.value = "";
    await Promise.all([refreshSelected(), loadActivity()]);
    props.bridge.toast("人工充值已到账");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    grantingCredits.value = false;
  }
}

async function loadRecords(): Promise<void> {
  if (!selectedUser.value) return;
  try {
    rechargeRecords.value = await props.bridge.api(`/api/v1/admin/users/${encodeURIComponent(selectedUser.value.user_id)}/recharge-records`);
    recordsLoaded.value = true;
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  }
}

onMounted(loadActivity);
</script>

<template>
  <div v-if="loading" class="loading">正在读取用户统计…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>用户管理</h1><p>按邮箱查找并配置单个用户，同时查看各周期的额度消耗与任务质量。</p></div></div>
    <section class="panel admin-user-lookup">
      <div class="section-head" style="margin-top:0"><div><h2>按邮箱设置用户</h2><p>不会默认列出所有用户；请输入完整注册邮箱。</p></div></div>
      <form class="admin-user-search" @submit.prevent="searchUser"><input v-model="email" type="email" required placeholder="user@example.com"><button class="primary-btn" type="submit" :disabled="searching">{{ searching ? "查找中…" : "查找用户" }}</button></form>
      <div v-if="!selectedUser" class="empty admin-user-search-empty">输入完整邮箱并点击查找，再进行充值或并发设置。</div>
      <template v-else>
        <div class="admin-selected-user-head"><div><strong>{{ selectedUser.email }}</strong><span class="status" :class="selectedUser.email_verified ? 'healthy' : 'unknown'">{{ selectedUser.email_verified ? "邮箱已验证" : "邮箱未验证" }}</span></div><div>可用额度 <b>{{ formatCredits(selectedUser.available_credits) }}</b> · 冻结额度 <b>{{ formatCredits(selectedUser.frozen_credits) }}</b></div></div>
        <div class="admin-user-control-grid">
          <form class="admin-user-control-card" @submit.prevent="saveConcurrency"><div><strong>设置生成并发</strong><small>允许 1–20，超出任务继续排队。</small></div><div class="row-actions"><input v-model.number="concurrency" type="number" min="1" max="20" required aria-label="单用户执行并发数"><button class="secondary-btn" type="submit" :disabled="savingConcurrency">{{ savingConcurrency ? "保存中…" : "保存并发" }}</button></div></form>
          <form class="admin-user-control-card" @submit.prevent="grant"><div><strong>人工充值</strong><small>充值会形成永久账务记录。</small></div><div class="row-actions"><input v-model="grantCredits" type="number" min="0.0001" step="0.0001" required placeholder="额度"><input v-model="grantReason" required maxlength="255" placeholder="充值原因"><button class="primary-btn" type="submit" :disabled="grantingCredits">{{ grantingCredits ? "充值中…" : "确认充值" }}</button></div></form>
        </div>
        <button class="text-btn" type="button" @click="loadRecords">查看此用户充值记录</button>
        <section v-if="recordsLoaded" class="panel"><div class="section-head" style="margin-top:0"><div><h2>{{ selectedUser.email }} 的充值记录</h2><p>不显示支付凭据或幂等引用。</p></div></div><div v-if="!rechargeRecords.length" class="empty">该用户暂无充值记录。</div><div v-else class="table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>额度</th><th>原因</th><th>状态</th></tr></thead><tbody><tr v-for="record in rechargeRecords" :key="record.posting_id"><td>{{ formatDate(record.occurred_at) }}</td><td>{{ typeLabels[record.type] || record.type }}</td><td>{{ formatCredits(record.credits) }}</td><td>{{ record.reason || "—" }}</td><td>{{ recordStatusLabels[record.status] || record.status }}</td></tr></tbody></table></div></section>
      </template>
    </section>
    <section class="panel admin-user-activity">
      <div class="section-head" style="margin-top:0"><div><h2>用户用量统计</h2><p>失败任务不计入消费。</p></div><div class="admin-period-tabs"><button v-for="item in [{v:'7d',l:'近 7 天'},{v:'30d',l:'近 30 天'},{v:'all',l:'全部时间'}]" :key="item.v" type="button" :class="{active:windowValue===item.v}" @click="selectWindow(item.v)">{{ item.l }}</button></div></div>
      <div v-if="!sortedUsers.length" class="empty">当前周期没有可统计的注册用户。</div>
      <div v-else class="table-wrap admin-user-activity-table"><table><thead><tr><th><button type="button" @click="changeSort('email')">{{ sortLabel("用户邮箱", "email") }}</button></th><th><button type="button" @click="changeSort('consumed_credits')">{{ sortLabel("消耗额度", "consumed_credits") }}</button></th><th><button type="button" @click="changeSort('total_tasks')">{{ sortLabel("任务总数", "total_tasks") }}</button></th><th><button type="button" @click="changeSort('succeeded_tasks')">{{ sortLabel("成功任务", "succeeded_tasks") }}</button></th><th><button type="button" @click="changeSort('failed_tasks')">{{ sortLabel("失败任务", "failed_tasks") }}</button></th><th><button type="button" @click="changeSort('available_credits')">{{ sortLabel("当前余额", "available_credits") }}</button></th><th><button type="button" @click="changeSort('registered_at')">{{ sortLabel("注册时间", "registered_at") }}</button></th></tr></thead><tbody><tr v-for="user in sortedUsers" :key="user.user_id"><td><strong>{{ user.email }}</strong></td><td>{{ formatCredits(user.consumed_credits) }}</td><td>{{ Number(user.total_tasks) }}</td><td>{{ Number(user.succeeded_tasks) }}</td><td><span :class="{'admin-failure-count':Number(user.failed_tasks)}">{{ Number(user.failed_tasks) }}</span></td><td>{{ formatCredits(user.available_credits) }}</td><td>{{ formatDate(user.registered_at) }}</td></tr></tbody></table></div>
    </section>
  </template>
</template>

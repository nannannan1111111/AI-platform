<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage, formatBytes } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const savingGlobal = ref(false);
const searching = ref(false);
const savingUser = ref(false);
const error = ref("");
const globalLimitMb = ref(0);
const searchEmail = ref("");
const selectedUser = ref<JsonRecord | null>(null);
const selectedAllowance = ref<JsonRecord | null>(null);
const selectedLimitMb = ref(0);
const currentStorage = props.bridge.currentUser?.storage_allowance ?? null;

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const globalStorage = await props.bridge.api("/api/v1/admin/storage-allowance");
    globalLimitMb.value = Number(globalStorage.limit_bytes) / 1_000_000;
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function saveGlobal(): Promise<void> {
  savingGlobal.value = true;
  try {
    await props.bridge.api("/api/v1/admin/storage-allowance", {
      method: "PUT",
      body: JSON.stringify({ limit_bytes: globalLimitMb.value * 1_000_000 }),
    });
    props.bridge.toast("统一存储额度已更新");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    savingGlobal.value = false;
  }
}

async function searchUser(): Promise<void> {
  searching.value = true;
  try {
    const normalized = searchEmail.value.trim().toLowerCase();
    const user = await props.bridge.api(`/api/v1/admin/users/by-email?email=${encodeURIComponent(normalized)}`);
    const allowance = await props.bridge.api(`/api/v1/admin/users/${encodeURIComponent(user.user_id)}/storage-allowance`);
    selectedUser.value = user;
    selectedAllowance.value = allowance;
    selectedLimitMb.value = Number(allowance.limit_bytes) / 1_000_000;
    searchEmail.value = user.email;
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    searching.value = false;
  }
}

async function saveUser(): Promise<void> {
  if (!selectedUser.value) return;
  savingUser.value = true;
  try {
    selectedAllowance.value = await props.bridge.api(
      `/api/v1/admin/users/${encodeURIComponent(selectedUser.value.user_id)}/storage-allowance`,
      { method: "PUT", body: JSON.stringify({ limit_bytes: selectedLimitMb.value * 1_000_000 }) },
    );
    props.bridge.toast("用户存储额度已更新");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    savingUser.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取存储额度…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>存储额度</h1><p>配置全站统一上限，或搜索用户并为选中用户设置单独额度。</p></div></div>
    <section class="grid three">
      <article class="stat-card"><span>当前统一上限</span><strong>{{ formatBytes(globalLimitMb * 1_000_000) }}</strong><small>未单独设置的用户使用此额度</small></article>
      <article class="stat-card"><span>当前账户已用</span><strong>{{ currentStorage ? formatBytes(currentStorage.used_bytes) : "—" }}</strong><small>账户内相同内容按哈希去重</small></article>
      <article class="stat-card"><span>当前账户剩余</span><strong>{{ currentStorage ? formatBytes(currentStorage.available_bytes) : "—" }}</strong><small>最低显示为零</small></article>
    </section>
    <section class="panel admin-panel">
      <div class="section-head" style="margin-top:0"><div><h2>调整统一上限</h2><p>请输入十进制 MB（1 MB = 1,000,000 bytes）。调低额度不会删除已有媒体。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="saveGlobal">
        <div class="field span-two"><label>统一存储额度（MB）</label><input v-model.number="globalLimitMb" type="number" min="0" step="1" required></div>
        <button class="primary-btn" type="submit" :disabled="savingGlobal">{{ savingGlobal ? "保存中…" : "保存统一上限" }}</button>
      </form>
    </section>
    <section class="panel admin-user-lookup">
      <div class="section-head" style="margin-top:0"><div><h2>单独设置用户额度</h2><p>搜索完整注册邮箱；保存后仅覆盖选中用户的统一额度。</p></div></div>
      <form class="admin-user-search" @submit.prevent="searchUser"><input v-model="searchEmail" type="email" required placeholder="user@example.com"><button class="primary-btn" type="submit" :disabled="searching">{{ searching ? "搜索中…" : "搜索用户" }}</button></form>
      <div v-if="!selectedUser" class="empty admin-user-search-empty">输入完整注册邮箱并点击搜索，再为选中用户设置单独存储额度。</div>
      <template v-else>
        <div class="admin-selected-user-head"><div><strong>{{ selectedUser.email }}</strong><span class="status" :class="selectedUser.email_verified ? 'healthy' : 'unknown'">{{ selectedUser.email_verified ? "邮箱已验证" : "邮箱未验证" }}</span></div><div>当前存储额度 <b>{{ formatBytes(selectedAllowance?.limit_bytes) }}</b></div></div>
        <form class="admin-user-control-card admin-storage-user-control" @submit.prevent="saveUser">
          <div><strong>设置该用户的存储额度</strong><small>单独额度优先于统一额度，且不会影响其他用户。</small></div>
          <div class="row-actions"><input v-model.number="selectedLimitMb" type="number" min="0" step="1" required aria-label="用户存储额度（MB）"><button class="primary-btn" type="submit" :disabled="savingUser">{{ savingUser ? "保存中…" : "保存用户额度" }}</button></div>
        </form>
      </template>
    </section>
  </template>
</template>

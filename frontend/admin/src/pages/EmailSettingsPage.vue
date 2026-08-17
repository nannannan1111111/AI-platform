<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const settings = reactive<JsonRecord>({});

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    Object.assign(settings, await props.bridge.api("/api/v1/admin/email-settings"), { smtp_password: "" });
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  try {
    const updated = await props.bridge.api("/api/v1/admin/email-settings", {
      method: "PUT",
      body: JSON.stringify({
        public_base_url: settings.public_base_url,
        smtp_host: settings.smtp_host,
        smtp_port: Number(settings.smtp_port),
        smtp_sender: settings.smtp_sender,
        smtp_username: settings.smtp_username,
        smtp_password: settings.smtp_password,
        smtp_security: settings.smtp_security,
        smtp_timeout_seconds: Number(settings.smtp_timeout_seconds),
      }),
    });
    Object.assign(settings, updated, { smtp_password: "" });
    props.bridge.toast("邮件设置已保存");
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取邮件设置…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>邮件设置</h1><p>配置真实 SMTP 邮箱验证投递。修改后立即作用于所有 Web 进程，无需重启。</p></div><span class="status" :class="settings.configured ? 'healthy' : 'unknown'">{{ settings.configured ? "已配置" : "尚未配置" }}</span></div>
    <section class="panel">
      <div class="section-head" style="margin-top:0"><div><h2>站点与 SMTP</h2><p>SMTP 密码保存在受控密钥目录中，不写入数据库，也不会返回浏览器。</p></div></div>
      <form class="admin-form-grid" @submit.prevent="save">
        <div class="field span-two"><label>公开站点地址</label><input v-model="settings.public_base_url" type="url" required placeholder="https://studio.example.com"><small>必须是 HTTPS Origin，不包含路径；验证链接将使用此地址。</small></div>
        <div class="field"><label>SMTP 主机</label><input v-model="settings.smtp_host" required placeholder="smtp.example.com"></div>
        <div class="field"><label>SMTP 端口</label><input v-model.number="settings.smtp_port" type="number" min="1" max="65535" required></div>
        <div class="field"><label>发件地址</label><input v-model="settings.smtp_sender" type="email" required placeholder="noreply@example.com"></div>
        <div class="field"><label>SMTP 用户名</label><input v-model="settings.smtp_username" autocomplete="off" placeholder="留空表示无需认证"></div>
        <div class="field"><label>SMTP 密码</label><input v-model="settings.smtp_password" type="password" autocomplete="new-password" :placeholder="settings.password_configured ? '密码已安全保存；留空表示保留' : '尚未保存密码'"><small>{{ settings.password_configured ? "密码已安全保存；留空表示保留" : "尚未保存密码" }}</small></div>
        <div class="field"><label>安全模式</label><select v-model="settings.smtp_security"><option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">无加密（仅受控内网）</option></select></div>
        <div class="field"><label>发送超时（秒）</label><input v-model.number="settings.smtp_timeout_seconds" type="number" min="1" max="120" step="0.5" required></div>
        <button class="primary-btn" type="submit" :disabled="saving">{{ saving ? "保存中…" : "保存邮件设置" }}</button>
      </form>
    </section>
  </template>
</template>

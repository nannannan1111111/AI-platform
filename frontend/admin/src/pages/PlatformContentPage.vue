<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";

import { errorMessage } from "../format";
import type { AdminBridge, JsonRecord } from "../types";

const props = defineProps<{ bridge: AdminBridge }>();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const formElement = ref<HTMLFormElement | null>(null);
const settings = reactive<JsonRecord>({});
const announcementPreview = ref("");
const supportPreview = ref("");

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    Object.assign(settings, await props.bridge.api("/api/v1/platform-content"));
    announcementPreview.value = settings.announcement_image_url
      ? await props.bridge.authenticatedImage(settings.announcement_image_url)
      : "";
    supportPreview.value = settings.support_image_url
      ? await props.bridge.authenticatedImage(settings.support_image_url)
      : "";
  } catch (caught) {
    error.value = errorMessage(caught);
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  if (!formElement.value) return;
  saving.value = true;
  try {
    await props.bridge.api("/api/v1/admin/platform-content", {
      method: "PUT",
      body: new FormData(formElement.value),
    });
    props.bridge.toast("公告与客服内容已保存");
    await load();
  } catch (caught) {
    props.bridge.toast(errorMessage(caught));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div v-if="loading" class="loading">正在读取公告与客服内容…</div>
  <div v-else-if="error" class="empty">{{ error }}</div>
  <template v-else>
    <div class="page-head"><div><h1>公告与客服</h1><p>编辑用户顶部图标中展示的图片与文字，保存后立即生效。</p></div></div>
    <form ref="formElement" class="platform-content-admin-grid" @submit.prevent="save">
      <section class="panel">
        <div class="section-head" style="margin-top:0"><div><h2>公告内容</h2><p>适合放置活动、维护通知和平台说明。</p></div></div>
        <div class="platform-content-admin-preview"><img v-if="announcementPreview" :src="announcementPreview" alt="当前公告图片"><span v-else>尚未配置图片</span></div>
        <div class="field"><label>公告图片</label><input name="announcement_image" type="file" accept="image/png,image/jpeg,image/webp"><small>支持 PNG、JPEG、WebP，最大 5MB；不选择则保留原图。</small></div>
        <label class="checkbox-row"><input name="remove_announcement_image" type="checkbox"> 删除当前公告图片</label>
        <div class="field"><label>公告文字</label><textarea v-model="settings.announcement_text" name="announcement_text" rows="9" maxlength="10000" placeholder="输入公告内容"></textarea></div>
      </section>
      <section class="panel">
        <div class="section-head" style="margin-top:0"><div><h2>客服内容</h2><p>可放客服二维码、联系方式和服务时间。</p></div></div>
        <div class="platform-content-admin-preview"><img v-if="supportPreview" :src="supportPreview" alt="当前客服图片"><span v-else>尚未配置图片</span></div>
        <div class="field"><label>客服图片</label><input name="support_image" type="file" accept="image/png,image/jpeg,image/webp"><small>支持 PNG、JPEG、WebP，最大 5MB；不选择则保留原图。</small></div>
        <label class="checkbox-row"><input name="remove_support_image" type="checkbox"> 删除当前客服图片</label>
        <div class="field"><label>客服文字</label><textarea v-model="settings.support_text" name="support_text" rows="9" maxlength="10000" placeholder="输入客服联系方式与服务时间"></textarea></div>
      </section>
      <div class="platform-content-admin-actions"><button class="primary-btn" type="submit" :disabled="saving">{{ saving ? "保存中…" : "保存公告与客服" }}</button></div>
    </form>
  </template>
</template>

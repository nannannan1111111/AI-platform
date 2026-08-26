# 画布媒体显示

Type: task
Status: resolved

## 问题

画布部分历史图片只有 `/api/v1/media/.../content` 或旧缩略图字段，缺少可直接展示的已鉴权缩略图，导致节点图片空白。

## 验收

- 有 media ID 或可解析媒体内容地址的图片统一加载鉴权缩略图。
- 首屏不因缩略图阻塞；缩略图失败保留可交互的稳定媒体地址并可按需加载原图。
- 结果、上传和历史数据不重复保存、不删除。

## Comments

- 2026-08-26: Canvas display normalization now derives `media_id` from legacy `/api/v1/media/{id}/content` URLs, rewrites the stable authenticated content URL, and hydrates an authenticated thumbnail for every image value.
- 2026-08-26: If the thumbnail endpoint fails, the code falls back to the authenticated original Blob so valid canvas media remains visible; persistent values remain stable JSON without Blob/placeholder data.
- 2026-08-26: Regression coverage added in `backend/tests/test_v34_image_order_canvas_display.py`; legacy content URL promotion and thumbnail hydration pass.

## Answer

Resolved in V34. Existing generated, uploaded, and derived canvas images with only a legacy content URL are recognized and displayed through the authenticated thumbnail path, with a controlled original-image fallback only when thumbnail loading fails. No media or historical canvas data is deleted.

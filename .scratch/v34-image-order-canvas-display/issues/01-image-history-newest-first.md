# 图片历史最新优先逐个渲染

Type: task
Status: resolved

## 问题

图片页历史恢复同时启动多路媒体读取，旧任务可能先完成并触发重绘，用户看不到稳定的最新结果优先顺序。

## 验收

- 最新任务的最新图片先出现在页面。
- 后续历史图片逐个受控恢复并渲染。
- 活动任务仍使用既有 SSE/受控轮询，不重复提交。

## Comments

- 2026-08-26: `restoreRecentImageResults` now processes successful history newest task first, sorts each task's media newest first, hydrates one authenticated thumbnail at a time, and renders after each successful image. Active tasks retain SSE/controlled polling.
- 2026-08-26: Regression coverage added in `backend/tests/test_v34_image_order_canvas_display.py`; ordering and per-image hydration assertions pass.

## Answer

Resolved in V34. Historical image recovery starts with the newest task/image and progressively appends older thumbnails without creating tasks or changing billing state.

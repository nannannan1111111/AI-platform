# 修复上游结果读取与迟到交付

Type: task
Status: resolved

## Answer

生产日志确认存在 `stream ended without a final image` 和超时后的 `provider acceptance conflicts`。Provider Adapter 现兼容错误标记为 `text/event-stream` 的普通 JSON 响应；任务截止延长至二十分钟，迟到交付保留权威超时结果而不再误报接收冲突。

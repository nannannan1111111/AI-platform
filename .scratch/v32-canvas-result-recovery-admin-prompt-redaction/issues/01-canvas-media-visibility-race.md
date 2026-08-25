# 画布成功终态媒体可见性竞态

Type: task
Status: claimed

## 问题

上游已返回并已扣费的图片任务，在成功 SSE 终态到达时媒体列表可能暂时为空，智能画布错误结束任务并不显示结果。

## 处理

对成功终态的媒体查询增加有界指数退避，媒体数量达到 `delivered_quantity` 后才完成；恢复入口复用该策略。

## Comments

- 证据：V31 生产日志显示 Provider/OSS HTTP 200，数据库任务 `succeeded`、`delivered_quantity=1`；客户端成功后单次媒体读取存在可见性窗口。

# 将图片生成移出 Web 请求

Type: task
Status: resolved

## Scope

实现 PostgreSQL 队列消费进程、跨进程任务互斥和生产 Compose Worker 服务。

## Answer

已实现 PostgreSQL 队列 Worker、按任务 advisory lock、生产 Compose 可扩 Worker，并以 SSE 替换图片工作区的浏览器轮询。

## Comments

- 2026-08-12：排查任务 `ca7a7efa-ca91-419b-a6e9-00d5dc64fe14`。数据库记录 `quantity=2`、`delivered_quantity=1`；Worker 日志证明同一批次向上游发出了两个独立 POST，并获得两个不同请求 ID，但只有一个流返回最终图片。新增逐子请求完成/失败日志，并在 HTTP 与 Web UI 中把这种情况明确标记为“部分完成 1/2”，避免误报为全部生成完成。使用 MockTransport 验证，不触发真实上游费用。

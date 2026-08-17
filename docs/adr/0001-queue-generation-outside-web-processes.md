# ADR 0001: 图片生成在独立 Worker 中执行

## Status

Accepted

## Context

一次 Provider 图片生成可能持续数分钟。若由提交任务的 HTTP 请求同步执行，少量并发生成就会长期占用 Web 线程、客户端连接和反向代理连接，无法稳定承载上百名活跃用户。

## Decision

生产环境的 Web 进程只持久化 `queued` 任务。独立生成 Worker 从 PostgreSQL 查找可提交任务，并在持有基于任务 ID 的 PostgreSQL advisory lock 时调用现有的幂等提交器。Worker 并发数和实例数可以独立于 Web 进程扩展。

开发和测试仍可选择 `inline` 模式，以保留快速的端到端反馈。

## Consequences

- HTTP 提交不会等待 Provider 完成，峰值由数据库队列吸收。
- PostgreSQL 是队列事实来源，不引入 Redis/RabbitMQ 运维依赖。
- Worker 崩溃时 advisory lock 随数据库连接释放；已进入 `submitting`/`unknown` 的任务继续遵循现有人工核实语义，避免重复计费。
- 生成吞吐需要按 Provider 配额调节 Worker 并发，而 Web 容量可以独立扩展。

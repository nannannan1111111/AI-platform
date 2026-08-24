# 修复 Worker 结果交付连接池耗尽

Type: task
Status: resolved

## Evidence

生产日志显示上游 HTTP 200 之后进入 `completed image delivery failed`，异常类型为 `TimeoutError`；同一时间 Worker 出现 `QueuePool limit of size 2 overflow 1 reached`。数据库中失败任务保留 Provider request ID、没有媒体记录，最终由截止扫描结束。

## Work

- 将长期 Worker/任务 advisory locks 与业务数据库连接池隔离。
- 为锁连接生命周期、异常释放和连接预算增加测试。
- 保持任务、Provider、用户并发限制及幂等语义。

## Comments

- 2026-08-24：基于 V27 生产日志建立；未对生产执行写操作。
- 2026-08-24：生产只读证据确认结果已由 Provider 返回并解析，失败发生在 GeneratedMedia 登记/任务终态持久化阶段；未用猜测替代日志结论。
- 2026-08-24：再次只读筛选生产 Worker 5 日志：09:36:45 JSON HTTP 200 后于 09:36:56 交付 `TimeoutError`；09:40:54 SSE HTTP 200 后于 09:42:34 同样交付 `TimeoutError`。两类响应均指向落库连接超时，而非响应字段解析。

## Answer

- 新增独立 `NullPool` advisory-lock engine；每个 Worker 用一个 AUTOCOMMIT 会话同时承载 ordinal lock 和全部任务 dispatch locks，不再占用业务连接池。
- 同一会话上的 advisory lock 对 PostgreSQL 是可重入的，因此增加进程内已持有 key 集合与互斥访问，确保不同线程不能重复使用同一任务、用户或 Provider 槽位。
- 业务连接池继续承担任务查询、状态更新和媒体持久化；未改变 Provider 幂等键、任务恢复或额度语义。
- 发布连接预算现在只增加每 Worker 1 个共享锁会话；生产最坏应用连接数为 `4 × (8 + 4) + 10 × (2 + 1 + 1) = 88`，加 20% 预留为 106，低于 PostgreSQL `max_connections=120`。
- 池隔离与可重入防护回归覆盖共享锁会话、业务池可用性、Worker 锁使用和连接预算。
- 回归测试覆盖 lock engine、Worker 锁使用与连接预算；连接池/Worker/交付定向 `72 passed`，后端全量 `641 passed, 5 skipped`。

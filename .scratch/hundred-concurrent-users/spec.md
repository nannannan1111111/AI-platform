# 百人并发容量改造

## 目标

让单机生产部署能够承载至少 100 个同时在线的活跃会话，并允许在不阻塞 Web 请求的前提下排队处理图片生成任务。生成吞吐由可配置的后台 Worker 并发数和上游 Provider 配额共同决定。

## 验收标准

- Web 服务默认启动 4 个进程，进程数可通过环境变量调整。
- 图片生成 HTTP 请求只完成鉴权、校验和持久化排队，不在 Web 进程内等待最长数分钟的 Provider 响应。
- 后台 Worker 从 PostgreSQL 读取待处理任务；多个 Worker 不会同时向 Provider 提交同一任务。
- 每个进程的 SQLAlchemy 连接池大小、溢出和等待超时均可配置，部署文档给出总连接预算公式。
- 每个 Web 进程重复启动的周期任务使用 PostgreSQL advisory lock 保证集群内同一时刻只运行一个副本。
- 同一账户的活动生成任务上限在多个 Web 进程并发提交时仍然有效。
- 提供数据库就绪探针，以及面向 100 并发连接的可重复冒烟压测脚本。
- 浏览器在任务提交后使用单条 SSE 状态流，不再每 1.8 秒创建一个新的状态查询请求。

## 非目标

- 不承诺 100 个图片生成任务同时完成；其上限取决于 Worker 数、机器资源和 Provider 配额。
- 本阶段生产 Compose 面向单主机部署。跨主机部署需要共享 POSIX 文件系统，或补齐支持读写的 S3 媒体 Adapter。

## 验证结果

- 容量相关定向测试：25 passed。
- 排除 5 个与本次改动无关的既存 Web UI 失败后：442 passed、4 skipped。
- Compose 生产配置解析成功；Alembic 单 head 为 `0044_generation_queue_index`。
- 新增运行时模块通过 Ruff 和严格 MyPy；Web UI JavaScript 通过 Node 语法检查。

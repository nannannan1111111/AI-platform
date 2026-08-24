# V28 图片结果交付与智能画布交互修复

## 生产证据

- V27 生产任务已取得上游 HTTP 200 和 `request:*` Provider 任务标识。
- 最近失败任务停留在 `provider_pending`，最终由任务截止扫描标记为 `generation task exceeded configured deadline`。
- 对应 Worker 日志记录 `completed image delivery failed ... error_type=TimeoutError`，另有 `sqlalchemy.exc.TimeoutError: QueuePool limit of size 2 overflow 1 reached`，随后 Worker 重启。
- V27 Worker 每进程数据库池上限为 3，但一个长期持有连接的 Worker 实例锁和每个执行中任务的调度锁都来自同一业务连接池；生产配置为每 Worker 并发 10，锁连接会耗尽连接池，导致结果已经解析后无法登记媒体或更新任务。

## 目标

- 隔离 Worker 长期 advisory lock 连接与业务数据库连接池，确保上游结果下载后能够幂等登记 GeneratedMedia 并完成任务。
- SSE 中断时以有界退避轮询恢复同一任务；成功终态必须通过媒体查询收敛，不能重提上游任务。
- 图片生成页面和智能画布都能立即显示结果，并在页面重进后恢复。
- 删除新建画布表单中的“智能画布”只读选择框及说明，不改变 smart-only 行为和 classic 历史兼容。
- 修复 CSP 事件桥与弹窗事件传播冲突，逐项验证智能画布现有交互按钮。

## 安全约束

- 不重复创建上游任务，不重复扣费、退款或写入额度流水。
- 以现有任务 ID、Provider 幂等键、result reference 和媒体唯一约束完成恢复与去重。
- 不删除用户图片、参考图、画布、历史生成记录或 classic 数据。
- 不修改生产环境、不迁移生产数据库、不重启或切换生产容器。
- 保留恢复并发限制、缩略图优先、原图按需加载和临时结果 24 小时清理策略。

## 验收标准

- Worker 并发高于业务池容量时，调度锁不再耗尽业务连接池，多个并发结果均可完成媒体登记。
- SSE 成功事件早于媒体事件或 SSE 断开时，两个入口都通过受控只读查询取得最终媒体。
- 页面重进可恢复任务和结果，同一媒体只显示一次。
- 工作流导入导出、关闭、编辑、下载、上传、节点操作和视图工具完成真实浏览器验证。
- 后端、Web UI 和生产构建达到或超过 V27 测试基线。

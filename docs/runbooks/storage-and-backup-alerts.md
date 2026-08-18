# 媒体存储与备份告警 Runbook

本文对应 `deploy/monitoring/storage-backup-alerts.yml`。开始处置时记录告警时间、实例、当前镜像摘要、迁移 head、最近一次成功恢复点和 request ID；不得把数据库连接串、快照 ID、文件路径或密钥复制到工单或聊天。

## Media storage probe failed

1. 在受控主机确认媒体卷仍已挂载，应用 UID/GID `10001` 对媒体目录具有读写权限，并检查内核、文件系统和 CBS 事件。
2. 调用已鉴权 `/metrics`，确认 `media_storage_probe_success` 是否持续为 `0`；不要通过重启或创建空目录掩盖丢失的挂载。
3. 若卷已卸载或只读，暂停新生成任务入口，保护 PostgreSQL 与现有媒体引用，再按云盘恢复流程处理。
4. 卷恢复后验证代表性媒体读取和临时文件读写删除探针；指标连续 10 分钟为 `1` 后确认恢复通知。

## Media storage nearly full

1. 核对 `media_storage_used_bytes / media_storage_total_bytes`、inode、增长速度和 CBS 控制台容量；四个 Web 进程看到同一卷时按实例去重。
2. 找出增长来源和生命周期清理是否停止。不要直接删除对象文件，因为数据库仍保存归属与账务引用。
3. 预计在 24 小时内达到 90% 时暂停非必要生成并扩容 CBS；新容量应按实际增长率预留恢复和快照空间。
4. 扩容后按腾讯云要求扩展文件系统，再验证媒体读写、容量指标和告警恢复。

## Backup metrics missing

1. 检查备份校验调度、最近退出码，以及 node_exporter/textfile collector 或腾讯云采集 Agent 是否仍能读取指标目录。
2. 确认定时任务包含 `backup_manifest.py verify ... --metrics-file <collector-dir>/infinite-canvas-backup.prom`，并且原子临时文件可在同一文件系统重命名。
3. 不要把“采集恢复”当作“备份恢复”；重新校验最近恢复点，并等待完整性和成功时间指标出现后确认恢复通知。

## Backup recovery point invalid

1. 立即隔离失败恢复点，禁止覆盖或作为恢复来源；保留 manifest 和失败日志供审计。
2. 对照 manifest 确认数据库、媒体、密钥及部署元数据是否齐全，检查 SHA-256、文件大小、映射路径和快照完成状态。
3. 选择上一个已验证恢复点评估实际数据缺口，同时重新生成新的同点一致备份。不得只补拷单个缺失文件并宣称原恢复点有效。
4. 新恢复点校验通过后，在隔离环境执行代表性恢复验收；`backup_recovery_point_integrity` 连续为 `1` 后确认恢复通知。

## Backup recovery point stale

1. 比较当前时间与 `backup_last_success_timestamp_seconds`，同时检查 `backup_last_verification_timestamp_seconds`：后者较新但完整性为 `0` 表示任务在运行但恢复点损坏。
2. 检查数据库备份、媒体快照、加密密钥快照和 manifest 调度；排除容量、权限、网络和保留策略问题。
3. 生成并校验新的同点一致恢复点。若超出批准的恢复目标，通知生产负责人并按事故流程记录实际 RPO 风险。
4. 该 26 小时规则只监控每日可验证恢复点，不证明 15 分钟 PITR RPO。真实腾讯云环境必须另行监控 TencentDB PITR 连续性并执行隔离恢复演练。

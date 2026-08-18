# 预发布端到端验收

本 Runbook 用于与生产同构、数据隔离的预发布环境。候选环境必须运行与待发布生产版本相同的镜像摘要和拓扑，只替换域名、数据库、Secret、Provider、SMTP 与支付账号。仓库自动测试证明领域规则，本文的预发布验收证明真实依赖和网络边界。

## 安全边界

- 使用专用测试用户、管理员、收件箱、Provider 小额度和支付沙箱或最小金额订单，不复用生产用户数据。
- Token、密码、API Key、支付签名、提示词、图片、响应体、媒体 ID、任务 ID、账号空间 ID 和内部文件路径不得进入验收附件。
- 所有凭据和业务标识只通过环境变量注入。支付通知原文保存在受控临时文件中，验收结束后按团队的机密文件流程销毁。
- 默认脚本只读。取消任务、重放支付通知和删除媒体必须显式增加 `--allow-state-change`。
- 清理只调用当前测试账号的公开媒体删除接口。生成任务、额度流水、订单和支付事件是审计事实，不执行 SQL 删除，也不删除媒体目录中的文件。
- 任一场景出现非预期资金变化、跨账号可见、凭据泄漏、图片落盘于迟到结果或告警失效时立即停止，不继续执行其余状态变更。

## 验收矩阵

| 场景 | 触发方式 | 自动判定 | 通过证据 |
| --- | --- | --- | --- |
| 健康、就绪、模型与支付目录 | `baseline` | 是 | HTTP 状态与请求 ID |
| 测试用户登录与会话 | `baseline` + 测试账号 | 是 | 登录及 `/auth/me` 状态与请求 ID |
| 注册、验证邮件、密码找回 | 操作员使用真实收件箱 | 否 | 脱敏时间点、邮件送达结果和请求 ID |
| 管理员权限边界 | 操作员分别使用管理员与普通账号 | 否 | 允许/拒绝状态和请求 ID |
| 画布保存恢复、媒体上传读取、LLM | 操作员使用专用数据 | 否 | 每步状态和请求 ID，不附内容 |
| Provider 正常生成与额度结算 | 操作员提交低成本任务 | 否 | 终态、额度不变量和 Provider 请求 ID |
| 管理员取消与重复取消 | `cancel-task` | 是 | 两次取消均为 `cancelled`、投影一致且第二次不再改变余额 |
| Provider 超时与额度释放 | 操作员制造超过任务时限的真实任务，随后执行 `provider-timeout` | 辅助 | 权威终态为超时失败且没有媒体 |
| 迟到结果拒收 | 超时后允许真实 Provider 返回，再执行 `late-result` | 辅助 | 任务仍为超时失败且媒体列表为空 |
| 重复支付通知幂等 | 支付沙箱/最小金额通知原文 + `payment-replay` | 是 | 两次均确认成功且第二次后余额不变 |
| 测试媒体清理 | `cleanup-media` | 是 | 每项删除为 204，重复清理 404 也视为幂等成功 |
| 备份恢复后复验 | 按备份 Runbook 恢复隔离环境并重跑代表场景 | 否 | 恢复点、候选镜像摘要和请求 ID |
| 告警与日志关联 | 操作员触发已批准的预发布故障 | 否 | 告警时间、请求 ID、任务 ID 指纹和处置记录 |

“辅助”表示脚本只验证最终权威状态；超时和迟到结果必须由真实 Worker 与 Provider 沙箱触发，不能只凭脚本结果宣称真实依赖已通过。

## 准备输入

PowerShell 示例：

```powershell
$env:STAGING_TEST_EMAIL = 'staging-test@example.com'
$env:STAGING_TEST_PASSWORD = '<从 Secret Manager 注入>'
$env:STAGING_ADMIN_EMAIL = 'staging-admin@example.com'
$env:STAGING_ADMIN_PASSWORD = '<从 Secret Manager 注入>'
$env:STAGING_ACCOUNT_SPACE_ID = '<专用测试账号空间>'
$env:STAGING_CANCEL_TASK_ID = '<仍处于活动状态的低成本任务>'
$env:STAGING_TIMEOUT_TASK_ID = '<已由真实 Worker 判定超时的任务>'
$env:STAGING_LATE_RESULT_TASK_ID = '<超时后 Provider 已返回结果的任务>'
$env:STAGING_EPAY_NOTIFICATION_FILE = '<受控目录中的原始表单文件>'
$env:STAGING_CLEANUP_MEDIA_IDS = '<媒体 ID 1>,<媒体 ID 2>'
```

支付通知文件必须是网关实际发送的 `application/x-www-form-urlencoded` 内容。脚本不会生成签名，也不会把文件内容、路径或订单号写入证据。取消任务必须属于 `STAGING_ACCOUNT_SPACE_ID`；后端还会执行管理员鉴权和账号空间匹配。

## 执行

先执行无状态基线：

```powershell
cd backend
& .venv/Scripts/python.exe scripts/staging_acceptance.py `
  https://staging.example.com `
  --scenario baseline `
  --evidence staging-baseline.jsonl
```

在负责人确认测试数据、支付金额和停止条件后，才执行状态型场景：

```powershell
& .venv/Scripts/python.exe scripts/staging_acceptance.py `
  https://staging.example.com `
  --scenario cancel-task `
  --scenario provider-timeout `
  --scenario late-result `
  --scenario payment-replay `
  --scenario cleanup-media `
  --allow-state-change `
  --evidence staging-stateful.jsonl
```

退出码为 0 只表示所选脚本场景通过。证据 JSONL 仅包含场景、执行模式、通过/失败、HTTP 状态、经校验的请求 ID 和固定错误代码。验收负责人还需把矩阵中的人工项、候选镜像摘要、恢复点和签字结论记录到发布工单。

## 清理与复验

1. 确认测试媒体没有被个人资产或画布引用，再执行 `cleanup-media`；409 表示仍被引用，应先通过产品界面解除引用。
2. 保留任务、额度、订单和支付事件作为审计证据，不直接删除数据库记录。
3. 清除当前 shell 中的 `STAGING_*` 环境变量，并按机密文件流程处理支付通知临时文件。
4. 从同点一致恢复点重建隔离环境，使用相同候选镜像摘要重跑基线、登录、代表性生成和账务查询。
5. 只有全部自动与人工项通过，或存在具名负责人、截止日期和风险接受记录的豁免，任务 10 才能关闭。

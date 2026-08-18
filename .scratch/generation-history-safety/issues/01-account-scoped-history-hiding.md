# 01 账户级非破坏性生成历史清理

Type: task
Status: resolved

## 目标

允许用户从最近生成任务列表隐藏自己的终态任务，同时保留活动任务、任务详情、额度事实、媒体事实和运营统计。

## 问题分析

- 数据库迁移 `0049_generation_task_history_visibility` 已加入 `history_hidden_at`，但 SQLAlchemy Adapter 未声明或使用该列。
- HTTP 没有历史清理路由，页面只提供刷新。
- 三个相关测试被改名为 `_rolled_back_test_...`，因此 pytest 不执行，形成虚假的覆盖印象。
- 直接删除生成任务会破坏额度、Provider 结果和运营审计链。

## 设计方案

- `clear_history(account_space_id, cleared_at)` 只标记当前账户尚未隐藏的终态任务，并返回影响数量。
- 活动任务不隐藏；重复调用返回零；其他账户不受影响。
- `recent_for_account` 和 `recent_for_canvas` 排除隐藏任务，`get`、`activity_summary` 和管理查询仍读取全部事实。
- HTTP 使用已认证账户空间，不接受客户端传入账户 ID。
- 页面在确认对话框中明确说明任务记录和生成图片不会被删除。

## 实施步骤

### 1. 分析问题

- 核对历史迁移、领域接口、内存/SQL Adapter、HTTP 路由和两套前端实现。

### 2. 设计确认

- 固定账户隔离、终态限定、幂等和事实保留语义。

### 3. 修改代码

- 实现领域方法和 Adapter。
- 增加 HTTP 路由并接入页面。
- 恢复并扩展自动化测试。

### 4. 质量检测

- 运行生成任务定向测试、迁移测试、Ruff、严格 MyPy、完整 pytest、前端检查与构建。

## 完成标准

- 清理后仅当前账户终态任务从最近列表消失。
- 活动任务、详情、统计、额度和媒体事实不变。
- SQLite/PostgreSQL 语义一致，重启后隐藏状态保留。
- 前端明确表达非破坏性行为，相关测试不再通过改名绕过。

## Comments

### 2026-08-18 完成

- 领域接口、内存 Adapter 和 SQLAlchemy Adapter 已统一实现 `clear_history`；仅隐藏当前账户的终态任务，重复调用幂等。
- `recent_for_account` 与 `recent_for_canvas` 排除隐藏任务；任务详情、活动任务、额度事实和 `activity_summary` 保持可追溯。
- 新增认证后的 `DELETE /api/v1/generation-tasks/history`，账户空间只能从服务端会话取得。
- 两套用户界面均提供“清除已结束记录”，确认文案明确任务记录、额度流水和生成图片不会被删除。
- 三个原先通过 `_rolled_back_test_...` 绕过 pytest 的相关测试已恢复，并增加 PostgreSQL 17 行为覆盖。
- 定向回归 3 项通过；完整 Windows 回归 629 项通过、5 项跳过，随后 4 项 PostgreSQL 17 专属测试全部通过，合并结果为 633 项通过、仅 1 项 Linux 权限合同因 Windows 跳过。
- Ruff、严格 MyPy（142 个源文件）、前端类型检查与构建、构建产物重复生成校验、生产 Compose 解析和 `git diff --check` 均通过。

## Answer

生成历史清理现已具备账户隔离、终态限定、幂等和非破坏性语义。该任务完全在仓库与一次性本地 PostgreSQL 17 环境中完成，不需要腾讯云、支付、SMTP 或真实 Provider；一次性数据库容器已在测试后删除。

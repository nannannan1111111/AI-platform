# 01 消除 PR 分支重复流水线

Type: task
Status: resolved

## 目标

让功能分支提交只通过 `pull_request` 事件执行 `quality-gate` 与 `supply-chain`，避免同一 SHA 重复构建、测试和扫描。

## 问题分析

- PR #18 的提交 `8bc1abd` 同时产生两次 `quality-gate` 和两次 `supply-chain`。
- `push` 运行分别为 Actions 32130389815、32130390033；`pull_request` 运行分别为 32130415060、32130415058。
- 四次运行使用同一源码 SHA，全部执行 PostgreSQL、生产镜像构建和漏洞扫描。
- 并发键包含 `github.ref`；分支 push 与 PR merge ref 不同，因此 `cancel-in-progress` 无法跨事件去重。

## 设计方案

- `quality-gate`：保留所有 `pull_request`、`main` 分支 `push` 和 `workflow_dispatch`。
- `supply-chain`：保留所有 `pull_request`、`main` 分支 `push`、`v*` 标签 `push` 和 `workflow_dispatch`。
- PR 必需检查名称 `release-gate` 与 `supply-chain-gate` 不变，不修改分支保护。
- `main` 合并后仍验证合并提交；标签仍先扫描再签名发布。
- 普通功能分支在创建 PR 前不自动运行，开发者可使用 `workflow_dispatch` 或本地质量脚本提前验证。

## 实施步骤

### 1. 分析问题

- 读取 workflow 触发与并发配置，并记录 PR #18 的真实运行事件。

### 2. 设计确认

- 固定 PR、`main`、`v*` 标签和手工触发矩阵。

### 3. 修改代码

- 收紧两个 workflow 的 `push.branches`。
- 添加触发合同测试并更新运维文档。

### 4. 质量检测

- 运行定向测试、Ruff、严格 MyPy 和完整回归。
- 推送新功能分支并创建 PR，核对 Actions 事件与必需检查。

## 完成标准

- 新 PR 的同一 SHA 只产生两个 `pull_request` 工作流。
- `release-gate` 与 `supply-chain-gate` 全部通过。
- `main` push、`v*` 标签发布和手工触发合同仍受测试保护。

## Comments

### 2026-08-18 完成

- PR #18 的提交 `8bc1abd` 确认产生 4 条重复运行：`push` 事件 32130389815、32130390033，以及 `pull_request` 事件 32130415060、32130415058。
- `quality-gate` 和 `supply-chain` 的普通分支 `push` 已限定为 `main`；PR、手工执行和 `v*` 标签发布路径保留。
- 推送功能分支提交 `acd75af` 后、创建 PR 前，GitHub Actions 查询结果为零条运行。
- 创建 PR #19 后，同一提交只产生 `pull_request` 事件的 `quality-gate` 32133793039 和 `supply-chain` 32133793047，没有同 SHA 的 `push` 运行。
- 两条远端工作流全部通过：PostgreSQL 17、Ruff、严格 MyPy、前端构建、production-contract、SBOM/Trivy、`release-gate` 和 `supply-chain-gate` 均成功；非标签发布 Job 正确跳过。
- 本地触发合同测试 12 项通过，完整回归 631 项通过、5 项跳过；Ruff、严格 MyPy（142 个源文件）和 `git diff --check` 通过。

## Answer

PR 功能分支不再同时执行 `push` 与 `pull_request` 两套流水线。分支保护依赖的检查名称未改变，`main` 合并后验证、手工验证和 `v*` 标签签名发布合同均保留。

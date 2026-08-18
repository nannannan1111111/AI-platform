# 01 升级 GitHub Actions 运行时依赖

Type: task
Status: resolved

## 目标

消除工作流对旧 Node 运行时 Action 版本的依赖，并保持质量门禁与发布流程合同不变。

## 问题分析

- `quality-gate.yml` 使用 `actions/checkout` v4.2.2、`actions/setup-python` v5.6.0 和 `actions/setup-node` v4.4.0。
- `supply-chain.yml` 使用旧版 checkout、Buildx、Build/Push、SBOM、Artifact、Docker 登录、Metadata 和 Cosign Actions。
- GitHub-hosted `ubuntu-24.04` runner 可满足新 Artifact Action 的 runner 版本要求，因此不需要引入自托管 runner 或云端资源。

## 设计方案

- 采用 Dependabot PR #4 的集中升级，避免手工重新选择版本和 SHA。
- 保持所有 Action 使用固定 commit SHA，并同步更新版本注释。
- 不改变以下合同：
  - `quality-gate` 的 `pull_request`、`main` push 和手工触发。
  - `supply-chain` 的 `pull_request`、`main` push、`v*` Tag push 和手工触发。
  - `release-gate`、`supply-chain-gate` job 名称。
  - SBOM、Trivy 漏洞门禁、Tag 发布镜像和 Cosign OIDC 签名。

## 实施步骤

### 1. 分析问题

- 审查 PR #4 的文件范围和完整 patch。
- 确认 PR 只修改 `.github/workflows/quality-gate.yml` 与 `.github/workflows/supply-chain.yml` 中的 Action 引用。

### 2. 设计确认

- 复用 Dependabot 提供的 10 项升级。
- 继续使用 GitHub-hosted `ubuntu-24.04` runner。

### 3. 修改代码

- 合并 PR #4，提交 `1aeb8c67d9f407b866a3aa325e831971ca910808`。
- 升级 checkout/setup-python/setup-node、Docker Actions、SBOM/Artifact 和 Cosign Installer 到 PR 中锁定的 SHA。

### 4. 质量检测

- PR #4 所有检查通过，且 PR 状态为 `MERGED`。
- 合并后的 `main`：
  - `quality-gate` 运行 32141198817 成功。
  - `supply-chain` 运行 32141198806 成功。
- 新版本 Action 在真实 GitHub runner 上成功执行：前端构建、PostgreSQL 17 后端测试、Ruff/MyPy、production-contract、SBOM、Trivy 和门禁 job 均通过。
- 非 Tag 的 `publish-signed-image` 仍正确跳过。
- 本地 `main` 已同步到远端，工作区干净。

## 完成标准

- 所有受影响 Action 已升级并固定到审核过的 commit SHA。
- 既有触发矩阵和必需检查名称未改变。
- 合并后的两条 `main` 流水线全部成功。

## Comments

### 2026-08-18 完成

- Dependabot PR #4 审查结果为仅依赖升级，无 workflow 语义变化。
- PR #4 的 `backend-quality`、`backend-tests`、`frontend-quality`、`production-contract`、`release-gate`、`image-sbom-vulnerability-gate` 和 `supply-chain-gate` 均通过。
- 合并后 `main` 的 quality-gate 与 supply-chain 均通过，非 Tag 发布 job 跳过符合预期。

## Answer

GitHub Actions 的旧 Node 运行时依赖已通过 Dependabot PR #4 完成升级。工作流合同、质量门禁和供应链发布语义保持不变，并已在合并后的 `main` 上完成真实 Actions 验证。


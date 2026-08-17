# 02 可重复构建与供应链安全

Type: task
Status: claimed
Stage: 发布可信
Blocked by: 01

## 目标

使同一提交可重复构建出依赖可追溯、经过漏洞扫描和签名的生产镜像。

## 问题分析

- `python:3.12-slim` 未固定 digest，Python 依赖使用宽版本范围，没有生产锁文件和 hash。
- npm 有 lockfile，但 Docker 不在镜像构建中验证 Vue 源码与静态产物关系。
- 当前没有 SBOM、镜像漏洞门禁、镜像签名或依赖更新策略。

## 设计方案

- 保留 `pyproject.toml` 作为声明源，生成带 hash 的生产/开发锁文件；Docker 只从生产锁安装。
- 基础镜像固定到 digest，并通过自动更新 PR 定期升级。
- CI 生成 CycloneDX 或 SPDX SBOM，使用 Trivy/Grype 扫描 OS 与 Python 依赖。
- 生产镜像使用 Cosign keyless 或云 KMS 签名，并记录源码提交、构建时间和版本 OCI labels。
- 高危/严重漏洞默认阻断；例外必须包含 CVE、影响分析、截止时间和责任人。

## 实施步骤

### 1. 分析问题

- 导出当前镜像软件清单并建立漏洞基线。
- 比较两次无缓存构建的依赖和镜像元数据，定位不可重复来源。

### 2. 设计确认

- 选择锁文件工具、SBOM 格式、扫描器和签名信任根。
- 定义漏洞严重度、可利用性和例外 SLA。

### 3. 修改代码

- 提交锁文件、基础镜像 digest、OCI labels 和多阶段 Docker 构建。
- CI 使用锁文件安装，生成并上传 SBOM、扫描报告和签名证明。
- 新增依赖更新机器人配置和升级回归说明。

### 4. 质量检测

- 两次干净构建比较安装依赖版本与 SBOM；允许构建时间等已声明差异。
- 注入过期依赖验证高危漏洞能阻止发布。
- 在隔离环境验证签名和镜像摘要后再允许运行。

## 完成标准

- 生产依赖和基础镜像均固定。
- 每个生产摘要都有 SBOM、扫描报告和可验证签名。
- 未批准的高危/严重漏洞无法进入生产。

## Comments

- 2026-08-17：用户明确要求在任务 01 远端平台配置尚未完成时直接进入任务 02；仅推进仓库侧供应链实现，不把任务 01 的外部门禁状态视为已完成。

### 2026-08-17 仓库侧实施结果

#### 分析问题

- 旧 Dockerfile 使用浮动 `python:3.12-slim` 并让 pip 根据宽版本范围在线解析；同一次基线无缓存构建已得到不同于开发虚拟环境的 `pwdlib`、`ruff`、`uvicorn` 等补丁版本。
- 旧镜像基线 digest 为 `sha256:ca4f44f77f2ed1c43456e146693f245381e334444737a4b33fbce2bce0e9e1b6`，Docker Scout 索引 165 个包，发现 Debian Perl 的 2 Critical + 2 High。
- `python:3.12-alpine@sha256:d09d...dc31` 基础镜像扫描为 0 Critical / 0 High；最终运行时不需要 Debian/Perl，因此选择 Alpine 而不是为不可利用性建立长期例外。
- Node 构建工具链不会进入最终运行镜像；其风险通过固定 digest、锁定 npm 依赖、每周更新和只扫描/发布最终 runtime target 控制。
- 首次双无缓存构建发现 pip 主动生成的 `.pyc` 内容带时间差异；加入 `--no-compile` 后消除了运行文件内容差异。BuildKit 的层归档时间元数据仍会改变本地 image ID，属于工单允许的已声明构建时间差异。

#### 设计方案

- `pyproject.toml` 保持直接依赖声明源；使用固定 Python Alpine digest 和 pip-tools 7.5.2 生成 Linux 目标的生产/开发 hash lock。
- Dockerfile 分为 `frontend-builder`、`python-dependencies`、`runtime` 三阶段；Node 根据 `package-lock.json` 重建 Vue 产物，Python 只安装 hash 匹配且存在 wheel 的生产依赖，最终运行时不包含 npm、编译器或测试工具。
- Pull Request/提交构建最终 runtime、生成 SPDX JSON、用 Trivy 对 Critical/High 执行硬门禁；版本标签只有在扫描成功后才推送 GHCR。
- 发布镜像附带 BuildKit SBOM 和 `mode=max` provenance，使用 GitHub OIDC + Cosign keyless 对摘要签名；生产仅使用验签后的 digest。
- 所有 GitHub Action 均固定到 40 位提交 SHA，Dependabot 每周更新 pip、npm、Docker 和 Actions。
- 漏洞例外默认为空；Critical 最长 7 天、High 最长 30 天，必须限定 CVE/包或路径并记录影响、补偿控制、责任人和到期日。

#### 修改代码

- 新增 `backend/requirements.lock`、`backend/requirements-dev.lock`，所有解析依赖固定版本并包含 SHA-256 hash。
- 重写根 `Dockerfile` 为固定 digest 的 Alpine 多阶段构建，加入 OCI source/revision/version/created labels、`--require-hashes`、`--only-binary=:all:`、`--no-compile` 和镜像内前端重建。
- 更新 `.dockerignore`，把前端构建输入纳入上下文并明确排除本地 `node_modules`/缓存；构建上下文由约 63.9 MB 降至约 200 kB。
- 新增 `.github/workflows/supply-chain.yml`、`.github/dependabot.yml`、`.trivyignore.yaml`。
- 质量工作流改为从 hash lock 安装，Node 固定为 24.19.0；所有 Actions 改为完整提交 SHA。
- 新增 `scripts/update-python-locks.ps1`、`scripts/compile-python-locks.py`、`scripts/compare-image-filesystems.py`。
- 新增 4 项 `test_supply_chain_contract.py` 契约测试并更新原生产容器契约。
- 新增 `docs/supply-chain-security.md`，部署文档改为只接受已验签的不可变摘要。

#### 质量检测

- 固定 Linux 容器两次生成 lockfile逐字一致；`-Check` 通过。
- 定向 CI/容器/供应链契约：15 passed；actionlint 1.7.7 对两个 workflow 检查通过。
- 完整 PostgreSQL 17 回归：`561 passed, 1 skipped`；唯一跳过仍为 Windows 无法表达 Linux mode bits 的既有权限契约。
- Ruff：通过；严格 MyPy：129 个源码文件通过；前端 `npm ci`、类型检查和构建通过。
- Production Compose 解析、Alembic 单 head、生产镜像构建及镜像内 `import app.runtime` 通过；最终以 UID/GID 10001 的 `app` 用户运行。
- 新 runtime 约 42 MB、92 个已索引包，Docker Scout 为 0 Critical / 0 High；生成的 SPDX 报告有效，包含 93 个 package 条目。
- 两次相同输入的无缓存构建：5,261 个文件系统条目的内容、模式、UID/GID、链接全部一致，SBOM package inventory 一致；只有 BuildKit 层归档/created 时间元数据导致本地 image ID 不同。
- 负向漏洞演练：旧基线中的 2 Critical + 2 High 使带 `--exit-code` 的门禁返回失败，新候选通过。
- 所有任务专用容器、SPDX 临时文件和 11 个临时镜像 tag 已清理；未触碰工作区原有业务容器和镜像。

#### 尚未完成的外部发布证明

- 当前快照没有 `.git` 和真实 GitHub 仓库，无法触发版本标签流水线、推送 GHCR 或取得 GitHub OIDC 身份。
- 因而尚未产生真实远端镜像 digest、registry SBOM/provenance attestation 和可在线验证的 Cosign 签名，也未能设置 `supply-chain-gate` 分支保护。
- 工单保持 `claimed`。获得仓库地址、默认分支及 GitHub/GHCR 权限后，应推送一次候选版本标签，保存 digest 和验签输出，再满足“每个生产摘要都有 SBOM、扫描报告和可验证签名”的完成标准。

### 2026-08-17 GitHub 接入进展

- 已确定远端为 `nannannan1111111/AI-platform`、默认分支为 `main`；本地供应链工作流已包含普通提交的构建/扫描门禁，以及 `v*` 标签触发的 GHCR 推送、provenance、SBOM 和 Cosign keyless 签名。
- 首次提交已创建为 `b11ad41`，但当前环境无法连接 `github.com:443`，且当前 Codex 进程没有 GitHub CLI 登录态，因此尚未获得真实 Actions、GHCR 和 Cosign 证明。
- 用户提供的测试发布选项仍是“允许/暂不允许”二选一原文，未形成单一授权结论。在用户明确选择“允许”前，不创建 `v0.1.0-rc.1`，也不触发 GHCR 发布。

### 2026-08-17 GitHub 远端验证结果

- 供应链工作流已在真实 GitHub Runner 上完成锁文件复现、生产镜像构建、SPDX SBOM、Trivy Critical/High 硬门禁和证据上传。
- 前两次失败分别发现 SBOM 输出目录缺失，以及 Trivy Action 的外部二进制安装脚本连续下载失败；均未被误判为漏洞命中或通过。最终方案预创建证据目录，并使用固定多架构摘要 `sha256:a22415a38938a56c379387a8163fcb0ce38b10ace73e593475d3658d578b2436` 的官方 Trivy 0.65.0 容器。
- 远端供应链运行 [31991272283](https://github.com/nannannan1111111/AI-platform/actions/runs/31991272283) 全绿，最终 `supply-chain-gate` 成功；证据制品 `supply-chain-evidence-8ff5af30823d68425979e326dbc9ca8d9c6a6192` 包含 SBOM 和 SARIF 漏洞报告。
- 普通 `main` 提交按设计跳过 `publish-signed-image`，没有创建版本标签或向 GHCR 发布镜像。
- 由于测试发布授权仍未明确，尚未验证真实 GHCR digest、BuildKit provenance 与 Cosign OIDC 签名；加上私有仓库套餐无法强制 `supply-chain-gate`，工单继续保持 `claimed`。

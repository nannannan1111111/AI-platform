# 01 自动化质量与发布门禁

Type: task
Status: claimed
Stage: 发布可信

## 目标

把当前手工通过的质量检查变成每次提交和镜像发布都必须通过的自动门禁。

## 问题分析

- 仓库没有 CI 工作流；“本机通过”不能阻止后续回归进入生产。
- PostgreSQL 测试在缺少 `POSTGRES_TEST_DATABASE_URL` 时会跳过，普通 SQLite 回归不能替代它。
- 前端构建产物由 Docker 直接打包；若只修改 Vue 源码但忘记构建，镜像可能包含旧 `admin.js`。
- 当前目录没有 Git 元数据，实施前必须确认代码实际托管平台和受保护分支规则。

## 设计方案

- 默认设计为 GitHub Actions；若实际使用 GitLab，则保持相同步骤和门禁名称迁移到 GitLab CI。
- 使用 PostgreSQL 17 CI service，完整运行迁移与数据库专属测试。
- 后端质量、前端质量、生产配置和镜像构建拆成并行 Job；最终 `release-gate` 依赖所有 Job。
- 前端 Job 构建后检查工作区是否出现未提交的生产产物差异，防止源码/产物漂移。
- 只有受保护分支、版本标签和人工批准环境可以推送生产镜像。

## 实施步骤

### 1. 分析问题

- 确认 Git 托管平台、默认分支、镜像仓库和 Secret 命名。
- 在干净检出中记录全部门禁耗时、跳过项和所需系统依赖。
- 确认 Linux 权限测试在 CI 上执行而不是跳过。

### 2. 设计确认

- 评审 Job 拆分、缓存键、并发取消、制品保留期和分支保护规则。
- 明确 PR 只构建不推送，标签构建才签名并推送。

### 3. 修改代码

- 新增 CI 工作流和可在本地复用的质量脚本。
- PostgreSQL Job 设置 `POSTGRES_TEST_DATABASE_URL`。
- 使用 `npm ci`，构建后校验 `backend/app/webui/static/admin-vue/admin.js` 一致性。
- 增加 Alembic 单 head、Compose 解析和 Docker build 检查。
- 在部署文档写明门禁名称和紧急绕过审批流程。

### 4. 质量检测

- 人为制造一次 Ruff、mypy、pytest、前端产物和迁移双 head 失败，确认各自能阻止 `release-gate`。
- 在干净分支运行一次全绿流水线并保存日志。

## 完成标准

- 所有 PR 必须通过固定名称的 `release-gate`。
- PostgreSQL 专属测试没有跳过。
- 失败构建不会产生可部署生产标签。
- 分支保护禁止直接绕过；紧急绕过有审批和审计记录。

## Comments

### 2026-08-17 仓库侧实施结果

#### 分析问题

- 确认仓库快照没有 `.github/`、根 `scripts/` 和 `.git`；无法从本地确认真实托管平台、默认分支或远端保护规则。
- 确认 `test_postgresql_migrations.py` 的 3 项 PostgreSQL 专属测试会在缺少 `POSTGRES_TEST_DATABASE_URL` 时跳过。
- 确认 Vite 直接把产物写入 `backend/app/webui/static/admin-vue/admin.js`，Dockerfile 再复制整个 `backend/app`；源码与已提交产物漂移会进入镜像。
- 确认现有 Alembic 为单 head：`0059_account_storage_allowances`。
- ADR 0001 要求 PostgreSQL 保持生成队列事实来源；本方案只把 PostgreSQL 17 纳入 CI，没有引入新的队列依赖或改变运行时架构。

#### 设计方案

- 新增 GitHub Actions 并行检查 `backend-quality`、`backend-tests`、`frontend-quality`、`production-contract`，最终由固定名称 `release-gate` 汇总。
- PostgreSQL 测试 Job 使用 `postgres:17` service；本地 `backend-tests`/`all` scope 缺少测试数据库连接串时硬失败。
- 前端统一执行 `npm ci`、类型检查和构建；Git 检出中对 `backend/app/webui/static/admin-vue` 执行 `git diff --exit-code`。
- Production scope 强制 Alembic 只有一个 head、Compose 能解析且生产 Dockerfile 能构建。
- 工作流权限为只读，PR/push 阶段不登录镜像仓库、不推送可部署标签；签名发布留给任务 02。

#### 修改代码

- 新增 `.github/workflows/quality-gate.yml`。
- 新增 `scripts/quality-gate.ps1`，提供 `backend-quality`、`backend-tests`、`frontend`、`production`、`all` 五个 scope。
- 新增 `backend/tests/test_ci_quality_gate.py`，以 5 项契约测试固定关键门禁语义。
- 更新 `docs/deployment-and-operations.md`，记录门禁名、本地执行方式、分支保护要求和紧急绕过审计流程。

#### 质量检测

- 定向契约测试：`11 passed`（新增 CI 契约 + 既有容器契约）。
- Ruff：通过；严格 MyPy：`129 source files` 无问题。
- 前端：`npm ci`、`npm run check`、`npm run build` 通过；构建产物 `admin.js` 约 182.99 kB。当前快照无 `.git`，本机只提示警告；GitHub Actions 中会硬失败。
- PostgreSQL 17 完整回归：`557 passed, 1 skipped`；3 项 PostgreSQL 专属测试全部执行。唯一跳过为 Windows 不支持 Linux mode bits 的既有权限契约。
- Alembic：单 head；Production Compose：解析通过；生产镜像：构建通过，临时本地镜像 ID `sha256:39bda9fe360cfb969668051305e7557e66024469c16cbea680e5b8e22470e2c4`。
- 负向演练：故意制造 Ruff、MyPy、Pytest 和 Alembic 双 head 错误，均得到非零阻断结果；缺少 `POSTGRES_TEST_DATABASE_URL` 也会在测试开始前失败。临时探针、PostgreSQL 容器和本地镜像已清理。
- 尝试用 `rhysd/actionlint:1.7.7` 额外校验 workflow，但 Docker Hub 网络连接失败，未获得 actionlint 结果；仓库契约测试与本地脚本验证均已通过。

#### 尚未完成的外部平台动作

- 需要真实 GitHub/GitLab 仓库地址、默认分支和管理员权限，才能运行一次干净远端流水线并完成前端产物漂移故障演练。
- 需要在默认分支启用必需检查 `release-gate`、禁止直接 push/管理员绕过并启用 Pull Request 审批。
- 上述两项属于仓库平台状态，当前源码快照无法代替配置和取证。因此工单保持 `claimed`，不得宣称已满足“所有 PR 强制门禁”的完成标准。

# 05 单机云基础设施与 HTTPS 入口

Type: task
Status: claimed
Stage: 云端落地
Blocked by: 02, 03, 04

## 目标

用可复现配置建立首发单机云环境、托管 PostgreSQL、私有镜像仓库、DNS 和 HTTPS 入口。

## 问题分析

- 当前 Compose 假设服务器、数据库、目录、域名和反向代理已由人工准备，没有云资源定义。
- 云厂商已确定为腾讯云；区域、可用区、域名/备案、预算、RTO/RPO 和账号最小权限仍未确认，不能安全地直接创建真实资源。
- SSE 需要关闭代理缓冲并延长读取超时；普通默认代理配置可能造成假超时。
- 数据库当前应用连接预算为 60，实例必须预留迁移、备份和人工排障连接。

## 设计方案

- 首发保持单主机 Compose；Web 与 Worker 同机，共享受控媒体路径。
- 数据库优先使用同区域托管 PostgreSQL 17，私网访问、PITR、TLS 和自动维护。
- 使用 Terraform/OpenTofu 管理网络、主机、数据库、磁盘、DNS、证书、镜像仓库和安全组；敏感值不进入 state 明文输出。
- Caddy 或 Nginx 只向回环地址转发；SSE 路径关闭缓冲，读取超时大于任务最长时限。
- 安全组只开放 80/443 和受控运维入口，数据库与应用端口不暴露公网。

## 实施步骤

### 1. 分析问题

- 确认云厂商、区域、域名、预计用户、预算、RTO/RPO 和数据合规地域。
- 根据 60 条应用连接和容量基线选择数据库与主机规格。

### 2. 设计确认

- 输出网络图、端口矩阵、IAM、磁盘布局、DNS/TLS、数据库参数和成本估算。
- 评审 Terraform state、Secret 注入和灾难恢复边界。

### 3. 修改代码

- 新增 IaC、Caddy/Nginx 配置、部署参数模板和初始化脚本。
- 配置私有镜像摘要部署、迁移先行、健康检查和失败自动停止开放流量。
- 修正部署文档中与当前画布行为不一致的内容。

### 4. 质量检测

- 在预发布账号从空环境执行一次完整创建、部署、销毁演练。
- 验证公网只暴露 80/443，数据库只走私网，TLS 自动续期。
- 测试 SSE、上传、下载、支付通知和 413/超时边界。

## 完成标准

- 新环境可由受控 IaC 和部署流程复现。
- `/readyz` 只有数据库可用时成功，迁移失败不会开放流量。
- TLS、DNS、安全组、数据库连接预算和成本均有验收记录。

## Comments

- 2026-08-17：用户明确要求在暂不创建测试标签、Release 或 GHCR 测试镜像的前提下继续部署开发。任务 02 的锁文件、固定基础镜像、SBOM、漏洞门禁和签名发布工作流已完成，真实生产摘要与 Cosign 证明仍须在首次部署发布时取得；本任务先推进腾讯云仓库侧 IaC、HTTPS 入口和部署流程，并把“仅部署已扫描、已签名的不可变摘要”保留为开放流量前的硬门禁。
- 2026-08-17：用户确认生产地域为腾讯云香港（`ap-hongkong`），采用主备跨可用区部署。主区和备区的具体编号不预设，须在真实 Plan 前通过腾讯云 API 同时核验 CVM 库存与 TencentDB PostgreSQL 17 支持情况；数据库容量目标按此前讨论暂定 1 TB，Terraform 中使用符合 10 GiB 步进的 `1000` GiB，最终规格与预算在 Plan 评审时确认。

### 2026-08-17 腾讯云仓库侧实施结果

#### 分析问题

- 确认现有 Production Compose 已具备迁移先行、应用端口只绑定 `127.0.0.1`、数据库驱动就绪检查、Web/Worker 独立扩缩和 60 条最坏应用连接预算，但云网络、主机、磁盘、托管数据库、DNS、证书和镜像仓库均依赖人工准备。
- 确认腾讯云 Provider 1.83.23 能管理 VPC/CVM/CBS/TencentDB PostgreSQL/TCR/DNSPod；其 PostgreSQL 字段说明仍只列到 16，因此不能靠静态文档假定 17 可用，必须在目标地域查询真实版本清单并禁止静默降级。
- 确认创建 TencentDB 的 `root_password` 是 Provider 必填字段，会存在于 Terraform state；方案把加密、版本化、最小权限 COS state 作为生产 Secret 边界，不宣称敏感值可从 state 中消失。应用连接串、认证 HMAC、Provider/SMTP/支付密钥不进入 IaC、cloud-init、输出或 Git。
- 腾讯云已选定，但真实地域/可用区、域名及备案、固定运维 CIDR、SSH Key、预算、RTO/RPO、账号权限和生产发布授权尚未提供，因此本轮不调用云 API 创建收费资源。

#### 设计方案

- 单 VPC/单 CVM 首发，公网安全组仅开放 80/443 和受限运维 `/32` 的 22；数据库安全组只接受应用安全组的 5432，应用 8000 不进入安全组。
- 同地域 TencentDB PostgreSQL 固定要求主版本 17、私网、SSL、删除保护和可选跨可用区节点；OpenTofu `plan` 查询地域版本清单，没有 17 时硬失败。
- 媒体和 Provider 密钥使用独立加密 CBS，cloud-init 以幂等 systemd 服务格式化/挂载并固定 `0750`、`0700` 边界；CBS 未挂载时发布失败闭合。
- 私有 TCR 关闭公网访问，通过 VPC Attachment 拉取；Namespace 自动扫描并阻断 High 漏洞。正式镜像先由 GitHub OIDC 签名，再用 `cosign copy` 把镜像、签名与证明晋级到 TCR，并按目标摘要再次验签。
- Caddy 2.11.4 由固定下载地址和 SHA-512 安装，自动 HTTPS/续期；SSE 禁用代理缓冲，响应头超时为 11 分钟，请求体默认 32 MB。Caddy 只连接回环应用，生产可信代理严格等于 `127.0.0.1/32`。
- 发布顺序固定为验签、Compose 解析/拉取、迁移、Web/Worker、回环 `/readyz`、Caddy 配置校验/启用、公网 HTTPS `/readyz`；首次部署任一前置失败均不开放流量。

#### 修改代码

- 新增 `deploy/tencent-cloud/infra/`：固定 OpenTofu/Provider、远端 COS S3-compatible state 模板、VPC/子网、安全组、CVM/EIP、加密 CBS、PostgreSQL 17、SSL、私有 TCR/VPC Attachment、DNSPod 和非敏感输出。
- 新增固定版本 cloud-init、Caddyfile、腾讯云生产环境模板、Caddy 环境模板，以及签名镜像晋级、失败闭合部署、公网边界验证和 IaC 校验脚本。
- 新增 `docs/tencent-cloud-production.md`，记录网络图、端口矩阵、IAM/Secret/state 边界、连接预算、成本清单、Plan/Apply、首次发布、验收、回滚以及部署后恢复 GitHub Private 的复核要求。
- CI `production-contract` 新增固定提交的 OpenTofu 1.12.5 安装和 `fmt/init/validate`；新增 7 项腾讯云部署契约并扩展质量门禁契约。

#### 本地质量检测

- OpenTofu 1.12.5 使用腾讯云 Provider 1.83.23 的真实 Schema 完成 `fmt -check`、`init -backend=false` 和 `validate`；Provider lock 已提交。未使用云账号执行 `plan/apply`。
- Caddy 2.11.4 对生产 Caddyfile 校验通过；cloud-init 模板通过 YAML 解析；腾讯云生产环境模板可解析现有 Production Compose；ShellCheck 0.11.0 和 Actionlint 1.7.7 通过。
- Ruff 通过；严格 MyPy 对 136 个源文件通过；PostgreSQL 17 完整后端回归为 `589 passed, 1 skipped`，唯一跳过仍是 Windows 无法表达 Linux mode bits 的权限契约。
- 前端 `npm ci`、类型检查和构建通过，提交产物无漂移；Alembic 单 head、Production Compose 和生产镜像构建通过。本地候选 manifest list 为 `sha256:773021d033780ce99e3796100b0a50ad3f437e0952b274a68211c263f900a477`，仅作本地门禁证据，未发布。
- 定向腾讯云/CI/Production 契约为 `19 passed`；临时 PostgreSQL 17 容器已清理，没有创建标签、Release、GHCR/TCR 镜像或腾讯云收费资源。
- PR #6 首次远端供应链运行正确阻断了不可重现的开发锁：更新命令写既有文件时保留 `pygments 2.20.0`，检查命令写临时新文件时解析到 `2.21.0`。锁编译现统一显式使用 `--upgrade`，陈旧检查同时输出两侧 SHA-256 与截断 diff；重新生成后固定解析检查通过。该修复只更新生产锁的可复现命令头和开发锁中的 Pygments，不改变应用生产依赖。
- 修复提交 `1c4e3cab8af8fc419ec7a991ab4f1eb4d122a502` 的 PR 流水线已全绿：[`quality-gate` 32013790047](https://github.com/nannannan1111111/AI-platform/actions/runs/32013790047) 的 PostgreSQL 17 完整测试、Ruff、严格 MyPy、前端、生产镜像、Compose、迁移和 OpenTofu 最终 `release-gate` 通过；[`supply-chain` 32013789946](https://github.com/nannannan1111111/AI-platform/actions/runs/32013789946) 的锁重现、SBOM、Trivy Critical/High 阻断和最终 `supply-chain-gate` 通过。发布 Job 正确跳过，未发布镜像。

#### 尚未完成的真实云验收

- 仍需用户提供并批准地域/双可用区、域名与备案状态、固定运维公网 CIDR、腾讯云 SSH Key 名、月预算、RTO/RPO 和 Terraform Runner 凭据，才能生成真实 Plan。
- 仍需在隔离预发布账号完成创建/部署/销毁演练，并验证 PostgreSQL 17 地域库存、连接上限、私网/TLS、TCR VPC 拉取、证书续期、端口扫描、SSE、上传/下载、413/超时和迁移失败不开放流量。
- 仍需首次正式发布授权，取得已扫描、带 SBOM/Provenance、可验证 Cosign 签名的真实摘要并晋级私有 TCR。完成这些外部验收前，工单保持 `claimed`，不得标记 `resolved`。

# 04 HTTP 安全边界与画布 XSS 加固

Type: task
Status: claimed
Stage: 公网安全
Blocked by: 01

## 目标

建立明确的 Host、反向代理、HTTPS、安全响应头和浏览器脚本执行边界，防止伪造转发信息与画布内容触发 XSS。

## 问题分析

- Uvicorn 当前信任任意 `X-Forwarded-*` 来源；只有端口绑定回环地址在单机上提供间接保护。
- FastAPI 没有统一 HSTS、CSP、frame、referrer、permissions 等响应头。
- 会话令牌保存在 `sessionStorage`；一旦 XSS 成立，影响可直接扩大为会话窃取。
- 经典/智能画布大量使用动态 HTML 和历史内联事件，不能未经验证直接启用严格 CSP，否则会破坏功能。

## 设计方案

- 增加允许 Host 和可信代理 CIDR 配置；生产拒绝通配代理信任。
- 应用层统一设置 `X-Content-Type-Options`、`Referrer-Policy`、`Permissions-Policy`、frame 限制和缓存策略；HSTS 只在确认 HTTPS 入口后启用。
- CSP 分两步：先 Report-Only 收集违规，再迁移内联脚本/事件和危险 DOM sink，最终执行 `script-src 'self'`。
- 画布导入、标题、提示词、Provider 显示名等不可信数据进入 HTML 前统一编码；需要富文本时使用明确 allowlist sanitizer。
- 不把 CSP 的 `'unsafe-inline'` 当作最终完成状态。

## 实施步骤

### 1. 分析问题

- 枚举全部 `innerHTML`、`insertAdjacentHTML`、内联事件和 URL sink，按是否接触用户输入分级。
- 用恶意画布/工作流样本验证可疑路径，形成可重复安全测试，不在文档中保存真实 Token。

### 2. 设计确认

- 确定安全头策略、CSP 迁移顺序、兼容浏览器和报告收集端点。
- 明确代理终止 TLS 时应用如何判定安全请求。

### 3. 修改代码

- 增加 TrustedHost、安全头和可信代理配置；收紧 Docker 启动参数。
- 将高风险 DOM sink 改为 `textContent`/DOM API 或经过审计的编码函数。
- 移除内联脚本和事件依赖，先发布 CSP Report-Only，再切换 enforce。
- 增加安全配置文档和反向代理契约测试。

### 4. 质量检测

- 自动测试恶意标题、提示词、工作流、文件名和 Provider 名称不能执行脚本。
- 使用 OWASP ZAP 或同类工具做认证前后扫描。
- 验证 HTTPS、SSE、媒体下载、支付跳转和画布编辑在 CSP 下仍正常。

## 完成标准

- 生产不再信任任意代理或任意 Host。
- 安全头扫描达到约定基线。
- CSP enforce 不依赖 `unsafe-eval` 或 `unsafe-inline`。
- 已识别的高风险 DOM sink 有测试覆盖且无法窃取会话。

## Comments

- 2026-08-17：当前稳定版本点为私有仓库 `main` 的 `c719bc3243e9270e15a64c9fcc2142c88c99c2c6`，对应 quality-gate 与 supply-chain 全绿且未发布镜像。按用户要求从该版本继续任务 04。
- 2026-08-17（问题分析）：确认生产容器使用 `--forwarded-allow-ips='*'`，应用没有 Host 白名单和统一 CSP/安全头；经典与智能画布共有 121 个内联事件属性和 2 段内联主题脚本。经典画布节点头还把工作流节点标题与节点 ID 拼入 `innerHTML`/`onclick`，恶意导入字段可进入 HTML 与脚本字符串上下文。
- 2026-08-17（设计确认）：生产强制精确 `ALLOWED_HOSTS`，拒绝通配 Host、`0.0.0.0/0`/`::/0` 等全网代理信任；Uvicorn 与认证客户端 IP 解析共享 `TRUSTED_PROXY_CIDRS` 契约。安全头使用全局 ASGI 中间件覆盖路由和静态挂载，HSTS 仅在 `ENABLE_HSTS=true` 且请求已由可信代理判定为 HTTPS 时发送。CSP 执行态锁定同源脚本并设置 `script-src-attr 'none'`；画布动态布局仍需要 `style-src-attr` 兼容，但该例外不进入脚本策略。
- 2026-08-17（代码修改）：新增集中式 HTTP 安全设置、中间件、TrustedHost 装配、缓存分层及生产配置校验；Docker 不再信任任意代理。121 个静态内联事件迁移为初始 DOM 白名单事件桥，内联主题脚本和 `document.write` 已移除；节点标题使用 `textContent`，删除按钮使用 `addEventListener`，媒体显示 URL 限制为受支持协议。Compose、环境模板、质量脚本和部署文档同步了 Host、代理和 HSTS 契约。
- 2026-08-17（本地质量检测）：Ruff、严格 MyPy、前端 `npm ci`/类型检查/构建和 Compose 解析通过；全量 Python 为 `578 passed, 5 skipped`，跳过项是 Windows 文件权限与未配置 PostgreSQL 的 4 个迁移/并发用例。真实浏览器中经典画布“展开→上传”使节点数从 0 变为 1，智能画布返回列表正常，控制台无 CSP 违规；未知 Host、HSTS 条件、安全头、缓存、无内联事件/脚本及恶意节点字段均有自动测试。当前权限不能访问 Docker Engine，生产镜像、PostgreSQL 专属测试、SBOM 和漏洞门禁交由 GitHub Actions 最终验证。

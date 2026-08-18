# SaaS 单服务器 Docker 部署与运维

腾讯云首发环境的网络、OpenTofu、私有 TCR、Caddy HTTPS、成本与验收步骤见 `docs/tencent-cloud-production.md`。通用 Compose 契约仍以本文为准；腾讯云部署必须额外通过该文档中的签名摘要和云边界检查。

仓库根目录的 `Dockerfile` 和 `deploy/compose.production.yml` 启动 Python/FastAPI SaaS 后端，不再启动旧的 `creative_studio.bootstrap.runtime:app`。镜像包含 `backend/app`、Alembic 迁移和 SaaS Web UI。第一版本不发布画布导航、画布工作区、经典/智能编辑器入口或画布 HTTP API；根目录旧静态资源仅作后续迁移与历史兼容保留。

当前单机部署形态是一个运行多个 Uvicorn 进程的 Web 容器、可独立扩缩的生成 Worker、一次性迁移容器、PostgreSQL、服务器本地媒体目录和受控 Provider 密钥目录。Web 完成鉴权、校验、持久化排队、SSE 状态推送以及易支付兼容网关的下单/通知验签；耗时的 Provider 图片请求由生成 Worker 执行。周期任务和任务领取使用 PostgreSQL advisory lock 做跨进程互斥。当前仍不包含支付退款/拒付自动化、跨主机对象存储或 KMS Adapter；账户验证邮件通过部署方提供的 SMTP 服务真实投递。

## 发布质量门禁

GitHub Actions 工作流 `.github/workflows/quality-gate.yml` 在 push、Pull Request 和手工触发时运行四组并行检查，并由固定名称 `release-gate` 汇总：

| 门禁 | 检查内容 |
| --- | --- |
| `backend-quality` | Ruff 与严格 MyPy |
| `backend-tests` | 在 PostgreSQL 17 上运行完整 Pytest；未提供测试数据库连接串时直接失败 |
| `frontend-quality` | `npm ci`、Vue 类型检查、生产构建以及已提交 `admin-vue` 产物漂移检查 |
| `production-contract` | Alembic 单 head、Production Compose 解析、Prometheus 告警规则语法和生产 Docker 镜像构建 |
| `release-gate` | 仅当以上四项全部成功时通过；它是分支保护唯一稳定的必需检查名 |

本地可从仓库根目录复用同一个脚本。完整校验必须显式提供隔离的 PostgreSQL 测试库，脚本不会静默跳过数据库测试：

```powershell
$env:POSTGRES_TEST_DATABASE_URL = "postgresql+psycopg://user:password@127.0.0.1:5432/quality_gate"
./scripts/quality-gate.ps1 -Scope all
```

也可以把 `Scope` 设为 `backend-quality`、`backend-tests`、`frontend` 或 `production` 单独排查。前端产物漂移依赖 Git 元数据；不含 `.git` 的源码快照会给出警告，而 CI 中缺少 Git 元数据会失败。

仓库管理员必须把 `release-gate` 设置为默认分支的必需状态检查，禁止直接 push 和管理员绕过，并要求 Pull Request 审批。当前工作流只验证提交且不登录镜像仓库、不推送镜像；带版本标签的签名发布流程在供应链任务完成后单独启用。

紧急绕过只能由生产负责人和另一名审批人共同批准，并在变更单记录原因、风险、提交 SHA、执行人、时间和回滚点。绕过后必须在 24 小时内补跑全量 `release-gate` 并关闭审计记录；不得通过修改或删除工作流规避失败检查。

## 必填配置

复制 `deploy/.env.example` 为不纳入版本控制的 `deploy/.env`，权限设置为仅部署账号可读：

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

填写以下值：

| 变量 | 用途 |
| --- | --- |
| `CREATIVE_STUDIO_IMAGE` | 已构建并推送的不可变镜像 tag 或 digest，生产不得使用 `latest` |
| `DATABASE_URL` | `postgresql+psycopg://...` PostgreSQL 连接串；密码只放未提交的部署密钥文件/环境，不进入管理员页面或仓库 |
| `GENERATED_MEDIA_HOST_PATH` | 宿主机上预先创建的图片持久目录，例如 `/srv/infinite-canvas/data/generated-media` |
| `PROVIDER_SECRETS_HOST_PATH` | 宿主机上预先创建的 Provider Key 目录，例如 `/srv/infinite-canvas/secrets/providers`；依赖 `0700/0600` 权限保护静态明文 |
| `PLATFORM_ADMIN_EMAILS` | 逗号分隔的平台管理员登录邮箱白名单 |
| `MAX_ACTIVE_GENERATION_TASKS` | 单账户空间最大排队及生成中图片名额，按请求图片数量计算，默认 `20` |
| `WEB_CONCURRENCY` | Web 进程数，百人规模起步值为 `4` |
| `WEB_MAX_CONNECTIONS` | 每个 Web 进程接受的并发连接上限，默认 `400` |
| `DATABASE_POOL_SIZE` | 每个 Web 进程常驻数据库连接数，默认 `8` |
| `DATABASE_MAX_OVERFLOW` | 每个 Web 进程临时溢出连接数，默认 `4` |
| `DATABASE_POOL_TIMEOUT_SECONDS` | 连接池耗尽时的最长等待时间，默认 `10` 秒 |
| `AUTH_RATE_LIMIT_HASH_KEY` | 至少 32 字节的随机 HMAC 密钥；所有 Web Worker 必须相同，只放未提交的部署密钥文件/Secret Manager |
| `AUTH_LOGIN_IP_LIMIT` / `AUTH_LOGIN_EMAIL_LIMIT` | 每个登录窗口的 IP/邮箱尝试上限，默认 `10` / `5` |
| `AUTH_LOGIN_WINDOW_SECONDS` | 登录固定窗口，默认 `600` 秒 |
| `AUTH_REGISTER_IP_LIMIT` / `AUTH_REGISTER_WINDOW_SECONDS` | 注册 IP 上限与窗口，默认 `5` 次/`3600` 秒 |
| `AUTH_EMAIL_VERIFICATION_ACCOUNT_LIMIT` / `AUTH_EMAIL_VERIFICATION_WINDOW_SECONDS` | 每账户重发验证邮件上限与窗口，默认 `3` 次/`3600` 秒 |
| `AUTH_PASSWORD_RESET_IP_LIMIT` / `AUTH_PASSWORD_RESET_EMAIL_LIMIT` | 每个密码重置窗口的 IP/邮箱请求上限，默认 `5` / `3` |
| `AUTH_PASSWORD_RESET_WINDOW_SECONDS` | 密码重置固定窗口，默认 `3600` 秒 |
| `TRUSTED_PROXY_CIDRS` | 直接连接应用的可信反向代理网段，逗号分隔；留空时完全忽略 `X-Forwarded-For` |
| `ALLOWED_HOSTS` | 逗号分隔的精确公网域名，不含协议、路径或通配符；应用自动补充 `127.0.0.1` 和 `localhost` 供本机健康检查 |
| `ENABLE_HSTS` | 仅在 HTTPS 入口、HTTP 到 HTTPS 跳转和可信代理均验收后设为 `true`；此前保持默认 `false` |
| `GENERATION_WORKER_REPLICAS` | 图片生成 Worker 数，默认 `4`；不得超过 Provider 并发配额 |

平台管理员可以在“用户管理”中为单个用户设置图片生成执行并发，范围为 `1–20`，默认 `2`。该值持久化在数据库中并由 Worker 动态读取，保存后无需重新部署；超过执行并发的任务保持排队状态。此设置不改变 `MAX_ACTIVE_GENERATION_TASKS` 所控制的单账户排队中加生成中图片总名额。
| `WORKER_DATABASE_POOL_SIZE` | 每个生成 Worker 的常驻数据库连接数，默认 `2` |
| `WORKER_DATABASE_MAX_OVERFLOW` | 每个生成 Worker 的临时连接数，默认 `1` |
| `CREATIVE_STUDIO_PORT` | 绑定到宿主机回环地址的端口，默认 `8000` |

缺少数据库、媒体目录、Provider 密钥目录、管理员白名单、认证限流 HMAC 密钥或 `ALLOWED_HOSTS` 时，SaaS 进程会拒绝启动。`ALLOWED_HOSTS` 和 `TRUSTED_PROXY_CIDRS` 禁止 `*`；代理配置还禁止 `0.0.0.0/0`、`::/0` 这类等价全网信任。媒体与密钥必须是两个不同目录；它们都只由部署配置决定。Provider Key、SMTP 密码和支付商户密钥由管理员页面写入同一个受控密钥目录，数据库只保存不透明引用。

## 准备媒体目录

镜像内应用用户固定为 UID/GID `10001`。首次部署时在服务器执行：

```bash
sudo install -d -o 10001 -g 10001 -m 0750 \
  /srv/infinite-canvas/data/generated-media
```

将同一路径写入 `GENERATED_MEDIA_HOST_PATH`。Compose 把它挂载到容器内 `/var/lib/infinite-canvas/generated-media`，并把该容器路径作为 `GENERATED_MEDIA_ROOT` 传给应用。应用启动时会执行一次创建、读取和删除临时文件的权限探测；目录缺失或不可写时拒绝启动。

数据库只保存图片归属、媒体 ID、对象键、MIME、大小、生命周期和引用关系；生成结果与用户上传的临时参考图片字节保存在这个服务器目录，并按账户空间和任务使用不透明对象键隔离。HTTP 内容读取仍需 Bearer 登录并按账户空间校验，不会向浏览器暴露宿主机路径。临时参考图片和未保留生成结果默认 24 小时失效；保存到个人资产库的结果升级为持久媒体并计入存储额度。

## 准备 Provider 密钥目录

同一个 UID/GID `10001` 还需要独占的 Provider 密钥目录：

```bash
sudo install -d -o 10001 -g 10001 -m 0700 \
  /srv/infinite-canvas/secrets/providers
```

将该路径写入 `PROVIDER_SECRETS_HOST_PATH`。Compose 只把它挂载给 Web 容器的 `/var/lib/infinite-canvas/provider-secrets`，并设置 `PROVIDER_SECRETS_ROOT`；migrate 服务不挂载、不读取这个目录。应用启动时会强制目录 `0700` 并执行安全读写删除探测，管理员保存的 Provider Key、SMTP 密码与支付商户密钥以不透明文件名原子写入并设置 `0600`；数据库只保存引用及非敏感设置。

该首版 Adapter 不提供静态加密，安全性依赖宿主机权限、磁盘/备份加密和部署账号隔离。不要把目录同步到普通文件共享、源码目录或未加密备份；未来接入 KMS/Secret Manager 时替换 `ProviderSecrets` Adapter，不改变管理员或用户 HTTP。

## 构建与启动

正式镜像由版本标签触发的供应链工作流构建、扫描、生成 SBOM/证明并签名；完整规则和验签命令见 `docs/supply-chain-security.md`。手工构建只用于本地验证，不得直接作为生产发布物：

```bash
docker build -t registry.example.com/infinite-canvas:<release> .
```

生产只把经过 Cosign 验签的不可变 digest 写入 `CREATIVE_STUDIO_IMAGE`，不得使用 `latest` 或只写可移动版本 tag。

启动时，Compose 先运行 `alembic upgrade head`。只有迁移成功，Web 服务才会启动：

```bash
docker compose --env-file deploy/.env -f deploy/compose.production.yml pull
docker compose --env-file deploy/.env -f deploy/compose.production.yml up -d
```

端口仅绑定 `127.0.0.1`，应由服务器上的 Caddy、Nginx 或同类反向代理终止 HTTPS；不要把容器 `8000` 端口直接暴露到公网。

认证限流默认只认 TCP 直连来源。反向代理必须覆盖客户端传入的 `X-Forwarded-Proto`，并追加而不是透传客户端自带的 `X-Forwarded-For`；把代理实际使用的容器网段或回环地址写入 `TRUSTED_PROXY_CIDRS`，不要填写公网大网段或 `0.0.0.0/0`。Uvicorn 只接受这些来源的转发头；应用仅在直连来源可信时从右向左剥离可信 `X-Forwarded-For`，畸形转发链会整体忽略。登录按 IP 和邮箱 HMAC 摘要共同计数，注册按 IP 计数，验证邮件按账户计数；任何维度超限均返回 429 和 `Retry-After`。计数数据库不可用时这些入口返回 503，不会绕过保护继续认证。

每个响应统一包含 `nosniff`、frame deny、严格 referrer、Permissions Policy、COOP/CORP 与执行态 CSP。HTML 和 API 默认 `no-store`，带内容指纹的静态资源使用一年 immutable 缓存，其他静态资源必须重新验证；媒体下载或 SSE 路由的更具体缓存策略保持优先。CSP 的脚本边界为 `script-src 'self'` 和 `script-src-attr 'none'`，不允许内联脚本或 `unsafe-eval`；经典/智能画布的静态事件已经迁移到受白名单约束的同源脚本。画布仍通过 style 属性表达动态位置、尺寸和 CSS 变量，因此仅 `style-src-attr` 暂时保留内联样式兼容，不能把它扩大到 `script-src`。

只有可信代理把已验证的 TLS 状态传为 `X-Forwarded-Proto: https` 时，请求才会在应用中表现为 HTTPS。任务 05 完成证书、强制跳转和预发布检查后再设置 `ENABLE_HSTS=true`；HSTS 启用后只对 HTTPS 响应发送，伪造转发头或普通 HTTP 请求不会触发。上线检查至少包括：未知 Host 返回 `400`，允许域名正常；HTTP 响应不带 HSTS；HTTPS 响应在显式启用后包含一年 `max-age`；浏览器控制台没有 CSP 违规。

窗口结束即自动解锁，过期行会在后续认证请求中清理。成功登录会清除对应邮箱失败窗口。若错误配置造成大面积误限流，生产负责人可在记录变更和数据库备份后清除相应短时维度，例如 `DELETE FROM auth_rate_limit_windows WHERE action = 'login' AND subject_scope = 'email';`；操作不会修改账户、密码或会话。调整阈值后应先在预发布压测，避免直接以放宽限流处理攻击流量。

健康检查只表示 HTTP 进程已启动，不调用上游 Provider：

```bash
curl -fsS http://127.0.0.1:8000/healthz
# {"status":"ok"}，仅表示 HTTP 进程存活
curl -fsS http://127.0.0.1:8000/readyz
# {"status":"ready"}，同时验证数据库可用
```

首次部署应先保持反向代理不对公网开放，由部署人员注册 `PLATFORM_ADMIN_EMAILS` 中的账号并登录。在 `/admin/email-settings` 填写公开 HTTPS 站点地址、SMTP 主机、端口、发件地址、可选账号密码、安全模式与超时，再核验管理员 API 并开放公网入口。邮件尚未配置时允许该首次管理员注册，注册不会生成伪验证邮件；普通用户的“重新发送验证邮件”会明确提示服务尚未配置。

上线前应使用真实收件箱完成一次注册验收：确认邮件 From/SPF/DKIM/DMARC 配置正确、验证链接指向后台配置的公开站点地址、链接可使用一次且 24 小时后失效。用户可在个人账户页重新发送验证邮件；新邮件发出后旧验证链接立即失效。修改密码需要当前密码，成功后会撤销该账户在所有设备上的登录会话。

在 `/admin/payment-settings` 填写易支付网关基础地址、对外 HTTPS 站点 Origin、PID、商户密钥及支付方式，再启用在线支付。网关必须能访问 `https://<站点>/api/v1/payments/epay/notify`；该路由接受易支付标准 GET/POST 通知，验证 PID、MD5 签名、支付方式和订单金额后才会幂等入账。上线前必须使用网关沙箱或最小金额完成一次真实支付，并确认重复通知不会重复增加额度。轮换商户密钥会使用新密钥验证后续通知，因此应先处理完待支付订单或与网关协调切换窗口。

同一页面可通过独立的普通充值比例接口设置“每 1 元兑换多少额度”。用户钱包固定展示 1、2、5、10、100 元并支持自定义金额；创建订单时会固化当时的支付金额和到账额度，后续修改全局比例不会改写待支付或历史订单。充值包作为独立的特惠商品，继续使用各自版本中固化的金额和额度，不受普通充值比例影响。

## 当前未装配能力

生产装配会启用账户、额度、模型价格、充值包、充值订单记录、个人资产、生成任务、媒体、存储额度、Provider 成本、RunningHub 能力目录、模型路由、文件密钥和图片生成；画布产品面在第一版本中不启用。`/workspace/images` 提供独立图片生成，参考图会先保存到同一受控媒体根目录，再按账户读取并通过 multipart `/images/edits` 提交；无参考图继续使用 JSON `/images/generations`。Provider 同步返回的 Base64/公网 HTTPS 图片，或首次响应包含任务标识后在同一请求内轮询得到的图片，会在用户提交请求内落盘、登记和结算；异步轮询持续到 Provider 绝对时限或任务权威截止点，且不会重复 POST。OriginBoost `gpt-image-2` 使用同一 POST 的 `stream=true` 心跳响应并只解析最终 completed 事件，避免生成期间超过约 30 秒无响应而被上游 Nginx 断开。HTTP 2xx 后返回 `processing`、`queued` 或 `running` 时继续视为已受理；只有 SSE 明确返回错误、或已返回任务标识后轮询到明确失败状态，才记录为“上游明确失败”。已受理后的连接中断或等待超时不冒充明确失败，任务保留运行态直到权威截止点，再按超时退款且不自动重提。任务从 Worker 开始调用上游时计时，达到管理员配置的截止时间仍未交付时超时退款，默认 10 分钟，迟到结果在落盘前作废。

管理员可以在 `/admin/users` 查看注册用户及额度，并通过独立的 `admin_grant` 账务记录给单个用户人工充值。该能力不依赖支付渠道，也不会创建充值订单或支付成功通知；每次操作必须填写原因，浏览器会生成幂等键。用户行还可读取支付充值、人工充值和相关冲正的去敏记录，不返回支付 reference 或幂等引用。存储页面统一以十进制 MB 输入和展示，但数据库仍保存 bytes。模型路由配置的不可恢复退役依赖 `0036_model_routing_deletion_tombstones`；部署升级必须先完成 `alembic upgrade head` 再开放新版管理页面。

管理员可以在 `/admin/generation-tasks` 查看全站排队中和生成中的任务，并取消单个任务。取消会把任务幂等标记为 `cancelled`、释放该任务的全部冻结额度并拒收迟到结果；如果 Provider 请求已经发出，当前 Adapter 不保证能够物理中断上游执行，因此可能仍产生 Provider 成本，但不会再向用户结算或交付。

生成 Worker 与 Provider 后台轮询/核实仍不装配；支付已装配易支付兼容下单和成功通知，但不包含退款、部分退款或拒付自动通知；每日只读对账和双人复核流程见 `docs/payment-and-account-operations.md`。邮箱验证和密码找回均已通过 SMTP 装配；密码重置令牌只存摘要、30 分钟单次有效，成功后撤销该用户全部会话。这里的“后台轮询”不包括 `OpenAICompatibleImageSubmissions` 在一次提交请求生命周期内、拿到任务标识后执行的最多 240 秒查询；该查询不会在请求结束后继续。Web 进程每秒扫描达到管理员配置截止点的生成任务，并每分钟判断路由健康是否已满 24 小时；生成截止扫描只失败退款和拒绝迟到交付，不查询或取消上游。没有上游任务标识时不得按本地任务 ID 盲目轮询或重新 POST，以免查询错误任务或产生二次扣费。路由检测期间继续沿用最近完成状态，完整结果持久化后才更新，并且不会改动路由 `enabled` 开关。Provider 密钥当前由受控文件权限而不是 KMS 静态加密。

## 可观测性与告警

应用为每个 HTTP 请求接受或生成安全的 `x-request-id`，并在响应中回传。Prometheus 兼容指标位于 `/metrics`；只有设置至少 16 个字符的 `METRICS_TOKEN` 后才开放，抓取时使用 `Authorization: Bearer <token>` 或 `X-Metrics-Token`。未配置令牌时该路径返回 404，避免把未保护的指标端点暴露到公网。

当前仓库侧指标包括 HTTP 请求计数/延迟、数据库连接池建立/借出/归还/当前占用、生成 Worker 心跳、最后成功领取时间、在途任务数、队列最老任务年龄、任务处理结果，以及媒体卷总量/已用量/可用量/探测状态。媒体容量只在通过鉴权的 `/metrics` 抓取时刷新，不把宿主机或容器路径写入标签。Worker 使用 JSON 日志格式；字段采用 allowlist，Bearer、Cookie、API Key、数据库连接串、密码、完整提示词和图片内容会被脱敏。

仓库提供 `deploy/monitoring/storage-backup-alerts.yml` 告警模板和 `docs/runbooks/storage-and-backup-alerts.md` 处置手册。云端仍需把应用指标接入腾讯云监控或 Prometheus，并为以下持续窗口配置告警：5 分钟 HTTP 5xx > 2%、数据库池等待/耗尽、Worker 心跳超过 2 分钟未更新、队列最老任务超过 10 分钟、媒体盘使用率 > 80%、备份成功时间超过 26 小时。每条告警都必须在预发布验证触发和恢复通知。

## 备份与恢复

一次可恢复的备份必须同时包含：

1. PostgreSQL 一致性备份；
2. `GENERATED_MEDIA_HOST_PATH` 的同一恢复点文件备份；
3. `PROVIDER_SECRETS_HOST_PATH` 的同一恢复点加密备份；
4. 使用中的不可变镜像 digest 和非敏感部署配置记录。

仓库提供本地恢复点 manifest 工具，可在不接触云凭据的情况下先校验文件完整性：

```bash
cd backend
python scripts/backup_manifest.py create \
  --output /secure-backups/recovery-point.json \
  --database-backup-id pg-2026-08-18T0000Z \
  --media-snapshot-id media-2026-08-18T0000Z \
  --secrets-snapshot-id secrets-2026-08-18T0000Z \
  --image-digest sha256:<signed-image-digest> \
  --migration-head 0061_password_reset_tokens \
  --config-version <git-commit> \
  --file database=/secure-backups/database.dump
python scripts/backup_manifest.py verify /secure-backups/recovery-point.json \
  --metrics-file /var/lib/node_exporter/textfile_collector/infinite-canvas-backup.prom
```

隔离恢复时不要直接信任 manifest 中原主机路径，可把恢复后的文件显式映射到隔离目录再校验：

```bash
python scripts/backup_manifest.py verify /secure-backups/recovery-point.json \
  --metrics-file /var/lib/node_exporter/textfile_collector/infinite-canvas-backup.prom \
  --file database=/srv/isolated/database.dump \
  --file media=/srv/isolated/media.snapshot \
  --file secrets=/srv/isolated/secrets.snapshot
```

`--metrics-file` 使用 Prometheus node_exporter textfile collector 格式原子写入三个非敏感指标：最新已验证恢复点的创建时间、最近校验时间和最近校验完整性。校验失败时完整性变为 `0`，并保留上一次成功时间，避免失败任务把陈旧备份伪装成新成功。目录必须由备份任务可写且 node_exporter 可读；指标文件不包含快照 ID、文件路径或密钥。若未部署 node_exporter，可由腾讯云采集 Agent 读取同一文本指标。

该工具只负责 manifest、SHA-256、本地文件校验和指标落盘，不会替代 TencentDB PITR、COS 快照、KMS 加密或隔离环境恢复演练；这些仍需云端凭据和真实资源后验收。每日恢复点超过 26 小时的规则也不能证明建议的 15 分钟 RPO，真实环境还必须单独监控 TencentDB PITR 连续性。

数据库、媒体目录与 Provider 密钥目录必须成对恢复。只恢复数据库会留下缺失图片和不可读 Provider 引用，只恢复目录会失去账户归属与引用关系。所有备份都应加密并限制访问；不要把数据库密码、会话、支付凭据或 Provider Key 写入普通日志或仓库。

## 日常检查

- `/healthz`、登录、独立图片生成、生成任务列表和代表性图片内容读取正常；`/workspace/canvases` 应返回 404。
- `migrate` 服务最近一次退出码为 0，Alembic 只有一个 head。
- PostgreSQL、媒体目录和 Provider 密钥目录备份可在隔离环境按同一恢复点恢复。
- Provider 密钥目录权限仍为 `0700`，Key 文件仍为 `0600`。
- 媒体目录容量、inode 和增长速度有监控，应用用户仍具有读写删除权限。
- `/readyz` 正常，且 PostgreSQL 连接使用率未逼近 `max_connections`。

## 百人并发容量基线

生产镜像默认运行 4 个 Web 进程。图片生成请求只写入 PostgreSQL 队列，实际 Provider 调用由 `generation-worker` 服务完成，因此慢生成不会占用 Web 线程数分钟。Compose 默认启动 4 个生成 Worker；请按上游 Provider 的并发额度调低或调高 `GENERATION_WORKER_REPLICAS`。

浏览器提交任务后会保持一条 `/api/v1/generation-tasks/<id>/events` SSE 长连接，服务端仅在任务状态变化时推送；不再每 1.8 秒创建新的 GET 轮询。反向代理必须关闭该路径的响应缓冲，并把读取超时设为大于最长生成时限（当前至少 6 分钟）。应用每 1.5 秒发送注释心跳，避免空闲连接被中间网络设备提前关闭。

数据库最坏连接预算为：

```text
Web 副本数 × WEB_CONCURRENCY × (DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW)
+ Worker 副本数 × (WORKER_DATABASE_POOL_SIZE + WORKER_DATABASE_MAX_OVERFLOW)
+ 迁移、监控和运维预留
```

当前单机默认值为 `1 × 4 × (8 + 4) + 4 × (2 + 1) = 60` 条应用连接。PostgreSQL 至少还应保留 20% 或 10 条（取较大值）给迁移、备份和人工排障。若数据库 `max_connections` 低于预算，不要直接增加 Web/Worker 数；应先减小池或使用 PgBouncer transaction pooling。

上线前在隔离环境执行 100 并发就绪探针冒烟：

```bash
cd backend
python scripts/capacity_smoke.py https://studio.example.com/readyz --concurrency 100 --requests 1000
```

预发布可以先执行仓库内的无破坏冒烟检查；测试账号只通过环境变量传入，脚本只输出状态码、路径和错误类型：

```bash
cd backend
export STAGING_TEST_EMAIL="staging-test@example.com"
export STAGING_TEST_PASSWORD="<从 Secret Manager 注入，不写入脚本>"
python scripts/staging_smoke.py https://staging.example.com
```

完整预发布验收使用带显式状态变更保护的矩阵脚本。默认仍只执行只读基线；取消任务、支付通知重放和媒体清理必须逐场景选择并提供 `--allow-state-change`。输入、人工触发步骤、脱敏证据和清理边界见 `docs/runbooks/staging-acceptance.md`：

```bash
python scripts/staging_acceptance.py https://staging.example.com \
  --scenario baseline \
  --evidence staging-baseline.jsonl
```

证据文件不能包含 Token、密钥、提示词、图片、业务标识或内部路径。超时与迟到结果由真实 Worker 和 Provider 沙箱触发，脚本只核验最终权威状态，不能代替真实依赖验收。

发布或回滚前先校验不可变镜像和迁移兼容性，并保存非敏感状态快照：

```bash
python scripts/migration_contract.py alembic/versions --expected 0061_password_reset_tokens
python scripts/release_contract.py \
  --image ghcr.io/example/ai-platform@sha256:<64-hex-digest> \
  --migration-head 0061_password_reset_tokens \
  --previous-image ghcr.io/example/ai-platform@sha256:<previous-64-hex-digest> \
  --previous-migration-head 0061_password_reset_tokens \
  --snapshot /secure-deploy/release-state.json
```

迁移 head 不一致时，脚本默认拒绝回滚；只有完成独立审批并提供 `--approval-reference` 后，才允许显式标记 schema 不兼容回滚。脚本不会切换流量、执行数据库 downgrade 或修改云资源。

验收时要求失败数为 0，并记录 p95、p99 和 RPS 作为该机器规格的基线。随后使用测试账号对登录、画布保存、任务提交和任务列表分别压测；Provider 生成吞吐应单独按“每分钟完成任务数”验证，不能用 `/readyz` 的 HTTP RPS 代替。

2026-08-12 在 4 个 Web 进程、4 个生成 Worker 和 PostgreSQL `max_connections=100` 的本机 Docker 环境实测：100 并发、1000 次 `/readyz` 请求为 0 失败，约 139 RPS，p95 约 2.16 秒，p99 约 3.51 秒；探针期间数据库观测到 42 条总连接。此数据仅作为该机器和 Docker Desktop 环境的起步基线，不代表 Provider 生图吞吐或公网端到端 SLA。

本 Compose 是单主机拓扑，Web 与 Worker 共享同一受控媒体目录。仓库已补齐 S3 兼容 Adapter 的写入、读取、删除和晋升契约，但尚未接入生产组合根；扩展到多台主机前，必须在隔离环境验证 COS 的私有桶、服务端加密、原子晋升和权限策略，再切换媒体装配。Provider 密钥也应迁移到共享 Secret Manager/KMS。PostgreSQL advisory lock 已保证周期任务和任务提交在多进程下不会重复执行。
- 反向代理证书有效，应用端口仍只绑定回环地址。
- Docker 日志轮转生效，日志中没有连接串、Cookie、Bearer token、API Key 或用户图片内容。

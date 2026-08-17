# 乐云工坊 SaaS Backend

当前版本已恢复完整经典与智能画布产品面：导航、`/workspace/canvases`、两类编辑器入口以及生产组合中的 `/api/v1/canvases*` 均已开放。成熟编辑器通过账户隔离的 SaaS Canvas Gateway 复用；视频、LLM 和 RunningHub 节点及历史字段继续保留，但尚未安全接入的真实执行能力仍明确阻断。

第二阶段 SaaS 后端独立于根目录兼容应用演进。账户切片提供邮箱密码注册、个人账户空间、零余额额度账户、登录会话和余额查询，并通过同一个 `AccountAccess` Interface 提供内存与 SQLAlchemy Adapter。生产组合根使用管理员后台动态配置的 SMTP 服务投递 24 小时一次性邮箱验证链接；非敏感设置保存在数据库，SMTP 密码只进入受控密钥 Adapter。个人账户页可重新发送（新令牌替换旧令牌）；用户可在该页校验当前密码后修改密码，成功时撤销该用户全部登录会话并要求重新登录。

## 图片输出档位与格式

新图片任务在创建和冻结额度时显式固化 `params.resolution_tier/size/quality/aspect_ratio/output_format`。独立图片工作区公开 `1K/2K/4K` 与 `1:1/4:3/16:9/3:4/9:16`，服务端按固定矩阵派生像素：1K 为 `1024x1024/1024x768/1280x720/768x1024/720x1280`，2K 为 `2048x2048/2048x1536/2048x1152/1536x2048/1152x2048`，4K 为 `2880x2880/3264x2448/3840x2160/2448x3264/2160x3840`。新档位请求固定 `quality=auto`，输出格式为 `png/jpeg/webp`，当前不开放自定义像素。`OpenAICompatibleImageSubmissions` 将派生 `size`、固定质量和格式原样送入兼容请求，并按返回图片的真实文件签名确定 MIME；若兼容上游忽略 `output_format`，则记录去敏告警并保存真实格式，不转码或伪装。历史任务可保持新增快照字段为空并使用遗留映射。清晰度档位与模型成品规格解耦，1K–4K 使用同一个逻辑模型价格。完整画布 Gateway 继续只规范化能够保持既有比例语义的旧尺寸，无法等价映射时明确拒绝。

额度切片通过 `RechargePackages` Interface 发布不可改写的充值包版本，通过 `CreditAccounting` Interface 记录幂等充值、冲销链和账户账务记录。`ModelPrices` 提供版本化的逻辑模型价格，迁移种子为 `gpt-image-2/4k = 0.1500` 额度/张；`GenerationCredits` 负责按提交时价格冻结、部分结算和失败释放。人民币以分保存，额度使用 `CREDIT_SCALE=10000` 的整数单位保存；充值取得的额度没有到期时间。`/admin/recharge-packages` 与 `/admin/model-prices` 分别显示当前生效目录，并复用管理员发布 HTTP 创建立即或未来生效的新版本；两者都不提供编辑、删除或完整历史查询。`/admin/payment-settings` 按 New API 的易支付兼容协议管理网关地址、PID、商户密钥和支付方式；非敏感设置持久化到 PostgreSQL，商户密钥只进入受控 `ProviderSecrets` Adapter。用户下单后获取已签名 POST 表单，网关成功通知验证 PID、MD5 签名、方式和金额后复用现有订单幂等入账；退款和拒付自动化仍未接入。

管理员用户管理通过 `GET /api/v1/admin/users` 返回注册邮箱、验证状态、注册时间、用户/账户标识和当前可用/冻结额度，不返回密码摘要、访问令牌或会话。`POST /api/v1/admin/users/{user_id}/credit-grants` 要求正额度、人工原因和 `Idempotency-Key`，追加独立的 `admin_grant` 账务记录并永久到账；引用中包含执行管理员身份，相同参数可以安全重放。该操作不选择充值包、不创建充值订单，也不伪造支付成功通知。`/admin/users` 提供对应管理页面；没有支付途径时，用户钱包中的充值包按钮会明确说明支付尚未开放并引导联系管理员，而不是静默禁用。

存储额度由平台管理员统一配置并持久化，默认作用于所有现有及未来个人账户空间；管理员也可在 `/admin/storage-allowance` 按完整邮箱搜索用户并设置优先于统一值的账户级额度。`GET /api/v1/auth/me` 在媒体 Module 装配后返回额度上限、按账户内内容哈希去重的可用临时生成媒体与持久媒体已用量和存储额度剩余；管理页面通过 `GET/PUT /api/v1/admin/storage-allowance` 读取和修改统一额度，通过 `GET/PUT /api/v1/admin/users/{user_id}/storage-allowance` 读取和修改单个用户额度。数据库和 HTTP 继续精确保存 bytes，页面以十进制 KB/MB/GB 展示，管理员以 MB 输入并按 `1 MB = 1,000,000 bytes` 换算。调低额度不会删除已有媒体，剩余容量最低显示为零。独立图片生成提交前要求至少剩余十进制 10 MB；不足时在任务创建、额度冻结和 Provider 调用之前返回清理提示。

Python SaaS HTTP Adapter 提供账户、钱包、完整画布和个人资产库 Web UI。个人账户页直接使用 `/api/v1/auth/me` 和额度余额 Interface；钱包页使用充值包、支付途径、账户隔离的充值订单列表和额度账务记录；`/workspace/canvases` 列出当前个人账户空间的版本化画布，允许创建经典或智能空画布、按预期版本修改标题，并打开 `/workspace/canvases/{canvas_id}/classic` 或 `/workspace/canvases/{canvas_id}/smart`。这两个入口复用成熟经典/智能编辑器，浏览器侧 SaaS Canvas Gateway 集中把旧编辑器的画布加载与保存映射到现有账户隔离的 `/api/v1/canvases` Interface，携带登录会话并保留未知文档字段；SaaS 模式不启动旧版多人轮询或直接恢复旧渠道任务。Gateway 还拦截旧 `GET /api/config`，只从 `/api/v1/image-models` 用户安全目录生成包含逻辑模型名称和合成“平台模型”来源的最小兼容投影，不向编辑器提供真实 Provider、地址、凭据、route、RunningHub 配置或成本。经典与智能画布的旧图片任务创建也由 Gateway 映射到 `/api/v1/generation-tasks`：一次用户操作只提交一个任务并通过 `quantity` 表达数量，逻辑模型与成品规格来自 `/api/v1/image-models` 安全目录，旧来源、路由和凭据字段不会进入请求。提交后编辑器立即保存安全任务标识并保持生成占位；重新打开画布时，Gateway 合并该画布不受最近历史条数限制的活动任务与最近任务，既恢复文档中已保存的活动任务，也恢复提交响应返回前退出形成的未跟踪任务。经典与智能画布只在页面打开期间观察这些已知活动任务，到达终态即停止；它不会创建新 attempt、重提 Provider、核实 `unknown`，也不提供页面关闭后的 Worker、队列或跨实例协调。成功图片由携带 Bearer 会话的内容请求读取为页面内 `blob:` 预览，经典画布同步源节点与已连接输出节点，智能画布恢复结果节点；编辑器通过 `media_id` 判断 SaaS 结果可保留，调用现有保留 Interface 后形成持久画布引用。局部处理上传、结果归并和结果拖拽都传播新的 `media_id`、MIME 与媒体状态；保存文档时仅保留该媒体身份与同源内容路径，不保存 `blob:`、平台对象键、服务器路径或上游 URL。视频、LLM 和 RunningHub 节点、参数及历史数据继续保留；Gateway 在 SaaS 模式下本地拦截旧 `/api/canvas-video`、`/api/canvas-llm` 与全部 `/api/runninghub/*` 请求并返回统一去敏错误，不把尚未安全接入的配置读取、素材上传、提交或查询转发给旧接口。经典和智能编辑器在 SaaS 模式下还会在读取或规范化旧 RunningHub 配置前，把对应节点的 Key、内部配置选择与运行控件替换为只读未发布提示；原节点字段仍随画布文档无损保存，非 SaaS 行为不变。`DELETE /api/v1/canvases/{canvas_id}` 与画布列表提供不可恢复的画布删除且不提供回收站；存在活动生成任务时必须使用 `confirm_running_tasks=true` 再次确认，任务继续独立执行。删除会清空画布内容、从所有用户读取和保存 Interface 隐藏，并协调释放画布媒体引用；SQL Adapter 只保留满足历史任务和媒体外键所需的最小删除墓碑。`/workspace/assets` 展示当前账户空间已保存资产的安全元数据，允许修改显示名称，并在二次确认后执行不可恢复的个人资产移除；移除不提供回收站，最后一条有效引用消失时发生媒体释放。`GET /api/v1/payment-methods` 只返回渠道标识与显示名称，`GET /api/v1/recharge-orders` 只返回当前登录账户空间的订单；真实支付、支付凭据和退款仍不属于当前实现。

Gateway 保存画布时会比较保存前后的生成任务节点；用户明确删除并成功保存的任务节点形成画布文档内的删除记录，后续活动任务与最近任务恢复必须尊重该记录，不得重新创建节点。该记录只约束画布展示，不取消或删除独立运行的生成任务、结果和媒体。

经典与智能编辑器的旧 `/api/ai/upload` 图片动作由 Gateway 映射到 `POST /api/v1/canvases/{canvas_id}/media`；首版只接收当前账户拥有画布的 PNG、JPEG、WebP，上传后立即形成持久画布引用并计入存储额度。受保护内容在当前页面转换为 `blob:` 预览，画布文档只保存 `media_id` 与同源内容路径。旧匿名上传后端、视频和音频上传仍不开放。

视频和 LLM 采用相同的 SaaS 可见面保护：经典画布在读取旧 Provider 或规范化模型前把对应节点替换为只读未发布提示；智能画布的视频参数区及提示词节点 LLM 区不再渲染旧 Provider、模型和运行控件。已有节点字段、用户文本、结果与历史继续随画布文档保存，关闭 SaaS Gateway 时仍使用原完整旧版行为。

`RunningHubCapabilities` Interface 将用户公开能力身份与内部 RunningHub workflow ID 分离，并通过内存与 SQLAlchemy Adapter 保存名称、粗粒度文本/图片输入能力和可用状态。平台管理员在 `/admin/runninghub-capabilities` 使用 `GET/POST/PATCH /api/v1/admin/runninghub-capabilities` 发布、编辑和停用能力；不提供删除。管理员还通过 `POST/GET /api/v1/admin/runninghub-capabilities/{capability_id}/input-schema-versions` 发布和读取不可改写的有序输入 schema 历史；页面可以新增文本或图片输入、设置用户标签与必填状态并调整顺序，但不能编辑或删除历史版本。首个 schema 发布后，能力的粗粒度输入能力由当前 schema 派生，原 PATCH 不再允许独立改写该字段。`POST/GET /api/v1/admin/runninghub-capabilities/{capability_id}/price-versions` 按每次能力使用发布和读取不可改写的用户价格历史，额度使用 `CREDIT_SCALE=10000` 精度并允许立即或未来生效；管理页面默认填写 `0.1000`，但不会自动给能力定价。登录用户通过 `GET /api/v1/runninghub-capabilities` 读取包含停用项的安全目录：未发布 schema 时 `input_schema` 为 `null`，否则只返回当前 schema；没有当前生效用户价格时 `credits_per_run` 为 `null`，否则只返回当前金额，不返回未来版本或历史。响应不包含 workflow ID、内部 node/field 绑定、Provider、地址、凭据、route 或成本。本切片没有定义默认值、选项、上传规则、大小/MIME 限制或工作流字段映射，仍不开放 RunningHub 上传、提交、查询、轮询、真实执行、额度冻结/结算、Provider 成本、凭据引用或输出流水线；Gateway 继续阻断旧 `/api/runninghub/*`。

Gateway 还会本地拒绝未安全接入的 Midjourney、ModelScope、旧在线图片、旧聚合图片查询以及旧视频上传路径，包括 `/api/midjourney/*`、`/generate`、`/api/angle/*`、`/api/ms/*`、`/api/online-image`、`/api/image-task-query` 与 `/api/cloud-video/upload`。精确的 `POST /api/canvas-image-tasks` 只映射为一个 SaaS generation task；精确的 `GET /api/canvas-image-tasks/{task_id}` 只映射到现有账户隔离任务和媒体读取 Interface，不访问旧后端、不核实 `unknown`、不重新提交；精确的 `POST /api/ai/upload` 仅在完整画布 Gateway 内映射到账户隔离画布图片上传。

未迁移且未账户隔离的旧本地运行数据同样不会穿过 Gateway：旧素材库、提示词库、工作流导入导出、画布资源下载、智能画布旧模板以及旧画布 `trash`、`restore`、`meta` 路径统一返回“旧本地数据不属于当前 SaaS 账户”。账户隔离画布读写和 `/workspace/assets` 个人资产库继续使用现有 SaaS Interface；本切片不迁移或删除任何旧本机数据。

模型路由切片通过 `/admin/model-routing` 和 `/api/v1/admin/providers`、`/api/v1/admin/image-model-routes` 由平台管理员集中配置多路 OpenAI 兼容图片来源。管理页按“API 来源 → 模型映射与路由 → 健康检测与选路资格 → 选择策略”展示完整流程，并逐条显示来源停用、路由停用、未检测或最近检测不可用等阻断原因。来源支持编辑显示名称、基础地址和可选 Key 轮换；路由停用后可以编辑上游模型名称、兼容组和优先级。地址、Key 或模型映射发生变化时，关联路由自动停用并清除旧健康证明，必须重新检测后启用。API Key 是只写字段，数据库只保存密钥 Adapter 返回的引用和短指纹；管理员可以检测 HTTPS、鉴权、上游模型存在性与总延时，并查看滚动 EWMA、P95 和成功率。生成任务只接受逻辑模型与成品规格，平台从已启用且最近完整检测可用的兼容路由中依次按成功率、健康水平与延时选择；路由优先级仅在前述指标相同时作为最后裁决。管理员指定来源不可用时自动回退其他兼容来源，不会切换逻辑模型。

管理员 `DELETE` 模型路由会不可恢复地将其移出当前目录、健康检测和未来选路，并在必要时把指定优先策略恢复为自动模式；Provider 成本版本、生成尝试和历史任务仍保留原路由身份。Provider 只有在其全部活动路由先被删除后才能删除，删除后当前密钥由 `ProviderSecrets` Adapter 幂等清理，来源代码与历史身份继续保留。已经发送给上游的请求不因配置删除而取消，尚未提交且只引用退役路由的任务按五分钟截止规则失败退款。迁移 `0036_model_routing_deletion_tombstones` 为来源和路由增加持久退役事实。

生成任务通过 `reference_media_ids` 固化有序普通参考图片，并通过独立的 `mask_media_id` 固化至多一个蒙版输入；公开任务投影只返回普通参考图数量和 `mask_media_present`，不暴露媒体标识。蒙版必须伴随普通参考图，全部输入媒体合计最多三张且不能复用同一标识。经典画布会保留账户媒体身份并把“原图 → 蒙版 → API 生图”转换为该结构，智能画布不会把 `role=mask` 改写为普通图片序号。OpenAI-compatible Adapter 对这类任务使用 `/images/edits` multipart：普通图片使用 `image`，蒙版使用独立 `mask`；没有任何编辑输入时仍使用 `/images/generations`。迁移 `0037_generation_task_mask_media` 为历史任务增加默认空值的蒙版快照。本契约只由 `httpx.MockTransport` 验证，正式上游字段仍需在文档或受控环境验收。

生产 Web 进程会按最近一次检测完成时间每 24 小时自动检测已启用 Provider 下的路由；尚无健康快照的路由会尽快执行首次检测，管理员也可以随时手动检测。两次检测之间持续沿用最近完成结果，不再按 5 分钟或其他 TTL 自动过期；探测进行中保持旧状态，只有完整结果持久化后才更新路由健康。内部每分钟只扫描是否已满 24 小时，并不会每分钟请求上游。检测结果不会自动修改路由 `enabled` 开关；新结果健康时，仍启用的路由恢复参与选路，新结果不健康时停止参与选路。

`ProviderCostRates` 为每条模型路由保留一个当前单张成本配置。管理员在 `/admin/provider-costs` 以整数分保存，`PUT /api/v1/admin/provider-cost-rates/{route_id}` 会立即追加更高版本并成为当前成本；旧版本不可改写，继续供历史 attempt 审计。内部仍按微单位持久化，`1 分 = 10,000 微单位`；路由历史由 `GET /api/v1/admin/provider-cost-rates?route_id=...` 返回。旧的按规格 POST/GET 暂时保留兼容。生成尝试创建时固化当时路由的最高有效成本版本；缺少成本时任务保持 `queued` 且不调用上游。`GET /api/v1/admin/provider-cost-summary` 按 Provider、逻辑模型和币种累计已提交 attempt 的固化成本乘以任务数量，重试分别计入，未提交 attempt 不计入；这是配置成本估算，不是 Provider 最终账单，不影响路由选择或用户额度结算。

用户模型目录通过 `/api/v1/image-models` 组合当前生效价格与只读可用状态，`/workspace/models` 直接展示该安全投影，不暴露来源或路由信息，也不提供生成提交。生成任务在创建并冻结额度时固化用户原始提示词与 `params.aspect_ratio`；首发比例限定为 `1:1`、`16:9` 和 `9:16`，相同任务标识只有在完整用户请求一致时才能幂等重放。用户端创建、单任务查询、画布活动任务和最近任务响应共用显式安全投影，只返回用户请求、额度、状态、时间和通用失败提示，不暴露账户归属、冻结与价格引用、来源路由、Provider 任务、内部错误或结果引用。`GenerationTasks.recent_for_canvas(..., limit)` 与 `GenerationTasks.recent_for_account(..., limit)` 通过内存和 SQLAlchemy Adapter 按创建时间从新到旧返回包含终态的账户隔离任务历史；`GET /api/v1/canvases/{canvas_id}/generation-tasks/recent` 提供单画布历史，`GET /api/v1/generation-tasks/recent` 提供账户空间全局历史，两者都限制为用户安全投影。账户隔离的 `GET /api/v1/generation-tasks/{task_id}/media`、`GET /api/v1/media/{media_id}` 与媒体保留响应共用生成结果目录投影，只返回媒体标识、任务标识、类型、MIME、大小、状态及创建/过期/保留时间；不返回用户或账户归属、画布标识、对象存储键、内容哈希、内部结果引用或释放时间。`GET /api/v1/media/{media_id}/content` 只在账户权限校验后读取仍为临时或持久状态的媒体内容，响应不公开对象键或文件路径并禁止共享缓存；临时媒体在读取前执行到期清理，精确到期后即使物理删除暂时失败也不再可读。`/workspace/generations` 通过账户级 Interface 读取足够覆盖最近 24 小时的任务并在客户端按 `created_at` 收窄，只为成功任务读取安全结果目录，显示实际交付数量以及临时可用至、已保留、已过期或已释放状态。任务来源显示“文生图”，或显示“画布名称”-智能画布/经典画布；永久删除画布后只显示“已删除画布”，不会恢复墓碑中已清空的标题或文档。页面只提供只读“查看”和重新 GET 的“刷新状态”，查看器通过鉴权内容 Interface 加载仍可用结果，不提供核实、重试、修改或删除，也不启动定时轮询；失败任务不会从页面消失，并显示持久去敏提醒。

`GenerationAttemptSubmitter.submit(account_space_id, task_id)` 在任何 Provider 调用前持久化当前 attempt、任务已固化路由、当时生效的 Provider 成本版本及服务端派生的稳定幂等键，再通过可替换的 Provider 提交 Interface 发送固化请求快照。缺少生效成本版本时不会创建 attempt 或调用 Provider；任务保持 `queued`，冻结额度不变。后续成本调价及重入不会改写已有 attempt 的版本；历史 attempt 可以没有该引用。同一任务最多进行两次生成尝试：首次 attempt 明确为 `failed` 时，再次提交会沿用任务已固化路由创建 attempt 2，派生新的 Provider 幂等键并固化其创建时生效的成本版本；attempt 2 再次明确失败后任务转为 `failed` 并释放全部冻结额度，后续重入只返回原 attempt 2，不再调用 Provider。重试不会重新冻结额度或改变任务的模型价格版本，`submitting`、`provider_pending` 或 `unknown` 均不会产生新 attempt。明确受理记为 `provider_pending`，并使用已校验的 Provider 任务标识把同一生成任务推进为 `running`；同步图片结果提交还会把响应中已经规范化的图片交给 `GenerationImageDelivery`，在同一用户 HTTP 请求中完成落盘、登记和结算后返回 `succeeded`。明确拒绝记为 `failed`，超时、异常或无效响应记为去敏的 `unknown`，不会自动重提。`GenerationAttemptReconciler.reconcile(account_space_id, task_id)` 只对 `unknown` attempt 使用原路由、Provider 幂等键和已有 Provider 任务标识核实原提交：确认受理或未受理时原地转为 `provider_pending` 或 `failed`，仍未知、查询异常及无效结果保持原记录；非 `unknown` 状态不会再次查询。核实不重新提交或创建新 attempt；attempt 2 经核实为未受理时执行同一任务失败和额度释放规则，重入可以补齐中断的任务运行或失败收口而不再次查询 Provider。遗留 `submitting` 仍安全恢复为 `unknown`，不会盲目重放。成本版本、核实信息和内部失败原因不进入用户任务响应。HTTP Adapter 注入 `GenerationAttemptSubmissions` 时，`POST /api/v1/generation-tasks` 会在任务持久化后立即提交或安全重入当前 attempt，并重新读取用户任务投影；明确受理会直接返回 `running`，同步图片结果完成交付会直接返回 `succeeded`，缺少生效 Provider 成本版本则仍返回 `queued`。同一注入还会开放 `/retry`：仅 queued 任务可以安全提交或重入当前 attempt，unknown 不会重提，running 和终态任务返回 409。注入 `GenerationAttemptReconciliations` 时，账户隔离的 `POST /api/v1/generation-tasks/{task_id}/reconcile` 会主动核实 unknown attempt，再返回同一用户安全任务投影；没有 attempt 时保持原任务状态，非 unknown 状态只执行幂等补齐而不查询 Provider。未注入对应 Interface 时不开放其可选行为。当前不包含 Worker、Provider 后台轮询/核实或自动重提。

所有 `queued` 或 `running` 生成任务从 `created_at` 起共享严格五分钟交付截止时间；开始运行不会重置计时。内存与 SQL `GenerationTasks.expire_due(now)` 在截止点使用稳定超时引用把任务幂等标记为 `failed`、释放全部冻结额度和并发名额。生产单 Web 进程每秒执行该扫描，所以页面关闭后仍会收口；用户安全投影显示“五分钟超时、额度已退回”。`GenerationImageDelivery` 在媒体落盘前按结果完成时间执行同一规则，恰好五分钟或更晚的图片不写入、不登记、不展示、不结算。OpenAI-compatible Adapter 可以在同一次提交请求内查询异步任务，但没有已验证的取消接口，平台无法保证上游停止执行或计费，迟到上游成本由平台承担。

`/workspace/images` 是与画布同级的独立图片生成工作区：左侧从 `/api/v1/image-models` 安全目录选择逻辑模型，并设置清晰度、预设比例、PNG/JPEG/WEBP 格式、1–8 张数量和最多 3 张 PNG、JPEG 或 WebP 参考图；右侧为 `queued/running` 任务显示带编号的旋转占位卡，页面内持续观察同一任务，完成后原位展示结果卡。生成结果固定保留 24 小时并计入个人存储，页面通过账户级最近任务恢复仍在排队或运行的独立任务，并通过安全媒体目录恢复已交付结果；单张删除与“清空”调用 `DELETE /api/v1/media/{media_id}` 真正清理字节、释放空间并留下不可恢复墓碑。结果卡提供完整视口适配、滚轮缩放和拖动预览、单张下载、参数详情、复用提示词和 `POST /api/v1/media/{media_id}/use-as-reference`；批量 ZIP 仍通过 `/api/v1/media/archive`。模型、清晰度、比例、格式和数量按账户保存到 `localStorage`，提示词重新打开时为空。参考图在选择后立即通过 `/api/v1/reference-media` 上传并显示鉴权缩略图，`GET /api/v1/reference-media/recent` 负责刷新恢复，手动删除立即清理，未删除时最多保留 24 小时。浏览器无法可靠区分刷新、关闭、崩溃与断网，因此不承诺关闭浏览器即删；任务历史中已固化的参考标识仍作为请求事实保留。提交失败时，HTTP 与结果卡会区分活动任务上限、可用额度不足、剩余存储不足、模型无可用来源以及参数或参考图无效；不会再把并发上限与额度不足合并成“当前无法提交”。页面观察不会核实或重提 `unknown` 尝试。

Provider Adapter 可通过内部 `ProviderGenerationTargets.resolve(route_id)` seam，把任务已固化路由解析为当前协议、规范化地址、上游模型名和只在调用期间可用的凭据。解析不会重新执行启用状态或健康准入，密钥轮换与地址更新对尚未提交的固化路由立即生效；敏感目标不从 Module 根导出，凭据不进入对象表示、公开响应或数据库查询结果。`OpenAICompatibleImageSubmissions` 迁移成熟的 Bearer `/images/generations` 协议，并兼容旧嵌套 `data/result/results/images/image/output/outputs/items/files`、常见 URL/Base64 字段别名；没有编辑输入时发送 JSON `/images/generations`，有普通参考图或蒙版时复用旧项目已验证的 multipart `/images/edits`，两种请求均携带模型、提示词、显式尺寸、质量与 `output_format`；edits 将有序普通参考图片作为 `image` 字段，并把唯一蒙版作为独立 `mask` 字段。为保持旧来源的 `n=1` 行为，任务 `quantity=N` 时顺序发送 N 次请求。首次响应没有图片但包含 `task_id/taskId/submit_id/submitId` 时，Adapter 不重复 POST，而是在同一用户请求内最多等待 240 秒，并依次探测 `/v1/images/tasks/{id}`、`/v1/tasks/{id}`、`/v1/images/generations/{id}` 后固定可用查询端点。OriginBoost 的 `gpt-image-2` 请求使用 `stream=true`，消费 SSE 心跳并只接收 `image_generation.completed` 最终事件，从而避免一分钟级生成在约 30 秒的代理空闲窗口被断开；心跳、partial image 和错误事件不会被误存为最终图片。Base64 结果只接受 PNG、JPEG 或 WebP 文件签名；若上游忽略请求格式，则按真实签名保存并记录去敏告警。URL 结果只允许无凭据的公网 HTTPS 地址，每次重定向重新解析并拒绝私网/本机，流式下载限制为单图 50 MiB；声明为具体图片 MIME 时必须与签名一致，`application/octet-stream` 则按真实签名接收，下载请求不携带 Provider Authorization。明确 4xx 或异步失败终态映射为去敏拒绝，超时、连接失败、5xx、无效 JSON、轮询超时或无有效图片映射为 `unknown`，不会自动重提。日志只记录 route、状态码、响应内容类型、上游请求标识、响应字段名、任务状态、错误类型和已去除 URL 的传输错误摘要，不记录 Key、提示词、图片内容或上游原始响应。所有外部边界测试仅使用 `httpx.MockTransport`，未调用真实来源；新增 4K 尺寸、外部 `/images/edits` 格式字段及第三方异步查询端点仍需在正式上游文档或受控测试环境验收。生产组合根使用 SQLAlchemy 模型路由、文件系统 Provider 密钥、临时参考媒体、生成 attempt、该 HTTP Adapter 与 `GenerationImageDelivery` 完成同步直返、心跳流或请求内异步轮询的真实图片链；仍不装配 Worker、Provider 后台核实或自动重提。

`GenerationImageDelivery.receive(account_space_id, task_id, images, completed_at=...)` 接收 Provider Adapter 已解析出的图片字节，不接收 Provider URL 或原始响应字段。该 Module 在任何文件写入前整体校验结果引用、任务请求数量、PNG/JPEG/WebP MIME 与文件签名，再通过 `MediaContentStore` 按账户、任务和稳定结果引用原子落盘；同一结果相同字节可安全重放，不同字节不能覆盖。全部内容写入后，它构造规范化输出并交给 `GenerationOutputReceiver.receive(...)`：Receiver 在登记前整体拒绝批内重复引用或累计超出任务请求数量的结果，随后逐项复用媒体登记的幂等语义；中途失败时不完成任务，相同批次可以继续重放，全部登记成功或空批次时才进入 Finalizer。`GenerationResultFinalizer.finalize(account_space_id, task_id, occurred_at=...)` 只根据任务已经登记且仍可用的图片完成结果交付：至少一张图片时任务成功，`delivered_quantity` 取实际图片数并复用现有额度状态机结算该数量、释放未交付余量；零张时任务失败并释放全部冻结额度。稳定结果引用使重复完成只产生一条结算或释放记录，并能在账务已完成但任务状态更新中断后安全补齐；超出请求数量、非图片结果以及 queued/cancelled 任务会被拒绝。本地 `FileSystemMediaObjects` Adapter 在受控根目录使用不透明对象键并支持进程重启后的读取、晋升与幂等删除。当前没有 Worker、后台轮询或定时清理；临时媒体由内容读取触发惰性到期清理，部署阶段仍可在既有 `expire_due` Interface 外接跨实例清理调度。

## 单服务器媒体持久目录

SaaS 后端通过服务器进程环境变量 `GENERATED_MEDIA_ROOT` 选择生成图片持久目录。`configured_file_system_media_objects()` 要求该值非空、为绝对路径并指向运维预先创建的目录；装配时会实际创建、读写和删除一个临时探测文件，目录不存在或应用进程权限不足都会抛出 `MediaStorageConfigurationError` 并拒绝继续启动，不会回退到系统临时目录或代码目录。该配置只属于部署环境，不进入管理员 Web 页面。

单服务器 Linux 目标配置示例：

```bash
sudo install -d -o infinitecanvas -g infinitecanvas -m 0750 /srv/infinite-canvas/data/generated-media
export GENERATED_MEDIA_ROOT=/srv/infinite-canvas/data/generated-media
```

生产 composition root `app.runtime:create_production_app` 只调用一次 `configured_file_system_media_objects()`，把同一个文件系统 Adapter 同时用于媒体元数据协调和账户安全的内容读取；数据库继续只保存账户归属、状态和不透明对象键。仓库根目录 `Dockerfile` 已改为打包并启动该 SaaS composition root，`deploy/compose.production.yml` 通过宿主机 bind mount 提供媒体目录，并在 Web 启动前由独立服务执行 Alembic 迁移。生产配置、UID/GID、备份和当前安全降级详见 `docs/deployment-and-operations.md`。

## 单服务器 Provider 密钥目录

生产进程通过 `PROVIDER_SECRETS_ROOT` 使用运维预先创建的绝对目录。`configured_file_system_provider_secrets()` 将目录权限设置为 `0700`，执行不含真实 Key 的创建、读取和删除探测；`FileSystemProviderSecrets` 以凭据身份的 SHA-256 摘要形成稳定不透明引用，原子写入权限 `0600` 的文件。Provider Key、SMTP 密码与支付商户密钥均从管理员页面轮换，数据库只保存 `provider-file://...` 引用及非敏感投影，用户、管理员读取响应、迁移服务与日志都不会得到明文或服务器路径。

Compose 通过 `PROVIDER_SECRETS_HOST_PATH` 把宿主机目录挂载到唯一 Web 副本的 `/var/lib/infinite-canvas/provider-secrets`；它不挂载给 Alembic migrate 服务。该实现依赖服务器权限保护静态明文，并要求密钥目录与 PostgreSQL 成对加密备份；未来可以用 KMS/Secret Manager Adapter 替换同一 `ProviderSecrets` seam。当前生产装配不会使用 `InMemoryProviderSecrets`，已启用模型路由管理、管理员健康探测、SMTP 邮件发送、易支付下单/通知和 OpenAI-compatible 图片生成（同步直返及请求内异步任务轮询）；Worker 与后台自动核实仍不在 Web 进程中执行。

## 本地环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

生产数据库使用 PostgreSQL 与 `psycopg`。数据库建好后设置安全保管的连接地址，再执行：

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

不要把数据库密码、支付凭据或 Provider Key 写入仓库。内存 Adapter 只用于自动化和开发切片，不能作为 SaaS 生产账户仓储。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy app
```

当前 Alembic 与 SQLAlchemy 行为已通过隔离 SQLite 数据库及临时 PostgreSQL 17 容器验证；PostgreSQL 专项覆盖完整迁移链的 `head → base → head`、主要账户与业务持久化流程及会话安全流程。验证仅使用随机临时密码和测试容器，不建立或保存任何长期数据库凭据。

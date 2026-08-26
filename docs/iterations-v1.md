# V1 本地迭代记录

## 版本边界

- 目录：`E:\豌豆工坊-SaaS-完整版-20260811-V1`
- 基于当前工作源码建立，未从旧 zip、旧 worktree 或干净 `main` 重建。
- 基线分支：`codex/v1-baseline`
- 基线提交：`9967387 chore: establish V1 baseline from current working source`
- 本次功能提交：在基线之上叠加经典画布下线、画布生图实时状态补丁和 V26 迁移链兼容修复。
- 当前发布提交：`89e195f feat: add single-host candidate release gate`（标签 `v1.0.7`）。
- 生产已部署 V27：`creative-studio:single-host-candidate-v27`；V26 保留为 `creative-studio:single-host-rollback-v26`。
- V26 只读核验摘要：`sha256:22e90333d05683b378bf5b0766b26ad44bc4ae025df3bfb2f7afb92122c02427`。

## 迭代门禁

1. 在 V1 目录查看 `git status`、基线提交和 V26 部署基线。
2. 为需求建立 `.scratch/canvas-smart-only-live-status/` 规格和逐项 issue。
3. 只修改本次涉及的接口、画布脚本、静态壳、管理文案和测试。
4. 先跑定向测试、语法检查、前端构建与 `git diff --check`，再决定是否提交。
5. 提交后保留可回滚 commit；部署前必须从服务器核实 V26 Web/Worker 镜像摘要并生成下一候选，不能直接把本地 V1 当生产版本。
6. 候选发布前必须核对部署脚本使用的环境文件与服务器实际 `/etc/infinite-canvas/single-host.env` 一致；生产切换仍需单独授权。

## 数据保护

历史 `classic` 画布只改变入口和展示状态，不删除记录或其中的图片/参考图/生成记录；任务和额度流水沿用原有保留策略。

## V1.0.1 迁移链修复

- 基于生产 V26 容器只读核实的真实迁移链补回 `0062_generation_timeout_headroom`、`0063_generated_media_thumbnails` 和 `0064_recharge_order_expiration`，未执行生产迁移。
- 补齐充值订单 `expires_at`/取消状态的内存与 SQLAlchemy 持久化，以及订单取消接口；不删除历史订单或账务流水。
- 画布网关在非成功任务阶段不请求媒体列表，并对宿主未暴露 `window.setTimeout` 的情况使用全局计时器兜底。

## V1.0.2 测试契约对齐

- Web UI 定向测试：`93 passed`。
- 后端全量测试：`632 passed, 5 skipped`。
- JS/Python 语法检查和 `git diff --check`：通过。
- 当时尚未生成 V27、未切换生产流量；最终部署状态见下节。

## V1.0.7 生产部署

- 最终全量测试：`633 passed, 5 skipped`。
- 供应链工作流 `32710607320` 全绿，包含锁复现、SBOM、Trivy、构建和 Cosign 签名。
- V27 摘要：`sha256:40c29e85691bf0460cf55aaed727a48b544f807e0457c279d9bedcec1858c71e`。
- 使用单机发布脚本切换 Web 与 10 个 Generation Worker，镜像摘要一致。
- 数据库迁移保持 `0065_merge_v17_redeem_concurrency`；回环与公网健康/就绪检查通过。
- V26 摘要 `sha256:22e90333d05683b378bf5b0766b26ad44bc4ae025df3bfb2f7afb92122c02427` 已保留为回滚镜像。

## V1.0.8 / V28 候选（未部署）

- 直接基于 V27 工作源码 HEAD `0437942` 叠加修改，未从旧目录、旧 ZIP、V17、干净 `main` 或其他 worktree 覆盖。
- 生产日志确认 Provider 已 HTTP 200 返回并完成结果解析；Worker 在登记 GeneratedMedia/更新任务终态时因业务 `QueuePool size 2 + overflow 1` 被长期 advisory lock 连接耗尽而超时。
- Worker ordinal lock 和任务 dispatch lock 改用独立 `NullPool` engine，并在每个 Worker 内复用一个 AUTOCOMMIT 锁会话；进程内 key 集合阻止 PostgreSQL 会话级锁的可重入槽位复用。业务任务和媒体持久化继续使用有界业务池，生产最坏应用连接预算为 88，低于 `max_connections=120`。
- 图片页和智能画布成功后统一通过任务/媒体 API 收敛，SSE 断开时对同一 task ID 做有界退避轮询，并按媒体 ID/result reference/URL 去重；不会重提 Provider 或触发额外账务动作。
- 新建画布不再显示“智能画布”只读选择框和说明，创建仍固定为 smart；classic 历史数据与旧地址兼容保留。
- CSP 事件桥改为白名单静态元素直接绑定，修复弹窗 `stopPropagation()` 阻断工作流、关闭等按钮事件的问题。
- 验证结果：后端 `641 passed, 5 skipped, 21 warnings`；生成链定向 `141 passed`；连接池/Worker/交付定向 `72 passed`；Web UI/SSE/恢复 `97 passed`；JavaScript/Python 语法、前端生产构建与 `git diff --check` 通过。
- 真实浏览器验证覆盖创建/返回、工作流导入导出、上传、编辑工具、下载、缩放/适应视图、节点选择/拖动/Delete 删除及其他现有工具栏；console 无 error/warn。因本地无已发布模型和额度，未发起付费生图，生成链由自动化覆盖且未对生产写入。
- 建议标签 `v1.0.8`，建议镜像 `creative-studio:single-host-candidate-v28`；当前生产继续运行 V27，本次未部署、未迁移数据库、未重启生产服务。

## V1.0.11 / V31 已部署

- 基于 V30 `d87f23e`（`v1.0.10`）继续开发，未从旧目录、旧 ZIP、V17、干净 `main` 或其他 worktree 覆盖。
- 新增管理员违规关键词设置和 UTF-8 TXT 导入；每行一个关键词、空行忽略、去重。普通生图和画布生图共用提交前拦截，命中不冻结额度、不创建任务、不调用上游。
- 新增脱敏风险事件和全站连续失败计数，连续 10 条失败生成一条风险事件；新增管理员风险日志页。
- 新增管理员生成任务历史监管接口和页面，支持 24 小时、7 天、30 天、全部窗口及分页，关键词命中标红。
- 新增迁移 `0066_prompt_safety_risk_events`；不删除历史任务、图片或账务流水。
- 定向测试、新增 V31 测试、迁移契约、前端检查和构建通过。完整后端 `645 passed, 5 skipped`，另有一个 V30 既有静态缓存版本断言失败（测试要求 `admin-vue-8`，源码已为 `admin-vue-9`）。
- 源码提交：`b18ea98 feat: add prompt safety risk monitoring and task oversight`；建议标签 `v1.0.11`。
- 生产镜像：`creative-studio:single-host-candidate-v31@sha256:d5a438bed458cc532c63f7b92cc3e6bf1e203bc1f312e08b379c8ab18d4fa253`。
- 回滚镜像：`creative-studio:single-host-rollback-v31@sha256:046baa8e8595a574517fc52aacceb1871d3dae26e3685930275c377fc67cc70`。
- 已执行迁移 `0066_prompt_safety_risk_events`；Web 与 10 个 Worker 使用同一 V31 摘要，健康/就绪检查通过。

## V1.0.12 / V32 已部署

- 基于 V31 `8db252e` 继续开发，源码提交 `893376e`，未从旧目录、旧 ZIP、V17、干净 `main` 或其他 worktree 覆盖。
- 画布和图片生成页统一采用任务终态后的有界媒体恢复：等待媒体达到 `delivered_quantity`，缩略图失败回退鉴权原图；SSE 断开继续对同一 task ID 受控轮询，不重提上游、不重复扣费。
- 管理员任务活动/历史投影与页面移除实际提示词显示，历史数据保留。
- 生产镜像：`creative-studio:single-host-candidate-v32@sha256:a0be4436d537ae85678bc931faecd288b5330d382711b4e662175abf18dcc9d0`；回滚镜像 `creative-studio:single-host-rollback-v32` 已保留。
- 部署脚本迁移、健康、就绪及 Web/10 Worker 镜像一致性检查通过；无新增迁移，生产 head 保持 `0066_prompt_safety_risk_events`。

## V1.0.14 / V34 候选

- 基于已部署 V33 继续开发，分支 `codex/v34-image-order-canvas-display`。
- 图片生成页历史恢复改为最新任务/最新图片优先，单张缩略图成功即渲染，随后按时间顺序补齐旧结果；不重新提交任务，不改变扣费、退款或额度流水。
- 画布媒体显示统一从 `media_id` 或旧 `/api/v1/media/{id}/content` 地址解析媒体，优先鉴权缩略图，缩略图失败才读取鉴权原图 Blob；覆盖生成、上传和派生图片，稳定文档不保存 Blob/占位图。
- 无新增迁移，不删除历史画布、媒体、参考图或用户资源。
- 新增 `backend/tests/test_v34_image_order_canvas_display.py`，并通过图片任务/媒体/画布定向测试、JS/Python 语法和 `git diff --check`。
- 源码标签 `v1.0.14`，生产镜像 `creative-studio:single-host-candidate-v34@sha256:f3ae5b7fb609dd8baeeeaa2522b70a5df6891b47d592acc6cc63dd3ef7f89fe2`；回滚镜像 `creative-studio:single-host-rollback-v33@sha256:67e3640ed3a4fea16583d9a7632df82ef36892622773ad64f7c6588eda16801b`。
- 部署时间 `2026-08-26T04:26:08Z`；Web 与 10 个 Worker 使用同一摘要，运行容器数量 11；迁移 head `0066_prompt_safety_risk_events`；发布脚本迁移、健康、就绪和镜像一致性门禁通过。

## V1.0.15 / V35

- 基于已部署 V34 继续开发，当前源码分支为 `codex/v35-llm-image2`。
- LLM 上游只请求一次；兼容字符串、内容数组、顶层 `output_text` 和旧版 `choices[].text` 返回格式，避免上游已返回结果时因解析形态不兼容而误报失败。
- 局部重绘统一放宽 `image2`、`image-2`、`gptimage2` 及带安全前后缀的模型名；`image21` 等不满足版本边界的名称仍拒绝。后端校验、Provider 流式模式和前端档位判断同步。
- 无新增数据库迁移，不改变 V34 的画布缩略图优先、最多 4 并发、点击编辑下载使用原图、最近记录查看使用原图行为。
- 定向测试 `80 passed`；完整后端 `659 passed, 5 skipped`，另有一个 V34 之前已存在的 `admin-vue-8` 静态版本断言失败（源码实际为 `admin-vue-9`）。JS 语法和 `git diff --check` 通过。
- 源码标签 `v1.0.15`，生产镜像 `ghcr.io/nannannan1111111/ai-platform@sha256:75665f6e296ce59555191408b07ef43120164b2cc0f6ce0230878cce0ee9cfd5`。
- 生产已部署：`2026-08-26T07:01:47Z`；Web 与 10 个 Worker 使用同一 V35 摘要，V34 摘要 `sha256:f3ae5b7fb609dd8baeeeaa2522b70a5df6891b47d592acc6cc63dd3ef7f89fe2` 保留回滚。
- 部署后验证：数据库迁移成功，本机 `/healthz`、`/readyz` 和公网 HTTPS `/readyz` 通过，运行容器数量 11。

## V1.0.16 / V36

- 基于已部署 V35，继续在分支 `codex/v35-llm-image2` 上修改。
- 修复画布缩略图回填事件早于编辑器监听时丢失，导致图片节点停留在透明占位图的问题；网关暂存回填快照，画布初始化后主动消费并重新渲染。
- 原图编辑、下载、保存、删除、用户媒体归属和缩略图最多 4 并发保持不变；无新增数据库迁移。
- 画布媒体回归 `4 passed`；管理端构建、JavaScript 语法和 `git diff --check` 通过。
- 源码标签建议为 `v1.0.16.1`（V36 供应链修订）；生产部署结果待补充发布镜像摘要和验收证据。

## V1.0.13 / V33 已部署

- 基于已部署 V32 `893376e`，分支 `codex/v33-progressive-workspace-loading`，未从旧目录、旧 ZIP、V17、干净 `main` 或其他 worktree 覆盖。
- 无限画布列表先展示画布元数据，再以 4 路受控并发回填 `/thumbnail?size=512`；生成中仅每 15 秒刷新仍在生成的画布。
- 画布编辑器先渲染保存的节点、连线和视口，后台恢复任务与媒体；会话快照只存稳定 JSON，重进后后台校验并合并结果。
- 图片生成页先展示任务摘要，历史成功结果按 3 路受控并发加载缩略图；活动任务保持既有 SSE 与受控轮询。
- 无新增数据库迁移；不重提上游、不重复扣费/退款、不改额度流水，不删除历史画布或媒体。
- 镜像：`creative-studio:single-host-candidate-v33@sha256:67e3640ed3a4fea16583d9a7632df82ef36892622773ad64f7c6588eda16801b`；回滚镜像 `creative-studio:single-host-rollback-v32@sha256:a0be4436d537ae85678bc931faecd288b5330d382711b4e662175abf18dcc9d0` 已保留。
- 部署时间：`2026-08-26T02:23:39Z`；Web 与 10 个 Worker 使用同一摘要，容器数量 11。
- 部署后验证：`/healthz` 和 `/readyz` 通过，迁移 head 为 `0066_prompt_safety_risk_events`。
- 验证：完整后端 `647 passed, 5 skipped`，另有一个 V30 既有 `admin-vue-8` 静态缓存断言失败；V33 渐进测试 `2 passed`，定向 `330 passed, 1 skipped`，前端构建、JS/Python 语法和 `git diff --check` 通过。

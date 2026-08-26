# 生产部署基线

生产源码基线不是当前 `main` 工作区。当前 `main` 保留了历史未提交改动，不能直接用于构建镜像或覆盖生产文件。

截至 2026-08-26，当前生产基线为：

- 运行镜像：`creative-studio:single-host-candidate-v32`
- 运行镜像摘要：`sha256:a0be4436d537ae85678bc931faecd288b5330d382711b4e662175abf18dcc9d0`
- 回滚镜像：`creative-studio:single-host-rollback-v32`
- 回滚镜像摘要：与 V32 运行镜像相同的不可变 V32 回滚副本
- Compose：`/opt/infinite-canvas/compose.production.yml`
- 环境文件：`/etc/infinite-canvas/single-host.env`
- Generation Worker：10 个

V28 及后续候选必须直接从当前生产镜像 V27（或服务器核实到的更新生产镜像）创建，先复制为不可变回滚标签，再叠加本次小范围补丁。不得重新从 V17、旧工作树或当前 `main` 的干净提交直接覆盖生产。

V32 于 2026-08-26 部署完成：Web 与 10 个 Generation Worker 使用同一镜像；数据库迁移为 `0066_prompt_safety_risk_events`；回环 `/healthz`、`/readyz` 均通过。

## V28 候选（未部署）

- 候选分支：`codex/v28-image-delivery-canvas-interactions`
- 源码标签建议：`v1.0.8`
- 镜像建议：`creative-studio:single-host-candidate-v28`
- 主要修复：隔离 Worker advisory-lock 连接与业务连接池；加固图片页/智能画布的 SSE、受控轮询、媒体恢复和去重；精简 smart-only 新建界面并修复 CSP 按钮绑定。
- 数据库迁移：无新增迁移；生产 head 仍为 `0065_merge_v17_redeem_concurrency`。
- 验证：后端 `641 passed, 5 skipped`，Web UI `97 passed`，生成链定向 `141 passed`，连接池/Worker/交付定向 `72 passed`，前端生产构建及语法/差异检查通过。
- 部署状态：未构建或切换生产容器，生产仍为上述 V27 镜像与摘要，V26 回滚镜像继续保留。

## V31 已部署

- 基线：V30 `d87f23e`（标签 `v1.0.10`），分支 `codex/v31-prompt-safety-risk-monitoring`。
- 功能：管理员违规关键词配置/TXT 导入与生图前拦截；脱敏运行风险事件及连续 10 次失败告警；管理员任务历史时间窗口分页监管。
- 数据库迁移：`0066_prompt_safety_risk_events`，向后兼容，不删除历史任务、媒体或额度流水。
- 镜像：`creative-studio:single-host-candidate-v31`，摘要 `sha256:d5a438bed458cc532c63f7b92cc3e6bf1e203bc1f312e08b379c8ab18d4fa253`；回滚镜像 `creative-studio:single-host-rollback-v31`，摘要 `sha256:046baa8e8595a574517fc52aacceb1871d3dae26e3685930275c377fc67cc70`。
- 验证：V31 定向后端、新增功能测试和前端构建通过；迁移 head、Web/Worker 镜像一致性、`/healthz`、`/readyz` 均通过；完整后端 `645 passed, 5 skipped`，另有 1 个 V30 既有 `admin-vue-8` 静态缓存断言失败。

## V32 已部署

- 基线：V31 `8db252e`（标签 `v1.0.11`），分支 `codex/v32-canvas-result-recovery-admin-prompt-redaction`。
- 功能：画布和图片生成页在成功终态后有界等待媒体入库，缩略图失败回退鉴权原图，SSE 断开使用受控轮询；管理员任务明细不返回或显示用户实际提示词。
- 数据库迁移：保持 `0066_prompt_safety_risk_events`，无新增迁移，不删除历史任务、媒体、提示词或额度流水。
- 源码提交：`893376e`。
- 镜像：`creative-studio:single-host-candidate-v32@sha256:a0be4436d537ae85678bc931faecd288b5330d382711b4e662175abf18dcc9d0`。
- 回滚镜像：`creative-studio:single-host-rollback-v32`（由当前 V31 运行 Web 容器保留）。
- 验证：部署脚本迁移、健康、就绪和 Web/10 Worker 镜像一致性检查通过；运行容器数量 11，`/healthz` 与 `/readyz` 均返回正常。

## V1.0.13 / V33 已部署

- 基线：已部署 V32 `893376e`，分支 `codex/v33-progressive-workspace-loading`。
- 功能：画布列表元数据与缩略图渐进加载；编辑器保存文档先渲染、任务和媒体后台恢复；会话快照支持快速重进；图片页任务摘要先出、历史缩略图受控恢复。
- 数据库迁移：无新增迁移，生产 head 保持 `0066_prompt_safety_risk_events`。
- 数据安全：不重提上游任务、不重复扣费/退款、不改额度流水；不删除媒体或历史画布；原图仍只在放大、编辑或下载时读取。
- 镜像：`creative-studio:single-host-candidate-v33@sha256:67e3640ed3a4fea16583d82ef36892622773ad64f7c6588eda16801b`。
- 回滚镜像：`creative-studio:single-host-rollback-v32@sha256:a0be4436d537ae85678bc931faecd288b5330d382711b4e662175abf18dcc9d0`，部署前已保留，失败自动恢复 V32。
- 部署时间：`2026-08-26T02:23:39Z`。
- 部署后 Web 与 10 个 Worker 使用同一 V33 摘要；运行容器数量 11。
- 验证：后端 `647 passed, 5 skipped`，其中 1 个 V30 既有 `admin-vue-8` 静态缓存断言失败；V33 渐进测试 `2 passed`，任务/画布/媒体/SSE 定向 `330 passed, 1 skipped`，前端构建、JS/Python 语法及 `git diff --check` 通过。

后续部署必须遵循：

1. 先在服务器核实当前 Web 与 Worker 的镜像标签/摘要，并从该生产镜像创建下一候选。
2. 只提交可审计的小范围补丁，禁止用当前 `main` 的整文件覆盖生产。
3. 候选必须通过完整测试、容器启动与 `/healthz`、`/readyz` 检查后才切换。
4. 切换前保留上一版本不可变回滚标签，并核对 Web 与所有 Generation Worker 使用同一镜像摘要。
5. 每次部署后更新本文中的运行镜像、回滚镜像、静态资源版本和受保护行为清单。

当前必须保留的受保护行为：

- 经典画布不再创建或作为可用入口展示；历史 classic 数据与 API 兼容读取保留，不做自动删除。
- 画布默认读取持久缩略图，点击放大、编辑或下载时才加载单张原图。
- 图片生成和局部重绘页面不被账户数据请求阻塞，SPA 切换时保留近期图片状态。
- 图片生成结果一有可用媒体即显示，支持 `Ctrl + Enter` 提交。
- 兑换码系统、管理员管理入口和余额兑换流程保留。
- 全局生成容量可扩展到 500，管理员可统一修改，单用户执行并发上限为 50。
- 页面导航旧请求隔离、账户摘要本地缓存、画布列表本地缓存与请求超时保护保留。
- 智能画布编辑按钮 CSP 事件桥修复保留；4K 正方形统一为 `2880x2880`。

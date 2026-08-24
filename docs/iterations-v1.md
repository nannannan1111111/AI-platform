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

# V1 验证与发布门禁

Type: task
Status: resolved

## Verification plan

- `test_canvases_http.py`、`test_generation_http.py` 和实时 Web UI 契约测试。
- `node --check` 检查画布脚本。
- 管理前端生产构建。
- `git diff --check`。
- 全量 pytest 仅记录真实阻塞：当前迁移链引用缺失的 `0064_recharge_order_expiration`，以及 V26 已存在的静态版本/环境断言；未经 V26 镜像或生产源码核实不补造迁移。

## Release gate

本地 V1 门禁已完成，当前进入 V27 候选准备阶段。服务器已只读核实 Web 与 10 个 Generation Worker 统一使用 V26，摘要为 `sha256:22e90333d05683b378bf5b0766b26ad44bc4ae025df3bfb2f7afb92122c02427`。生成候选前必须核对部署脚本实际使用的 `/etc/infinite-canvas/single-host.env`，保留 V26 回滚标签；候选健康检查通过后仍需用户明确授权才切换生产流量。

## Answer

- 画布与生成定向测试通过：`43 passed`；Web UI 定向测试通过：`93 passed`（含实时状态与历史 classic 兼容契约）。
- 管理前端 `npm run build` 通过；`node --check`、Python `compileall` 和 `git diff --check` 通过。
- 后端全量测试为 `632 passed, 5 skipped`；仅保留依赖环境的弃用告警。
- V1 虚拟环境的 editable 路径默认指向旧目录，验证命令显式设置 `PYTHONPATH` 指向 V1，避免误测原目录。

## Comments

- 2026-08-24：提交 `cfa5d2c`（`v1.0.2`）对齐当前 V1 静态资源版本、任务接口和按需原图加载契约；未修改生产流量。

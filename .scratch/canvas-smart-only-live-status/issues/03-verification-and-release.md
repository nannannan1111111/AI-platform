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

本次只提交 V1 本地迭代，不生成 V27、不切换镜像、不操作服务器生产环境。下次部署必须从 V26 核实 Web/Worker 镜像摘要后生成候选并保留 V26 回滚标签。

## Answer

- 画布与生成定向测试通过：`43 passed`；Web UI 本次契约/入口测试通过：`13 passed`（含新增实时状态契约）。
- 管理前端 `npm run build` 通过；`node --check` 和 `git diff --check` 通过。
- 全量后端（排除 Web UI）为 `490 passed, 49 failed, 5 skipped`。49 个失败由 V26 基线已有的缺失 `0064_recharge_order_expiration` 迁移引用（级联影响 SQLAlchemy/Alembic 测试）、既有并发范围断言和生产配置环境断言组成；本次未凭猜测补造迁移。
- V1 虚拟环境的 editable 路径默认指向旧目录，验证命令显式设置 `PYTHONPATH` 指向 V1，避免误测原目录。

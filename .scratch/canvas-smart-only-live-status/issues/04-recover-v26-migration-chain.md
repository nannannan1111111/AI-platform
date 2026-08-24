# 恢复 V26 真实迁移链

Type: research
Status: resolved

## Findings

- 当前 V1 的 `0065_merge_v17_redeem_concurrency.py` 声明父迁移为 `0064_recharge_order_expiration` 和 `0063_account_generation_concurrency_50`。
- V1 和原目录均没有 `0064_recharge_order_expiration.py`。
- Git refs、Git unreachable blobs、本地部署 staging 快照均未找到与当前链匹配的 `0064`。
- 一个旧部署 worktree/tar 中存在同名文件，但它依赖另一条 `0062_generation_timeout_headroom -> 0063_generated_media_thumbnails` 链，并配套订单过期字段；不能直接复制到当前 V1，也不能据此覆盖当前代码。

## Required evidence

从 V26 生产镜像或服务器源码只读核对以下内容后才能修改：

1. `0064_recharge_order_expiration.py` 的完整内容和父 revision。
2. 生产数据库当前 `alembic_version`。
3. V26 镜像内完整 `backend/alembic/versions` 列表。

在证据到位前，不新增猜测迁移、不改写 `0065` 父链、不执行生产迁移。

## Comments

2026-08-24：在 V1 本地复现 `alembic heads` 和
`test_provider_cost_http.py::test_provider_cost_publication_maps_an_unknown_route_to_404`，均因
`0065_merge_v17_redeem_concurrency.py` 引用缺失的 `0064_recharge_order_expiration` 而失败。
该错误发生在 Alembic 构建 revision map 阶段，尚未执行任何迁移 SQL；因此当前批量失败属于同一迁移链阻塞的级联结果。

2026-08-24：经腾讯云生产 Web 容器只读核验，V26 镜像为 `creative-studio:single-host-candidate-v26`，生产数据库当前版本为
`0065_merge_v17_redeem_concurrency`。V26 实际包含并已核对完整内容的迁移分支：
`0062_generation_timeout_headroom` → `0063_generated_media_thumbnails` →
`0064_recharge_order_expiration`。本地 V1 已按该真实分支补回三个迁移文件，并补齐订单持久化对新增非空 `expires_at` 字段的写入。

## Answer

已在 V1 本地补回 V26 真实迁移链，并验证 `alembic heads` 为
`0065_merge_v17_redeem_concurrency`；临时 SQLite 数据库可从 base 升级到 head。SQLAlchemy/Alembic 级联失败已解除。

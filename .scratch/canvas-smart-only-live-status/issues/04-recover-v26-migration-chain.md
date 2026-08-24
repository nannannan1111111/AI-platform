# 恢复 V26 真实迁移链

Type: research
Status: claimed

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

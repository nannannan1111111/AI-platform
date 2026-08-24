# 修复供应链门禁中的 Pillow 高危漏洞

Type: task
Status: resolved

## 问题

V1.0.3 标签触发的供应链工作流 `32701074142` 在 Trivy 阶段失败。报告显示 Pillow `11.3.0` 命中 13 个 High CVE，修复版本从 `12.1.1` 到 `12.3.0` 不等。

## 处理

- 将 `backend/pyproject.toml` 约束升级为 `pillow>=12.3,<13`。
- 使用项目锁生成脚本重新生成 `backend/requirements.lock` 和 `backend/requirements-dev.lock`，保留完整哈希。
- Pillow `12.3.0` 下媒体缩略图定向测试通过。

## 验证

- `632 passed, 5 skipped`（显式设置 V1 `PYTHONPATH`）。
- `compile-python-locks.py --check`、管理前端构建、JS/Python 语法检查和 `git diff --check` 通过。
- `v1.0.5` 供应链工作流 `32703818088` 已全绿：锁复现、镜像构建、SBOM/Trivy、Cosign 签名和 `supply-chain-gate` 均通过。
- GHCR 候选摘要：`sha256:01909153b221e20093553b4cd27f70a28f1fdcea2a549981a036cdf7aa11e125`。
- 未修改生产环境；服务器隔离 smoke 仅验证到 `/healthz`，临时容器/卷已清理，`/readyz` 因未执行生产 Compose 迁移而返回 503。

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
- 待提交新版本标签后重新运行供应链门禁；未修改生产环境。

# GitHub Actions 运行时升级

## 背景

GitHub Actions 正在从 Node 20 运行时迁移到更新的 Node 版本。仓库中的多个官方 Action 仍使用旧版本，继续保留会产生弃用告警，并增加未来流水线被平台强制迁移的风险。

## 目标

升级仓库工作流中的第三方 Actions 到 Dependabot 提供的兼容版本，同时保持现有触发矩阵、质量门禁、供应链扫描和 Tag 签名发布语义不变。

## 阶段与退出标准

1. **分析问题**：确认旧 Action 版本、受影响 workflow 和现有 Node 运行时风险。
2. **设计方案**：优先审查 Dependabot PR，使用固定 commit SHA，保留既有工作流合同。
3. **修改代码**：合并 Dependabot PR #4，升级 10 个 GitHub Actions 依赖。
4. **质量检测**：确认 PR 检查全绿，并验证合并后的 `main` 两条流水线成功。

## 任务索引

| 编号 | 任务 | 依赖 |
| --- | --- | --- |
| 01 | 升级 GitHub Actions 运行时依赖 | 无 |


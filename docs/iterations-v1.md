# V1 本地迭代记录

## 版本边界

- 目录：`E:\豌豆工坊-SaaS-完整版-20260811-V1`
- 基于当前工作源码建立，未从旧 zip、旧 worktree 或干净 `main` 重建。
- 基线分支：`codex/v1-baseline`
- 基线提交：`9967387 chore: establish V1 baseline from current working source`
- 本次功能提交：在基线之上叠加经典画布下线和画布生图实时状态补丁。
- 生产仍保持 V26：`creative-studio:single-host-candidate-v26`；本次未部署。

## 迭代门禁

1. 在 V1 目录查看 `git status`、基线提交和 V26 部署基线。
2. 为需求建立 `.scratch/canvas-smart-only-live-status/` 规格和逐项 issue。
3. 只修改本次涉及的接口、画布脚本、静态壳、管理文案和测试。
4. 先跑定向测试、语法检查、前端构建与 `git diff --check`，再决定是否提交。
5. 提交后保留可回滚 commit；部署前必须从服务器核实 V26 Web/Worker 镜像摘要并生成下一候选，不能直接把本地 V1 当生产版本。

## 数据保护

历史 `classic` 画布只改变入口和展示状态，不删除记录或其中的图片/参考图/生成记录；任务和额度流水沿用原有保留策略。

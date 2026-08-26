# V34 图片页顺序恢复与画布媒体显示

## 基线

- 基于已部署 V33，源码实现提交 `a0e76e1`，部署记录提交 `489392c`。
- V33 生产镜像为 `creative-studio:single-host-candidate-v33`，数据库 head 为 `0066_prompt_safety_risk_events`。

## 目标

1. 图片生成页从最新任务/最新图片开始，逐个向后恢复并渲染，避免旧结果阻塞或抢占首屏。
2. 画布中的 SaaS 生成图片、上传图片及历史媒体统一补齐可显示的缩略图地址；鉴权缩略图失败时保留受控原图回退，不改变媒体归属和持久化。

## 约束

- 不重新创建上游任务，不重复扣费、退款或写额度流水。
- 不删除历史画布、图片、参考图或生成记录。
- 保留原图仅在放大、编辑或下载时读取的策略。
- 无新增数据库迁移。

## 验证

- 图片页顺序恢复、画布媒体 URL 归一化定向测试。
- V33 既有任务/媒体/SSE/画布测试、JS/Python 语法、前端构建和 `git diff --check`。

## 发布

- V34 源码提交：`18e1cb0`，标签：`v1.0.14`。
- 生产镜像：`creative-studio:single-host-candidate-v34@sha256:f3ae5b7fb609dd8baeeeaa2522b70a5df6891b47d592acc6cc63dd3ef7f89fe2`。
- 回滚镜像：`creative-studio:single-host-rollback-v33@sha256:67e3640ed3a4fea16583d9a7632df82ef36892622773ad64f7c6588eda16801b`。
- 部署时间：`2026-08-26T04:26:08Z`；Web/10 Worker 一致性、迁移 head、健康和就绪检查通过。

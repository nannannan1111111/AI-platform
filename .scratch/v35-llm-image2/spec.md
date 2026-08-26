# V35 LLM 返回兼容与 image2 重绘

## 基线

- 基于已部署 V34，当前源码分支为 `codex/v34-image-order-canvas-display`。
- V34 生产镜像为 `creative-studio:single-host-candidate-v34`，数据库 head 为 `0066_prompt_safety_risk_events`。
- 本次只增加兼容性补丁，不新增数据库迁移，不覆盖 V34 画布缩略图和原图交互行为。

## 目标

1. LLM 请求在上游已成功返回、但返回体采用常见兼容格式时，正确提取并返回文本，不重复发起请求。
2. 放宽重绘对 `image2`、`image-2`、`gptimage2` 及安全的前后缀模型名判断，避免误拦截合法模型，同时继续拒绝 `image21`。

## 约束

- 使用用户配置的 Provider 和密钥，保持上游调用次数为一次。
- 不重复扣费、退款或写额度流水；不改数据库结构和历史媒体。
- 后端校验、图片 Provider 流式判断和前端档位判断保持一致。
- 继续保留 V34 的画布缩略图优先、最多 4 并发、点击编辑下载使用原图行为。

## 验证

- LLM API 测试覆盖字符串、内容数组、顶层 `output_text` 和旧版 `choices[].text`。
- 重绘测试覆盖 `image2` 别名及 `image21` 负例。
- 现有生成、Provider、前端 JS 语法测试和 `git diff --check`。

## 发布

- V35 源码标签：`v1.0.15`。
- 建议镜像：`creative-studio:single-host-candidate-v35`。
- 部署后补充实际镜像 digest、回滚 digest、部署时间、健康/就绪、迁移 head 和 Web/10 Worker 一致性。

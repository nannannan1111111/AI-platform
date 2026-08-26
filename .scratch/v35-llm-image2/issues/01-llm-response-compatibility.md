# LLM 返回值兼容

Type: task
Status: resolved

## 问题

上游已完成扣费并返回结果时，接口只读取 `choices[0].message.content` 字符串，导致内容数组、`output_text` 或旧版 `choices[].text` 被误报为格式无效。

## 验收

- 一次请求成功后，兼容格式均返回 `{"text": ...}`。
- 不因解析失败重试或重复调用上游。
- Provider、模型和 API Key 仍按当前用户账户隔离。

## Answer

Resolved in V35. Added one-pass extraction for common OpenAI-compatible completion payloads and regression coverage for array content, top-level `output_text`, and legacy text completions.

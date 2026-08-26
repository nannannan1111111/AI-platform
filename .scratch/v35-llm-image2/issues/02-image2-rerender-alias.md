# image2 重绘判断

Type: task
Status: resolved

## 问题

局部重绘仅识别完整的 `gpt-image-2` 变体，部分上游模型命名为 `image2` 或 `gptimage2` 时被错误拒绝。

## 验收

- `image2`、`image-2`、`gptimage2` 和带安全前后缀的变体可以提交局部重绘。
- `image21` 不被误识别为 `image2`。
- 后端、Provider 和前端判断保持一致。

## Answer

Resolved in V35. The normalized token matcher accepts the requested image2 aliases while requiring a token boundary around version 2.

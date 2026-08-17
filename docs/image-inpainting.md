# 局部重绘接口约定

## 统一业务参数

| 参数 | 类型 | 约束 | 用途 |
| --- | --- | --- | --- |
| `operation` | `generate \| edit \| inpaint` | 新入口显式传递；旧入口可省略为 `auto` | 区分生成、整图编辑和局部重绘 |
| `prompt` | string | 必填 | 描述编辑后的完整目标画面 |
| `reference_media_ids` | string[] | 局部重绘至少 1 张；与遮罩合计最多 3 张 | 第 1 张为遮罩对应的原图，其余为上下文参考图 |
| `mask_media_id` | string | 局部重绘必填；必须为 PNG | 透明区域重绘，非透明区域保留 |
| `input_fidelity` | `auto \| low \| high` | 仅编辑和局部重绘可用 | 控制输入图像细节保真度 |
| `quality` | `auto \| low \| medium \| high` | 复用现有生成约束 | 输出质量 |
| `size` / `resolution_tier` / `aspect_ratio` | string | 复用现有尺寸换算 | 输出尺寸 |
| `output_format` | `png \| jpeg \| webp` | 可选 | 输出格式 |
| `quantity` | integer | 1–5 | 输出数量；适配器按独立请求并发 |

局部重绘还要求逻辑模型属于 `gpt-image-2` 系列，遮罩尺寸与第 1 张原图完全一致。

## 接口映射

| 场景 | SaaS 请求 | 上游端点 | 上游编码 |
| --- | --- | --- | --- |
| 图片生成 | 无参考图、无遮罩 | `/images/generations` | JSON；`model`、`prompt`、`size`、`n`、质量和格式 |
| 图片编辑 | 有参考图、无���罩 | `/images/edits` | multipart；重复的 `image` 文件字段和文本参数 |
| 局部重绘 | 有原图和遮罩 | `/images/edits` | multipart；第 1 个 `image` 是原图、可追加参考图，`mask` 单独上传，传递 `input_fidelity` |

智能画布的遮罩画笔导出透明 PNG：被画笔选中的像素 alpha 为 0，未选中的像素 alpha 为 255。图片工作台上传的遮罩也遵循同一约定。

"""生成任务请求快照的共享校验。"""

import re
from dataclasses import replace

from app.generation.models import GenerationParameters, GenerationSubmission, InvalidGenerationRequest

_LEGACY_ASPECT_RATIOS = frozenset({"1:1", "16:9", "9:16"})
_ALLOWED_QUALITIES = frozenset({"auto", "low", "medium", "high"})
_ALLOWED_OUTPUT_FORMATS = frozenset({"png", "jpeg", "webp"})
_ALLOWED_OPERATIONS = frozenset({"auto", "generate", "edit", "inpaint"})
_ALLOWED_INPUT_FIDELITIES = frozenset({"auto", "low", "high"})
MAX_GENERATION_QUANTITY = 5
_RESOLUTION_TIER_SIZES = {
    ("1k", "1:1"): "1024x1024",
    ("1k", "4:3"): "1024x768",
    ("1k", "16:9"): "1280x720",
    ("1k", "3:4"): "768x1024",
    ("1k", "9:16"): "720x1280",
    ("2k", "1:1"): "2048x2048",
    ("2k", "4:3"): "2048x1536",
    ("2k", "16:9"): "2048x1152",
    ("2k", "3:4"): "1536x2048",
    ("2k", "9:16"): "1152x2048",
    ("4k", "1:1"): "2880x2880",
    ("4k", "4:3"): "3264x2448",
    ("4k", "16:9"): "3840x2160",
    ("4k", "3:4"): "2448x3264",
    ("4k", "9:16"): "2160x3840",
}
_OPENAI_IMAGE_SIZE_ASPECT_RATIOS = {
    "1024x1024": "1:1",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
    "2048x1152": "16:9",
    "2048x2048": "1:1",
}
_CUSTOM_SIZE_PATTERN = re.compile(r"^(\d+)x(\d+)$")


def _validated_custom_size(size: str) -> str:
    match = _CUSTOM_SIZE_PATTERN.fullmatch(size)
    if match is None:
        raise InvalidGenerationRequest("自定义像素尺寸不对，请修改！")
    width, height = (int(value) for value in match.groups())
    if any(value < 256 or value > 8192 or value % 16 for value in (width, height)):
        raise InvalidGenerationRequest("自定义像素尺寸不对，请修改！")
    return f"{width}x{height}"


def validated_submission(submission: GenerationSubmission) -> GenerationSubmission:
    """保留原始提示词，并规范化当前支持的成品比例。"""
    if not submission.prompt.strip():
        raise InvalidGenerationRequest("提示词不能为空")
    if isinstance(submission.quantity, bool) or not 1 <= submission.quantity <= MAX_GENERATION_QUANTITY:
        raise InvalidGenerationRequest("单次最多生成 5 张图片")
    if not isinstance(submission.params, GenerationParameters):
        raise InvalidGenerationRequest("生成参数无效")
    aspect_ratio = submission.params.aspect_ratio.strip()
    quality = submission.params.quality.strip().lower()
    if quality not in _ALLOWED_QUALITIES:
        raise InvalidGenerationRequest("暂不支持该图片质量")
    resolution_tier = submission.params.resolution_tier.strip().lower()
    output_format = submission.params.output_format.strip().lower()
    operation = submission.params.operation.strip().lower()
    input_fidelity = submission.params.input_fidelity.strip().lower()
    if operation not in _ALLOWED_OPERATIONS:
        raise InvalidGenerationRequest("暂不支持该图片操作")
    if input_fidelity not in _ALLOWED_INPUT_FIDELITIES:
        raise InvalidGenerationRequest("暂不支持该输入保真度")
    size = submission.params.size.strip().lower()
    if resolution_tier:
        expected_size = _RESOLUTION_TIER_SIZES.get((resolution_tier, aspect_ratio))
        if expected_size is None:
            raise InvalidGenerationRequest("暂不支持该输出档位和比例")
        if quality != "auto":
            raise InvalidGenerationRequest("输出档位任务不支持自定义图片质量")
        if output_format not in _ALLOWED_OUTPUT_FORMATS:
            raise InvalidGenerationRequest("暂不支持该图片格式")
        if size and size != expected_size:
            raise InvalidGenerationRequest("图片尺寸与输出档位不一致")
        size = expected_size
    elif aspect_ratio == "custom":
        size = _validated_custom_size(size)
    elif size:
        expected_aspect_ratio = _OPENAI_IMAGE_SIZE_ASPECT_RATIOS.get(size)
        if expected_aspect_ratio is None:
            raise InvalidGenerationRequest("暂不支持该图片尺寸")
        if aspect_ratio != expected_aspect_ratio:
            raise InvalidGenerationRequest("图片尺寸与成品比例不一致")
    elif aspect_ratio not in _LEGACY_ASPECT_RATIOS:
        raise InvalidGenerationRequest("暂不支持该成品比例")
    if output_format and output_format not in _ALLOWED_OUTPUT_FORMATS:
        raise InvalidGenerationRequest("暂不支持该图片格式")
    reference_media_ids = tuple(media_id.strip() for media_id in submission.reference_media_ids)
    mask_media_id = submission.mask_media_id.strip()
    if (
        len(reference_media_ids) > 16
        or any(not media_id for media_id in reference_media_ids)
        or len(set(reference_media_ids)) != len(reference_media_ids)
        or (mask_media_id and not reference_media_ids)
        or mask_media_id in reference_media_ids
    ):
        raise InvalidGenerationRequest("参考图片标识无效")
    inferred_operation = "inpaint" if mask_media_id else "edit" if reference_media_ids else "generate"
    if operation != "auto" and operation != inferred_operation:
        raise InvalidGenerationRequest("图片操作与参考图或遮罩不匹配")
    if inferred_operation == "generate" and input_fidelity != "auto":
        raise InvalidGenerationRequest("图片生成不支持输入保真度")
    if inferred_operation == "inpaint" and not _is_gpt_image_2(submission.logical_model):
        raise InvalidGenerationRequest("局部重绘当前仅支持 gpt-image-2")
    return replace(
        submission,
        params=GenerationParameters(
            aspect_ratio=aspect_ratio,
            quality=quality,
            size=size,
            resolution_tier=resolution_tier,
            output_format=output_format,
            operation=operation,
            input_fidelity=input_fidelity,
        ),
        reference_media_ids=reference_media_ids,
        mask_media_id=mask_media_id,
    )


def _is_gpt_image_2(model: str) -> bool:
    normalized = model.strip().lower().replace("_", "-")
    return (
        normalized == "gpt-image-2"
        or normalized.startswith("gpt-image-2-")
        or normalized.endswith("-gpt-image-2")
        or "-gpt-image-2-" in normalized
    )

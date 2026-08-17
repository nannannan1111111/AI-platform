"""Shared prompt-asset seed data and validation helpers."""

from __future__ import annotations

from app.prompt_assets.models import InvalidPromptAsset, PromptItemSave

DEFAULT_CATEGORIES = [
    {"id": "view", "name": "视角"},
    {"id": "storyboard", "name": "分镜"},
    {"id": "character", "name": "角色"},
    {"id": "product", "name": "产品"},
    {"id": "lighting", "name": "光影"},
    {"id": "custom", "name": "我的"},
]
DEFAULT_PROMPTS = [
    {"id": "builtin-cinematic-portrait", "name": "电影感人像", "category": "character", "scene": "人物写真", "positive": "电影感人像摄影，自然皮肤质感，浅景深，柔和轮廓光，细腻眼神，高级色彩分级", "negative": "低清晰度，畸形五官，过度磨皮，杂乱背景", "params": {}},
    {"id": "builtin-product-studio", "name": "商业产品摄影", "category": "product", "scene": "电商与广告", "positive": "专业商业产品摄影，干净背景，柔和棚拍光，清晰材质细节，精致构图，高端广告质感", "negative": "文字水印，脏污背景，过曝，产品变形", "params": {}},
    {"id": "builtin-storyboard-wide", "name": "电影分镜远景", "category": "storyboard", "scene": "故事分镜", "positive": "电影分镜，大全景，明确主体与环境关系，强叙事构图，具有景深层次，电影级光影", "negative": "主体不明确，构图拥挤，透视错误", "params": {}},
    {"id": "builtin-golden-hour", "name": "黄金时刻光影", "category": "lighting", "scene": "自然光场景", "positive": "黄金时刻，温暖低角度阳光，柔和长阴影，空气透视，金色轮廓光，真实摄影质感", "negative": "死黑阴影，刺眼高光，色彩断层", "params": {}},
    {"id": "builtin-top-view", "name": "俯拍构图", "category": "view", "scene": "静物与空间", "positive": "正上方俯拍视角，平面构成，主体排列有序，留白均衡，清晰细节，杂志编辑风格", "negative": "倾斜透视，画面杂乱，主体被裁切", "params": {}},
]


def clean_name(value: str) -> str:
    """Normalize and validate a display name."""
    value = value.strip()
    if not value or len(value) > 120:
        raise InvalidPromptAsset("名称无效")
    return value


def clean_prompt(command: PromptItemSave) -> dict[str, object]:
    """Normalize and validate a prompt-item command."""
    positive = command.positive.strip()
    if not positive or len(positive) > 20_000:
        raise InvalidPromptAsset("提示词内容无效")
    return {
        "name": clean_name(command.name),
        "positive": positive,
        "negative": command.negative.strip()[:20_000],
        "category": command.category.strip()[:64] or "custom",
        "scene": command.scene.strip()[:500],
        "params": command.params if isinstance(command.params, dict) else {},
    }

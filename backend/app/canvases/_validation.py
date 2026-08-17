"""Canvases Adapter 共享的领域输入校验。"""

from copy import deepcopy
from typing import Any

from app.canvases.models import CanvasKind, InvalidCanvas


def validated_title(value: str) -> str:
    """返回规范化且可持久化的画布标题。"""
    title = value.strip() or "未命名画布"
    if len(title) > 80:
        raise InvalidCanvas("画布标题不能超过 80 个字符")
    return title


def validated_kind(value: str) -> CanvasKind:
    """返回首版支持的画布类型。"""
    try:
        return CanvasKind(value)
    except ValueError as exc:
        raise InvalidCanvas("画布类型无效") from exc


def validated_document(document: dict[str, Any]) -> dict[str, Any]:
    """校验稳定顶层结构并保留所有节点字段。"""
    if (
        not isinstance(document.get("nodes"), list)
        or not isinstance(document.get("connections"), list)
        or not isinstance(document.get("viewport"), dict)
    ):
        raise InvalidCanvas("画布文档结构无效")
    return deepcopy(document)

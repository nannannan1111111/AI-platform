"""SaaS 画布 Module 的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class CanvasKind(StrEnum):
    """首版支持的画布类型。"""

    CLASSIC = "classic"
    SMART = "smart"


class CanvasNotFound(LookupError):
    """画布不存在或不属于请求的个人账户空间。"""


class InvalidCanvas(ValueError):
    """画布参数或文档结构无效。"""


class CanvasVersionConflict(ValueError):
    """保存请求基于过期的画布版本。"""


@dataclass(frozen=True, slots=True)
class CanvasCreation:
    """一次由登录用户发起的空画布创建请求。"""

    user_id: str
    account_space_id: str
    title: str
    kind: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CanvasSave:
    """一次带预期版本的画布文档保存请求。"""

    account_space_id: str
    canvas_id: str
    expected_version: int
    document: dict[str, Any]
    saved_at: datetime
    title: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasDeletion:
    """一次由画布所有者确认的不可恢复删除。"""

    account_space_id: str
    canvas_id: str
    deleted_at: datetime


@dataclass(frozen=True, slots=True)
class Canvas:
    """归属明确且带乐观版本的持久画布快照。"""

    canvas_id: str
    user_id: str
    account_space_id: str
    title: str
    kind: CanvasKind
    document: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime

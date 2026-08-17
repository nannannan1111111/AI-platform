"""临时生成媒体 Module 的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GeneratedMediaKind(StrEnum):
    """生成渠道可交付的媒体类别。"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class GeneratedMediaState(StrEnum):
    """生成媒体生命周期状态。"""

    TEMPORARY = "temporary"
    EXPIRED = "expired"
    PERSISTENT = "persistent"
    RELEASED = "released"
    DELETED = "deleted"


class GeneratedMediaNotFound(LookupError):
    """媒体不存在或不属于请求的个人账户空间。"""


class GeneratedMediaConflict(ValueError):
    """同一任务结果引用已经登记为不同媒体。"""


class InvalidGeneratedMedia(ValueError):
    """生成媒体参数或任务归属无效。"""


class GeneratedMediaNotRetainable(ValueError):
    """媒体已经过期或释放，不能再转为持久媒体。"""


class GeneratedMediaNotDeletable(ValueError):
    """媒体已有持久引用或不再可由工作区删除。"""


class StorageAllowanceExceeded(ValueError):
    """账户空间没有足够存储额度保留媒体。"""


@dataclass(frozen=True, slots=True)
class StorageAllowance:
    """个人账户空间的持久媒体存储额度快照。"""

    limit_bytes: int
    used_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class GeneratedMediaRegistration:
    """Worker 对一项已写入对象存储的生成结果进行登记。"""

    user_id: str
    account_space_id: str
    canvas_id: str | None
    task_id: str
    result_reference: str
    object_key: str
    kind: str
    mime_type: str
    size_bytes: int
    content_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CanvasMediaUpload:
    """Authenticated image bytes to retain immediately for one owned canvas."""

    user_id: str
    account_space_id: str
    canvas_id: str
    original_name: str
    declared_mime_type: str
    content: bytes
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GeneratedMediaRecord:
    """归属明确且受 24 小时生命周期管理的媒体元数据。"""

    media_id: str
    user_id: str
    account_space_id: str
    canvas_id: str | None
    task_id: str | None
    result_reference: str
    object_key: str
    kind: GeneratedMediaKind
    mime_type: str
    size_bytes: int
    content_hash: str
    state: GeneratedMediaState
    created_at: datetime
    expires_at: datetime | None
    retained_at: datetime | None = None
    released_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MediaExpirationReport:
    """一次到期清理的稳定结果。"""

    expired_media_ids: tuple[str, ...]
    failed_media_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MediaReferenceReconciliation:
    """一次画布媒体引用协调的稳定结果。"""

    released_media_ids: tuple[str, ...]
    failed_media_ids: tuple[str, ...]

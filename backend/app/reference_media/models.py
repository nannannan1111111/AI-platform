"""账户隔离临时参考媒体的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReferenceMediaState(StrEnum):
    """临时参考媒体当前生命周期状态。"""

    TEMPORARY = "temporary"
    EXPIRED = "expired"
    DELETED = "deleted"


class ReferenceMediaOrigin(StrEnum):
    """The product surface that created a temporary reference image."""

    STANDALONE = "standalone"
    CANVAS = "canvas"


class InvalidReferenceMedia(ValueError):
    """上传内容不是平台支持的安全图片。"""


class ReferenceMediaNotFound(LookupError):
    """参考媒体不存在或不属于当前账户空间。"""


class ReferenceMediaExpired(LookupError):
    """参考媒体已经到达 24 小时失效时间。"""


@dataclass(frozen=True, slots=True)
class ReferenceMediaUpload:
    """一项已经由认证 HTTP seam 接收的参考图片上传。"""

    user_id: str
    account_space_id: str
    original_name: str
    declared_mime_type: str
    content: bytes
    created_at: datetime
    origin: ReferenceMediaOrigin = ReferenceMediaOrigin.STANDALONE


@dataclass(frozen=True, slots=True)
class ReferenceMediaRecord:
    """归属明确且默认保留 24 小时的参考媒体元数据。"""

    media_id: str
    user_id: str
    account_space_id: str
    original_name: str
    object_key: str
    mime_type: str
    size_bytes: int
    content_hash: str
    state: ReferenceMediaState
    origin: ReferenceMediaOrigin
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReferenceMediaContent:
    """通过账户校验后交给预览或 Provider Adapter 的参考图片字节。"""

    media: ReferenceMediaRecord
    content: bytes

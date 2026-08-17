"""个人资产库 Module 的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime

from app.media import GeneratedMediaKind


class PersonalAssetConflict(ValueError):
    """同一幂等键已经保存了参数不同的个人资产。"""


class InvalidPersonalAsset(ValueError):
    """个人资产命令参数无效。"""


class PersonalAssetNotFound(LookupError):
    """个人资产不存在或不属于请求的个人账户空间。"""


@dataclass(frozen=True, slots=True)
class PersonalAssetSave:
    """一次把生成媒体保存到个人资产库的命令。"""

    user_id: str
    account_space_id: str
    media_id: str
    display_name: str
    idempotency_key: str
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalAssetRename:
    """一次修改个人资产显示名称的命令。"""

    account_space_id: str
    asset_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class PersonalAsset:
    """个人账户空间中可长期复用的一项媒体资产。"""

    asset_id: str
    user_id: str
    account_space_id: str
    media_id: str
    display_name: str
    kind: GeneratedMediaKind
    mime_type: str
    size_bytes: int
    created_at: datetime

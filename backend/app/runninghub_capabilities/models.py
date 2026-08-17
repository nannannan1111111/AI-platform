"""管理员发布的 RunningHub 能力目录模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunningHubInputCapability(StrEnum):
    """用户可见的粗粒度 RunningHub 输入能力。"""

    TEXT = "text"
    IMAGE = "image"


class InvalidRunningHubCapability(ValueError):
    """RunningHub 能力包含无效字段。"""


class RunningHubCapabilityNotFound(LookupError):
    """指定的 RunningHub 能力不存在。"""


class RunningHubUserPriceConflict(ValueError):
    """同一 RunningHub 能力在相同生效时间已经存在用户价格版本。"""


class RunningHubUserPriceNotFound(LookupError):
    """指定时刻没有已生效的 RunningHub 用户价格。"""


@dataclass(frozen=True, slots=True)
class RunningHubCapabilityPublication:
    """管理员发布一个 RunningHub 能力的命令。"""

    name: str
    workflow_id: str
    input_capabilities: tuple[RunningHubInputCapability, ...]
    available: bool


@dataclass(frozen=True, slots=True)
class RunningHubCapabilityUpdate:
    """管理员更新能力目录字段或可用状态的命令。"""

    capability_id: str
    name: str | None = None
    workflow_id: str | None = None
    input_capabilities: tuple[RunningHubInputCapability, ...] | None = None
    available: bool | None = None


@dataclass(frozen=True, slots=True)
class RunningHubCapabilityInput:
    """一个用户公开的 RunningHub 能力输入槽位。"""

    input_key: str
    label: str
    kind: RunningHubInputCapability
    required: bool


@dataclass(frozen=True, slots=True)
class RunningHubInputSchemaPublication:
    """管理员发布一个不可改写输入 schema 版本的命令。"""

    capability_id: str
    inputs: tuple[RunningHubCapabilityInput, ...]


@dataclass(frozen=True, slots=True)
class RunningHubInputSchemaVersion:
    """一个不可改写的 RunningHub 能力输入 schema 版本。"""

    schema_version_id: str
    capability_id: str
    version: int
    inputs: tuple[RunningHubCapabilityInput, ...]
    published_at: datetime


@dataclass(frozen=True, slots=True)
class RunningHubUserPricePublication:
    """管理员发布一个不可改写用户价格版本的命令。"""

    capability_id: str
    credits_per_run: str
    effective_from: datetime


@dataclass(frozen=True, slots=True)
class RunningHubUserPriceVersion:
    """一个按时间生效且不可改写的 RunningHub 用户价格版本。"""

    price_version_id: str
    capability_id: str
    version: int
    credits_per_run: str
    effective_from: datetime
    published_at: datetime


@dataclass(frozen=True, slots=True)
class PublicRunningHubInputSchema:
    """不包含工作流绑定的用户安全当前 schema。"""

    schema_version_id: str
    version: int
    inputs: tuple[RunningHubCapabilityInput, ...]


@dataclass(frozen=True, slots=True)
class RunningHubCapability:
    """包含内部工作流绑定的管理员能力快照。"""

    capability_id: str
    name: str
    workflow_id: str
    input_capabilities: tuple[RunningHubInputCapability, ...]
    available: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PublicRunningHubCapability:
    """不包含内部工作流绑定的用户安全目录项。"""

    capability_id: str
    name: str
    input_capabilities: tuple[RunningHubInputCapability, ...]
    available: bool
    input_schema: PublicRunningHubInputSchema | None = None
    credits_per_run: str | None = None

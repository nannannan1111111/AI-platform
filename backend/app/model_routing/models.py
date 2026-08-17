"""管理员拥有的 API 来源与模型路由公开模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProviderProtocol(StrEnum):
    """平台支持的上游协议。"""

    OPENAI_COMPATIBLE_IMAGES = "openai_compatible_images"


class ImageResponseMode(StrEnum):
    """图片上游返回生成结果的传输契约。"""

    AUTO = "auto"
    SYNC_JSON = "sync_json"
    SSE = "sse"
    ASYNC_TASK = "async_task"


class RouteHealthStatus(StrEnum):
    """模型路由最近一次可观察健康状态。"""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class InvalidProviderConfiguration(ValueError):
    """API 来源配置不满足安全或完整性要求。"""


class ProviderCodeConflict(ValueError):
    """API 来源代码已被其他来源使用。"""


class ApiProviderNotFound(LookupError):
    """API 来源不存在。"""


class ProviderHasRoutes(ValueError):
    """API 来源仍有尚未退役的模型路由。"""


class InvalidModelRoute(ValueError):
    """模型路由配置无效。"""


class ModelRouteConflict(ValueError):
    """相同来源与上游模型的路由已经存在。"""


class ModelRouteNotFound(LookupError):
    """模型路由不存在。"""


class RouteHealthNotFound(LookupError):
    """模型路由尚无健康检测快照。"""


class RouteProbeUnavailable(RuntimeError):
    """当前运行环境没有装配来源探测 Adapter。"""


class InvalidRoutingPolicy(ValueError):
    """路由策略与目标逻辑模型或来源路由不匹配。"""


class NoAvailableModelRoute(LookupError):
    """逻辑模型规格当前没有健康且已启用的兼容路由。"""


class RoutingMode(StrEnum):
    """逻辑模型规格的来源选择模式。"""

    AUTOMATIC = "automatic"
    PREFERRED = "preferred"


class ModelAvailabilityStatus(StrEnum):
    """普通用户可见的逻辑模型规格可用状态。"""

    AVAILABLE = "available"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True, slots=True)
class ApiProvider:
    """由平台管理员统一维护的 API 来源公开快照。"""

    provider_id: str
    code: str
    display_name: str
    protocol: ProviderProtocol
    base_url: str
    image_response_mode: ImageResponseMode
    concurrency_group: str
    max_concurrency: int
    request_timeout_seconds: int
    credential_configured: bool
    key_fingerprint: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderCreation:
    """创建禁用状态 API 来源的命令。"""

    code: str
    display_name: str
    protocol: ProviderProtocol
    base_url: str
    api_key: str
    image_response_mode: ImageResponseMode = ImageResponseMode.AUTO
    concurrency_group: str = ""
    max_concurrency: int = 20
    request_timeout_seconds: int = 600


@dataclass(frozen=True, slots=True)
class ProviderUpdate:
    """更新来源公开字段、启用状态或轮换只写凭据的命令。"""

    provider_id: str
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    image_response_mode: ImageResponseMode | None = None
    concurrency_group: str | None = None
    max_concurrency: int | None = None
    request_timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ImageModelRoute:
    """逻辑模型规格到一个上游模型的兼容映射。"""

    route_id: str
    provider_id: str
    logical_model: str
    output_spec: str
    provider_model_name: str
    compatibility_group: str
    priority: int
    enabled: bool
    health_status: RouteHealthStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ModelRouteCreation:
    """创建禁用状态模型路由的命令。"""

    provider_id: str
    logical_model: str
    output_spec: str
    provider_model_name: str
    compatibility_group: str
    priority: int = 100
    max_reference_images: int = 3


@dataclass(frozen=True, slots=True)
class ModelRouteUpdate:
    """调整停用路由的映射字段、优先级或启用状态的命令。"""

    route_id: str
    provider_model_name: str | None = None
    compatibility_group: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    max_reference_images: int | None = None


@dataclass(frozen=True, slots=True)
class RouteHealth:
    """模型路由最近滚动检测窗口的公开健康快照。"""

    route_id: str
    status: RouteHealthStatus
    available: bool
    total_latency_ms: int
    ewma_latency_ms: int
    p95_latency_ms: int
    success_rate: float
    sample_count: int
    checked_at: datetime
    error_code: str = ""


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """一个逻辑模型规格的管理员来源选择策略。"""

    logical_model: str
    output_spec: str
    mode: RoutingMode
    preferred_route_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RoutingPolicyUpdate:
    """设置自动模式或管理员指定优先来源的命令。"""

    logical_model: str
    output_spec: str
    mode: RoutingMode
    preferred_route_id: str = ""


@dataclass(frozen=True, slots=True)
class RouteSelection:
    """生成任务使用的、不包含来源地址与凭据的路由选择结果。"""

    logical_model: str
    output_spec: str
    route_id: str
    provider_id: str
    provider_model_name: str
    compatibility_group: str
    selection_reason: str
    selected_at: datetime


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    """不暴露任何来源信息的逻辑模型规格可用状态。"""

    logical_model: str
    output_spec: str
    status: ModelAvailabilityStatus

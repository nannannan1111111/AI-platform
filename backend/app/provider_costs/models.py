"""版本化 Provider 成本的公开模型。"""

from dataclasses import dataclass
from datetime import datetime


class InvalidProviderCostRate(ValueError):
    """Provider 成本版本包含无效字段。"""


class ProviderCostRateConflict(ValueError):
    """同一路由规格在相同生效时间已经存在成本版本。"""


class ProviderCostRouteNotFound(LookupError):
    """Provider 成本所属的模型路由不存在。"""


class ProviderCostRateNotFound(LookupError):
    """指定时刻没有已生效的 Provider 成本版本。"""


@dataclass(frozen=True, slots=True)
class ProviderCostRate:
    """一条不可改写的 Provider 单张成本版本。"""

    version_id: str
    route_id: str
    variant_code: str
    version: int
    provider_currency: str
    cost_per_image_micros: int
    effective_from: datetime
    published_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderCostSummary:
    """Estimated submitted-attempt spend grouped by provider and logical model."""

    provider_id: str
    provider_display_name: str
    logical_model: str
    provider_currency: str
    submitted_attempts: int
    submitted_images: int
    total_cost_cents: int

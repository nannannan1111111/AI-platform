"""Provider 成本版本 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.provider_costs.models import ProviderCostRate, ProviderCostSummary


class ProviderCostSummaries(Protocol):
    """Read submitted-attempt configured-cost estimates."""

    def summarize(self) -> tuple[ProviderCostSummary, ...]:
        """Group estimates by provider, logical model, and currency."""


class ProviderCostRates(Protocol):
    """发布并按生效时间读取不可改写的 Provider 成本版本。"""

    def publish(
        self,
        route_id: str,
        *,
        variant_code: str,
        provider_currency: str,
        cost_per_image_micros: int,
        effective_from: datetime,
    ) -> ProviderCostRate:
        """发布一个立即或未来生效的新成本版本。"""

    def replace(
        self,
        route_id: str,
        *,
        provider_currency: str,
        cost_per_image_cents: int,
    ) -> ProviderCostRate:
        """立即替换路由当前成本并追加下一历史版本。"""

    def effective_at(self, route_id: str, variant_code: str, at: datetime) -> ProviderCostRate:
        """返回指定时刻已经生效的最新成本版本。"""

    def current_at(self, route_id: str, at: datetime) -> ProviderCostRate:
        """Return the highest route-level cost version effective at the given time."""

    def versions(self, route_id: str, variant_code: str) -> tuple[ProviderCostRate, ...]:
        """按版本号返回指定路由规格的不可改写历史。"""

    def versions_for_route(self, route_id: str) -> tuple[ProviderCostRate, ...]:
        """按版本号返回路由的全部不可改写成本历史。"""

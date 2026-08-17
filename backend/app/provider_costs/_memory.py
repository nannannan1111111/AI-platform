"""Provider 成本版本 Interface 的内存 Adapter。"""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from app.provider_costs._validation import cost_cents, cost_micros, currency, effective_time, required
from app.provider_costs.models import ProviderCostRate, ProviderCostRateConflict, ProviderCostRateNotFound


class InMemoryProviderCostRates:
    """在单进程内保存不可改写的 Provider 成本版本。"""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._versions: list[ProviderCostRate] = []
        self._lock = Lock()

    def publish(
        self,
        route_id: str,
        *,
        variant_code: str,
        provider_currency: str,
        cost_per_image_micros: int,
        effective_from: datetime,
    ) -> ProviderCostRate:
        """新增成本版本，不改写已有版本。"""
        route_id = required(route_id, "模型路由", maximum=36)
        variant_code = required(variant_code, "成本规格", maximum=64)
        published_at = self._clock()
        effective_from = effective_time(effective_from, published_at)
        provider_currency = currency(provider_currency)
        cost_per_image_micros = cost_micros(cost_per_image_micros)
        with self._lock:
            matching = [
                version
                for version in self._versions
                if version.route_id == route_id and version.variant_code == variant_code
            ]
            if any(version.effective_from == effective_from for version in matching):
                raise ProviderCostRateConflict(f"{route_id}/{variant_code}")
            version = ProviderCostRate(
                version_id=self._id_factory(),
                route_id=route_id,
                variant_code=variant_code,
                version=len(matching) + 1,
                provider_currency=provider_currency,
                cost_per_image_micros=cost_per_image_micros,
                effective_from=effective_from,
                published_at=published_at,
            )
            self._versions.append(version)
        return version

    def replace(
        self,
        route_id: str,
        *,
        provider_currency: str,
        cost_per_image_cents: int,
    ) -> ProviderCostRate:
        """立即替换路由当前成本，同时保留旧版本供历史 attempt 审计。"""
        route_id = required(route_id, "模型路由", maximum=36)
        provider_currency = currency(provider_currency)
        cost_per_image_cents = cost_cents(cost_per_image_cents)
        published_at = self._clock()
        with self._lock:
            matching = [version for version in self._versions if version.route_id == route_id]
            version = ProviderCostRate(
                version_id=self._id_factory(),
                route_id=route_id,
                variant_code="",
                version=max((item.version for item in matching), default=0) + 1,
                provider_currency=provider_currency,
                cost_per_image_micros=cost_per_image_cents * 10_000,
                effective_from=published_at,
                published_at=published_at,
            )
            self._versions.append(version)
        return version

    def effective_at(self, route_id: str, variant_code: str, at: datetime) -> ProviderCostRate:
        """读取指定时刻最新生效的成本版本。"""
        with self._lock:
            candidates = [
                version
                for version in self._versions
                if version.route_id == route_id
                and version.variant_code == variant_code
                and version.effective_from <= at
            ]
        if not candidates:
            raise ProviderCostRateNotFound(f"{route_id}/{variant_code}")
        return max(candidates, key=lambda version: version.effective_from)

    def current_at(self, route_id: str, at: datetime) -> ProviderCostRate:
        """Return the highest route-level cost version effective at the given time."""
        with self._lock:
            candidates = [
                version for version in self._versions if version.route_id == route_id and version.effective_from <= at
            ]
        if not candidates:
            raise ProviderCostRateNotFound(route_id)
        return max(candidates, key=lambda version: version.version)

    def versions(self, route_id: str, variant_code: str) -> tuple[ProviderCostRate, ...]:
        """按版本号返回指定路由规格的不可改写历史。"""
        with self._lock:
            matching = tuple(
                version
                for version in self._versions
                if version.route_id == route_id and version.variant_code == variant_code
            )
        return tuple(sorted(matching, key=lambda version: version.version))

    def versions_for_route(self, route_id: str) -> tuple[ProviderCostRate, ...]:
        """按版本号返回路由的全部成本历史。"""
        with self._lock:
            matching = tuple(version for version in self._versions if version.route_id == route_id)
        return tuple(sorted(matching, key=lambda version: version.version))

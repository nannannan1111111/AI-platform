"""Provider 成本版本 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.provider_costs._validation import cost_cents, cost_micros, currency, effective_time, required
from app.provider_costs.models import (
    ProviderCostRate,
    ProviderCostRateConflict,
    ProviderCostRateNotFound,
    ProviderCostRouteNotFound,
)

_metadata = MetaData()
_image_model_routes = Table(
    "image_model_routes",
    _metadata,
    Column("id", String(36), primary_key=True),
)
_provider_cost_rates = Table(
    "provider_cost_rates",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("image_model_route_id", String(36), nullable=False),
    Column("variant_code", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("provider_currency", String(3), nullable=False),
    Column("cost_per_image_micros", BigInteger, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyProviderCostRates:
    """使用 SQL 事务持久化不可改写的 Provider 成本版本。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyProviderCostRates:
        """为已经由 Alembic 初始化的数据库创建 Adapter。"""
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False), id_factory=id_factory, clock=clock)

    def publish(
        self,
        route_id: str,
        *,
        variant_code: str,
        provider_currency: str,
        cost_per_image_micros: int,
        effective_from: datetime,
    ) -> ProviderCostRate:
        """在所属模型路由锁内追加成本版本。"""
        route_id = required(route_id, "模型路由", maximum=36)
        variant_code = required(variant_code, "成本规格", maximum=64)
        published_at = self._clock()
        effective_from = effective_time(effective_from, published_at)
        provider_currency = currency(provider_currency)
        cost_per_image_micros = cost_micros(cost_per_image_micros)
        try:
            with self._session_factory.begin() as database:
                route = database.execute(
                    select(_image_model_routes.c.id).where(_image_model_routes.c.id == route_id).with_for_update()
                ).one_or_none()
                if route is None:
                    raise ProviderCostRouteNotFound(route_id)
                current_version = database.scalar(
                    select(func.max(_provider_cost_rates.c.version)).where(
                        _provider_cost_rates.c.image_model_route_id == route_id,
                        _provider_cost_rates.c.variant_code == variant_code,
                    )
                )
                version = ProviderCostRate(
                    version_id=self._id_factory(),
                    route_id=route_id,
                    variant_code=variant_code,
                    version=1 if current_version is None else int(current_version) + 1,
                    provider_currency=provider_currency,
                    cost_per_image_micros=cost_per_image_micros,
                    effective_from=effective_from,
                    published_at=published_at,
                )
                database.execute(
                    insert(_provider_cost_rates).values(
                        id=version.version_id,
                        image_model_route_id=version.route_id,
                        variant_code=version.variant_code,
                        version=version.version,
                        provider_currency=version.provider_currency,
                        cost_per_image_micros=version.cost_per_image_micros,
                        effective_from=version.effective_from,
                        published_at=version.published_at,
                    )
                )
        except IntegrityError as exc:
            raise ProviderCostRateConflict(f"{route_id}/{variant_code}") from exc
        return version

    def replace(
        self,
        route_id: str,
        *,
        provider_currency: str,
        cost_per_image_cents: int,
    ) -> ProviderCostRate:
        """在路由锁内追加立即生效的新当前成本版本。"""
        route_id = required(route_id, "模型路由", maximum=36)
        provider_currency = currency(provider_currency)
        cost_per_image_cents = cost_cents(cost_per_image_cents)
        published_at = self._clock()
        try:
            with self._session_factory.begin() as database:
                route = database.execute(
                    select(_image_model_routes.c.id).where(_image_model_routes.c.id == route_id).with_for_update()
                ).one_or_none()
                if route is None:
                    raise ProviderCostRouteNotFound(route_id)
                current_version = database.scalar(
                    select(func.max(_provider_cost_rates.c.version)).where(
                        _provider_cost_rates.c.image_model_route_id == route_id
                    )
                )
                version = ProviderCostRate(
                    version_id=self._id_factory(),
                    route_id=route_id,
                    variant_code="",
                    version=1 if current_version is None else int(current_version) + 1,
                    provider_currency=provider_currency,
                    cost_per_image_micros=cost_per_image_cents * 10_000,
                    effective_from=published_at,
                    published_at=published_at,
                )
                database.execute(
                    insert(_provider_cost_rates).values(
                        id=version.version_id,
                        image_model_route_id=version.route_id,
                        variant_code=version.variant_code,
                        version=version.version,
                        provider_currency=version.provider_currency,
                        cost_per_image_micros=version.cost_per_image_micros,
                        effective_from=version.effective_from,
                        published_at=version.published_at,
                    )
                )
        except IntegrityError as exc:
            raise ProviderCostRateConflict(route_id) from exc
        return version

    def effective_at(self, route_id: str, variant_code: str, at: datetime) -> ProviderCostRate:
        """读取指定时刻最新生效的成本版本。"""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_provider_cost_rates)
                    .where(
                        _provider_cost_rates.c.image_model_route_id == route_id,
                        _provider_cost_rates.c.variant_code == variant_code,
                        _provider_cost_rates.c.effective_from <= at,
                    )
                    .order_by(_provider_cost_rates.c.effective_from.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ProviderCostRateNotFound(f"{route_id}/{variant_code}")
        return _rate_from_row(row)

    def current_at(self, route_id: str, at: datetime) -> ProviderCostRate:
        """Return the highest route-level cost version effective at the given time."""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_provider_cost_rates)
                    .where(
                        _provider_cost_rates.c.image_model_route_id == route_id,
                        _provider_cost_rates.c.effective_from <= at,
                    )
                    .order_by(_provider_cost_rates.c.version.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ProviderCostRateNotFound(route_id)
        return _rate_from_row(row)

    def versions(self, route_id: str, variant_code: str) -> tuple[ProviderCostRate, ...]:
        """按版本号返回指定路由规格的不可改写历史。"""
        with self._session_factory() as database:
            rows = (
                database.execute(
                    select(_provider_cost_rates)
                    .where(
                        _provider_cost_rates.c.image_model_route_id == route_id,
                        _provider_cost_rates.c.variant_code == variant_code,
                    )
                    .order_by(_provider_cost_rates.c.version)
                )
                .mappings()
                .all()
            )
        return tuple(_rate_from_row(row) for row in rows)

    def versions_for_route(self, route_id: str) -> tuple[ProviderCostRate, ...]:
        """按版本号读取路由的全部成本历史。"""
        with self._session_factory() as database:
            rows = (
                database.execute(
                    select(_provider_cost_rates)
                    .where(_provider_cost_rates.c.image_model_route_id == route_id)
                    .order_by(_provider_cost_rates.c.version)
                )
                .mappings()
                .all()
            )
        return tuple(_rate_from_row(row) for row in rows)


def _rate_from_row(row: RowMapping) -> ProviderCostRate:
    effective_from = row["effective_from"]
    published_at = row["published_at"]
    if not isinstance(effective_from, datetime) or not isinstance(published_at, datetime):
        raise RuntimeError("数据库 Provider 成本日期时间类型无效")
    return ProviderCostRate(
        version_id=str(row["id"]),
        route_id=str(row["image_model_route_id"]),
        variant_code=str(row["variant_code"]),
        version=int(row["version"]),
        provider_currency=str(row["provider_currency"]),
        cost_per_image_micros=int(row["cost_per_image_micros"]),
        effective_from=effective_from.replace(tzinfo=UTC) if effective_from.tzinfo is None else effective_from,
        published_at=published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at,
    )

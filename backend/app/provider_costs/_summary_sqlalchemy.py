"""SQLAlchemy read model for submitted-attempt configured-cost estimates."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.provider_costs.models import ProviderCostSummary

_metadata = MetaData()
_providers = Table(
    "api_providers",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("display_name", String(128), nullable=False),
)
_routes = Table(
    "image_model_routes",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("provider_id", String(36), nullable=False),
)
_tasks = Table(
    "generation_tasks",
    _metadata,
    Column("id", String(255), primary_key=True),
    Column("logical_model", String(128), nullable=False),
    Column("quantity", BigInteger, nullable=False),
)
_attempts = Table(
    "image_generation_attempts",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("generation_task_id", String(255), nullable=False),
    Column("route_id", String(36), nullable=False),
    Column("provider_cost_rate_id", String(36), nullable=True),
    Column("submitted_at", DateTime(timezone=True), nullable=True),
)
_rates = Table(
    "provider_cost_rates",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("provider_currency", String(3), nullable=False),
    Column("cost_per_image_micros", BigInteger, nullable=False),
)


class SqlAlchemyProviderCostSummaries:
    """Aggregate each submitted attempt using its frozen cost version."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def summarize(self) -> tuple[ProviderCostSummary, ...]:
        submitted_attempts = func.count(_attempts.c.id).label("submitted_attempts")
        submitted_images = func.sum(_tasks.c.quantity).label("submitted_images")
        total_cost_micros = func.sum(_rates.c.cost_per_image_micros * _tasks.c.quantity).label("total_cost_micros")
        statement = (
            select(
                _providers.c.id.label("provider_id"),
                _providers.c.display_name.label("provider_display_name"),
                _tasks.c.logical_model,
                _rates.c.provider_currency,
                submitted_attempts,
                submitted_images,
                total_cost_micros,
            )
            .select_from(
                _attempts.join(_tasks, _tasks.c.id == _attempts.c.generation_task_id)
                .join(_routes, _routes.c.id == _attempts.c.route_id)
                .join(_providers, _providers.c.id == _routes.c.provider_id)
                .join(_rates, _rates.c.id == _attempts.c.provider_cost_rate_id)
            )
            .where(_attempts.c.submitted_at.is_not(None))
            .group_by(
                _providers.c.id,
                _providers.c.display_name,
                _tasks.c.logical_model,
                _rates.c.provider_currency,
            )
            .order_by(_providers.c.display_name, _tasks.c.logical_model, _rates.c.provider_currency)
        )
        with self._session_factory() as database:
            rows = database.execute(statement).mappings().all()
        return tuple(
            ProviderCostSummary(
                provider_id=str(row["provider_id"]),
                provider_display_name=str(row["provider_display_name"]),
                logical_model=str(row["logical_model"]),
                provider_currency=str(row["provider_currency"]),
                submitted_attempts=int(row["submitted_attempts"]),
                submitted_images=int(row["submitted_images"]),
                total_cost_cents=int(row["total_cost_micros"]) // 10_000,
            )
            for row in rows
        )

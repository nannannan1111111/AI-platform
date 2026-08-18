"""SQLAlchemy connection-pool metrics without changing pool behavior."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Engine, event

from app.observability import METRICS, MetricsRegistry


def install_database_pool_metrics(engine: Engine, metrics: MetricsRegistry = METRICS) -> None:
    """Attach idempotent checkout/checkin listeners to one SQLAlchemy engine."""
    if getattr(engine, "_observability_pool_metrics_installed", False):
        return
    pool = engine.pool
    pool_any = cast(Any, pool)

    def update_pool_gauges(*, checked_out: int | None = None) -> None:
        current = pool_any.checkedout() if checked_out is None else checked_out
        metrics.set("database_pool_checked_out", float(current))
        metrics.set("database_pool_overflow", float(pool_any.overflow()))
        metrics.set("database_pool_size", float(pool_any.size()))

    def on_connect(_dbapi_connection: object, _connection_record: object) -> None:
        metrics.inc("database_pool_connections_created_total")
        update_pool_gauges()

    def on_checkout(_dbapi_connection: object, _connection_record: object, _connection_proxy: object) -> None:
        metrics.inc("database_pool_checkouts_total")
        update_pool_gauges()

    def on_checkin(_dbapi_connection: object, _connection_record: object) -> None:
        metrics.inc("database_pool_checkins_total")
        update_pool_gauges(checked_out=max(0, int(pool_any.checkedout()) - 1))

    event.listen(pool, "connect", on_connect)
    event.listen(pool, "checkout", on_checkout)
    event.listen(pool, "checkin", on_checkin)
    engine.__dict__["_observability_pool_metrics_installed"] = True
    update_pool_gauges()

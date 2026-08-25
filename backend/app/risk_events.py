"""脱敏运行风险事件和连续失败计数。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True, slots=True)
class RiskEvent:
    event_id: str
    kind: str
    severity: str
    message: str
    occurred_at: datetime
    count: int = 0


class RiskEvents(Protocol):
    def record(self, kind: str, message: str, *, severity: str = "warning", count: int = 0) -> RiskEvent: ...
    def record_generation_outcome(self, success: bool) -> RiskEvent | None: ...
    def list(self, *, since: datetime | None, offset: int, limit: int) -> tuple[RiskEvent, ...]: ...
    def total(self, *, since: datetime | None) -> int: ...


class InMemoryRiskEvents:
    def __init__(self) -> None:
        self._events: list[RiskEvent] = []
        self._failure_count = 0

    def record(self, kind: str, message: str, *, severity: str = "warning", count: int = 0) -> RiskEvent:
        event = RiskEvent(str(uuid4()), kind, severity, message[:255], datetime.now(UTC), count)
        self._events.insert(0, event)
        return event

    def record_generation_outcome(self, success: bool) -> RiskEvent | None:
        self._failure_count = 0 if success else self._failure_count + 1
        if self._failure_count == 10:
            return self.record("consecutive_generation_failures", "全站连续 10 条生图失败", count=self._failure_count)
        return None

    def list(self, *, since: datetime | None, offset: int, limit: int) -> tuple[RiskEvent, ...]:
        values = [event for event in self._events if since is None or event.occurred_at >= since]
        return tuple(values[offset : offset + limit])

    def total(self, *, since: datetime | None) -> int:
        return sum(1 for event in self._events if since is None or event.occurred_at >= since)


_metadata = MetaData()
_events = Table(
    "risk_events",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(64), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("message", String(255), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("count", BigInteger, nullable=False, default=0),
)
_counters = Table(
    "risk_event_counters",
    _metadata,
    Column("counter_key", String(64), primary_key=True),
    Column("failure_count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyRiskEvents:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(self, kind: str, message: str, *, severity: str = "warning", count: int = 0) -> RiskEvent:
        event = RiskEvent(str(uuid4()), kind, severity, message[:255], datetime.now(UTC), count)
        with self._session_factory.begin() as database:
            database.execute(insert(_events).values(id=event.event_id, kind=event.kind, severity=event.severity, message=event.message, occurred_at=event.occurred_at, count=event.count))
        return event

    def record_generation_outcome(self, success: bool) -> RiskEvent | None:
        now = datetime.now(UTC)
        with self._session_factory.begin() as database:
            row = database.execute(select(_counters).where(_counters.c.counter_key == "generation").with_for_update()).mappings().one_or_none()
            count = 0 if success else int(row["failure_count"] if row else 0) + 1
            if row is None:
                database.execute(insert(_counters).values(counter_key="generation", failure_count=count, updated_at=now))
            else:
                database.execute(update(_counters).where(_counters.c.counter_key == "generation").values(failure_count=count, updated_at=now))
            if count != 10:
                return None
            event = RiskEvent(str(uuid4()), "consecutive_generation_failures", "critical", "全站连续 10 条生图失败", now, count)
            database.execute(insert(_events).values(id=event.event_id, kind=event.kind, severity=event.severity, message=event.message, occurred_at=event.occurred_at, count=event.count))
            return event

    def list(self, *, since: datetime | None, offset: int, limit: int) -> tuple[RiskEvent, ...]:
        query = select(_events).order_by(_events.c.occurred_at.desc(), _events.c.id.desc()).offset(offset).limit(limit)
        if since is not None:
            query = query.where(_events.c.occurred_at >= since)
        with self._session_factory() as database:
            rows = database.execute(query).mappings()
            return tuple(RiskEvent(str(row["id"]), str(row["kind"]), str(row["severity"]), str(row["message"]), row["occurred_at"], int(row["count"])) for row in rows)

    def total(self, *, since: datetime | None) -> int:
        from sqlalchemy import func
        query = select(func.count()).select_from(_events)
        if since is not None:
            query = query.where(_events.c.occurred_at >= since)
        with self._session_factory() as database:
            return int(database.scalar(query) or 0)


__all__ = ["InMemoryRiskEvents", "RiskEvent", "RiskEvents", "SqlAlchemyRiskEvents"]

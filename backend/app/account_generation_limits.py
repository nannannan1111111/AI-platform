"""Per-account image generation execution concurrency settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_ACCOUNT_GENERATION_CONCURRENCY = 2
MAX_ACCOUNT_GENERATION_CONCURRENCY = 50

_metadata = MetaData()
_limits = Table(
    "account_generation_limits",
    _metadata,
    Column("account_space_id", String(36), primary_key=True),
    Column("execution_concurrency", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"execution_concurrency BETWEEN 1 AND {MAX_ACCOUNT_GENERATION_CONCURRENCY}",
        name="ck_account_generation_execution_concurrency",
    ),
)


@dataclass(frozen=True, slots=True)
class AccountGenerationLimit:
    """The maximum number of provider tasks one account may run at once."""

    account_space_id: str
    execution_concurrency: int
    updated_at: datetime | None = None


class AccountGenerationLimits(Protocol):
    """Read and update account-specific execution concurrency."""

    def current(self, account_space_id: str) -> AccountGenerationLimit:
        """Return an explicit limit or the platform default."""

    def update(self, account_space_id: str, execution_concurrency: int) -> AccountGenerationLimit:
        """Persist a validated account-specific limit."""


class InMemoryAccountGenerationLimits:
    """In-memory adapter used by HTTP and unit tests."""

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        """Validate and retain optional account-specific limits."""
        self._limits = dict(limits or {})
        for value in self._limits.values():
            _validate(value)

    def current(self, account_space_id: str) -> AccountGenerationLimit:
        """Return the configured limit or the default of two."""
        return AccountGenerationLimit(
            account_space_id=account_space_id,
            execution_concurrency=self._limits.get(
                account_space_id,
                DEFAULT_ACCOUNT_GENERATION_CONCURRENCY,
            ),
        )

    def update(self, account_space_id: str, execution_concurrency: int) -> AccountGenerationLimit:
        """Replace one account's execution concurrency."""
        _validate(execution_concurrency)
        self._limits[account_space_id] = execution_concurrency
        return AccountGenerationLimit(
            account_space_id=account_space_id,
            execution_concurrency=execution_concurrency,
            updated_at=datetime.now(UTC),
        )


class SqlAlchemyAccountGenerationLimits:
    """Database-backed adapter shared by every Web and Worker process."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Retain the shared application session factory."""
        self._sessions = sessions

    @classmethod
    def for_database_url(cls, database_url: str) -> SqlAlchemyAccountGenerationLimits:
        """Create an independent adapter for tests and scripts."""
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False))

    def current(self, account_space_id: str) -> AccountGenerationLimit:
        """Read one persisted override or return the platform default."""
        with self._sessions() as database:
            row = database.execute(
                _limits.select().where(_limits.c.account_space_id == account_space_id)
            ).mappings().one_or_none()
        if row is None:
            return AccountGenerationLimit(
                account_space_id=account_space_id,
                execution_concurrency=DEFAULT_ACCOUNT_GENERATION_CONCURRENCY,
            )
        return AccountGenerationLimit(
            account_space_id=account_space_id,
            execution_concurrency=int(row["execution_concurrency"]),
            updated_at=_aware(row["updated_at"]),
        )

    def update(self, account_space_id: str, execution_concurrency: int) -> AccountGenerationLimit:
        """Upsert one validated account-specific limit."""
        _validate(execution_concurrency)
        now = datetime.now(UTC)
        values = {
            "account_space_id": account_space_id,
            "execution_concurrency": execution_concurrency,
            "updated_at": now,
        }
        with self._sessions.begin() as database:
            dialect = database.get_bind().dialect.name
            insert_statement = (
                postgresql_insert(_limits) if dialect == "postgresql" else sqlite_insert(_limits)
            )
            database.execute(
                insert_statement.values(**values).on_conflict_do_update(
                    index_elements=[_limits.c.account_space_id],
                    set_={
                        "execution_concurrency": execution_concurrency,
                        "updated_at": now,
                    },
                )
            )
        return AccountGenerationLimit(
            account_space_id=account_space_id,
            execution_concurrency=execution_concurrency,
            updated_at=now,
        )


def _validate(value: int) -> None:
    if not 1 <= value <= MAX_ACCOUNT_GENERATION_CONCURRENCY:
        raise ValueError(
            f"单用户执行并发数必须在 1 到 {MAX_ACCOUNT_GENERATION_CONCURRENCY} 之间"
        )


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("账户生成并发更新时间无效")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

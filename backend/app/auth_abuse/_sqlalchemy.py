"""PostgreSQL adapter for atomic, cross-process authentication rate limits."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import DateTime, Integer, String, create_engine, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.accounts._sqlalchemy import _Base
from app.auth_abuse.models import (
    AuthAction,
    RateLimitBackendUnavailable,
    RateLimitDecision,
    RateLimitSubject,
)


class _AuthRateLimitRow(_Base):
    """One privacy-preserving fixed window shared by every Web worker."""

    __tablename__ = "auth_rate_limit_windows"

    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyAuthAbuseProtection:
    """Use PostgreSQL UPSERT row locks to enforce a limit across processes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        hash_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(hash_key.encode("utf-8")) < 32:
            raise ValueError("authentication rate-limit hash key must contain at least 32 bytes")
        self._session_factory = session_factory
        self._hash_key = hash_key.encode("utf-8")
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        hash_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyAuthAbuseProtection:
        return cls(sessionmaker(create_engine(database_url), expire_on_commit=False), hash_key=hash_key, clock=clock)

    def consume(self, action: AuthAction, subjects: tuple[RateLimitSubject, ...]) -> RateLimitDecision:
        now = _utc(self._clock())
        prepared = sorted(
            [
                (
                    subject,
                    self._subject_hash(action, subject.scope, subject.value),
                    int(subject.policy.window.total_seconds()),
                )
                for subject in subjects
            ],
            key=lambda item: (item[0].scope, item[1], item[2]),
        )
        blocked: list[tuple[str, int]] = []
        try:
            with self._session_factory.begin() as database:
                database.execute(delete(_AuthRateLimitRow).where(_AuthRateLimitRow.window_ends_at <= now))
                for subject, subject_hash, window_seconds in prepared:
                    window_started = _window_start(now, window_seconds)
                    window_ends = window_started + timedelta(seconds=window_seconds)
                    statement = (
                        insert(_AuthRateLimitRow)
                        .values(
                            action=action.value,
                            subject_scope=subject.scope,
                            subject_hash=subject_hash,
                            window_seconds=window_seconds,
                            window_started_at=window_started,
                            window_ends_at=window_ends,
                            request_count=1,
                            last_seen_at=now,
                        )
                        .on_conflict_do_update(
                            index_elements=(
                                _AuthRateLimitRow.action,
                                _AuthRateLimitRow.subject_scope,
                                _AuthRateLimitRow.subject_hash,
                                _AuthRateLimitRow.window_seconds,
                                _AuthRateLimitRow.window_started_at,
                            ),
                            set_={
                                "request_count": _AuthRateLimitRow.request_count + 1,
                                "last_seen_at": now,
                                "window_ends_at": window_ends,
                            },
                        )
                        .returning(_AuthRateLimitRow.request_count)
                    )
                    count = database.scalar(statement)
                    if count is None:
                        raise RuntimeError("rate-limit UPSERT returned no count")
                    if count > subject.policy.limit:
                        blocked.append((subject.scope, max(1, ceil((window_ends - now).total_seconds()))))
        except SQLAlchemyError as exc:
            raise RateLimitBackendUnavailable("authentication rate-limit database unavailable") from exc
        return RateLimitDecision(
            allowed=not blocked,
            retry_after_seconds=max((retry for _, retry in blocked), default=0),
            blocked_scopes=tuple(scope for scope, _ in blocked),
        )

    def reset(self, action: AuthAction, scope: str, subject_value: str) -> None:
        subject_hash = self._subject_hash(action, scope, subject_value)
        try:
            with self._session_factory.begin() as database:
                database.execute(
                    delete(_AuthRateLimitRow).where(
                        _AuthRateLimitRow.action == action.value,
                        _AuthRateLimitRow.subject_scope == scope,
                        _AuthRateLimitRow.subject_hash == subject_hash,
                    )
                )
        except SQLAlchemyError as exc:
            raise RateLimitBackendUnavailable("authentication rate-limit database unavailable") from exc

    def _subject_hash(self, action: AuthAction, scope: str, value: str) -> str:
        message = f"{action.value}\0{scope}\0{value}".encode()
        return hmac.new(self._hash_key, message, hashlib.sha256).hexdigest()


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp()) // window_seconds * window_seconds
    return datetime.fromtimestamp(epoch, tz=UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

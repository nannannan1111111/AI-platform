"""Deterministic in-memory authentication limiter for HTTP and boundary tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from math import ceil
from threading import Lock

from app.auth_abuse.models import AuthAction, RateLimitDecision, RateLimitSubject


class InMemoryAuthAbuseProtection:
    """Thread-safe fixed-window implementation used only outside production composition."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        """Keep counters behind a lock and use an injectable UTC clock."""
        self._clock = clock or (lambda: datetime.now(UTC))
        self._counts: dict[tuple[str, str, str, int, int], int] = {}
        self._lock = Lock()

    def consume(self, action: AuthAction, subjects: tuple[RateLimitSubject, ...]) -> RateLimitDecision:
        """Consume each supplied subject within its current fixed window."""
        now = _utc(self._clock())
        blocked: list[tuple[str, int]] = []
        with self._lock:
            for subject in subjects:
                window_seconds = int(subject.policy.window.total_seconds())
                window_started = int(now.timestamp()) // window_seconds * window_seconds
                key = (action.value, subject.scope, subject.value, window_seconds, window_started)
                count = self._counts.get(key, 0) + 1
                self._counts[key] = count
                if count > subject.policy.limit:
                    retry_after = max(1, ceil(window_started + window_seconds - now.timestamp()))
                    blocked.append((subject.scope, retry_after))
            self._remove_expired(int(now.timestamp()))
        return RateLimitDecision(
            allowed=not blocked,
            retry_after_seconds=max((retry for _, retry in blocked), default=0),
            blocked_scopes=tuple(scope for scope, _ in blocked),
        )

    def reset(self, action: AuthAction, scope: str, subject_value: str) -> None:
        """Remove every active fixed window for one subject."""
        with self._lock:
            matching = [
                key for key in self._counts if key[0] == action.value and key[1] == scope and key[2] == subject_value
            ]
            for key in matching:
                del self._counts[key]

    def _remove_expired(self, now_epoch: int) -> None:
        expired = [key for key in self._counts if key[4] + key[3] <= now_epoch]
        for key in expired:
            del self._counts[key]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

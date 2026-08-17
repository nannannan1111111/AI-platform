"""Port for cross-process authentication abuse protection."""

from __future__ import annotations

from typing import Protocol

from app.auth_abuse.models import AuthAction, RateLimitDecision, RateLimitSubject


class AuthAbuseProtection(Protocol):
    """Consume or clear fixed-window counters without exposing stored identifiers."""

    def consume(self, action: AuthAction, subjects: tuple[RateLimitSubject, ...]) -> RateLimitDecision:
        """Atomically count a request and report whether every subject remains allowed."""
        ...

    def reset(self, action: AuthAction, scope: str, subject_value: str) -> None:
        """Clear all active windows for one subject, for success or emergency unlock."""
        ...

"""Authentication abuse-protection values shared by HTTP and persistence adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class AuthAction(StrEnum):
    """Public authentication actions with independent rate-limit windows."""

    LOGIN = "login"
    REGISTER = "register"
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """One fixed-window request allowance."""

    limit: int
    window: timedelta

    def __post_init__(self) -> None:
        """Reject policies that cannot define a usable fixed window."""
        if self.limit <= 0:
            raise ValueError("rate-limit count must be positive")
        if self.window.total_seconds() <= 0:
            raise ValueError("rate-limit window must be positive")


@dataclass(frozen=True, slots=True)
class AuthAbusePolicies:
    """Initial public-authentication limits; production may override every value."""

    login_ip: RateLimitPolicy
    login_email: RateLimitPolicy
    register_ip: RateLimitPolicy
    email_verification_account: RateLimitPolicy
    password_reset_ip: RateLimitPolicy
    password_reset_email: RateLimitPolicy

    @classmethod
    def defaults(cls) -> AuthAbusePolicies:
        """Return the reviewed low-volume public-launch thresholds."""
        return cls(
            login_ip=RateLimitPolicy(10, timedelta(minutes=10)),
            login_email=RateLimitPolicy(5, timedelta(minutes=10)),
            register_ip=RateLimitPolicy(5, timedelta(hours=1)),
            email_verification_account=RateLimitPolicy(3, timedelta(hours=1)),
            password_reset_ip=RateLimitPolicy(5, timedelta(hours=1)),
            password_reset_email=RateLimitPolicy(3, timedelta(hours=1)),
        )


@dataclass(frozen=True, slots=True)
class RateLimitSubject:
    """A private subject value consumed under one independently configured scope."""

    scope: str
    value: str
    policy: RateLimitPolicy


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of atomically consuming one request across all supplied subjects."""

    allowed: bool
    retry_after_seconds: int = 0
    blocked_scopes: tuple[str, ...] = ()


class RateLimitBackendUnavailable(RuntimeError):
    """The shared rate-limit store could not safely make a decision."""

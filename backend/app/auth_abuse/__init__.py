"""Authentication abuse protection public API."""

from app.auth_abuse._sqlalchemy import SqlAlchemyAuthAbuseProtection
from app.auth_abuse.client_ip import ClientIpResolver
from app.auth_abuse.interface import AuthAbuseProtection
from app.auth_abuse.memory import InMemoryAuthAbuseProtection
from app.auth_abuse.models import (
    AuthAbusePolicies,
    AuthAction,
    RateLimitBackendUnavailable,
    RateLimitDecision,
    RateLimitPolicy,
    RateLimitSubject,
)

__all__ = [
    "AuthAbusePolicies",
    "AuthAbuseProtection",
    "AuthAction",
    "ClientIpResolver",
    "InMemoryAuthAbuseProtection",
    "RateLimitBackendUnavailable",
    "RateLimitDecision",
    "RateLimitPolicy",
    "RateLimitSubject",
    "SqlAlchemyAuthAbuseProtection",
]

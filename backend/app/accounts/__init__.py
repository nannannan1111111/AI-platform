"""账户注册与个人账户空间的公开 Interface。"""

from app.accounts._memory import InMemoryAccountAccess
from app.accounts._sqlalchemy import SqlAlchemyAccountAccess
from app.accounts.interface import AccountAccess, AccountDirectory, EmailVerificationDelivery
from app.accounts.models import (
    AuthenticatedSession,
    CreditBalance,
    CurrentUser,
    EmailAlreadyRegistered,
    EmailVerificationUnavailable,
    InvalidCredentials,
    InvalidEmail,
    InvalidEmailVerification,
    InvalidSession,
    RegisteredUser,
    Registration,
    WeakPassword,
)
from app.accounts.smtp import EmailDeliveryFailed, SmtpEmailVerificationDelivery

__all__ = [
    "AuthenticatedSession",
    "AccountAccess",
    "AccountDirectory",
    "CreditBalance",
    "CurrentUser",
    "EmailAlreadyRegistered",
    "EmailDeliveryFailed",
    "EmailVerificationDelivery",
    "EmailVerificationUnavailable",
    "InMemoryAccountAccess",
    "InvalidCredentials",
    "InvalidEmail",
    "InvalidEmailVerification",
    "InvalidSession",
    "Registration",
    "RegisteredUser",
    "SqlAlchemyAccountAccess",
    "SmtpEmailVerificationDelivery",
    "WeakPassword",
]

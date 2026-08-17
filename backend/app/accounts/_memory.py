"""账户 Interface 的内存 Adapter。"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from pwdlib import PasswordHash

from app.accounts._validation import registration_email, registration_password
from app.accounts.interface import EmailVerificationDelivery
from app.accounts.models import (
    AuthenticatedSession,
    CreditBalance,
    CurrentUser,
    EmailAlreadyRegistered,
    EmailVerificationUnavailable,
    InvalidCredentials,
    InvalidEmailVerification,
    InvalidPasswordReset,
    InvalidSession,
    PasswordResetUnavailable,
    RegisteredUser,
    Registration,
)


@dataclass(slots=True)
class _AccountRecord:
    """保存一个用户及其一对一个人账户空间。"""

    registration: Registration
    password_hash: str
    registered_at: datetime
    email_verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _SessionRecord:
    """绑定用户且具有明确到期时间的访问会话。"""

    registration: Registration
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _EmailVerificationRecord:
    """只保存验证令牌摘要绑定的邮箱和到期时间。"""

    email: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _PasswordResetRecord:
    """只保存密码重置令牌摘要绑定的邮箱和到期时间。"""

    email: str
    expires_at: datetime


class InMemoryAccountAccess:
    """在单进程内原子注册用户并创建个人账户空间。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(days=30),
        verification_delivery: EmailVerificationDelivery | None = None,
        verification_ttl: timedelta = timedelta(hours=24),
        password_reset_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        """创建空账户仓储。"""
        self._records_by_email: dict[str, _AccountRecord] = {}
        self._sessions: dict[str, _SessionRecord] = {}
        self._email_verifications: dict[str, _EmailVerificationRecord] = {}
        self._password_resets: dict[str, _PasswordResetRecord] = {}
        self._lock = Lock()
        self._password_hash = PasswordHash.recommended()
        self._dummy_password_hash = self._password_hash.hash(secrets.token_urlsafe(32))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_ttl = session_ttl
        self._verification_delivery = verification_delivery
        self._verification_ttl = verification_ttl
        self._password_reset_ttl = password_reset_ttl

    def register(self, email: str, password: str) -> Registration:
        """注册邮箱身份并返回其个人账户空间和零余额。"""
        normalized_email = registration_email(email)
        validated_password = registration_password(password)
        with self._lock:
            if normalized_email in self._records_by_email:
                raise EmailAlreadyRegistered(normalized_email)
        registration = Registration(
            user_id=str(uuid4()),
            account_space_id=str(uuid4()),
            email=normalized_email,
            available_credits="0.0000",
        )
        record = _AccountRecord(registration, self._password_hash.hash(validated_password), self._clock())
        with self._lock:
            if normalized_email in self._records_by_email:
                raise EmailAlreadyRegistered(normalized_email)
            self._records_by_email[normalized_email] = record
        if _delivery_is_available(self._verification_delivery):
            try:
                self._deliver_verification(normalized_email)
            except Exception:
                with self._lock:
                    self._records_by_email.pop(normalized_email, None)
                    self._email_verifications = {
                        key: item for key, item in self._email_verifications.items() if item.email != normalized_email
                    }
                raise
        return registration

    def login(self, email: str, password: str) -> AuthenticatedSession:
        """校验邮箱密码并创建不透明访问令牌。"""
        normalized_email = email.strip().casefold()
        with self._lock:
            record = self._records_by_email.get(normalized_email)
        stored_hash = record.password_hash if record is not None else self._dummy_password_hash
        if not self._password_hash.verify(password, stored_hash) or record is None:
            raise InvalidCredentials
        access_token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[access_token] = _SessionRecord(
                registration=record.registration,
                expires_at=self._clock() + self._session_ttl,
            )
        return AuthenticatedSession(
            user_id=record.registration.user_id,
            account_space_id=record.registration.account_space_id,
            email=record.registration.email,
            access_token=access_token,
        )

    def current_user(self, access_token: str) -> CurrentUser:
        """返回访问令牌绑定的当前用户。"""
        with self._lock:
            session = self._sessions.get(access_token)
            record = self._records_by_email.get(session.registration.email) if session is not None else None
        if session is None or record is None or session.expires_at <= self._clock():
            raise InvalidSession
        return CurrentUser(
            user_id=session.registration.user_id,
            account_space_id=session.registration.account_space_id,
            email=session.registration.email,
            email_verified=record.email_verified_at is not None,
        )

    def verify_email(self, token: str) -> None:
        """消费一次性验证令牌并确认对应邮箱。"""
        token_hash = _token_hash(token)
        with self._lock:
            verification = self._email_verifications.pop(token_hash, None)
            if verification is None or verification.expires_at <= self._clock():
                raise InvalidEmailVerification
            record = self._records_by_email.get(verification.email)
            if record is None:
                raise InvalidEmailVerification
            record.email_verified_at = self._clock()

    def request_email_verification(self, access_token: str) -> None:
        """重新签发当前用户的验证令牌；已验证邮箱保持幂等。"""
        if self._verification_delivery is None:
            raise EmailVerificationUnavailable
        with self._lock:
            session = self._sessions.get(access_token)
            record = self._records_by_email.get(session.registration.email) if session is not None else None
            if session is None or record is None or session.expires_at <= self._clock():
                raise InvalidSession
            if record.email_verified_at is not None:
                return
            email = record.registration.email
        self._deliver_verification(email)

    def change_password(self, access_token: str, current_password: str, new_password: str) -> None:
        """更新当前用户密码并撤销该用户的所有会话。"""
        validated_password = registration_password(new_password)
        with self._lock:
            session = self._sessions.get(access_token)
            record = self._records_by_email.get(session.registration.email) if session is not None else None
            if session is None or record is None or session.expires_at <= self._clock():
                raise InvalidSession
            if not self._password_hash.verify(current_password, record.password_hash):
                raise InvalidCredentials
            record.password_hash = self._password_hash.hash(validated_password)
            user_id = record.registration.user_id
            self._sessions = {
                token: item for token, item in self._sessions.items() if item.registration.user_id != user_id
            }

    def request_password_reset(self, email: str) -> None:
        """若邮箱存在则替换旧令牌并投递新密码重置链接。"""
        if not _delivery_is_available(self._verification_delivery):
            raise PasswordResetUnavailable
        normalized_email = email.strip().casefold()
        with self._lock:
            if normalized_email not in self._records_by_email:
                return
        self._deliver_password_reset(normalized_email)

    def reset_password(self, token: str, new_password: str) -> None:
        """消费有效令牌、更新密码并撤销该用户全部会话。"""
        validated_password = registration_password(new_password)
        token_hash = _token_hash(token)
        with self._lock:
            reset = self._password_resets.pop(token_hash, None)
            if reset is None or reset.expires_at <= self._clock():
                raise InvalidPasswordReset
            record = self._records_by_email.get(reset.email)
            if record is None:
                raise InvalidPasswordReset
            record.password_hash = self._password_hash.hash(validated_password)
            user_id = record.registration.user_id
            self._password_resets = {
                key: item for key, item in self._password_resets.items() if item.email != reset.email
            }
            self._sessions = {
                key: item for key, item in self._sessions.items() if item.registration.user_id != user_id
            }

    def _deliver_verification(self, email: str) -> None:
        """替换该邮箱的旧令牌并把新令牌交给投递 Adapter。"""
        if self._verification_delivery is None:
            raise EmailVerificationUnavailable
        token = secrets.token_urlsafe(32)
        with self._lock:
            previous = {
                key: item for key, item in self._email_verifications.items() if item.email == email
            }
            self._email_verifications = {
                key: item for key, item in self._email_verifications.items() if item.email != email
            }
            token_hash = _token_hash(token)
            self._email_verifications[token_hash] = _EmailVerificationRecord(
                email=email,
                expires_at=self._clock() + self._verification_ttl,
            )
        try:
            self._verification_delivery.send_verification(email, token)
        except Exception:
            with self._lock:
                self._email_verifications.pop(token_hash, None)
                self._email_verifications.update(previous)
            raise

    def _deliver_password_reset(self, email: str) -> None:
        """替换该邮箱的旧重置令牌，并在投递失败时恢复旧令牌。"""
        delivery = self._verification_delivery
        if delivery is None:
            raise PasswordResetUnavailable
        token = secrets.token_urlsafe(32)
        with self._lock:
            previous = {key: item for key, item in self._password_resets.items() if item.email == email}
            self._password_resets = {
                key: item for key, item in self._password_resets.items() if item.email != email
            }
            token_hash = _token_hash(token)
            self._password_resets[token_hash] = _PasswordResetRecord(
                email=email,
                expires_at=self._clock() + self._password_reset_ttl,
            )
        try:
            delivery.send_password_reset(email, token)
        except Exception:
            with self._lock:
                self._password_resets.pop(token_hash, None)
                self._password_resets.update(previous)
            raise

    def credit_balance(self, access_token: str) -> CreditBalance:
        """返回当前用户个人账户空间的可用与冻结额度。"""
        with self._lock:
            session = self._sessions.get(access_token)
        if session is None or session.expires_at <= self._clock():
            raise InvalidSession
        return CreditBalance(available_credits="0.0000", frozen_credits="0.0000")

    def logout(self, access_token: str) -> None:
        """撤销一个访问令牌；重复退出保持幂等。"""
        with self._lock:
            self._sessions.pop(access_token, None)

    def list_registered_users(self) -> tuple[RegisteredUser, ...]:
        """按邮箱返回不含密码和会话的注册用户目录。"""
        with self._lock:
            records = tuple(self._records_by_email[email] for email in sorted(self._records_by_email))
        return tuple(_registered_user(record) for record in records)

    def registered_user(self, user_id: str) -> RegisteredUser:
        """按稳定用户标识读取一个注册用户。"""
        with self._lock:
            record = next(
                (item for item in self._records_by_email.values() if item.registration.user_id == user_id),
                None,
            )
        if record is None:
            raise KeyError(user_id)
        return _registered_user(record)

    def registered_user_by_email(self, email: str) -> RegisteredUser:
        """按规范化邮箱读取一个注册用户。"""
        with self._lock:
            record = self._records_by_email.get(email.strip().casefold())
        if record is None:
            raise KeyError(email)
        return _registered_user(record)


def _token_hash(token: str) -> str:
    """返回一次性邮箱验证令牌的 SHA-256 摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _delivery_is_available(delivery: EmailVerificationDelivery | None) -> bool:
    if delivery is None:
        return False
    availability = getattr(delivery, "is_available", None)
    return not callable(availability) or bool(availability())


def _registered_user(record: _AccountRecord) -> RegisteredUser:
    return RegisteredUser(
        user_id=record.registration.user_id,
        account_space_id=record.registration.account_space_id,
        email=record.registration.email,
        email_verified=record.email_verified_at is not None,
        registered_at=record.registered_at,
    )

"""账户 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pwdlib import PasswordHash
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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
    InvalidSession,
    RegisteredUser,
    Registration,
)


class _Base(DeclarativeBase):
    """账户表声明基类。"""


class _UserRow(_Base):
    """邮箱密码登录身份。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _AccountSpaceRow(_Base):
    """与用户一对一的个人账户空间。"""

    __tablename__ = "account_spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)


class _CreditAccountRow(_Base):
    """个人账户空间的整数额度账户。"""

    __tablename__ = "credit_accounts"

    account_space_id: Mapped[str] = mapped_column(
        ForeignKey("account_spaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    available_credit_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    frozen_credit_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class _AuthSessionRow(_Base):
    """只保存访问令牌摘要的登录会话。"""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class _EmailVerificationRow(_Base):
    """只保存邮箱验证令牌摘要及其有效期。"""

    __tablename__ = "email_verification_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyAccountAccess:
    """使用 SQL 事务持久化账户公开行为。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(days=30),
        verification_delivery: EmailVerificationDelivery | None = None,
        verification_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        """保存数据库会话工厂和密码哈希 Adapter。"""
        self._session_factory = session_factory
        self._password_hash = PasswordHash.recommended()
        self._dummy_password_hash = self._password_hash.hash(secrets.token_urlsafe(32))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_ttl = session_ttl
        self._verification_delivery = verification_delivery
        self._verification_ttl = verification_ttl

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        initialize_schema: bool = False,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(days=30),
        verification_delivery: EmailVerificationDelivery | None = None,
        verification_ttl: timedelta = timedelta(hours=24),
    ) -> SqlAlchemyAccountAccess:
        """为数据库 URL 创建 Adapter；测试可显式初始化空 schema。"""
        engine = create_engine(database_url)
        if initialize_schema:
            _Base.metadata.create_all(engine)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            clock=clock,
            session_ttl=session_ttl,
            verification_delivery=verification_delivery,
            verification_ttl=verification_ttl,
        )

    def register(self, email: str, password: str) -> Registration:
        """在一个事务中创建用户、个人账户空间和零余额账户。"""
        normalized_email = registration_email(email)
        validated_password = registration_password(password)
        registration = Registration(
            user_id=str(uuid4()),
            account_space_id=str(uuid4()),
            email=normalized_email,
            available_credits="0.0000",
        )
        verification_token = secrets.token_urlsafe(32) if _delivery_is_available(self._verification_delivery) else None
        try:
            with self._session_factory.begin() as database:
                database.add(
                    _UserRow(
                        id=registration.user_id,
                        email=registration.email,
                        password_hash=self._password_hash.hash(validated_password),
                        created_at=self._clock(),
                    )
                )
                database.flush()
                database.add(
                    _AccountSpaceRow(
                        id=registration.account_space_id,
                        user_id=registration.user_id,
                    )
                )
                database.flush()
                database.add(_CreditAccountRow(account_space_id=registration.account_space_id))
                if verification_token is not None:
                    database.add(
                        _EmailVerificationRow(
                            token_hash=_token_hash(verification_token),
                            user_id=registration.user_id,
                            expires_at=self._clock() + self._verification_ttl,
                        )
                    )
                    if self._verification_delivery is not None:
                        self._verification_delivery.send_verification(normalized_email, verification_token)
        except IntegrityError as exc:
            raise EmailAlreadyRegistered(normalized_email) from exc
        return registration

    def login(self, email: str, password: str) -> AuthenticatedSession:
        """校验密码并持久化不透明访问令牌摘要。"""
        normalized_email = email.strip().casefold()
        with self._session_factory() as database:
            user = database.scalar(select(_UserRow).where(_UserRow.email == normalized_email))
            stored_hash = user.password_hash if user is not None else self._dummy_password_hash
            if not self._password_hash.verify(password, stored_hash) or user is None:
                raise InvalidCredentials
            account_space_id = database.scalar(select(_AccountSpaceRow.id).where(_AccountSpaceRow.user_id == user.id))
            if account_space_id is None:
                raise RuntimeError("用户缺少个人账户空间")
            access_token = secrets.token_urlsafe(32)
            database.add(
                _AuthSessionRow(
                    token_hash=_token_hash(access_token),
                    user_id=user.id,
                    expires_at=self._clock() + self._session_ttl,
                )
            )
            database.commit()
        return AuthenticatedSession(
            user_id=user.id,
            account_space_id=account_space_id,
            email=user.email,
            access_token=access_token,
        )

    def current_user(self, access_token: str) -> CurrentUser:
        """通过访问令牌摘要查询当前用户及个人账户空间。"""
        with self._session_factory() as database:
            row = database.execute(
                select(_UserRow.id, _UserRow.email, _AccountSpaceRow.id, _UserRow.email_verified_at)
                .join(_AuthSessionRow, _AuthSessionRow.user_id == _UserRow.id)
                .join(_AccountSpaceRow, _AccountSpaceRow.user_id == _UserRow.id)
                .where(
                    _AuthSessionRow.token_hash == _token_hash(access_token),
                    _AuthSessionRow.expires_at > self._clock(),
                )
            ).one_or_none()
        if row is None:
            raise InvalidSession
        return CurrentUser(
            user_id=row[0],
            email=row[1],
            account_space_id=row[2],
            email_verified=row[3] is not None,
        )

    def logout(self, access_token: str) -> None:
        """撤销一个持久化访问令牌；重复退出保持幂等。"""
        with self._session_factory.begin() as database:
            database.execute(delete(_AuthSessionRow).where(_AuthSessionRow.token_hash == _token_hash(access_token)))

    def verify_email(self, token: str) -> None:
        """原子消费有效令牌并持久化邮箱验证时间。"""
        with self._session_factory.begin() as database:
            user_id = database.scalar(
                delete(_EmailVerificationRow)
                .where(
                    _EmailVerificationRow.token_hash == _token_hash(token),
                    _EmailVerificationRow.expires_at > self._clock(),
                )
                .returning(_EmailVerificationRow.user_id)
            )
            if user_id is None:
                raise InvalidEmailVerification
            database.execute(update(_UserRow).where(_UserRow.id == user_id).values(email_verified_at=self._clock()))

    def request_email_verification(self, access_token: str) -> None:
        """替换当前用户的旧验证令牌并投递新令牌。"""
        if self._verification_delivery is None:
            raise EmailVerificationUnavailable
        token = secrets.token_urlsafe(32)
        with self._session_factory.begin() as database:
            user = database.scalar(
                select(_UserRow)
                .join(_AuthSessionRow, _AuthSessionRow.user_id == _UserRow.id)
                .where(
                    _AuthSessionRow.token_hash == _token_hash(access_token),
                    _AuthSessionRow.expires_at > self._clock(),
                )
            )
            if user is None:
                raise InvalidSession
            if user.email_verified_at is not None:
                return
            database.execute(delete(_EmailVerificationRow).where(_EmailVerificationRow.user_id == user.id))
            database.add(
                _EmailVerificationRow(
                    token_hash=_token_hash(token),
                    user_id=user.id,
                    expires_at=self._clock() + self._verification_ttl,
                )
            )
            email = user.email
            self._verification_delivery.send_verification(email, token)

    def change_password(self, access_token: str, current_password: str, new_password: str) -> None:
        """更新当前用户密码并在同一事务中撤销其全部会话。"""
        validated_password = registration_password(new_password)
        with self._session_factory.begin() as database:
            user = database.scalar(
                select(_UserRow)
                .join(_AuthSessionRow, _AuthSessionRow.user_id == _UserRow.id)
                .where(
                    _AuthSessionRow.token_hash == _token_hash(access_token),
                    _AuthSessionRow.expires_at > self._clock(),
                )
            )
            if user is None:
                raise InvalidSession
            if not self._password_hash.verify(current_password, user.password_hash):
                raise InvalidCredentials
            user.password_hash = self._password_hash.hash(validated_password)
            database.execute(delete(_AuthSessionRow).where(_AuthSessionRow.user_id == user.id))

    def credit_balance(self, access_token: str) -> CreditBalance:
        """查询当前用户个人账户空间的整数额度并格式化为四位小数。"""
        with self._session_factory() as database:
            row = database.execute(
                select(
                    _CreditAccountRow.available_credit_units,
                    _CreditAccountRow.frozen_credit_units,
                )
                .join(_AccountSpaceRow, _AccountSpaceRow.id == _CreditAccountRow.account_space_id)
                .join(_AuthSessionRow, _AuthSessionRow.user_id == _AccountSpaceRow.user_id)
                .where(
                    _AuthSessionRow.token_hash == _token_hash(access_token),
                    _AuthSessionRow.expires_at > self._clock(),
                )
            ).one_or_none()
        if row is None:
            raise InvalidSession
        return CreditBalance(
            available_credits=_format_credit_units(row[0]),
            frozen_credits=_format_credit_units(row[1]),
        )

    def list_registered_users(self) -> tuple[RegisteredUser, ...]:
        """按邮箱返回不含认证秘密的注册用户目录。"""
        with self._session_factory() as database:
            rows = database.execute(
                select(
                    _UserRow.id,
                    _AccountSpaceRow.id,
                    _UserRow.email,
                    _UserRow.email_verified_at,
                    _UserRow.created_at,
                )
                .join(_AccountSpaceRow, _AccountSpaceRow.user_id == _UserRow.id)
                .order_by(_UserRow.email)
            ).all()
        return tuple(_registered_user_from_row(tuple(row)) for row in rows)

    def registered_user(self, user_id: str) -> RegisteredUser:
        """按稳定用户标识读取注册用户。"""
        with self._session_factory() as database:
            row = database.execute(
                select(
                    _UserRow.id,
                    _AccountSpaceRow.id,
                    _UserRow.email,
                    _UserRow.email_verified_at,
                    _UserRow.created_at,
                )
                .join(_AccountSpaceRow, _AccountSpaceRow.user_id == _UserRow.id)
                .where(_UserRow.id == user_id)
            ).one_or_none()
        if row is None:
            raise KeyError(user_id)
        return _registered_user_from_row(tuple(row))

    def registered_user_by_email(self, email: str) -> RegisteredUser:
        """按规范化邮箱读取注册用户。"""
        with self._session_factory() as database:
            row = database.execute(
                select(
                    _UserRow.id,
                    _AccountSpaceRow.id,
                    _UserRow.email,
                    _UserRow.email_verified_at,
                    _UserRow.created_at,
                )
                .join(_AccountSpaceRow, _AccountSpaceRow.user_id == _UserRow.id)
                .where(_UserRow.email == email.strip().casefold())
            ).one_or_none()
        if row is None:
            raise KeyError(email)
        return _registered_user_from_row(tuple(row))


def _token_hash(access_token: str) -> str:
    """返回不透明访问令牌的 SHA-256 摘要。"""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _delivery_is_available(delivery: EmailVerificationDelivery | None) -> bool:
    if delivery is None:
        return False
    availability = getattr(delivery, "is_available", None)
    return not callable(availability) or bool(availability())


def _format_credit_units(value: int) -> str:
    """把整数额度子单位格式化为四位小数字符串。"""
    whole, fraction = divmod(value, 10_000)
    return f"{whole}.{fraction:04d}"


def _registered_user_from_row(values: tuple[object, ...]) -> RegisteredUser:
    created_at = values[4]
    if not isinstance(created_at, datetime):
        raise RuntimeError("用户注册时间无效")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return RegisteredUser(
        user_id=str(values[0]),
        account_space_id=str(values[1]),
        email=str(values[2]),
        email_verified=values[3] is not None,
        registered_at=created_at,
    )

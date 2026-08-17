"""Administrator-managed SMTP settings with isolated password storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, sessionmaker

from app.accounts import EmailVerificationUnavailable, SmtpEmailVerificationDelivery
from app.model_routing import ProviderSecrets


class InvalidEmailSettings(ValueError):
    """The requested public URL or SMTP settings are unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class EmailSettingsSnapshot:
    """Public administrator projection that never contains the SMTP password."""

    configured: bool
    public_base_url: str
    smtp_host: str
    smtp_port: int
    smtp_sender: str
    smtp_username: str
    password_configured: bool
    smtp_security: str
    smtp_timeout_seconds: float
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class EmailSettingsUpdate:
    """A complete replacement of non-secret SMTP settings and optional password rotation."""

    public_base_url: str
    smtp_host: str
    smtp_port: int
    smtp_sender: str
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_timeout_seconds: float = 10.0


class EmailSettings(Protocol):
    """Read, update and consume the platform email configuration."""

    def current(self) -> EmailSettingsSnapshot:
        """Return the administrator-safe current settings."""

    def update(self, command: EmailSettingsUpdate) -> EmailSettingsSnapshot:
        """Validate and persist settings, rotating the password when supplied."""

    def is_available(self) -> bool:
        """Return whether verification mail can currently be sent."""

    def send_verification(self, email: str, token: str) -> None:
        """Send a verification message using the latest persisted settings."""


class InMemoryEmailSettings:
    """Thread-safe administrator email settings for HTTP tests."""

    def __init__(self) -> None:
        """Create an initially unavailable in-memory configuration."""
        self._snapshot = _empty_snapshot()
        self._password = ""
        self._lock = RLock()

    def current(self) -> EmailSettingsSnapshot:
        """Return the administrator-safe in-memory settings."""
        with self._lock:
            return self._snapshot

    def update(self, command: EmailSettingsUpdate) -> EmailSettingsSnapshot:
        """Validate and replace the in-memory settings."""
        values = _validated(command)
        with self._lock:
            password = command.smtp_password or self._password
            if values.smtp_username and not password:
                raise InvalidEmailSettings("SMTP 用户名已填写时必须配置 SMTP 密码")
            if not values.smtp_username:
                password = ""
            self._password = password
            self._snapshot = EmailSettingsSnapshot(
                configured=True,
                public_base_url=values.public_base_url,
                smtp_host=values.smtp_host,
                smtp_port=values.smtp_port,
                smtp_sender=values.smtp_sender,
                smtp_username=values.smtp_username,
                password_configured=bool(password),
                smtp_security=values.smtp_security,
                smtp_timeout_seconds=values.smtp_timeout_seconds,
                updated_at=datetime.now(UTC),
            )
            return self._snapshot

    def is_available(self) -> bool:
        """Return whether SMTP has been configured."""
        return self.current().configured

    def send_verification(self, email: str, token: str) -> None:
        """Send with the current in-memory settings."""
        with self._lock:
            snapshot = self._snapshot
            password = self._password
        _delivery(snapshot, password).send_verification(email, token)


_metadata = MetaData()
_settings = Table(
    "platform_email_settings",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("configured", Boolean, nullable=False),
    Column("public_base_url", String(1024), nullable=False),
    Column("smtp_host", String(255), nullable=False),
    Column("smtp_port", Integer, nullable=False),
    Column("smtp_sender", String(320), nullable=False),
    Column("smtp_username", String(320), nullable=False),
    Column("smtp_password_secret_ref", String(1024), nullable=True),
    Column("smtp_security", String(16), nullable=False),
    Column("smtp_timeout_seconds", Float, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)


class SqlAlchemyEmailSettings:
    """Persist public SMTP settings in SQL and its password in the secret adapter."""

    def __init__(self, sessions: sessionmaker[Session], secrets: ProviderSecrets) -> None:
        """Use shared SQL sessions and a separately controlled secret adapter."""
        self._sessions = sessions
        self._secrets = secrets

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        secrets: ProviderSecrets,
        *,
        initialize_schema: bool = False,
    ) -> SqlAlchemyEmailSettings:
        """Create an adapter for tests or an already-migrated database."""
        engine = create_engine(database_url)
        if initialize_schema:
            _metadata.create_all(engine)
        return cls(sessionmaker(engine, expire_on_commit=False), secrets)

    def current(self) -> EmailSettingsSnapshot:
        """Return the persisted settings without selecting the password value."""
        with self._sessions() as database:
            row = database.execute(
                select(_settings).where(_settings.c.settings_key == "global")
            ).mappings().one_or_none()
        return _snapshot_from_row(row) if row is not None else _empty_snapshot()

    def update(self, command: EmailSettingsUpdate) -> EmailSettingsSnapshot:
        """Persist validated metadata and rotate the isolated password if supplied."""
        values = _validated(command)
        new_secret_ref: str | None = None
        old_secret_ref: str | None = None
        committed_secret_ref: str | None = None
        now = datetime.now(UTC)
        try:
            with self._sessions.begin() as database:
                row = database.execute(
                    select(_settings)
                    .where(_settings.c.settings_key == "global")
                    .with_for_update()
                ).mappings().one_or_none()
                old_secret_ref = str(row["smtp_password_secret_ref"]) if row and row["smtp_password_secret_ref"] else None
                secret_ref = old_secret_ref
                if command.smtp_password:
                    stored = self._secrets.store(f"platform-smtp-{uuid4()}", command.smtp_password)
                    new_secret_ref = stored.secret_ref
                    secret_ref = new_secret_ref
                if values.smtp_username and not secret_ref:
                    raise InvalidEmailSettings("SMTP 用户名已填写时必须配置 SMTP 密码")
                if not values.smtp_username:
                    secret_ref = None
                committed_secret_ref = secret_ref
                payload = {
                    "configured": True,
                    "public_base_url": values.public_base_url,
                    "smtp_host": values.smtp_host,
                    "smtp_port": values.smtp_port,
                    "smtp_sender": values.smtp_sender,
                    "smtp_username": values.smtp_username,
                    "smtp_password_secret_ref": secret_ref,
                    "smtp_security": values.smtp_security,
                    "smtp_timeout_seconds": values.smtp_timeout_seconds,
                    "updated_at": now,
                }
                if row is None:
                    database.execute(insert(_settings).values(settings_key="global", **payload))
                else:
                    database.execute(
                        update(_settings).where(_settings.c.settings_key == "global").values(**payload)
                    )
        except BaseException:
            if new_secret_ref is not None:
                self._secrets.delete(new_secret_ref)
            raise
        if old_secret_ref is not None and old_secret_ref != committed_secret_ref:
            self._secrets.delete(old_secret_ref)
        return self.current()

    def is_available(self) -> bool:
        """Return whether the global settings row is configured."""
        return self.current().configured

    def send_verification(self, email: str, token: str) -> None:
        """Resolve the latest password only for the SMTP call."""
        with self._sessions() as database:
            row = database.execute(
                select(_settings).where(_settings.c.settings_key == "global")
            ).mappings().one_or_none()
        if row is None or not bool(row["configured"]):
            raise EmailVerificationUnavailable
        snapshot = _snapshot_from_row(row)
        secret_ref = str(row["smtp_password_secret_ref"]) if row["smtp_password_secret_ref"] else ""
        password = self._secrets.read(secret_ref) if secret_ref else ""
        _delivery(snapshot, password).send_verification(email, token)


def _validated(command: EmailSettingsUpdate) -> EmailSettingsUpdate:
    public_base_url = command.public_base_url.strip().rstrip("/")
    parsed = urlsplit(public_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise InvalidEmailSettings("公开站点地址必须是没有路径、查询或凭据的 HTTPS Origin")
    host = command.smtp_host.strip()
    sender = command.smtp_sender.strip()
    username = command.smtp_username.strip()
    security = command.smtp_security.strip().casefold()
    if not host or len(host) > 255:
        raise InvalidEmailSettings("SMTP 主机不能为空")
    if not sender or "@" not in sender or len(sender) > 320:
        raise InvalidEmailSettings("SMTP 发件地址无效")
    if not 1 <= command.smtp_port <= 65535:
        raise InvalidEmailSettings("SMTP 端口必须在 1 到 65535 之间")
    if security not in {"starttls", "ssl", "none"}:
        raise InvalidEmailSettings("SMTP 安全模式必须是 starttls、ssl 或 none")
    if not 1 <= command.smtp_timeout_seconds <= 120:
        raise InvalidEmailSettings("SMTP 超时必须在 1 到 120 秒之间")
    return EmailSettingsUpdate(
        public_base_url=public_base_url,
        smtp_host=host,
        smtp_port=command.smtp_port,
        smtp_sender=sender,
        smtp_username=username,
        smtp_password=command.smtp_password,
        smtp_security=security,
        smtp_timeout_seconds=command.smtp_timeout_seconds,
    )


def _snapshot_from_row(row: object) -> EmailSettingsSnapshot:
    mapping = row
    updated_at = mapping["updated_at"]  # type: ignore[index]
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return EmailSettingsSnapshot(
        configured=bool(mapping["configured"]),  # type: ignore[index]
        public_base_url=str(mapping["public_base_url"]),  # type: ignore[index]
        smtp_host=str(mapping["smtp_host"]),  # type: ignore[index]
        smtp_port=int(mapping["smtp_port"]),  # type: ignore[index]
        smtp_sender=str(mapping["smtp_sender"]),  # type: ignore[index]
        smtp_username=str(mapping["smtp_username"]),  # type: ignore[index]
        password_configured=bool(mapping["smtp_password_secret_ref"]),  # type: ignore[index]
        smtp_security=str(mapping["smtp_security"]),  # type: ignore[index]
        smtp_timeout_seconds=float(mapping["smtp_timeout_seconds"]),  # type: ignore[index]
        updated_at=updated_at if isinstance(updated_at, datetime) else None,
    )


def _empty_snapshot() -> EmailSettingsSnapshot:
    return EmailSettingsSnapshot(False, "", "", 587, "", "", False, "starttls", 10.0, None)


def _delivery(snapshot: EmailSettingsSnapshot, password: str) -> SmtpEmailVerificationDelivery:
    if not snapshot.configured:
        raise EmailVerificationUnavailable
    return SmtpEmailVerificationDelivery(
        host=snapshot.smtp_host,
        port=snapshot.smtp_port,
        sender=snapshot.smtp_sender,
        public_base_url=snapshot.public_base_url,
        username=snapshot.smtp_username,
        password=password,
        security=snapshot.smtp_security,
        timeout_seconds=snapshot.smtp_timeout_seconds,
    )

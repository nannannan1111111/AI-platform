"""Administrator-managed announcement and customer-service content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.media import MediaContentStore


class InvalidPlatformContent(ValueError):
    """The requested public platform content is invalid."""


@dataclass(frozen=True, slots=True)
class PlatformContentSnapshot:
    """Public content configuration without stored media internals."""

    announcement_text: str
    announcement_image_configured: bool
    support_text: str
    support_image_configured: bool
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlatformContentUpdate:
    """Administrator request to replace text and optional content images."""

    announcement_text: str
    support_text: str
    announcement_image: bytes | None = None
    announcement_image_mime: str = ""
    support_image: bytes | None = None
    support_image_mime: str = ""
    remove_announcement_image: bool = False
    remove_support_image: bool = False


class PlatformContentSettings(Protocol):
    """Read and update the platform announcement and support content."""

    def current(self) -> PlatformContentSnapshot:
        """Return the current public content snapshot."""

    def update(self, command: PlatformContentUpdate) -> PlatformContentSnapshot:
        """Apply an administrator-owned content update."""

    def image(self, kind: str) -> tuple[bytes, str]:
        """Read one configured announcement or support image."""


class InMemoryPlatformContentSettings:
    """In-memory platform-content adapter used by tests."""

    def __init__(self) -> None:
        """Initialize empty text and media state."""
        self._snapshot = _empty_snapshot()
        self._images: dict[str, tuple[bytes, str]] = {}
        self._lock = RLock()

    def current(self) -> PlatformContentSnapshot:
        """Return the current public content snapshot."""
        with self._lock:
            return self._snapshot

    def update(self, command: PlatformContentUpdate) -> PlatformContentSnapshot:
        """Validate and apply an in-memory content update."""
        values = _validated(command)
        with self._lock:
            _update_memory_image(self._images, "announcement", values.announcement_image, values.announcement_image_mime, values.remove_announcement_image)
            _update_memory_image(self._images, "support", values.support_image, values.support_image_mime, values.remove_support_image)
            self._snapshot = PlatformContentSnapshot(
                announcement_text=values.announcement_text,
                announcement_image_configured="announcement" in self._images,
                support_text=values.support_text,
                support_image_configured="support" in self._images,
                updated_at=datetime.now(UTC),
            )
            return self._snapshot

    def image(self, kind: str) -> tuple[bytes, str]:
        """Return one configured content image."""
        with self._lock:
            if kind not in self._images:
                raise KeyError(kind)
            return self._images[kind]


_metadata = MetaData()
_settings = Table(
    "platform_content_settings",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("announcement_text", Text, nullable=False),
    Column("announcement_image_object_key", String(1024), nullable=True),
    Column("announcement_image_mime", String(64), nullable=True),
    Column("support_text", Text, nullable=False),
    Column("support_image_object_key", String(1024), nullable=True),
    Column("support_image_mime", String(64), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)


class SqlAlchemyPlatformContentSettings:
    """Database-backed content adapter with media-object storage."""

    def __init__(self, sessions: sessionmaker[Session], media_objects: MediaContentStore) -> None:
        """Retain the shared sessions and media store."""
        self._sessions = sessions
        self._media_objects = media_objects

    @classmethod
    def for_database_url(cls, database_url: str, media_objects: MediaContentStore, *, initialize_schema: bool = False) -> SqlAlchemyPlatformContentSettings:
        """Create an adapter for tests and standalone scripts."""
        engine = create_engine(database_url)
        if initialize_schema:
            _metadata.create_all(engine)
        return cls(sessionmaker(engine, expire_on_commit=False), media_objects)

    def current(self) -> PlatformContentSnapshot:
        """Read the current content snapshot."""
        with self._sessions() as database:
            row = database.execute(select(_settings).where(_settings.c.settings_key == "global")).mappings().one_or_none()
        return _snapshot_from_row(row) if row is not None else _empty_snapshot()

    def update(self, command: PlatformContentUpdate) -> PlatformContentSnapshot:
        """Atomically persist text and coordinate media replacement."""
        values = _validated(command)
        created_keys: list[str] = []
        obsolete_keys: list[str] = []
        try:
            with self._sessions.begin() as database:
                row = database.execute(select(_settings).where(_settings.c.settings_key == "global").with_for_update()).mappings().one_or_none()
                payload: dict[str, object] = {
                    "announcement_text": values.announcement_text,
                    "support_text": values.support_text,
                    "updated_at": datetime.now(UTC),
                }
                for kind, content, mime, remove in (
                    ("announcement", values.announcement_image, values.announcement_image_mime, values.remove_announcement_image),
                    ("support", values.support_image, values.support_image_mime, values.remove_support_image),
                ):
                    key_column = f"{kind}_image_object_key"
                    mime_column = f"{kind}_image_mime"
                    old_key = str(row[key_column]) if row and row[key_column] else ""
                    if content is not None:
                        stored = self._media_objects.put_temporary(
                            account_space_id="platform-content",
                            task_id="global",
                            result_reference=f"{kind}-{uuid4()}",
                            content=content,
                            mime_type=mime,
                        )
                        created_keys.append(stored.object_key)
                        payload[key_column] = stored.object_key
                        payload[mime_column] = mime
                        if old_key:
                            obsolete_keys.append(old_key)
                    elif remove:
                        payload[key_column] = None
                        payload[mime_column] = None
                        if old_key:
                            obsolete_keys.append(old_key)
                    elif row is None:
                        payload[key_column] = None
                        payload[mime_column] = None
                if row is None:
                    database.execute(insert(_settings).values(settings_key="global", **payload))
                else:
                    database.execute(update(_settings).where(_settings.c.settings_key == "global").values(**payload))
        except BaseException:
            for key in created_keys:
                self._media_objects.delete(key)
            raise
        for key in obsolete_keys:
            self._media_objects.delete(key)
        return self.current()

    def image(self, kind: str) -> tuple[bytes, str]:
        """Read one configured content image from object storage."""
        if kind not in {"announcement", "support"}:
            raise KeyError(kind)
        with self._sessions() as database:
            row = database.execute(select(_settings).where(_settings.c.settings_key == "global")).mappings().one_or_none()
        if row is None or not row[f"{kind}_image_object_key"]:
            raise KeyError(kind)
        return self._media_objects.read(str(row[f"{kind}_image_object_key"])), str(row[f"{kind}_image_mime"])


def _validated(command: PlatformContentUpdate) -> PlatformContentUpdate:
    announcement_text = command.announcement_text.strip()
    support_text = command.support_text.strip()
    if len(announcement_text) > 10_000 or len(support_text) > 10_000:
        raise InvalidPlatformContent("公告和客服文字均不能超过 10000 个字符")
    announcement_image, announcement_mime = _validated_image(command.announcement_image, command.announcement_image_mime)
    support_image, support_mime = _validated_image(command.support_image, command.support_image_mime)
    return PlatformContentUpdate(
        announcement_text=announcement_text,
        support_text=support_text,
        announcement_image=announcement_image,
        announcement_image_mime=announcement_mime,
        support_image=support_image,
        support_image_mime=support_mime,
        remove_announcement_image=command.remove_announcement_image,
        remove_support_image=command.remove_support_image,
    )


def _validated_image(content: bytes | None, mime_type: str) -> tuple[bytes | None, str]:
    if content is None:
        return None, ""
    raw = bytes(content)
    normalized = mime_type.strip().casefold()
    if normalized not in {"image/png", "image/jpeg", "image/webp"}:
        raise InvalidPlatformContent("内容图片仅支持 PNG、JPEG 或 WebP")
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise InvalidPlatformContent("内容图片必须小于等于 5MB")
    return raw, normalized


def _update_memory_image(images: dict[str, tuple[bytes, str]], kind: str, content: bytes | None, mime: str, remove: bool) -> None:
    if content is not None:
        images[kind] = (content, mime)
    elif remove:
        images.pop(kind, None)


def _snapshot_from_row(row: object) -> PlatformContentSnapshot:
    return PlatformContentSnapshot(
        announcement_text=str(row["announcement_text"]),  # type: ignore[index]
        announcement_image_configured=bool(row["announcement_image_object_key"]),  # type: ignore[index]
        support_text=str(row["support_text"]),  # type: ignore[index]
        support_image_configured=bool(row["support_image_object_key"]),  # type: ignore[index]
        updated_at=row["updated_at"],  # type: ignore[index]
    )


def _empty_snapshot() -> PlatformContentSnapshot:
    return PlatformContentSnapshot("", False, "", False, None)

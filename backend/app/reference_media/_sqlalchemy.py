"""SQLAlchemy Adapter for account-owned temporary reference media."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.media import MediaContentStore
from app.reference_media._validation import validated_reference_media
from app.reference_media.models import (
    InvalidReferenceMedia,
    ReferenceMediaContent,
    ReferenceMediaExpired,
    ReferenceMediaNotFound,
    ReferenceMediaOrigin,
    ReferenceMediaRecord,
    ReferenceMediaState,
    ReferenceMediaUpload,
)

_metadata = MetaData()
_reference_media = Table(
    "reference_media",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("object_key", String(1024), nullable=False),
    Column("mime_type", String(128), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyReferenceMedia:
    """Persist reference metadata in SQL and bytes in the configured media store."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        media_objects: MediaContentStore,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._media_objects = media_objects
        self._id_factory = id_factory or (lambda: str(uuid4()))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        media_objects: MediaContentStore,
        id_factory: Callable[[], str] | None = None,
    ) -> SqlAlchemyReferenceMedia:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            media_objects=media_objects,
            id_factory=id_factory,
        )

    def upload(self, upload: ReferenceMediaUpload) -> ReferenceMediaRecord:
        media_id = self._id_factory()
        provisional = validated_reference_media(upload, media_id=media_id, object_key="pending")
        stored = self._media_objects.put_temporary(
            account_space_id=upload.account_space_id,
            task_id="reference-media",
            result_reference=media_id,
            content=upload.content,
            mime_type=provisional.mime_type,
        )
        media = replace(
            provisional,
            object_key=stored.object_key,
            size_bytes=stored.size_bytes,
            content_hash=stored.content_hash,
        )
        try:
            with self._session_factory.begin() as database:
                database.execute(insert(_reference_media).values(**_media_values(media)))
        except IntegrityError as exc:
            self._media_objects.delete(stored.object_key)
            raise InvalidReferenceMedia("参考图片标识冲突") from exc
        return media

    def read(self, account_space_id: str, media_id: str, *, at: datetime) -> ReferenceMediaContent:
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_reference_media).where(
                        _reference_media.c.account_space_id == account_space_id,
                        _reference_media.c.id == media_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ReferenceMediaNotFound(media_id)
        media = _media_from_row(row)
        if media.state is ReferenceMediaState.DELETED:
            raise ReferenceMediaNotFound(media_id)
        if media.state is ReferenceMediaState.EXPIRED or at >= media.expires_at:
            raise ReferenceMediaExpired(media_id)
        try:
            content = self._media_objects.read(media.object_key)
        except OSError as exc:
            raise ReferenceMediaNotFound(media_id) from exc
        return ReferenceMediaContent(media, content)

    def list_recent(self, account_space_id: str, *, at: datetime) -> tuple[ReferenceMediaRecord, ...]:
        with self._session_factory() as database:
            rows = database.execute(
                select(_reference_media)
                .where(
                    _reference_media.c.account_space_id == account_space_id,
                    _reference_media.c.state == ReferenceMediaState.TEMPORARY.value,
                    _reference_media.c.origin == ReferenceMediaOrigin.STANDALONE.value,
                    _reference_media.c.expires_at > at,
                )
                .order_by(_reference_media.c.created_at, _reference_media.c.id)
            ).mappings()
            return tuple(_media_from_row(row) for row in rows)

    def delete(self, account_space_id: str, media_id: str) -> ReferenceMediaRecord:
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_reference_media)
                    .where(
                        _reference_media.c.account_space_id == account_space_id,
                        _reference_media.c.id == media_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ReferenceMediaNotFound(media_id)
            media = _media_from_row(row)
            if media.state is ReferenceMediaState.DELETED:
                return media
            self._media_objects.delete(media.object_key)
            deleted = replace(media, state=ReferenceMediaState.DELETED)
            database.execute(
                update(_reference_media)
                .where(_reference_media.c.id == media_id)
                .values(state=ReferenceMediaState.DELETED.value)
            )
            return deleted


def _media_values(media: ReferenceMediaRecord) -> dict[str, object]:
    return {
        "id": media.media_id,
        "user_id": media.user_id,
        "account_space_id": media.account_space_id,
        "original_name": media.original_name,
        "object_key": media.object_key,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "content_hash": media.content_hash,
        "state": media.state.value,
        "origin": media.origin.value,
        "created_at": media.created_at,
        "expires_at": media.expires_at,
    }


def _media_from_row(row: Any) -> ReferenceMediaRecord:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return ReferenceMediaRecord(
        media_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        original_name=str(row["original_name"]),
        object_key=str(row["object_key"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        content_hash=str(row["content_hash"]),
        state=ReferenceMediaState(str(row["state"])),
        origin=ReferenceMediaOrigin(str(row["origin"])),
        created_at=aware(row["created_at"]),
        expires_at=aware(row["expires_at"]),
    )

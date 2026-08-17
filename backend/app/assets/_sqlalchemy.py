"""SQLAlchemy Adapter for the PersonalAssets Interface."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, sessionmaker

from app.assets._validation import matches_replay, validated_rename, validated_save
from app.assets.models import (
    PersonalAsset,
    PersonalAssetConflict,
    PersonalAssetNotFound,
    PersonalAssetRename,
    PersonalAssetSave,
)
from app.media import GeneratedMedia, GeneratedMediaNotFound, GeneratedMediaRecord

_metadata = MetaData()
_personal_assets = Table(
    "personal_assets",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("media_id", String(36), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("state", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("removed_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("account_space_id", "idempotency_key"),
)


class SqlAlchemyPersonalAssets:
    """Persist account-owned personal assets and idempotency state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        generated_media: GeneratedMedia,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._generated_media = generated_media
        self._id_factory = id_factory or (lambda: str(uuid4()))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        generated_media: GeneratedMedia,
        id_factory: Callable[[], str] | None = None,
    ) -> SqlAlchemyPersonalAssets:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            generated_media=generated_media,
            id_factory=id_factory,
        )

    def save_generated_media(self, command: PersonalAssetSave) -> PersonalAsset:
        normalized = validated_save(command)
        media = self._generated_media.get(normalized.account_space_id, normalized.media_id)
        if media.user_id != normalized.user_id:
            raise GeneratedMediaNotFound(normalized.media_id)
        with self._session_factory.begin() as database:
            row = _by_key(database, normalized.account_space_id, normalized.idempotency_key)
            if row is None:
                values = {
                    "id": self._id_factory(),
                    "user_id": normalized.user_id,
                    "account_space_id": normalized.account_space_id,
                    "media_id": normalized.media_id,
                    "display_name": normalized.display_name,
                    "idempotency_key": normalized.idempotency_key,
                    "state": "pending",
                    "created_at": normalized.saved_at,
                    "removed_at": None,
                }
                database.execute(insert(_personal_assets).values(**values))
                row = values
            elif str(row["state"]) in {"removing", "removed"}:
                raise PersonalAssetConflict(normalized.idempotency_key)
            elif not matches_replay(
                _command_from_row(row),
                normalized,
                include_display_name=str(row["state"]) == "pending",
            ):
                raise PersonalAssetConflict(normalized.idempotency_key)
            if str(row["state"]) == "active":
                return _asset_from_row(row, media)
            asset_id = str(row["id"])
        retained = self._generated_media.retain_to_personal_asset(
            normalized.account_space_id,
            normalized.media_id,
            asset_id,
            normalized.saved_at,
        )
        with self._session_factory.begin() as database:
            database.execute(
                update(_personal_assets)
                .where(
                    _personal_assets.c.account_space_id == normalized.account_space_id,
                    _personal_assets.c.id == asset_id,
                    _personal_assets.c.state == "pending",
                )
                .values(state="active")
            )
            active = _by_id(database, normalized.account_space_id, asset_id)
            assert active is not None
            return _asset_from_row(active, retained)

    def list(self, account_space_id: str) -> tuple[PersonalAsset, ...]:
        with self._session_factory() as database:
            rows = tuple(
                database.execute(
                    select(_personal_assets)
                    .where(
                        _personal_assets.c.account_space_id == account_space_id,
                        _personal_assets.c.state == "active",
                    )
                    .order_by(_personal_assets.c.created_at, _personal_assets.c.id)
                ).mappings()
            )
        return tuple(
            _asset_from_row(row, self._generated_media.get(account_space_id, str(row["media_id"]))) for row in rows
        )

    def rename(self, command: PersonalAssetRename) -> PersonalAsset:
        normalized = validated_rename(command)
        with self._session_factory.begin() as database:
            existing = _active_by_id(database, normalized.account_space_id, normalized.asset_id)
            if existing is None:
                raise PersonalAssetNotFound(normalized.asset_id)
            database.execute(
                update(_personal_assets)
                .where(
                    _personal_assets.c.account_space_id == normalized.account_space_id,
                    _personal_assets.c.id == normalized.asset_id,
                    _personal_assets.c.state == "active",
                )
                .values(display_name=normalized.display_name)
            )
            renamed = _active_by_id(database, normalized.account_space_id, normalized.asset_id)
            assert renamed is not None
        media = self._generated_media.get(normalized.account_space_id, str(renamed["media_id"]))
        return _asset_from_row(renamed, media)

    def remove(self, account_space_id: str, asset_id: str, removed_at: datetime) -> None:
        normalized_account_space_id = account_space_id.strip()
        normalized_asset_id = asset_id.strip()
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_personal_assets)
                    .where(
                        _personal_assets.c.account_space_id == normalized_account_space_id,
                        _personal_assets.c.id == normalized_asset_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or str(row["state"]) == "pending":
                raise PersonalAssetNotFound(normalized_asset_id)
            state = str(row["state"])
            if state == "removed":
                return
            media_id = str(row["media_id"])
            if state == "active":
                database.execute(
                    update(_personal_assets)
                    .where(
                        _personal_assets.c.account_space_id == normalized_account_space_id,
                        _personal_assets.c.id == normalized_asset_id,
                        _personal_assets.c.state == "active",
                    )
                    .values(state="removing", removed_at=removed_at)
                )
        self._generated_media.release_from_personal_asset(
            normalized_account_space_id,
            media_id,
            normalized_asset_id,
            removed_at,
        )
        with self._session_factory.begin() as database:
            database.execute(
                update(_personal_assets)
                .where(
                    _personal_assets.c.account_space_id == normalized_account_space_id,
                    _personal_assets.c.id == normalized_asset_id,
                    _personal_assets.c.state == "removing",
                )
                .values(state="removed")
            )


def _by_key(database: Session, account_space_id: str, idempotency_key: str) -> Any:
    return (
        database.execute(
            select(_personal_assets).where(
                _personal_assets.c.account_space_id == account_space_id,
                _personal_assets.c.idempotency_key == idempotency_key,
            )
        )
        .mappings()
        .one_or_none()
    )


def _by_id(database: Session, account_space_id: str, asset_id: str) -> Any:
    return (
        database.execute(
            select(_personal_assets).where(
                _personal_assets.c.account_space_id == account_space_id,
                _personal_assets.c.id == asset_id,
            )
        )
        .mappings()
        .one_or_none()
    )


def _active_by_id(database: Session, account_space_id: str, asset_id: str) -> Any:
    return (
        database.execute(
            select(_personal_assets).where(
                _personal_assets.c.account_space_id == account_space_id,
                _personal_assets.c.id == asset_id,
                _personal_assets.c.state == "active",
            )
        )
        .mappings()
        .one_or_none()
    )


def _command_from_row(row: Any) -> PersonalAssetSave:
    created_at = row["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return PersonalAssetSave(
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        media_id=str(row["media_id"]),
        display_name=str(row["display_name"]),
        idempotency_key=str(row["idempotency_key"]),
        saved_at=created_at,
    )


def _asset_from_row(row: Any, media: GeneratedMediaRecord) -> PersonalAsset:
    created_at: datetime = row["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return PersonalAsset(
        asset_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        media_id=media.media_id,
        display_name=str(row["display_name"]),
        kind=media.kind,
        mime_type=media.mime_type,
        size_bytes=media.size_bytes,
        created_at=created_at,
    )

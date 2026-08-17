"""SQLAlchemy Adapter for the GeneratedMedia Interface."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.generation import GenerationTasks
from app.media._validation import (
    matches_registration,
    validate_running_task,
    validated_canvas_image_mime,
    validated_media,
)
from app.media.allowances import StorageAllowances
from app.media.models import (
    CanvasMediaUpload,
    GeneratedMediaConflict,
    GeneratedMediaKind,
    GeneratedMediaNotDeletable,
    GeneratedMediaNotFound,
    GeneratedMediaNotRetainable,
    GeneratedMediaRecord,
    GeneratedMediaRegistration,
    GeneratedMediaState,
    MediaExpirationReport,
    MediaReferenceReconciliation,
    StorageAllowance,
    StorageAllowanceExceeded,
)
from app.media.objects import MediaContentStore, MediaObjectDeletionFailed

_metadata = MetaData()
_generated_media = Table(
    "generated_media",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("canvas_id", String(36), nullable=True),
    Column("task_id", String(255), nullable=True),
    Column("result_reference", String(255), nullable=False),
    Column("object_key", String(1024), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("mime_type", String(128), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("retained_at", DateTime(timezone=True), nullable=True),
    Column("released_at", DateTime(timezone=True), nullable=True),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("account_space_id", "task_id", "result_reference"),
)
_account_spaces = Table(
    "account_spaces",
    _metadata,
    Column("id", String(36), primary_key=True),
)
_canvas_media_references = Table(
    "canvas_media_references",
    _metadata,
    Column("account_space_id", String(36), primary_key=True),
    Column("canvas_id", String(36), primary_key=True),
    Column("media_id", String(36), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
_personal_assets = Table(
    "personal_assets",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("account_space_id", String(36), nullable=False),
    Column("media_id", String(36), nullable=False),
    Column("state", String(16), nullable=False),
)


class SqlAlchemyGeneratedMedia:
    """Persist account-owned temporary generated media metadata."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        generation_tasks: GenerationTasks,
        media_objects: MediaContentStore,
        storage_allowances: StorageAllowances,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._generation_tasks = generation_tasks
        self._media_objects = media_objects
        self._storage_allowances = storage_allowances
        self._id_factory = id_factory or (lambda: str(uuid4()))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        generation_tasks: GenerationTasks,
        media_objects: MediaContentStore,
        storage_allowances: StorageAllowances,
        id_factory: Callable[[], str] | None = None,
    ) -> SqlAlchemyGeneratedMedia:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            generation_tasks=generation_tasks,
            media_objects=media_objects,
            storage_allowances=storage_allowances,
            id_factory=id_factory,
        )

    def register(self, registration: GeneratedMediaRegistration) -> GeneratedMediaRecord:
        key = (registration.account_space_id, registration.task_id, registration.result_reference)
        with self._session_factory.begin() as database:
            existing_row = _by_result(database, *key)
            if existing_row is not None:
                return _replayed_or_conflict(_media_from_row(existing_row), registration)
            task = self._generation_tasks.get(registration.account_space_id, registration.task_id)
            validate_running_task(task, registration)
            media = validated_media(registration, self._id_factory())
            try:
                with database.begin_nested():
                    database.execute(insert(_generated_media).values(**_media_values(media)))
            except IntegrityError:
                raced_row = _by_result(database, *key)
                if raced_row is None:
                    raise GeneratedMediaConflict(registration.result_reference) from None
                return _replayed_or_conflict(_media_from_row(raced_row), registration)
            return media

    def upload_to_canvas(self, upload: CanvasMediaUpload) -> GeneratedMediaRecord:
        """Persist one owned canvas image as quota-counted persistent media."""
        mime_type = validated_canvas_image_mime(upload)
        media_id = self._id_factory()
        stored = self._media_objects.put_temporary(
            account_space_id=upload.account_space_id,
            task_id=f"canvas-upload-{upload.canvas_id}",
            result_reference=media_id,
            content=upload.content,
            mime_type=mime_type,
        )
        with self._session_factory.begin() as database:
            database.execute(
                select(_account_spaces.c.id).where(_account_spaces.c.id == upload.account_space_id).with_for_update()
            ).scalar_one_or_none()
            active_sizes = {
                str(content_hash): int(size_bytes)
                for content_hash, size_bytes in database.execute(
                    select(_generated_media.c.content_hash, func.max(_generated_media.c.size_bytes))
                    .where(
                        _generated_media.c.account_space_id == upload.account_space_id,
                        _generated_media.c.state.in_(
                            (GeneratedMediaState.TEMPORARY.value, GeneratedMediaState.PERSISTENT.value)
                        ),
                    )
                    .group_by(_generated_media.c.content_hash)
                )
            }
            used_bytes = sum(active_sizes.values())
            if stored.content_hash not in active_sizes:
                used_bytes += stored.size_bytes
            if used_bytes > self._storage_allowances.limit_bytes(upload.account_space_id):
                self._media_objects.delete(stored.object_key)
                raise StorageAllowanceExceeded(upload.account_space_id)
            persistent_key = f"persistent/{upload.account_space_id}/{stored.content_hash}"
            self._media_objects.promote(stored.object_key, persistent_key)
            media = GeneratedMediaRecord(
                media_id=media_id,
                user_id=upload.user_id,
                account_space_id=upload.account_space_id,
                canvas_id=upload.canvas_id,
                task_id=None,
                result_reference=media_id,
                object_key=persistent_key,
                kind=GeneratedMediaKind.IMAGE,
                mime_type=mime_type,
                size_bytes=stored.size_bytes,
                content_hash=stored.content_hash,
                state=GeneratedMediaState.PERSISTENT,
                created_at=upload.created_at,
                expires_at=None,
                retained_at=upload.created_at,
            )
            database.execute(insert(_generated_media).values(**_media_values(media)))
            database.execute(
                insert(_canvas_media_references).values(
                    account_space_id=upload.account_space_id,
                    canvas_id=upload.canvas_id,
                    media_id=media_id,
                    created_at=upload.created_at,
                )
            )
            return media

    def get(self, account_space_id: str, media_id: str) -> GeneratedMediaRecord:
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_generated_media).where(
                        _generated_media.c.account_space_id == account_space_id,
                        _generated_media.c.id == media_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise GeneratedMediaNotFound(media_id)
        return _media_from_row(row)

    def list_for_task(self, account_space_id: str, task_id: str) -> tuple[GeneratedMediaRecord, ...]:
        self._generation_tasks.get(account_space_id, task_id)
        with self._session_factory() as database:
            rows = database.execute(
                select(_generated_media)
                .where(
                    _generated_media.c.account_space_id == account_space_id,
                    _generated_media.c.task_id == task_id,
                )
                .order_by(_generated_media.c.created_at, _generated_media.c.id)
            ).mappings()
            return tuple(_media_from_row(row) for row in rows)

    def storage_allowance(self, account_space_id: str) -> StorageAllowance:
        """返回仅计入账户持久媒体且按内容哈希去重的额度快照。"""
        with self._session_factory() as database:
            active_sizes = database.execute(
                select(
                    _generated_media.c.content_hash,
                    func.max(_generated_media.c.size_bytes),
                )
                .where(
                    _generated_media.c.account_space_id == account_space_id,
                    _generated_media.c.state.in_(
                        (
                            GeneratedMediaState.TEMPORARY.value,
                            GeneratedMediaState.PERSISTENT.value,
                        )
                    ),
                )
                .group_by(_generated_media.c.content_hash)
            )
            used_bytes = sum(int(size_bytes) for _, size_bytes in active_sizes)
        limit_bytes = self._storage_allowances.limit_bytes(account_space_id)
        return StorageAllowance(
            limit_bytes=limit_bytes,
            used_bytes=used_bytes,
            available_bytes=max(limit_bytes - used_bytes, 0),
        )

    def expire_due(self, now: datetime) -> MediaExpirationReport:
        with self._session_factory() as database:
            due_ids = tuple(
                database.scalars(
                    select(_generated_media.c.id)
                    .where(
                        _generated_media.c.state == GeneratedMediaState.TEMPORARY.value,
                        _generated_media.c.expires_at <= now,
                    )
                    .order_by(_generated_media.c.expires_at, _generated_media.c.id)
                )
            )
        expired_ids: list[str] = []
        failed_ids: list[str] = []
        for media_id in due_ids:
            try:
                with self._session_factory.begin() as database:
                    row = (
                        database.execute(
                            select(_generated_media)
                            .where(
                                _generated_media.c.id == media_id,
                                _generated_media.c.state == GeneratedMediaState.TEMPORARY.value,
                                _generated_media.c.expires_at <= now,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        continue
                    media = _media_from_row(row)
                    self._media_objects.delete(media.object_key)
                    database.execute(
                        update(_generated_media)
                        .where(
                            _generated_media.c.id == media.media_id,
                            _generated_media.c.state == GeneratedMediaState.TEMPORARY.value,
                        )
                        .values(state=GeneratedMediaState.EXPIRED.value)
                    )
                expired_ids.append(str(media_id))
            except MediaObjectDeletionFailed:
                failed_ids.append(str(media_id))
        return MediaExpirationReport(tuple(expired_ids), tuple(failed_ids))

    def delete(self, account_space_id: str, media_id: str, deleted_at: datetime) -> GeneratedMediaRecord:
        """删除未持久引用的临时对象并持久化删除墓碑。"""
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_generated_media)
                    .where(
                        _generated_media.c.account_space_id == account_space_id,
                        _generated_media.c.id == media_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise GeneratedMediaNotFound(media_id)
            media = _media_from_row(row)
            if media.state is GeneratedMediaState.DELETED:
                return media
            if media.state is not GeneratedMediaState.TEMPORARY:
                raise GeneratedMediaNotDeletable(media_id)
            self._media_objects.delete(media.object_key)
            deleted = replace(media, state=GeneratedMediaState.DELETED, deleted_at=deleted_at)
            database.execute(
                update(_generated_media)
                .where(_generated_media.c.id == media_id)
                .values(state=GeneratedMediaState.DELETED.value, deleted_at=deleted_at)
            )
            return deleted

    def retain_to_canvas(
        self,
        account_space_id: str,
        media_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        with self._session_factory.begin() as database:
            retained = self._retain(database, account_space_id, media_id, retained_at)
            existing_reference = database.execute(
                select(_canvas_media_references.c.media_id)
                .where(
                    _canvas_media_references.c.account_space_id == account_space_id,
                    _canvas_media_references.c.canvas_id == retained.canvas_id,
                    _canvas_media_references.c.media_id == retained.media_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if existing_reference is None:
                database.execute(
                    insert(_canvas_media_references).values(
                        account_space_id=account_space_id,
                        canvas_id=retained.canvas_id,
                        media_id=retained.media_id,
                        created_at=retained_at,
                    )
                )
            return retained

    def reconcile_canvas_references(
        self,
        account_space_id: str,
        canvas_id: str,
        retained_media_ids: Iterable[str],
        occurred_at: datetime,
    ) -> MediaReferenceReconciliation:
        retained = frozenset(retained_media_ids)
        with self._session_factory() as database:
            referenced_ids = tuple(
                str(media_id)
                for media_id in database.scalars(
                    select(_canvas_media_references.c.media_id)
                    .where(
                        _canvas_media_references.c.account_space_id == account_space_id,
                        _canvas_media_references.c.canvas_id == canvas_id,
                    )
                    .order_by(_canvas_media_references.c.media_id)
                )
                if str(media_id) not in retained
            )
        released_ids: list[str] = []
        failed_ids: list[str] = []
        for media_id in referenced_ids:
            try:
                with self._session_factory.begin() as database:
                    database.execute(
                        select(_account_spaces.c.id).where(_account_spaces.c.id == account_space_id).with_for_update()
                    ).scalar_one_or_none()
                    row = (
                        database.execute(
                            select(_generated_media)
                            .where(
                                _generated_media.c.account_space_id == account_space_id,
                                _generated_media.c.id == media_id,
                                _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        continue
                    media = _media_from_row(row)
                    media_still_referenced = (
                        database.execute(
                            select(_canvas_media_references.c.media_id)
                            .where(
                                _canvas_media_references.c.account_space_id == account_space_id,
                                _canvas_media_references.c.canvas_id != canvas_id,
                                _canvas_media_references.c.media_id == media.media_id,
                            )
                            .limit(1)
                        ).scalar_one_or_none()
                        is not None
                    ) or (
                        database.execute(
                            select(_personal_assets.c.id)
                            .where(
                                _personal_assets.c.account_space_id == account_space_id,
                                _personal_assets.c.media_id == media.media_id,
                                _personal_assets.c.state.in_(("pending", "active", "removing")),
                            )
                            .limit(1)
                        ).scalar_one_or_none()
                        is not None
                    )
                    shared_reference_exists = (
                        media_still_referenced
                        or (
                            database.execute(
                                select(_canvas_media_references.c.media_id)
                                .join(
                                    _generated_media,
                                    _generated_media.c.id == _canvas_media_references.c.media_id,
                                )
                                .where(
                                    _canvas_media_references.c.account_space_id == account_space_id,
                                    _canvas_media_references.c.media_id != media.media_id,
                                    _generated_media.c.content_hash == media.content_hash,
                                    _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                                )
                                .limit(1)
                            ).scalar_one_or_none()
                            is not None
                        )
                        or (
                            database.execute(
                                select(_personal_assets.c.id)
                                .join(_generated_media, _generated_media.c.id == _personal_assets.c.media_id)
                                .where(
                                    _personal_assets.c.account_space_id == account_space_id,
                                    _personal_assets.c.state.in_(("pending", "active", "removing")),
                                    _generated_media.c.content_hash == media.content_hash,
                                    _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                                )
                                .limit(1)
                            ).scalar_one_or_none()
                            is not None
                        )
                    )
                    if not shared_reference_exists:
                        self._media_objects.delete(media.object_key)
                    database.execute(
                        delete(_canvas_media_references).where(
                            _canvas_media_references.c.account_space_id == account_space_id,
                            _canvas_media_references.c.canvas_id == canvas_id,
                            _canvas_media_references.c.media_id == media.media_id,
                        )
                    )
                    if media_still_referenced:
                        continue
                    database.execute(
                        update(_generated_media)
                        .where(
                            _generated_media.c.account_space_id == account_space_id,
                            _generated_media.c.id == media.media_id,
                            _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                        )
                        .values(
                            state=GeneratedMediaState.RELEASED.value,
                            released_at=occurred_at,
                        )
                    )
                released_ids.append(media_id)
            except MediaObjectDeletionFailed:
                failed_ids.append(media_id)
        return MediaReferenceReconciliation(tuple(released_ids), tuple(failed_ids))

    def retain_to_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        with self._session_factory.begin() as database:
            return self._retain(database, account_space_id, media_id, retained_at)

    def release_from_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        released_at: datetime,
    ) -> GeneratedMediaRecord:
        with self._session_factory.begin() as database:
            database.execute(
                select(_account_spaces.c.id).where(_account_spaces.c.id == account_space_id).with_for_update()
            ).scalar_one_or_none()
            row = (
                database.execute(
                    select(_generated_media)
                    .where(
                        _generated_media.c.account_space_id == account_space_id,
                        _generated_media.c.id == media_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise GeneratedMediaNotFound(media_id)
            media = _media_from_row(row)
            if media.state is GeneratedMediaState.RELEASED:
                return media
            reference = database.execute(
                select(_personal_assets.c.id).where(
                    _personal_assets.c.account_space_id == account_space_id,
                    _personal_assets.c.id == asset_id,
                    _personal_assets.c.media_id == media_id,
                    _personal_assets.c.state == "removing",
                )
            ).scalar_one_or_none()
            if reference is None:
                return media
            media_still_referenced = (
                database.execute(
                    select(_canvas_media_references.c.media_id)
                    .where(
                        _canvas_media_references.c.account_space_id == account_space_id,
                        _canvas_media_references.c.media_id == media_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            ) or (
                database.execute(
                    select(_personal_assets.c.id)
                    .where(
                        _personal_assets.c.account_space_id == account_space_id,
                        _personal_assets.c.id != asset_id,
                        _personal_assets.c.media_id == media_id,
                        _personal_assets.c.state.in_(("pending", "active", "removing")),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            )
            shared_reference_exists = (
                media_still_referenced
                or (
                    database.execute(
                        select(_canvas_media_references.c.media_id)
                        .join(_generated_media, _generated_media.c.id == _canvas_media_references.c.media_id)
                        .where(
                            _canvas_media_references.c.account_space_id == account_space_id,
                            _generated_media.c.content_hash == media.content_hash,
                            _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    is not None
                )
                or (
                    database.execute(
                        select(_personal_assets.c.id)
                        .join(_generated_media, _generated_media.c.id == _personal_assets.c.media_id)
                        .where(
                            _personal_assets.c.account_space_id == account_space_id,
                            _personal_assets.c.id != asset_id,
                            _personal_assets.c.state.in_(("pending", "active", "removing")),
                            _generated_media.c.content_hash == media.content_hash,
                            _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    is not None
                )
            )
            if not shared_reference_exists:
                self._media_objects.delete(media.object_key)
            if media_still_referenced:
                return media
            database.execute(
                update(_generated_media)
                .where(
                    _generated_media.c.account_space_id == account_space_id,
                    _generated_media.c.id == media_id,
                    _generated_media.c.state == GeneratedMediaState.PERSISTENT.value,
                )
                .values(
                    state=GeneratedMediaState.RELEASED.value,
                    released_at=released_at,
                )
            )
            return replace(
                media,
                state=GeneratedMediaState.RELEASED,
                released_at=released_at,
            )

    def _retain(
        self,
        database: Session,
        account_space_id: str,
        media_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        database.execute(
            select(_account_spaces.c.id).where(_account_spaces.c.id == account_space_id).with_for_update()
        ).scalar_one_or_none()
        row = (
            database.execute(
                select(_generated_media)
                .where(
                    _generated_media.c.account_space_id == account_space_id,
                    _generated_media.c.id == media_id,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GeneratedMediaNotFound(media_id)
        media = _media_from_row(row)
        if media.state is GeneratedMediaState.PERSISTENT:
            return media
        if media.state in {GeneratedMediaState.EXPIRED, GeneratedMediaState.RELEASED}:
            raise GeneratedMediaNotRetainable(media_id)
        active_sizes = {
            str(content_hash): int(size_bytes)
            for content_hash, size_bytes in database.execute(
                select(
                    _generated_media.c.content_hash,
                    func.max(_generated_media.c.size_bytes),
                )
                .where(
                    _generated_media.c.account_space_id == account_space_id,
                    _generated_media.c.state.in_(
                        (
                            GeneratedMediaState.TEMPORARY.value,
                            GeneratedMediaState.PERSISTENT.value,
                        )
                    ),
                )
                .group_by(_generated_media.c.content_hash)
            )
        }
        used_bytes = sum(active_sizes.values())
        if media.content_hash not in active_sizes:
            used_bytes += media.size_bytes
        if used_bytes > self._storage_allowances.limit_bytes(account_space_id):
            raise StorageAllowanceExceeded(account_space_id)
        persistent_key = f"persistent/{account_space_id}/{media.content_hash}"
        self._media_objects.promote(media.object_key, persistent_key)
        retained = GeneratedMediaRecord(
            media_id=media.media_id,
            user_id=media.user_id,
            account_space_id=media.account_space_id,
            canvas_id=media.canvas_id,
            task_id=media.task_id,
            result_reference=media.result_reference,
            object_key=persistent_key,
            kind=media.kind,
            mime_type=media.mime_type,
            size_bytes=media.size_bytes,
            content_hash=media.content_hash,
            state=GeneratedMediaState.PERSISTENT,
            created_at=media.created_at,
            expires_at=None,
            retained_at=retained_at,
        )
        database.execute(
            update(_generated_media)
            .where(
                _generated_media.c.id == media.media_id,
                _generated_media.c.state == GeneratedMediaState.TEMPORARY.value,
            )
            .values(**_media_values(retained))
        )
        return retained


def _by_result(database: Session, account_space_id: str, task_id: str, result_reference: str) -> Any:
    return (
        database.execute(
            select(_generated_media).where(
                _generated_media.c.account_space_id == account_space_id,
                _generated_media.c.task_id == task_id,
                _generated_media.c.result_reference == result_reference,
            )
        )
        .mappings()
        .one_or_none()
    )


def _media_values(media: GeneratedMediaRecord) -> dict[str, Any]:
    return {
        "id": media.media_id,
        "user_id": media.user_id,
        "account_space_id": media.account_space_id,
        "canvas_id": media.canvas_id,
        "task_id": media.task_id,
        "result_reference": media.result_reference,
        "object_key": media.object_key,
        "kind": media.kind.value,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "content_hash": media.content_hash,
        "state": media.state.value,
        "created_at": media.created_at,
        "expires_at": media.expires_at,
        "retained_at": media.retained_at,
        "released_at": media.released_at,
        "deleted_at": media.deleted_at,
    }


def _media_from_row(row: Any) -> GeneratedMediaRecord:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return GeneratedMediaRecord(
        media_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        canvas_id=None if row["canvas_id"] is None else str(row["canvas_id"]),
        task_id=None if row["task_id"] is None else str(row["task_id"]),
        result_reference=str(row["result_reference"]),
        object_key=str(row["object_key"]),
        kind=GeneratedMediaKind(str(row["kind"])),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        content_hash=str(row["content_hash"]),
        state=GeneratedMediaState(str(row["state"])),
        created_at=aware(row["created_at"]),
        expires_at=None if row["expires_at"] is None else aware(row["expires_at"]),
        retained_at=None if row["retained_at"] is None else aware(row["retained_at"]),
        released_at=None if row["released_at"] is None else aware(row["released_at"]),
        deleted_at=None if row["deleted_at"] is None else aware(row["deleted_at"]),
    )


def _replayed_or_conflict(
    media: GeneratedMediaRecord,
    registration: GeneratedMediaRegistration,
) -> GeneratedMediaRecord:
    if matches_registration(media, registration):
        return media
    raise GeneratedMediaConflict(registration.result_reference)

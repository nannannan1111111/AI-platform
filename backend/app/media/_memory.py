"""GeneratedMedia Interface 的内存 Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import uuid4

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


class InMemoryGeneratedMedia:
    """在单进程内登记账户空间归属的临时生成媒体。"""

    def __init__(
        self,
        generation_tasks: GenerationTasks,
        *,
        media_objects: MediaContentStore,
        storage_allowances: StorageAllowances,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._generation_tasks = generation_tasks
        self._media_objects = media_objects
        self._storage_allowances = storage_allowances
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._media_by_id: dict[str, GeneratedMediaRecord] = {}
        self._media_id_by_result: dict[tuple[str, str, str], str] = {}
        self._canvas_references: set[tuple[str, str, str]] = set()
        self._asset_references: set[tuple[str, str, str]] = set()
        self._lock = RLock()

    def register(self, registration: GeneratedMediaRegistration) -> GeneratedMediaRecord:
        """登记一项运行中任务已经写入对象存储的结果。"""
        key = (registration.account_space_id, registration.task_id, registration.result_reference)
        with self._lock:
            existing_id = self._media_id_by_result.get(key)
            if existing_id is not None:
                existing = self._media_by_id[existing_id]
                if matches_registration(existing, registration):
                    return existing
                raise GeneratedMediaConflict(registration.result_reference)
            task = self._generation_tasks.get(registration.account_space_id, registration.task_id)
            validate_running_task(task, registration)
            media = validated_media(registration, self._id_factory())
            self._media_by_id[media.media_id] = media
            self._media_id_by_result[key] = media.media_id
            return media

    def upload_to_canvas(self, upload: CanvasMediaUpload) -> GeneratedMediaRecord:
        """Persist one authenticated canvas image and reference it immediately."""
        mime_type = validated_canvas_image_mime(upload)
        media_id = self._id_factory()
        stored = self._media_objects.put_temporary(
            account_space_id=upload.account_space_id,
            task_id=f"canvas-upload-{upload.canvas_id}",
            result_reference=media_id,
            content=upload.content,
            mime_type=mime_type,
        )
        with self._lock:
            active_sizes = {
                item.content_hash: item.size_bytes
                for item in self._media_by_id.values()
                if item.account_space_id == upload.account_space_id
                and item.state in {GeneratedMediaState.TEMPORARY, GeneratedMediaState.PERSISTENT}
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
            self._media_by_id[media_id] = media
            self._canvas_references.add((upload.account_space_id, upload.canvas_id, media_id))
            return media

    def get(self, account_space_id: str, media_id: str) -> GeneratedMediaRecord:
        """读取账户空间拥有的媒体元数据。"""
        with self._lock:
            media = self._media_by_id.get(media_id)
        if media is None or media.account_space_id != account_space_id:
            raise GeneratedMediaNotFound(media_id)
        return media

    def list_for_task(self, account_space_id: str, task_id: str) -> tuple[GeneratedMediaRecord, ...]:
        """读取账户空间指定任务登记的媒体元数据。"""
        self._generation_tasks.get(account_space_id, task_id)
        with self._lock:
            return tuple(
                sorted(
                    (
                        media
                        for media in self._media_by_id.values()
                        if media.account_space_id == account_space_id and media.task_id == task_id
                    ),
                    key=lambda media: (media.created_at, media.media_id),
                )
            )

    def storage_allowance(self, account_space_id: str) -> StorageAllowance:
        """返回仅计入账户持久媒体且按内容哈希去重的额度快照。"""
        with self._lock:
            active_sizes: dict[str, int] = {}
            for media in self._media_by_id.values():
                if media.account_space_id == account_space_id and media.state in {
                    GeneratedMediaState.TEMPORARY,
                    GeneratedMediaState.PERSISTENT,
                }:
                    active_sizes[media.content_hash] = max(
                        media.size_bytes,
                        active_sizes.get(media.content_hash, 0),
                    )
            used_bytes = sum(active_sizes.values())
            limit_bytes = self._storage_allowances.limit_bytes(account_space_id)
            return StorageAllowance(
                limit_bytes=limit_bytes,
                used_bytes=used_bytes,
                available_bytes=max(limit_bytes - used_bytes, 0),
            )

    def expire_due(self, now: datetime) -> MediaExpirationReport:
        """删除到期对象并把成功项的元数据标记为已过期。"""
        expired_ids: list[str] = []
        failed_ids: list[str] = []
        with self._lock:
            due = sorted(
                (
                    media
                    for media in self._media_by_id.values()
                    if media.state is GeneratedMediaState.TEMPORARY
                    and media.expires_at is not None
                    and media.expires_at <= now
                ),
                key=lambda media: (media.expires_at, media.media_id),
            )
            for media in due:
                try:
                    self._media_objects.delete(media.object_key)
                except MediaObjectDeletionFailed:
                    failed_ids.append(media.media_id)
                    continue
                self._media_by_id[media.media_id] = replace(media, state=GeneratedMediaState.EXPIRED)
                expired_ids.append(media.media_id)
        return MediaExpirationReport(tuple(expired_ids), tuple(failed_ids))

    def delete(self, account_space_id: str, media_id: str, deleted_at: datetime) -> GeneratedMediaRecord:
        """删除尚未持久引用的工作区结果；重复删除保持幂等。"""
        with self._lock:
            media = self._media_by_id.get(media_id)
            if media is None or media.account_space_id != account_space_id:
                raise GeneratedMediaNotFound(media_id)
            if media.state is GeneratedMediaState.DELETED:
                return media
            if media.state is not GeneratedMediaState.TEMPORARY:
                raise GeneratedMediaNotDeletable(media_id)
            self._media_objects.delete(media.object_key)
            deleted = replace(
                media,
                state=GeneratedMediaState.DELETED,
                deleted_at=deleted_at,
            )
            self._media_by_id[media_id] = deleted
            return deleted

    def retain_to_canvas(
        self,
        account_space_id: str,
        media_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        """在账户额度内把临时媒体晋升并引用到原所属画布。"""
        with self._lock:
            retained = self._retain(account_space_id, media_id, retained_at)
            if retained.canvas_id is None:
                raise GeneratedMediaNotRetainable(media_id)
            self._canvas_references.add((account_space_id, retained.canvas_id, retained.media_id))
            return retained

    def reconcile_canvas_references(
        self,
        account_space_id: str,
        canvas_id: str,
        retained_media_ids: Iterable[str],
        occurred_at: datetime,
    ) -> MediaReferenceReconciliation:
        """释放当前画布文档中已经不存在的持久媒体引用。"""
        retained = frozenset(retained_media_ids)
        released_ids: list[str] = []
        failed_ids: list[str] = []
        with self._lock:
            removed_references = sorted(
                reference
                for reference in self._canvas_references
                if reference[0] == account_space_id and reference[1] == canvas_id and reference[2] not in retained
            )
            for reference in removed_references:
                media = self._media_by_id[reference[2]]
                media_still_referenced = any(
                    existing_reference != reference and existing_reference[2] == media.media_id
                    for existing_reference in self._canvas_references
                ) or any(
                    asset_reference[0] == account_space_id and asset_reference[2] == media.media_id
                    for asset_reference in self._asset_references
                )
                shared_reference_exists = (
                    media_still_referenced
                    or any(
                        existing_reference != reference
                        and existing_reference[0] == account_space_id
                        and self._media_by_id[existing_reference[2]].content_hash == media.content_hash
                        for existing_reference in self._canvas_references
                    )
                    or any(
                        asset_reference[0] == account_space_id
                        and self._media_by_id[asset_reference[2]].content_hash == media.content_hash
                        for asset_reference in self._asset_references
                    )
                )
                if not shared_reference_exists:
                    try:
                        self._media_objects.delete(media.object_key)
                    except MediaObjectDeletionFailed:
                        failed_ids.append(media.media_id)
                        continue
                self._canvas_references.remove(reference)
                if media_still_referenced:
                    continue
                self._media_by_id[media.media_id] = replace(
                    media,
                    state=GeneratedMediaState.RELEASED,
                    released_at=occurred_at,
                )
                released_ids.append(media.media_id)
        return MediaReferenceReconciliation(tuple(released_ids), tuple(failed_ids))

    def retain_to_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        """在账户额度内把媒体晋升并引用到个人资产。"""
        with self._lock:
            retained = self._retain(account_space_id, media_id, retained_at)
            self._asset_references.add((account_space_id, asset_id, retained.media_id))
            return retained

    def release_from_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        released_at: datetime,
    ) -> GeneratedMediaRecord:
        """移除个人资产引用，并在没有其他引用时释放媒体。"""
        reference = (account_space_id, asset_id, media_id)
        with self._lock:
            media = self._media_by_id.get(media_id)
            if media is None or media.account_space_id != account_space_id:
                raise GeneratedMediaNotFound(media_id)
            if reference not in self._asset_references:
                return media
            media_still_referenced = any(
                canvas_reference[0] == account_space_id and canvas_reference[2] == media_id
                for canvas_reference in self._canvas_references
            ) or any(
                asset_reference != reference
                and asset_reference[0] == account_space_id
                and asset_reference[2] == media_id
                for asset_reference in self._asset_references
            )
            shared_reference_exists = (
                media_still_referenced
                or any(
                    canvas_reference[0] == account_space_id
                    and self._media_by_id[canvas_reference[2]].content_hash == media.content_hash
                    for canvas_reference in self._canvas_references
                )
                or any(
                    asset_reference != reference
                    and asset_reference[0] == account_space_id
                    and self._media_by_id[asset_reference[2]].content_hash == media.content_hash
                    for asset_reference in self._asset_references
                )
            )
            if not shared_reference_exists:
                self._media_objects.delete(media.object_key)
            self._asset_references.remove(reference)
            if media_still_referenced:
                return media
            released = replace(
                media,
                state=GeneratedMediaState.RELEASED,
                released_at=released_at,
            )
            self._media_by_id[media_id] = released
            return released

    def _retain(self, account_space_id: str, media_id: str, retained_at: datetime) -> GeneratedMediaRecord:
        media = self._media_by_id.get(media_id)
        if media is None or media.account_space_id != account_space_id:
            raise GeneratedMediaNotFound(media_id)
        if media.state is GeneratedMediaState.PERSISTENT:
            return media
        if media.state in {GeneratedMediaState.EXPIRED, GeneratedMediaState.RELEASED}:
            raise GeneratedMediaNotRetainable(media_id)
        active_sizes: dict[str, int] = {}
        for item in self._media_by_id.values():
            if item.account_space_id == account_space_id and item.state in {
                GeneratedMediaState.TEMPORARY,
                GeneratedMediaState.PERSISTENT,
            }:
                active_sizes[item.content_hash] = max(
                    item.size_bytes,
                    active_sizes.get(item.content_hash, 0),
                )
        used_bytes = sum(active_sizes.values())
        if media.content_hash not in active_sizes:
            used_bytes += media.size_bytes
        if used_bytes > self._storage_allowances.limit_bytes(account_space_id):
            raise StorageAllowanceExceeded(account_space_id)
        persistent_key = f"persistent/{account_space_id}/{media.content_hash}"
        self._media_objects.promote(media.object_key, persistent_key)
        retained = replace(
            media,
            object_key=persistent_key,
            state=GeneratedMediaState.PERSISTENT,
            expires_at=None,
            retained_at=retained_at,
        )
        self._media_by_id[media.media_id] = retained
        return retained

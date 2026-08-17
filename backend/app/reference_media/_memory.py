"""临时参考媒体 Interface 的内存 Adapter。"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import uuid4

from app.reference_media._validation import validated_reference_media
from app.reference_media.models import (
    ReferenceMediaContent,
    ReferenceMediaExpired,
    ReferenceMediaNotFound,
    ReferenceMediaOrigin,
    ReferenceMediaRecord,
    ReferenceMediaState,
    ReferenceMediaUpload,
)


class InMemoryReferenceMedia:
    """在单进程内模拟账户隔离参考图片及其字节内容。"""

    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._records: dict[str, ReferenceMediaRecord] = {}
        self._contents: dict[str, bytes] = {}
        self._lock = RLock()

    def upload(self, upload: ReferenceMediaUpload) -> ReferenceMediaRecord:
        media_id = self._id_factory()
        media = validated_reference_media(
            upload,
            media_id=media_id,
            object_key=f"memory-reference://{media_id}",
        )
        with self._lock:
            self._records[media_id] = media
            self._contents[media_id] = bytes(upload.content)
        return media

    def read(self, account_space_id: str, media_id: str, *, at: datetime) -> ReferenceMediaContent:
        with self._lock:
            media = self._records.get(media_id)
            content = self._contents.get(media_id)
        if media is None or content is None or media.account_space_id != account_space_id:
            raise ReferenceMediaNotFound(media_id)
        if media.state is not ReferenceMediaState.TEMPORARY:
            raise ReferenceMediaNotFound(media_id)
        if at >= media.expires_at:
            raise ReferenceMediaExpired(media_id)
        return ReferenceMediaContent(media, content)

    def list_recent(self, account_space_id: str, *, at: datetime) -> tuple[ReferenceMediaRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        media
                        for media in self._records.values()
                        if media.account_space_id == account_space_id
                        and media.state is ReferenceMediaState.TEMPORARY
                        and media.origin is ReferenceMediaOrigin.STANDALONE
                        and at < media.expires_at
                    ),
                    key=lambda media: (media.created_at, media.media_id),
                )
            )

    def delete(self, account_space_id: str, media_id: str) -> ReferenceMediaRecord:
        with self._lock:
            media = self._records.get(media_id)
            if media is None or media.account_space_id != account_space_id:
                raise ReferenceMediaNotFound(media_id)
            if media.state is ReferenceMediaState.DELETED:
                return media
            self._contents.pop(media_id, None)
            deleted = replace(media, state=ReferenceMediaState.DELETED)
            self._records[media_id] = deleted
            return deleted

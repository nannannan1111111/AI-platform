"""生成媒体对象删除的外部存储 Interface 与内存 Adapter。"""

import hashlib
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


class MediaObjectDeletionFailed(RuntimeError):
    """外部对象存储未能完成幂等删除。"""


class MediaObjectPromotionFailed(RuntimeError):
    """外部对象存储未能完成临时对象晋升。"""


class MediaObjectConflict(ValueError):
    """稳定对象键已经保存了不同的媒体内容。"""


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    """已写入平台存储的媒体内容描述。"""

    object_key: str
    size_bytes: int
    content_hash: str


class FileSystemMediaObjects:
    """在受控根目录内原子保存和读取生成媒体内容。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        """装配不会公开给调用方的媒体根目录。"""
        self._root = Path(root).resolve()

    def put_temporary(
        self,
        *,
        account_space_id: str,
        task_id: str,
        result_reference: str,
        content: bytes,
        mime_type: str,
    ) -> StoredMediaObject:
        """按稳定结果身份原子写入临时图片。"""
        raw = bytes(content)
        identity = "\0".join((account_space_id, task_id, result_reference)).encode()
        account_segment = hashlib.sha256(account_space_id.encode()).hexdigest()[:16]
        task_segment = hashlib.sha256(task_id.encode()).hexdigest()[:16]
        result_segment = hashlib.sha256(identity).hexdigest()[:32]
        object_key = f"temporary/{account_segment}/{task_segment}/{result_segment}"
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != raw:
                raise MediaObjectConflict(result_reference)
        else:
            self._atomic_write(destination, raw)
        return StoredMediaObject(
            object_key=object_key,
            size_bytes=len(raw),
            content_hash=hashlib.sha256(raw).hexdigest(),
        )

    def read(self, object_key: str) -> bytes:
        """读取受控对象键对应的媒体字节。"""
        return self._path(object_key).read_bytes()

    def delete(self, object_key: str) -> None:
        """幂等删除受控对象键。"""
        try:
            self._path(object_key).unlink(missing_ok=True)
        except OSError as exc:
            raise MediaObjectDeletionFailed(object_key) from exc

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        """幂等复制临时内容到持久对象键后删除临时副本。"""
        temporary = self._path(temporary_key)
        persistent = self._path(persistent_key)
        try:
            if persistent.exists():
                if temporary.exists() and persistent.read_bytes() != temporary.read_bytes():
                    raise MediaObjectConflict(temporary_key)
            elif temporary.exists():
                persistent.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(persistent, temporary.read_bytes())
            else:
                raise FileNotFoundError(temporary_key)
            temporary.unlink(missing_ok=True)
        except MediaObjectConflict:
            raise
        except Exception as exc:
            raise MediaObjectPromotionFailed(temporary_key) from exc

    def _path(self, object_key: str) -> Path:
        candidate = (self._root / object_key).resolve()
        if not object_key or not candidate.is_relative_to(self._root):
            raise ValueError("媒体对象键无效")
        return candidate

    @staticmethod
    def _atomic_write(destination: Path, content: bytes) -> None:
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=destination.parent)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise


class MediaObjects(Protocol):
    """删除由不透明对象键标识的生成媒体内容。"""

    def delete(self, object_key: str) -> None:
        """幂等删除对象；失败时抛出 `MediaObjectDeletionFailed`。"""

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        """幂等晋升对象；失败时抛出 `MediaObjectPromotionFailed`。"""


class MediaContentStore(MediaObjects, Protocol):
    """保存并读取由不透明对象键标识的媒体内容。"""

    def put_temporary(
        self,
        *,
        account_space_id: str,
        task_id: str,
        result_reference: str,
        content: bytes,
        mime_type: str,
    ) -> StoredMediaObject:
        """幂等写入一项临时媒体内容。"""

    def read(self, object_key: str) -> bytes:
        """读取仍然存在的媒体内容。"""


class InMemoryMediaObjects:
    """在单进程内模拟外部媒体对象存储。"""

    def __init__(self, object_keys: Iterable[str] = ()) -> None:
        """用已存在的不透明对象键初始化内存 Adapter。"""
        self._object_keys = set(object_keys)
        self._lock = RLock()

    def delete(self, object_key: str) -> None:
        """幂等删除一个对象键。"""
        with self._lock:
            self._object_keys.discard(object_key)

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        """把已存在的临时对象幂等移动到持久对象键。"""
        with self._lock:
            if persistent_key in self._object_keys:
                self._object_keys.discard(temporary_key)
                return
            if temporary_key not in self._object_keys:
                raise MediaObjectPromotionFailed(temporary_key)
            self._object_keys.add(persistent_key)
            self._object_keys.remove(temporary_key)


class _S3Client(Protocol):
    def head_object(self, **request: Any) -> object:
        """读取 S3 兼容对象元数据。"""

    def delete_object(self, **request: Any) -> object:
        """提交一次 S3 兼容对象删除请求。"""

    def copy_object(self, **request: Any) -> object:
        """提交一次 S3 兼容对象复制请求。"""


class S3CompatibleMediaObjects:
    """通过外部注入客户端删除 S3 兼容对象，不管理任何凭据。"""

    def __init__(self, client: _S3Client, *, bucket: str) -> None:
        """装配外部客户端和非空 bucket 名称。"""
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("媒体对象 bucket 不能为空")
        self._client = client
        self._bucket = normalized_bucket

    def delete(self, object_key: str) -> None:
        """按配置 bucket 和不透明对象键发起幂等删除。"""
        if not object_key:
            raise ValueError("媒体对象键不能为空")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_key)
        except Exception as exc:
            raise MediaObjectDeletionFailed(object_key) from exc

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        """先复制到持久对象键，再幂等删除临时对象。"""
        if not temporary_key or not persistent_key:
            raise ValueError("媒体对象键不能为空")
        try:
            try:
                self._client.head_object(Bucket=self._bucket, Key=persistent_key)
            except Exception:
                self._client.copy_object(
                    Bucket=self._bucket,
                    CopySource={"Bucket": self._bucket, "Key": temporary_key},
                    Key=persistent_key,
                )
            self._client.delete_object(Bucket=self._bucket, Key=temporary_key)
        except Exception as exc:
            raise MediaObjectPromotionFailed(temporary_key) from exc

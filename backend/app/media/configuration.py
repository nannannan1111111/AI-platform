"""从服务器部署环境装配本地媒体内容存储。"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from app.media.objects import FileSystemMediaObjects


class MediaStorageConfigurationError(RuntimeError):
    """服务器媒体持久目录缺失或不能安全使用。"""


def configured_file_system_media_objects(
    environ: Mapping[str, str] | None = None,
) -> FileSystemMediaObjects:
    """使用部署环境选择的持久绝对目录创建文件系统 Adapter。"""
    environment = os.environ if environ is None else environ
    configured_root = environment.get("GENERATED_MEDIA_ROOT", "").strip()
    if not configured_root:
        raise MediaStorageConfigurationError("GENERATED_MEDIA_ROOT 必须配置")
    root = Path(configured_root)
    if not root.is_absolute():
        raise MediaStorageConfigurationError("GENERATED_MEDIA_ROOT 必须是绝对路径")
    if not root.is_dir():
        raise MediaStorageConfigurationError("GENERATED_MEDIA_ROOT 必须指向已存在的目录")
    try:
        with tempfile.NamedTemporaryFile(prefix=".media-access-probe-", dir=root) as probe:
            probe.write(b"media-storage-access-probe")
            probe.flush()
            probe.seek(0)
            if probe.read() != b"media-storage-access-probe":
                raise OSError("media storage probe could not be read")
    except OSError as exc:
        raise MediaStorageConfigurationError(
            "GENERATED_MEDIA_ROOT 必须允许应用进程创建文件并保持可读写、可删除"
        ) from exc
    return FileSystemMediaObjects(root)

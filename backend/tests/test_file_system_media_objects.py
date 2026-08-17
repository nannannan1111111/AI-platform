import base64
import hashlib
from pathlib import Path

import pytest

from app.media import FileSystemMediaObjects, MediaObjectConflict

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_temporary_image_content_survives_file_system_adapter_restart(tmp_path: Path) -> None:
    media_root = tmp_path / "generated-media"
    stored = FileSystemMediaObjects(media_root).put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=_PNG_BYTES,
        mime_type="image/png",
    )

    restarted = FileSystemMediaObjects(media_root)

    assert stored.object_key.startswith("temporary/")
    assert stored.size_bytes == len(_PNG_BYTES)
    assert stored.content_hash == hashlib.sha256(_PNG_BYTES).hexdigest()
    assert restarted.read(stored.object_key) == _PNG_BYTES


def test_temporary_result_replay_cannot_overwrite_different_content(tmp_path: Path) -> None:
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    first = objects.put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=_PNG_BYTES,
        mime_type="image/png",
    )

    replay = objects.put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=_PNG_BYTES,
        mime_type="image/png",
    )
    with pytest.raises(MediaObjectConflict):
        objects.put_temporary(
            account_space_id="account-space-1",
            task_id="task-1",
            result_reference="result-1",
            content=b"different-image",
            mime_type="image/png",
        )

    assert replay == first
    assert objects.read(first.object_key) == _PNG_BYTES


def test_file_system_media_content_promotes_and_deletes_idempotently(tmp_path: Path) -> None:
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    stored = objects.put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=_PNG_BYTES,
        mime_type="image/png",
    )
    persistent_key = f"persistent/account-space-1/{stored.content_hash}"

    objects.promote(stored.object_key, persistent_key)
    objects.promote(stored.object_key, persistent_key)

    with pytest.raises(FileNotFoundError):
        objects.read(stored.object_key)
    assert objects.read(persistent_key) == _PNG_BYTES
    objects.delete(persistent_key)
    objects.delete(persistent_key)
    with pytest.raises(FileNotFoundError):
        objects.read(persistent_key)

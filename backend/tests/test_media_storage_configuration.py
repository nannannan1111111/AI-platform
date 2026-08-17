from pathlib import Path

import pytest

from app.media import (
    FileSystemMediaObjects,
    MediaStorageConfigurationError,
    configured_file_system_media_objects,
)


def test_configured_media_storage_survives_adapter_restart_in_the_selected_directory(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "server-generated-media"
    media_root.mkdir()

    configured = configured_file_system_media_objects({"GENERATED_MEDIA_ROOT": str(media_root.resolve())})
    stored = configured.put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=b"image-bytes",
        mime_type="image/png",
    )

    restarted = FileSystemMediaObjects(media_root)
    assert restarted.read(stored.object_key) == b"image-bytes"


@pytest.mark.parametrize("environ", [{}, {"GENERATED_MEDIA_ROOT": "   "}])
def test_media_storage_configuration_requires_an_explicit_directory(
    environ: dict[str, str],
) -> None:
    with pytest.raises(MediaStorageConfigurationError, match="GENERATED_MEDIA_ROOT"):
        configured_file_system_media_objects(environ)


def test_media_storage_configuration_rejects_a_relative_directory() -> None:
    with pytest.raises(MediaStorageConfigurationError, match="绝对路径"):
        configured_file_system_media_objects({"GENERATED_MEDIA_ROOT": "relative/generated-media"})


def test_media_storage_configuration_does_not_create_a_missing_server_directory(
    tmp_path: Path,
) -> None:
    missing_root = (tmp_path / "not-created-by-the-application").resolve()

    with pytest.raises(MediaStorageConfigurationError, match="已存在的目录"):
        configured_file_system_media_objects({"GENERATED_MEDIA_ROOT": str(missing_root)})

    assert not missing_root.exists()


def test_media_storage_configuration_rejects_a_directory_without_runtime_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "unwritable-generated-media"
    media_root.mkdir()

    def deny_probe(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated deployment permission failure")

    monkeypatch.setattr(
        "app.media.configuration.tempfile.NamedTemporaryFile",
        deny_probe,
    )

    with pytest.raises(MediaStorageConfigurationError, match="可读写"):
        configured_file_system_media_objects({"GENERATED_MEDIA_ROOT": str(media_root.resolve())})


def test_media_storage_configuration_reads_the_server_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "environment-generated-media"
    media_root.mkdir()
    monkeypatch.setenv("GENERATED_MEDIA_ROOT", str(media_root.resolve()))

    configured = configured_file_system_media_objects()
    stored = configured.put_temporary(
        account_space_id="account-space-1",
        task_id="task-1",
        result_reference="result-1",
        content=b"configured-image",
        mime_type="image/png",
    )

    assert FileSystemMediaObjects(media_root).read(stored.object_key) == b"configured-image"

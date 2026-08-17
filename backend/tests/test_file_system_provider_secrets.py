import os
import stat
from pathlib import Path

import pytest

from app.model_routing import (
    FileSystemProviderSecrets,
    ProviderSecretConfigurationError,
    configured_file_system_provider_secrets,
)


def test_provider_secret_survives_file_system_adapter_restart_without_exposing_its_location(
    tmp_path: Path,
) -> None:
    root = tmp_path / "provider-secrets"
    root.mkdir()
    stored = FileSystemProviderSecrets(root).store("provider-1", "provider-key-value")

    restarted = FileSystemProviderSecrets(root)

    assert stored.key_fingerprint == "52ab503a"
    assert restarted.read(stored.secret_ref) == "provider-key-value"
    assert str(root) not in stored.secret_ref
    assert "provider-1" not in stored.secret_ref
    assert "provider-key-value" not in stored.secret_ref
    filenames = [item.name for item in root.iterdir()]
    assert len(filenames) == 1
    assert "provider-1" not in filenames[0]
    assert "provider-key-value" not in filenames[0]


def test_provider_secret_rotation_atomically_replaces_the_previous_value(tmp_path: Path) -> None:
    root = tmp_path / "provider-secrets"
    root.mkdir()
    secrets = FileSystemProviderSecrets(root)
    original = secrets.store("provider-1", "original-provider-key")

    rotated = secrets.store("provider-1", "rotated-provider-key")

    assert rotated.secret_ref == original.secret_ref
    assert secrets.read(rotated.secret_ref) == "rotated-provider-key"
    stored_files = tuple(item for item in root.iterdir() if item.is_file())
    assert len(stored_files) == 1
    assert b"original-provider-key" not in stored_files[0].read_bytes()


def test_provider_secret_deletion_is_irreversible_and_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "provider-secrets"
    root.mkdir()
    secrets = FileSystemProviderSecrets(root)
    stored = secrets.store("provider-1", "provider-key-value")

    secrets.delete(stored.secret_ref)
    secrets.delete(stored.secret_ref)

    assert tuple(root.iterdir()) == ()
    with pytest.raises(KeyError):
        secrets.read(stored.secret_ref)


def test_provider_secret_configuration_requires_an_explicit_directory() -> None:
    with pytest.raises(ProviderSecretConfigurationError, match="PROVIDER_SECRETS_ROOT"):
        configured_file_system_provider_secrets({})


def test_provider_secret_configuration_rejects_a_relative_directory() -> None:
    with pytest.raises(ProviderSecretConfigurationError, match="绝对路径"):
        configured_file_system_provider_secrets({"PROVIDER_SECRETS_ROOT": "relative/provider-secrets"})


def test_provider_secret_configuration_requires_a_precreated_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing-provider-secrets"

    with pytest.raises(ProviderSecretConfigurationError, match="已存在"):
        configured_file_system_provider_secrets({"PROVIDER_SECRETS_ROOT": str(missing)})

    assert not missing.exists()


def test_provider_secret_configuration_rejects_a_directory_without_safe_file_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "provider-secrets"
    root.mkdir()

    def deny_file_access(*args: object, **kwargs: object) -> object:
        raise OSError("simulated provider secret directory denial")

    monkeypatch.setattr("app.model_routing.secrets.tempfile.NamedTemporaryFile", deny_file_access)

    with pytest.raises(ProviderSecretConfigurationError, match="读写"):
        configured_file_system_provider_secrets({"PROVIDER_SECRETS_ROOT": str(root)})


@pytest.mark.skipif(os.name == "nt", reason="Windows mode bits do not represent the Linux deployment contract")
def test_provider_secret_directory_and_files_use_owner_only_permissions(tmp_path: Path) -> None:
    root = tmp_path / "provider-secrets"
    root.mkdir(mode=0o755)
    secrets = configured_file_system_provider_secrets({"PROVIDER_SECRETS_ROOT": str(root)})

    secrets.store("provider-1", "provider-key-value")

    stored_file = next(item for item in root.iterdir() if item.suffix == ".secret")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored_file.stat().st_mode) == 0o600

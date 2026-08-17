from pathlib import Path

import pytest

from app.backup_manifest import BackupManifestError, build_manifest, verify_manifest, write_manifest


def test_backup_manifest_hashes_and_verifies_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "database.dump"
    artifact.write_bytes(b"database backup")
    manifest_path = tmp_path / "recovery-point.json"
    manifest = build_manifest(
        database_backup_id="pg-2026-08-18T00:00Z",
        media_snapshot_id="media-1",
        secrets_snapshot_id="secrets-1",
        image_digest="sha256:abc",
        migration_head="0061_password_reset_tokens",
        config_version="git-abc",
        files=(("database", artifact),),
    )
    write_manifest(manifest, manifest_path)
    assert len(verify_manifest(manifest_path)) == 1

    artifact.write_bytes(b"tampered")
    with pytest.raises(BackupManifestError, match="checksum"):
        verify_manifest(manifest_path)


def test_backup_manifest_requires_all_recovery_point_components(tmp_path: Path) -> None:
    with pytest.raises(BackupManifestError, match="secrets_snapshot_id"):
        build_manifest(
            database_backup_id="db",
            media_snapshot_id="media",
            secrets_snapshot_id="",
            image_digest="sha256:abc",
            migration_head="head",
            config_version="config",
        )


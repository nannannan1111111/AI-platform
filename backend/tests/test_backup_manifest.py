import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.backup_manifest import (
    BackupManifestError,
    build_manifest,
    verify_manifest,
    write_backup_metrics,
    write_manifest,
)


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


def test_backup_manifest_can_verify_a_mapped_isolated_restore_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.dump"
    source.write_bytes(b"database backup")
    manifest_path = tmp_path / "recovery-point.json"
    write_manifest(
        build_manifest(
            database_backup_id="db",
            media_snapshot_id="media",
            secrets_snapshot_id="secrets",
            image_digest="sha256:abc",
            migration_head="head",
            config_version="config",
            files=(("database", source),),
        ),
        manifest_path,
    )
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    restored = isolated / "database.dump"
    restored.write_bytes(source.read_bytes())
    source.unlink()

    assert len(verify_manifest(manifest_path, path_overrides={"database": restored})) == 1

    with pytest.raises(BackupManifestError, match="unknown backup artifact"):
        verify_manifest(manifest_path, path_overrides={"missing": restored})


def test_backup_metrics_preserve_last_success_when_verification_fails(tmp_path: Path) -> None:
    metrics_path = tmp_path / "backup.prom"
    write_backup_metrics(
        metrics_path,
        recovery_point_created_at="2026-08-18T00:00:00Z",
        integrity_valid=True,
        checked_at=datetime(2026, 8, 18, 0, 5, tzinfo=UTC),
    )
    write_backup_metrics(
        metrics_path,
        recovery_point_created_at=None,
        integrity_valid=False,
        checked_at=datetime(2026, 8, 18, 1, 0, tzinfo=UTC),
    )

    rendered = metrics_path.read_text(encoding="utf-8")
    assert "backup_last_success_timestamp_seconds 1787011200.000" in rendered
    assert "backup_last_verification_timestamp_seconds 1787014800.000" in rendered
    assert "backup_recovery_point_integrity 0" in rendered


def test_backup_metrics_reject_naive_recovery_point_timestamps(tmp_path: Path) -> None:
    with pytest.raises(BackupManifestError, match="timezone"):
        write_backup_metrics(
            tmp_path / "backup.prom",
            recovery_point_created_at="2026-08-18T00:00:00",
            integrity_valid=True,
        )


def test_backup_verify_cli_publishes_failure_metric(tmp_path: Path) -> None:
    metrics_path = tmp_path / "backup.prom"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/backup_manifest.py",
            "verify",
            str(tmp_path / "missing-manifest.json"),
            "--metrics-file",
            str(metrics_path),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 2
    assert "backup_recovery_point_integrity 0" in metrics_path.read_text(encoding="utf-8")

"""Create and verify immutable backup recovery-point manifests."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BackupManifestError(ValueError):
    """Raised when a recovery point is incomplete or no longer matches files."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    """A file included in a recovery point and its content digest."""

    name: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Metadata tying database, media, secrets and deployment to one point."""

    schema_version: int
    created_at: str
    database_backup_id: str
    media_snapshot_id: str
    secrets_snapshot_id: str
    image_digest: str
    migration_head: str
    config_version: str
    files: tuple[BackupFile, ...]


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise BackupManifestError(f"{field} is required")
    return normalized


def build_manifest(
    *,
    database_backup_id: str,
    media_snapshot_id: str,
    secrets_snapshot_id: str,
    image_digest: str,
    migration_head: str,
    config_version: str,
    files: tuple[tuple[str, Path], ...] = (),
    created_at: datetime | None = None,
) -> BackupManifest:
    """Build a manifest and hash local backup artifacts before publication."""
    entries: list[BackupFile] = []
    for name, raw_path in files:
        if raw_path.is_symlink():
            raise BackupManifestError(f"backup file is missing or not regular: {raw_path}")
        path = raw_path.resolve()
        if not path.is_file():
            raise BackupManifestError(f"backup file is missing or not regular: {path}")
        digest, size = _sha256(path)
        entries.append(BackupFile(_required(name, "file name"), str(path), digest, size))
    return BackupManifest(
        schema_version=1,
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        database_backup_id=_required(database_backup_id, "database_backup_id"),
        media_snapshot_id=_required(media_snapshot_id, "media_snapshot_id"),
        secrets_snapshot_id=_required(secrets_snapshot_id, "secrets_snapshot_id"),
        image_digest=_required(image_digest, "image_digest"),
        migration_head=_required(migration_head, "migration_head"),
        config_version=_required(config_version, "config_version"),
        files=tuple(entries),
    )


def write_manifest(manifest: BackupManifest, destination: Path) -> None:
    """Write a manifest atomically with restrictive permissions."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    payload = asdict(manifest)
    payload["files"] = [asdict(entry) for entry in manifest.files]
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(destination)


def _manifest_timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupManifestError("backup manifest created_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BackupManifestError("backup manifest created_at must include a timezone")
    return parsed.astimezone(UTC).timestamp()


def _previous_success_timestamp(destination: Path) -> float:
    try:
        lines = destination.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    prefix = "backup_last_success_timestamp_seconds "
    for line in lines:
        if not line.startswith(prefix):
            continue
        try:
            value = float(line.removeprefix(prefix))
        except ValueError:
            return 0
        return value if math.isfinite(value) and value >= 0 else 0
    return 0


def write_backup_metrics(
    destination: Path,
    *,
    recovery_point_created_at: str | None,
    integrity_valid: bool,
    checked_at: datetime | None = None,
) -> None:
    """Atomically publish non-sensitive backup signals for a textfile collector."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    checked_value = checked_at or datetime.now(UTC)
    if checked_value.tzinfo is None:
        raise BackupManifestError("backup verification time must include a timezone")
    checked = checked_value.astimezone(UTC).timestamp()
    last_success = _previous_success_timestamp(destination)
    if integrity_valid:
        if recovery_point_created_at is None:
            raise BackupManifestError("a valid recovery point requires created_at")
        last_success = max(last_success, _manifest_timestamp(recovery_point_created_at))
    payload = (
        "# HELP backup_last_success_timestamp_seconds Creation time of the newest verified recovery point.\n"
        "# TYPE backup_last_success_timestamp_seconds gauge\n"
        f"backup_last_success_timestamp_seconds {last_success:.3f}\n"
        "# HELP backup_last_verification_timestamp_seconds Time of the latest integrity verification attempt.\n"
        "# TYPE backup_last_verification_timestamp_seconds gauge\n"
        f"backup_last_verification_timestamp_seconds {checked:.3f}\n"
        "# HELP backup_recovery_point_integrity Whether the latest recovery-point verification succeeded.\n"
        "# TYPE backup_recovery_point_integrity gauge\n"
        f"backup_recovery_point_integrity {1 if integrity_valid else 0}\n"
    )
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(destination)


def read_manifest(source: Path) -> BackupManifest:
    """Parse and validate the shape of a manifest without trusting its files."""
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("manifest must be an object")
        files = tuple(BackupFile(**item) for item in payload.pop("files", []))
        manifest = BackupManifest(files=files, **payload)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise BackupManifestError(f"invalid backup manifest: {source}") from exc
    if manifest.schema_version != 1:
        raise BackupManifestError("unsupported backup manifest schema")
    return manifest


def verify_manifest(
    source: Path,
    *,
    path_overrides: Mapping[str, Path] | None = None,
) -> tuple[BackupFile, ...]:
    """Verify every artifact, optionally mapping names into an isolated restore root."""
    manifest = read_manifest(source)
    _manifest_timestamp(manifest.created_at)
    overrides = dict(path_overrides or {})
    known_names = {entry.name for entry in manifest.files}
    unknown_names = set(overrides) - known_names
    if unknown_names:
        raise BackupManifestError(f"unknown backup artifact mapping: {sorted(unknown_names)[0]}")
    for entry in manifest.files:
        path = overrides.get(entry.name, Path(entry.path))
        if path.is_symlink() or not path.is_file():
            raise BackupManifestError(f"backup artifact is missing: {path}")
        try:
            digest, size = _sha256(path)
        except OSError as exc:
            raise BackupManifestError(f"backup artifact cannot be read: {path}") from exc
        if digest != entry.sha256 or size != entry.size_bytes:
            raise BackupManifestError(f"backup artifact checksum mismatch: {path}")
    return manifest.files

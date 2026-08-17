"""Create and verify immutable backup recovery-point manifests."""

from __future__ import annotations

import hashlib
import json
import os
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


def verify_manifest(source: Path) -> tuple[BackupFile, ...]:
    """Verify every local artifact digest and return the checked entries."""
    manifest = read_manifest(source)
    for entry in manifest.files:
        path = Path(entry.path)
        if path.is_symlink() or not path.is_file():
            raise BackupManifestError(f"backup artifact is missing: {path}")
        digest, size = _sha256(path)
        if digest != entry.sha256 or size != entry.size_bytes:
            raise BackupManifestError(f"backup artifact checksum mismatch: {path}")
    return manifest.files

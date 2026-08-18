"""Non-destructive release and rollback contract validation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


class ReleaseContractError(ValueError):
    """Raised when a release cannot be safely promoted or rolled back."""


_IMAGE_DIGEST = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_MIGRATION_HEAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ReleaseSnapshot:
    """Non-sensitive state needed to reproduce a release decision."""

    captured_at: str
    image: str
    migration_head: str


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ReleaseContractError(f"{field} is required")
    return normalized


def validate_release_contract(
    *,
    image: str,
    migration_head: str,
    previous_image: str | None = None,
    previous_migration_head: str | None = None,
    allow_schema_incompatible: bool = False,
    approval_reference: str | None = None,
) -> None:
    """Require immutable images and an expand/contract-compatible rollback pair."""
    image = _required(image, "image")
    migration_head = _required(migration_head, "migration_head")
    if not _IMAGE_DIGEST.fullmatch(image):
        raise ReleaseContractError("image must be an immutable image@sha256:<64 hex> reference")
    if not _MIGRATION_HEAD.fullmatch(migration_head):
        raise ReleaseContractError("migration_head contains invalid characters")
    if previous_image is not None:
        previous_image = _required(previous_image, "previous_image")
        if not _IMAGE_DIGEST.fullmatch(previous_image):
            raise ReleaseContractError("previous_image must be an immutable image@sha256:<64 hex> reference")
    if previous_migration_head is None:
        return
    previous_migration_head = _required(previous_migration_head, "previous_migration_head")
    if not _MIGRATION_HEAD.fullmatch(previous_migration_head):
        raise ReleaseContractError("previous_migration_head contains invalid characters")
    if previous_migration_head != migration_head:
        if not allow_schema_incompatible:
            raise ReleaseContractError("rollback migration heads differ; downgrade approval is required")
        if not _required(approval_reference or "", "approval_reference"):
            raise ReleaseContractError("schema-incompatible rollback requires an approval reference")


def write_release_snapshot(*, destination: Path, image: str, migration_head: str) -> ReleaseSnapshot:
    """Validate and atomically persist a release snapshot without credentials."""
    validate_release_contract(image=image, migration_head=migration_head)
    snapshot = ReleaseSnapshot(
        captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        image=image,
        migration_head=migration_head,
    )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    return snapshot


#!/usr/bin/env python3
"""Create or verify a recovery-point manifest without contacting cloud APIs."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.backup_manifest import BackupManifestError, build_manifest, verify_manifest, write_manifest


def main() -> int:
    """Parse the command line and create or verify one manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--database-backup-id", required=True)
    create.add_argument("--media-snapshot-id", required=True)
    create.add_argument("--secrets-snapshot-id", required=True)
    create.add_argument("--image-digest", required=True)
    create.add_argument("--migration-head", required=True)
    create.add_argument("--config-version", required=True)
    create.add_argument("--file", action="append", default=[], metavar="NAME=PATH")
    verify = subparsers.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--file", action="append", default=[], metavar="NAME=PATH", help="map an artifact into an isolated restore directory")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            overrides: dict[str, Path] = {}
            for value in args.file:
                name, separator, path = value.partition("=")
                if not separator or not name or not path or name in overrides:
                    raise BackupManifestError("--file must use each NAME=PATH mapping once")
                overrides[name] = Path(path)
            entries = verify_manifest(args.manifest, path_overrides=overrides)
            print(f"verified {len(entries)} backup artifacts")
            return 0
        files: list[tuple[str, Path]] = []
        for value in args.file:
            name, separator, path = value.partition("=")
            if not separator or not name or not path:
                raise BackupManifestError("--file must use NAME=PATH")
            files.append((name, Path(path)))
        manifest = build_manifest(
            database_backup_id=args.database_backup_id,
            media_snapshot_id=args.media_snapshot_id,
            secrets_snapshot_id=args.secrets_snapshot_id,
            image_digest=args.image_digest,
            migration_head=args.migration_head,
            config_version=args.config_version,
            files=tuple(files),
        )
        write_manifest(manifest, args.output)
        print(args.output)
        return 0
    except BackupManifestError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

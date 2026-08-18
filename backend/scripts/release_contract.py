#!/usr/bin/env python3
"""Validate immutable release and rollback inputs without changing infrastructure."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.release_contract import ReleaseContractError, validate_release_contract, write_release_snapshot


def main() -> int:
    """Parse and validate a promotion or rollback contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--migration-head", required=True)
    parser.add_argument("--previous-image")
    parser.add_argument("--previous-migration-head")
    parser.add_argument("--allow-schema-incompatible", action="store_true")
    parser.add_argument("--approval-reference")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    try:
        validate_release_contract(
            image=args.image,
            migration_head=args.migration_head,
            previous_image=args.previous_image,
            previous_migration_head=args.previous_migration_head,
            allow_schema_incompatible=args.allow_schema_incompatible,
            approval_reference=args.approval_reference,
        )
        if args.snapshot is not None:
            write_release_snapshot(destination=args.snapshot, image=args.image, migration_head=args.migration_head)
    except ReleaseContractError as exc:
        parser.error(str(exc))
        return 2
    print("release contract valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


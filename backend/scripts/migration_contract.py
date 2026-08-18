#!/usr/bin/env python3
"""Check that an Alembic directory has one known migration head."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.migration_contract import MigrationContractError, validate_migration_head


def main() -> int:
    """Parse the migration directory and print its single head."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected")
    args = parser.parse_args()
    try:
        head = validate_migration_head(args.directory, args.expected)
    except MigrationContractError as exc:
        parser.error(str(exc))
        return 2
    print(head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


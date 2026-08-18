from pathlib import Path

import pytest

from app.migration_contract import MigrationContractError, validate_migration_head


def _migration(path: Path, revision: str, down_revision: str | tuple[str, ...] | None) -> None:
    path.write_text(
        f'revision: str = "{revision}"\n' + f"down_revision = {down_revision!r}\n",
        encoding="utf-8",
    )


def test_migration_contract_finds_single_head(tmp_path: Path) -> None:
    _migration(tmp_path / "0001.py", "one", None)
    _migration(tmp_path / "0002.py", "two", "one")
    assert validate_migration_head(tmp_path, expected="two") == "two"


def test_migration_contract_rejects_multiple_heads(tmp_path: Path) -> None:
    _migration(tmp_path / "0001.py", "one", None)
    _migration(tmp_path / "0002.py", "two", "one")
    _migration(tmp_path / "0003.py", "three", "one")
    with pytest.raises(MigrationContractError, match="exactly one migration head"):
        validate_migration_head(tmp_path)


def test_migration_contract_rejects_unknown_parent(tmp_path: Path) -> None:
    _migration(tmp_path / "0001.py", "one", "missing")
    with pytest.raises(MigrationContractError, match="unknown parent"):
        validate_migration_head(tmp_path)


"""Static Alembic migration-chain checks for release preflight."""

from __future__ import annotations

import ast
from pathlib import Path


class MigrationContractError(ValueError):
    """Raised when migration files do not describe exactly one head."""


def _literal_assignment(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        candidates: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                candidates.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if node.value is not None:
                candidates.append(node.value)
        if candidates:
            try:
                return ast.literal_eval(candidates[0])
            except (ValueError, TypeError) as exc:
                raise MigrationContractError(f"{name} must be a literal value") from exc
    raise MigrationContractError(f"migration is missing {name}")


def _revision_values(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise MigrationContractError(f"{field} must be a string, sequence of strings, or null")


def migration_heads(directory: Path) -> tuple[str, ...]:
    """Parse migration metadata and return sorted heads without importing app code."""
    revisions: set[str] = set()
    parents: set[str] = set()
    files = tuple(sorted(path for path in directory.glob("*.py") if path.name != "__init__.py"))
    if not files:
        raise MigrationContractError(f"no migration files found in {directory}")
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise MigrationContractError(f"cannot parse migration: {path}") from exc
        revision = _revision_values(_literal_assignment(tree, "revision"), "revision")
        if len(revision) != 1:
            raise MigrationContractError(f"migration revision must be one string: {path}")
        revision_id = revision[0]
        if revision_id in revisions:
            raise MigrationContractError(f"duplicate migration revision: {revision_id}")
        revisions.add(revision_id)
        parents.update(_revision_values(_literal_assignment(tree, "down_revision"), "down_revision"))
    unknown_parents = parents - revisions
    if unknown_parents:
        raise MigrationContractError(f"migration references unknown parent: {sorted(unknown_parents)[0]}")
    heads = tuple(sorted(revisions - parents))
    if len(heads) != 1:
        raise MigrationContractError(f"expected exactly one migration head, found {len(heads)}")
    return heads


def validate_migration_head(directory: Path, expected: str | None = None) -> str:
    """Return the sole head and optionally compare it with an expected release value."""
    head = migration_heads(directory)[0]
    if expected is not None and head != expected:
        raise MigrationContractError(f"migration head mismatch: expected {expected}, found {head}")
    return head

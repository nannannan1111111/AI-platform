#!/usr/bin/env python3
"""Validate a capacity/canary plan and evaluate an optional observation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from app.deployment_contract import (
    DeploymentContractError,
    capacity_from_mapping,
    evaluate_canary,
    observation_from_mapping,
    rollback_from_mapping,
    stage_from_mapping,
    validate_deployment_contract,
    write_deployment_snapshot,
)
from app.release_contract import ReleaseContractError


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise DeploymentContractError(f"{field} must be an object")
    return value


def _plan(path: Path) -> tuple[Mapping[str, object], Mapping[str, object], tuple[Mapping[str, object], ...]]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    root = _mapping(payload, "plan")
    capacity = _mapping(root.get("capacity"), "capacity")
    rollback = _mapping(root.get("rollback"), "rollback")
    stages_value = root.get("stages")
    if not isinstance(stages_value, list):
        raise DeploymentContractError("stages must be an array")
    stages = tuple(_mapping(value, "stage") for value in stages_value)
    return capacity, rollback, stages


def _observation(path: Path) -> tuple[str, Mapping[str, object]]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    root = _mapping(payload, "observation")
    stage_name = root.get("stage")
    if not isinstance(stage_name, str):
        raise DeploymentContractError("observation stage must be a string")
    return stage_name, root


def main() -> int:
    """Parse, validate, snapshot, and optionally evaluate one release stage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--migration-head", required=True)
    parser.add_argument("--previous-image", required=True)
    parser.add_argument("--previous-migration-head", required=True)
    parser.add_argument("--allow-schema-incompatible", action="store_true")
    parser.add_argument("--approval-reference")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--observation", type=Path)
    args = parser.parse_args()
    try:
        capacity_values, rollback_values, stage_values = _plan(args.plan)
        capacity = capacity_from_mapping(capacity_values)
        rollback = rollback_from_mapping(rollback_values)
        stages = tuple(stage_from_mapping(values) for values in stage_values)
        validate_deployment_contract(
            image=args.image,
            migration_head=args.migration_head,
            previous_image=args.previous_image,
            previous_migration_head=args.previous_migration_head,
            capacity=capacity,
            stages=stages,
            rollback=rollback,
            allow_schema_incompatible=args.allow_schema_incompatible,
            approval_reference=args.approval_reference,
        )
        if args.snapshot is not None:
            if args.allow_schema_incompatible:
                raise DeploymentContractError("schema-incompatible releases require an independent migration record")
            write_deployment_snapshot(
                destination=args.snapshot,
                image=args.image,
                migration_head=args.migration_head,
                previous_image=args.previous_image,
                previous_migration_head=args.previous_migration_head,
                capacity=capacity,
                stages=stages,
                rollback=rollback,
            )
        if args.observation is not None:
            stage_name, observation_values = _observation(args.observation)
            selected = next((stage for stage in stages if stage.name == stage_name), None)
            if selected is None:
                raise DeploymentContractError("observation references an unknown stage")
            violations = evaluate_canary(selected, observation_from_mapping(observation_values))
            if violations:
                print(f"decision=stop violations={','.join(violations)}")
                return 1
            print(f"decision=promote stage={selected.name}")
            return 0
    except (DeploymentContractError, ReleaseContractError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
        return 2
    print(
        f"deployment contract valid application_connections={capacity.application_connections} "
        f"operations_reserve={capacity.operations_reserve} stages={len(stages)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

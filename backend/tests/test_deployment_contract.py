import json
from pathlib import Path

import pytest

from app.deployment_contract import (
    CanaryObservation,
    CanaryStage,
    CapacityBudget,
    DeploymentContractError,
    RollbackBudget,
    capacity_from_mapping,
    evaluate_canary,
    rollback_from_mapping,
    stage_from_mapping,
    validate_capacity_budget,
    validate_deployment_contract,
    write_deployment_snapshot,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
IMAGE = "registry.example.com/application@sha256:" + "a" * 64
PREVIOUS_IMAGE = "registry.example.com/application@sha256:" + "b" * 64


def _capacity(**overrides: int) -> CapacityBudget:
    values = {
        "web_replicas": 1,
        "web_concurrency": 4,
        "web_pool_size": 8,
        "web_max_overflow": 4,
        "worker_replicas": 4,
        "worker_pool_size": 2,
        "worker_max_overflow": 1,
        "database_max_connections": 100,
        "operations_reserve": 20,
        **overrides,
    }
    return CapacityBudget(**values)


def _stages() -> tuple[CanaryStage, ...]:
    return (
        CanaryStage("internal", 0, 600, 100, 1.0, 2500, 600, 70, 0.1, "10.00"),
        CanaryStage("limited", 10, 1800, 1000, 1.0, 2500, 600, 75, 0.5, "50.00"),
        CanaryStage("full", 100, 3600, 5000, 1.0, 2500, 600, 80, 1.0, "200.00"),
    )


def _rollback() -> RollbackBudget:
    return RollbackBudget(600, 300, 900)


def test_capacity_budget_preserves_database_operations_reserve() -> None:
    capacity = _capacity()

    validate_capacity_budget(capacity)

    assert capacity.application_connections == 60
    assert capacity.minimum_operations_reserve == 12
    assert capacity.application_connection_ceiling == 80

    with pytest.raises(DeploymentContractError, match="operations_reserve"):
        validate_capacity_budget(_capacity(operations_reserve=11))
    with pytest.raises(DeploymentContractError, match="at least 80"):
        validate_capacity_budget(_capacity(database_max_connections=79))


def test_deployment_contract_requires_monotonic_canary_and_compatible_rollback() -> None:
    validate_deployment_contract(
        image=IMAGE,
        migration_head="0061_password_reset_tokens",
        previous_image=PREVIOUS_IMAGE,
        previous_migration_head="0061_password_reset_tokens",
        capacity=_capacity(),
        stages=_stages(),
        rollback=_rollback(),
    )

    invalid_stages = (*_stages()[:-1], CanaryStage("full", 90, 3600, 5000, 1.0, 2500, 600, 80, 1.0, "200.00"))
    with pytest.raises(DeploymentContractError, match="100 percent"):
        validate_deployment_contract(
            image=IMAGE,
            migration_head="0061_password_reset_tokens",
            previous_image=PREVIOUS_IMAGE,
            previous_migration_head="0061_password_reset_tokens",
            capacity=_capacity(),
            stages=invalid_stages,
            rollback=_rollback(),
        )


def test_canary_observation_stops_on_every_exceeded_threshold() -> None:
    stage = _stages()[1]
    observation = CanaryObservation(
        request_count=999,
        error_rate_percent=1.1,
        p95_ms=2501,
        queue_oldest_seconds=601,
        database_connections=76,
        provider_completions_per_minute=0.4,
        provider_cost_cny="50.01",
    )

    assert evaluate_canary(stage, observation) == (
        "minimum_requests",
        "error_rate_percent",
        "p95_ms",
        "queue_oldest_seconds",
        "database_connections",
        "provider_completions_per_minute",
        "provider_cost_cny",
    )
    assert evaluate_canary(
        stage,
        CanaryObservation(1000, 1.0, 2500, 600, 75, 0.5, "50.00"),
    ) == ()
    with pytest.raises(DeploymentContractError, match="error_rate_percent"):
        evaluate_canary(stage, CanaryObservation(1000, float("nan"), 0, 0, 0, 0.5, "0.00"))


def test_deployment_snapshot_contains_only_reproducible_release_state(tmp_path: Path) -> None:
    destination = tmp_path / "deployment-state.json"

    snapshot = write_deployment_snapshot(
        destination=destination,
        image=IMAGE,
        migration_head="0061_password_reset_tokens",
        previous_image=PREVIOUS_IMAGE,
        previous_migration_head="0061_password_reset_tokens",
        capacity=_capacity(),
        stages=_stages(),
        rollback=_rollback(),
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert snapshot.application_connections == 60
    assert payload["stage_names"] == ["internal", "limited", "full"]
    assert payload["rollback_maximum_duration_seconds"] == 600
    assert "approval_reference" not in payload
    assert "credentials" not in destination.read_text(encoding="utf-8")


def test_committed_canary_plan_matches_the_connection_budget_contract() -> None:
    payload = json.loads((REPOSITORY_ROOT / "deploy/tencent-cloud/canary-plan.example.json").read_text(encoding="utf-8"))
    capacity = capacity_from_mapping(payload["capacity"])
    rollback = rollback_from_mapping(payload["rollback"])
    stages = tuple(stage_from_mapping(stage) for stage in payload["stages"])

    validate_deployment_contract(
        image=IMAGE,
        migration_head="0061_password_reset_tokens",
        previous_image=PREVIOUS_IMAGE,
        previous_migration_head="0061_password_reset_tokens",
        capacity=capacity,
        stages=stages,
        rollback=rollback,
    )

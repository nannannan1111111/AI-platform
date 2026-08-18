"""Capacity, canary, and rollback contracts for production releases."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.release_contract import validate_release_contract


class DeploymentContractError(ValueError):
    """Raised when capacity or canary inputs cannot support a safe release."""


_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class CapacityBudget:
    """Worst-case application database connection allocation."""

    web_replicas: int
    web_concurrency: int
    web_pool_size: int
    web_max_overflow: int
    worker_replicas: int
    worker_pool_size: int
    worker_max_overflow: int
    database_max_connections: int
    operations_reserve: int

    @property
    def application_connections(self) -> int:
        """Return the configured worst-case application connection count."""
        web = self.web_replicas * self.web_concurrency * (self.web_pool_size + self.web_max_overflow)
        workers = self.worker_replicas * (self.worker_pool_size + self.worker_max_overflow)
        return web + workers

    @property
    def minimum_operations_reserve(self) -> int:
        """Return the larger of 20 percent or ten operational connections."""
        return max(10, (self.application_connections + 4) // 5)

    @property
    def application_connection_ceiling(self) -> int:
        """Return the maximum observed count that still preserves the reserve."""
        return self.database_max_connections - self.operations_reserve


@dataclass(frozen=True, slots=True)
class CanaryStage:
    """One rollout stage and its automatic stop thresholds."""

    name: str
    traffic_percent: int
    observation_seconds: int
    minimum_requests: int
    maximum_error_rate_percent: float
    maximum_p95_ms: int
    maximum_queue_oldest_seconds: int
    maximum_database_connections: int
    minimum_provider_completions_per_minute: float
    maximum_provider_cost_cny: str


@dataclass(frozen=True, slots=True)
class CanaryObservation:
    """Measured values used to decide whether one canary stage can advance."""

    request_count: int
    error_rate_percent: float
    p95_ms: int
    queue_oldest_seconds: int
    database_connections: int
    provider_completions_per_minute: float
    provider_cost_cny: str


@dataclass(frozen=True, slots=True)
class RollbackBudget:
    """Time budget for graceful worker drain and post-rollback observation."""

    maximum_duration_seconds: int
    worker_grace_seconds: int
    post_rollback_observation_seconds: int


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """Non-sensitive release state needed for a rollback decision."""

    captured_at: str
    image: str
    migration_head: str
    previous_image: str
    previous_migration_head: str
    application_connections: int
    operations_reserve: int
    database_max_connections: int
    stage_names: tuple[str, ...]
    rollback_maximum_duration_seconds: int
    worker_grace_seconds: int
    post_rollback_observation_seconds: int


def _positive(value: int, field: str, *, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise DeploymentContractError(f"{field} must be at least {minimum}")


def _cost(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise DeploymentContractError(f"{field} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise DeploymentContractError(f"{field} must be a finite non-negative decimal")
    return parsed


def validate_capacity_budget(capacity: CapacityBudget) -> None:
    """Require positive pool inputs and an explicit operational reserve."""
    for field in (
        "web_replicas",
        "web_concurrency",
        "web_pool_size",
        "worker_replicas",
        "worker_pool_size",
        "database_max_connections",
        "operations_reserve",
    ):
        _positive(getattr(capacity, field), field)
    _positive(capacity.web_max_overflow, "web_max_overflow", allow_zero=True)
    _positive(capacity.worker_max_overflow, "worker_max_overflow", allow_zero=True)
    if capacity.operations_reserve < capacity.minimum_operations_reserve:
        raise DeploymentContractError(
            "operations_reserve must preserve at least 20 percent or 10 connections, whichever is larger"
        )
    required = capacity.application_connections + capacity.operations_reserve
    if capacity.database_max_connections < required:
        raise DeploymentContractError(
            f"database_max_connections must be at least {required} for the application budget and reserve"
        )


def validate_canary_stages(stages: Sequence[CanaryStage], capacity: CapacityBudget) -> None:
    """Require a monotonic internal-to-full rollout with bounded stop thresholds."""
    if len(stages) < 3:
        raise DeploymentContractError("at least internal, limited, and full canary stages are required")
    if stages[0].traffic_percent != 0:
        raise DeploymentContractError("the first canary stage must target internal accounts at 0 percent public traffic")
    if stages[-1].traffic_percent != 100:
        raise DeploymentContractError("the final canary stage must target 100 percent traffic")
    names: set[str] = set()
    previous_traffic = -1
    for stage in stages:
        if not _NAME.fullmatch(stage.name) or stage.name in names:
            raise DeploymentContractError("canary stage names must be unique lowercase identifiers")
        names.add(stage.name)
        if not 0 <= stage.traffic_percent <= 100 or stage.traffic_percent <= previous_traffic:
            raise DeploymentContractError("canary traffic percentages must increase from 0 to 100")
        previous_traffic = stage.traffic_percent
        if stage.observation_seconds < 60:
            raise DeploymentContractError("each canary observation window must be at least 60 seconds")
        _positive(stage.minimum_requests, "minimum_requests")
        if not math.isfinite(stage.maximum_error_rate_percent) or not 0 <= stage.maximum_error_rate_percent < 100:
            raise DeploymentContractError("maximum_error_rate_percent must be between 0 and 100")
        _positive(stage.maximum_p95_ms, "maximum_p95_ms")
        _positive(stage.maximum_queue_oldest_seconds, "maximum_queue_oldest_seconds", allow_zero=True)
        _positive(stage.maximum_database_connections, "maximum_database_connections")
        if stage.maximum_database_connections > capacity.application_connection_ceiling:
            raise DeploymentContractError("canary database threshold would consume the operational reserve")
        if (
            not math.isfinite(stage.minimum_provider_completions_per_minute)
            or stage.minimum_provider_completions_per_minute < 0
        ):
            raise DeploymentContractError("minimum_provider_completions_per_minute cannot be negative")
        _cost(stage.maximum_provider_cost_cny, "maximum_provider_cost_cny")


def validate_rollback_budget(rollback: RollbackBudget) -> None:
    """Require an explicit rollback target that includes graceful worker drain."""
    if rollback.maximum_duration_seconds < 60:
        raise DeploymentContractError("rollback maximum duration must be at least 60 seconds")
    if rollback.worker_grace_seconds < 30:
        raise DeploymentContractError("worker grace period must be at least 30 seconds")
    if rollback.worker_grace_seconds >= rollback.maximum_duration_seconds:
        raise DeploymentContractError("worker grace period must fit inside the rollback duration target")
    if rollback.post_rollback_observation_seconds < 60:
        raise DeploymentContractError("post-rollback observation must be at least 60 seconds")


def validate_canary_observation(observation: CanaryObservation) -> None:
    """Reject missing, negative, or non-finite canary measurements."""
    _positive(observation.request_count, "request_count", allow_zero=True)
    if not math.isfinite(observation.error_rate_percent) or not 0 <= observation.error_rate_percent <= 100:
        raise DeploymentContractError("error_rate_percent must be between 0 and 100")
    _positive(observation.p95_ms, "p95_ms", allow_zero=True)
    _positive(observation.queue_oldest_seconds, "queue_oldest_seconds", allow_zero=True)
    _positive(observation.database_connections, "database_connections", allow_zero=True)
    if (
        not math.isfinite(observation.provider_completions_per_minute)
        or observation.provider_completions_per_minute < 0
    ):
        raise DeploymentContractError("provider_completions_per_minute must be finite and non-negative")
    _cost(observation.provider_cost_cny, "provider_cost_cny")


def validate_deployment_contract(
    *,
    image: str,
    migration_head: str,
    previous_image: str,
    previous_migration_head: str,
    capacity: CapacityBudget,
    stages: Sequence[CanaryStage],
    rollback: RollbackBudget,
    allow_schema_incompatible: bool = False,
    approval_reference: str | None = None,
) -> None:
    """Validate immutable release, rollback, capacity, and rollout inputs together."""
    validate_release_contract(
        image=image,
        migration_head=migration_head,
        previous_image=previous_image,
        previous_migration_head=previous_migration_head,
        allow_schema_incompatible=allow_schema_incompatible,
        approval_reference=approval_reference,
    )
    validate_capacity_budget(capacity)
    validate_canary_stages(stages, capacity)
    validate_rollback_budget(rollback)


def evaluate_canary(stage: CanaryStage, observation: CanaryObservation) -> tuple[str, ...]:
    """Return stable metric names for every automatic stop threshold exceeded."""
    validate_canary_observation(observation)
    violations: list[str] = []
    if observation.request_count < stage.minimum_requests:
        violations.append("minimum_requests")
    if observation.error_rate_percent > stage.maximum_error_rate_percent:
        violations.append("error_rate_percent")
    if observation.p95_ms > stage.maximum_p95_ms:
        violations.append("p95_ms")
    if observation.queue_oldest_seconds > stage.maximum_queue_oldest_seconds:
        violations.append("queue_oldest_seconds")
    if observation.database_connections > stage.maximum_database_connections:
        violations.append("database_connections")
    if observation.provider_completions_per_minute < stage.minimum_provider_completions_per_minute:
        violations.append("provider_completions_per_minute")
    if _cost(observation.provider_cost_cny, "provider_cost_cny") > _cost(
        stage.maximum_provider_cost_cny, "maximum_provider_cost_cny"
    ):
        violations.append("provider_cost_cny")
    return tuple(violations)


def write_deployment_snapshot(
    *,
    destination: Path,
    image: str,
    migration_head: str,
    previous_image: str,
    previous_migration_head: str,
    capacity: CapacityBudget,
    stages: Sequence[CanaryStage],
    rollback: RollbackBudget,
) -> DeploymentSnapshot:
    """Atomically persist validated, non-sensitive release and rollback state."""
    validate_deployment_contract(
        image=image,
        migration_head=migration_head,
        previous_image=previous_image,
        previous_migration_head=previous_migration_head,
        capacity=capacity,
        stages=stages,
        rollback=rollback,
    )
    snapshot = DeploymentSnapshot(
        captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        image=image,
        migration_head=migration_head,
        previous_image=previous_image,
        previous_migration_head=previous_migration_head,
        application_connections=capacity.application_connections,
        operations_reserve=capacity.operations_reserve,
        database_max_connections=capacity.database_max_connections,
        stage_names=tuple(stage.name for stage in stages),
        rollback_maximum_duration_seconds=rollback.maximum_duration_seconds,
        worker_grace_seconds=rollback.worker_grace_seconds,
        post_rollback_observation_seconds=rollback.post_rollback_observation_seconds,
    )
    payload = {
        "captured_at": snapshot.captured_at,
        "image": snapshot.image,
        "migration_head": snapshot.migration_head,
        "previous_image": snapshot.previous_image,
        "previous_migration_head": snapshot.previous_migration_head,
        "application_connections": snapshot.application_connections,
        "operations_reserve": snapshot.operations_reserve,
        "database_max_connections": snapshot.database_max_connections,
        "stage_names": list(snapshot.stage_names),
        "rollback_maximum_duration_seconds": snapshot.rollback_maximum_duration_seconds,
        "worker_grace_seconds": snapshot.worker_grace_seconds,
        "post_rollback_observation_seconds": snapshot.post_rollback_observation_seconds,
    }
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    return snapshot


def _integer(values: Mapping[str, object], field: str) -> int:
    value = values.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeploymentContractError(f"{field} must be an integer")
    return value


def _number(values: Mapping[str, object], field: str) -> float:
    value = values.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeploymentContractError(f"{field} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DeploymentContractError(f"{field} must be finite")
    return parsed


def _string(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str):
        raise DeploymentContractError(f"{field} must be a string")
    return value


def capacity_from_mapping(values: Mapping[str, object]) -> CapacityBudget:
    """Parse one capacity budget from a JSON-compatible mapping."""
    return CapacityBudget(
        web_replicas=_integer(values, "web_replicas"),
        web_concurrency=_integer(values, "web_concurrency"),
        web_pool_size=_integer(values, "web_pool_size"),
        web_max_overflow=_integer(values, "web_max_overflow"),
        worker_replicas=_integer(values, "worker_replicas"),
        worker_pool_size=_integer(values, "worker_pool_size"),
        worker_max_overflow=_integer(values, "worker_max_overflow"),
        database_max_connections=_integer(values, "database_max_connections"),
        operations_reserve=_integer(values, "operations_reserve"),
    )


def stage_from_mapping(values: Mapping[str, object]) -> CanaryStage:
    """Parse one canary stage from a JSON-compatible mapping."""
    return CanaryStage(
        name=_string(values, "name"),
        traffic_percent=_integer(values, "traffic_percent"),
        observation_seconds=_integer(values, "observation_seconds"),
        minimum_requests=_integer(values, "minimum_requests"),
        maximum_error_rate_percent=_number(values, "maximum_error_rate_percent"),
        maximum_p95_ms=_integer(values, "maximum_p95_ms"),
        maximum_queue_oldest_seconds=_integer(values, "maximum_queue_oldest_seconds"),
        maximum_database_connections=_integer(values, "maximum_database_connections"),
        minimum_provider_completions_per_minute=_number(values, "minimum_provider_completions_per_minute"),
        maximum_provider_cost_cny=_string(values, "maximum_provider_cost_cny"),
    )


def observation_from_mapping(values: Mapping[str, object]) -> CanaryObservation:
    """Parse one measured observation from a JSON-compatible mapping."""
    return CanaryObservation(
        request_count=_integer(values, "request_count"),
        error_rate_percent=_number(values, "error_rate_percent"),
        p95_ms=_integer(values, "p95_ms"),
        queue_oldest_seconds=_integer(values, "queue_oldest_seconds"),
        database_connections=_integer(values, "database_connections"),
        provider_completions_per_minute=_number(values, "provider_completions_per_minute"),
        provider_cost_cny=_string(values, "provider_cost_cny"),
    )


def rollback_from_mapping(values: Mapping[str, object]) -> RollbackBudget:
    """Parse one rollback time budget from a JSON-compatible mapping."""
    return RollbackBudget(
        maximum_duration_seconds=_integer(values, "maximum_duration_seconds"),
        worker_grace_seconds=_integer(values, "worker_grace_seconds"),
        post_rollback_observation_seconds=_integer(values, "post_rollback_observation_seconds"),
    )

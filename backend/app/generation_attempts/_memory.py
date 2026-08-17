"""生成尝试 Interface 的内存 Adapter。"""

from collections.abc import Callable
from threading import RLock
from uuid import uuid4

from app.generation import GenerationTasks
from app.generation_attempts._transitions import transitioned
from app.generation_attempts._validation import provider_idempotency_key, selected_route
from app.generation_attempts.models import (
    GenerationAttempt,
    GenerationAttemptConflict,
    GenerationAttemptNotFound,
    GenerationAttemptPreparation,
    GenerationAttemptStatus,
    GenerationAttemptTransition,
)
from app.provider_costs import ProviderCostRates


class InMemoryGenerationAttempts:
    """在内存中保留生成尝试的有序历史与幂等身份。"""

    def __init__(
        self,
        generation_tasks: GenerationTasks,
        *,
        provider_cost_rates: ProviderCostRates,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._generation_tasks = generation_tasks
        self._provider_cost_rates = provider_cost_rates
        self._id_factory = id_factory
        self._attempts_by_task: dict[tuple[str, str], list[GenerationAttempt]] = {}
        self._attempts_by_id: dict[str, tuple[str, GenerationAttempt]] = {}
        self._lock = RLock()

    def prepare(self, preparation: GenerationAttemptPreparation) -> GenerationAttempt:
        """幂等预备当前尝试，或在明确失败后预备下一次尝试。"""
        task = self._generation_tasks.get(preparation.account_space_id, preparation.task_id)
        route_id = selected_route(task, preparation)
        key = (preparation.account_space_id, preparation.task_id)
        with self._lock:
            attempts = self._attempts_by_task.get(key, [])
            existing = attempts[-1] if attempts else None
            if existing is not None:
                if existing.route_id != route_id:
                    raise GenerationAttemptConflict(preparation.task_id)
                if existing.status is not GenerationAttemptStatus.FAILED:
                    return existing
            cost_rate = self._provider_cost_rates.current_at(route_id, preparation.occurred_at)
            attempt_no = 1 if existing is None else existing.attempt_no + 1
            attempt = GenerationAttempt(
                attempt_id=self._id_factory(),
                task_id=preparation.task_id,
                attempt_no=attempt_no,
                route_id=route_id,
                provider_idempotency_key=provider_idempotency_key(preparation.task_id, attempt_no, route_id),
                provider_cost_rate_id=cost_rate.version_id,
                status=GenerationAttemptStatus.CREATED,
                created_at=preparation.occurred_at,
                updated_at=preparation.occurred_at,
            )
            self._attempts_by_task.setdefault(key, []).append(attempt)
            self._attempts_by_id[attempt.attempt_id] = (preparation.account_space_id, attempt)
            return attempt

    def for_task(self, account_space_id: str, task_id: str) -> tuple[GenerationAttempt, ...]:
        """按序读取任务的生成尝试并隐藏其他账户空间。"""
        self._generation_tasks.get(account_space_id, task_id)
        with self._lock:
            return tuple(self._attempts_by_task.get((account_space_id, task_id), ()))

    def transition(
        self,
        account_space_id: str,
        attempt_id: str,
        event: GenerationAttemptTransition,
    ) -> GenerationAttempt:
        """幂等记录提交阶段事实并隐藏其他账户空间。"""
        with self._lock:
            owned = self._attempts_by_id.get(attempt_id)
            if owned is None or owned[0] != account_space_id:
                raise GenerationAttemptNotFound(attempt_id)
            updated = transitioned(owned[1], event)
            self._attempts_by_id[attempt_id] = (account_space_id, updated)
            attempts = self._attempts_by_task[(account_space_id, updated.task_id)]
            attempts[updated.attempt_no - 1] = updated
            return updated

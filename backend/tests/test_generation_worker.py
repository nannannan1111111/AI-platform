from contextlib import contextmanager

from app.worker import _PENDING_TASKS, _process_candidate


def test_worker_query_is_bounded_and_skips_provider_owned_tasks() -> None:
    query = str(_PENDING_TASKS)

    assert "task.status = 'queued'" in query
    assert "attempt.status IN ('provider_pending', 'unknown')" in query
    assert "ORDER BY task.created_at" in query
    assert "LIMIT :limit" in query
    assert "task.selected_route_id" in query
    assert "provider.concurrency_group" in query
    assert "MIN(peer.max_concurrency)" in query
    assert "running_task.status = 'running'" in query
    assert "account_generation_limits" in query
    assert "account_limit.execution_concurrency" in query
    assert "earlier_task.status = 'queued'" in query
    assert "COALESCE(account_limit.execution_concurrency, 2) > 1" in query


class _Submitter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def submit(self, account_space_id: str, task_id: str) -> None:
        self.calls.append((account_space_id, task_id))


class _EligibilityResult:
    status = "queued"
    running_count = 0
    earlier_queued_count = 0
    execution_concurrency = 6


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object, **_kwargs: object):
        return self

    def one_or_none(self) -> _EligibilityResult:
        return _EligibilityResult()


class _Engine:
    def connect(self) -> _Connection:
        return _Connection()


class _Locks:
    def __init__(self, result: tuple[int, int] | None = (1, 7)) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int, str, int]] = []

    @contextmanager
    def generation_dispatch_lock(
        self,
        task_key: str,
        account_pool_key: str,
        account_slots: int,
        pool_key: str,
        slots: int,
    ):
        self.calls.append((task_key, account_pool_key, account_slots, pool_key, slots))
        yield self.result


def test_worker_holds_one_shared_provider_slot_while_submitting() -> None:
    locks = _Locks()

    submitter = _Submitter()
    engine = _Engine()

    assert _process_candidate(locks, engine, submitter, "account-1", "task-1", 6, "upstream-main", 20) is True
    assert locks.calls == [(
        "generation-task:task-1",
        "generation-account-running:account-1",
        6,
        "generation-provider-pool:upstream-main",
        20,
    )]
    assert submitter.calls == [("account-1", "task-1")]


def test_worker_skips_submission_when_shared_provider_pool_is_full() -> None:
    locks = _Locks(None)
    submitter = _Submitter()

    assert _process_candidate(locks, _Engine(), submitter, "account-1", "task-1", 6, "upstream-main", 20) is False
    assert submitter.calls == []


def test_single_slot_account_cannot_dispatch_a_later_queued_task() -> None:
    class EarlierQueuedEligibility:
        status = "queued"
        running_count = 0
        earlier_queued_count = 1
        execution_concurrency = 1

    class Connection(_Connection):
        def one_or_none(self) -> EarlierQueuedEligibility:
            return EarlierQueuedEligibility()

    class Engine(_Engine):
        def connect(self) -> Connection:
            return Connection()

    submitter = _Submitter()

    assert _process_candidate(_Locks((1, 1)), Engine(), submitter, "account-1", "task-2", 1, "upstream-main", 20) is False
    assert submitter.calls == []


def test_worker_uses_business_pool_only_for_eligibility_and_submission() -> None:
    locks = _Locks((1, 1))
    business_engine = _Engine()
    submitter = _Submitter()

    assert _process_candidate(
        locks,
        business_engine,
        submitter,
        "account-1",
        "task-1",
        6,
        "upstream-main",
        20,
    ) is True
    assert len(locks.calls) == 1
    assert submitter.calls == [("account-1", "task-1")]

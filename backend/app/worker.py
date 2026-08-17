"""Database-backed image-generation worker entry point."""

from __future__ import annotations

import logging
import os
import signal
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from threading import Event

from sqlalchemy import Engine, text

from app.database_runtime import postgres_advisory_generation_dispatch_lock, postgres_advisory_worker_slot
from app.observability import METRICS, install_structured_logging
from app.runtime import create_production_app

_LOG = logging.getLogger(__name__)
_DISPATCH_ELIGIBILITY = text(
    """
    SELECT task.status,
           (
               SELECT COUNT(*)
                 FROM generation_tasks AS running_task
                WHERE running_task.account_space_id = task.account_space_id
                  AND running_task.status = 'running'
           ) AS running_count,
           (
               SELECT COUNT(*)
                 FROM generation_tasks AS earlier_task
                WHERE earlier_task.account_space_id = task.account_space_id
                  AND earlier_task.status = 'queued'
                  AND (earlier_task.created_at, earlier_task.id) < (task.created_at, task.id)
           ) AS earlier_queued_count,
           COALESCE(account_limit.execution_concurrency, 2) AS execution_concurrency
      FROM generation_tasks AS task
      LEFT JOIN account_generation_limits AS account_limit
        ON account_limit.account_space_id = task.account_space_id
     WHERE task.account_space_id = :account_space_id
       AND task.id = :task_id
    """
)
_PENDING_TASKS = text(
    """
    SELECT task.account_space_id,
           task.id,
           task.selected_route_id,
           COALESCE(provider.concurrency_group, 'unresolved:' || task.id),
           COALESCE((
               SELECT MIN(peer.max_concurrency)
                 FROM api_providers AS peer
                WHERE peer.concurrency_group = provider.concurrency_group
                  AND peer.deleted_at IS NULL
           ), 1) AS max_concurrency,
           COALESCE(account_limit.execution_concurrency, 2) AS account_concurrency
      FROM generation_tasks AS task
      LEFT JOIN image_model_routes AS route
        ON route.id = task.selected_route_id
       AND route.deleted_at IS NULL
      LEFT JOIN api_providers AS provider
        ON provider.id = route.provider_id
       AND provider.deleted_at IS NULL
      LEFT JOIN account_generation_limits AS account_limit
        ON account_limit.account_space_id = task.account_space_id
     WHERE task.status = 'queued'
       AND (
           SELECT COUNT(*)
             FROM generation_tasks AS running_task
            WHERE running_task.account_space_id = task.account_space_id
              AND running_task.status = 'running'
       ) < COALESCE(account_limit.execution_concurrency, 2)
       AND (
           COALESCE(account_limit.execution_concurrency, 2) > 1
           OR NOT EXISTS (
               SELECT 1
                 FROM generation_tasks AS earlier_task
                WHERE earlier_task.account_space_id = task.account_space_id
                  AND earlier_task.status = 'queued'
                  AND (earlier_task.created_at, earlier_task.id) < (task.created_at, task.id)
           )
       )
       AND NOT EXISTS (
           SELECT 1
             FROM image_generation_attempts AS attempt
            WHERE attempt.generation_task_id = task.id
              AND attempt.status IN ('provider_pending', 'unknown')
       )
     ORDER BY task.created_at, task.id
     LIMIT :limit
    """
)
_OLDEST_QUEUED = text(
    "SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(created_at))) "
    "FROM generation_tasks WHERE status = 'queued'"
)


def run() -> None:
    """Poll durable queued tasks until the container is asked to stop."""
    install_structured_logging(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    poll_seconds = _positive_float("GENERATION_WORKER_POLL_SECONDS", 0.5)
    batch_size = _positive_int("GENERATION_WORKER_BATCH_SIZE", 20)
    application = create_production_app()
    engine: Engine = application.state.database_engine
    submitter = application.state.generation_attempt_submissions
    capacity_settings = application.state.worker_capacity
    deployed_limit = capacity_settings.current().deployed_worker_limit
    stopping = Event()

    def stop(_signal: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker_index: int | None = None
    try:
        with postgres_advisory_worker_slot(engine, deployed_limit) as worker_index:
            if worker_index is None:
                raise RuntimeError("no generation worker deployment slot is available")
            _LOG.info(
                "generation worker started index=%s poll_seconds=%s batch_size=%s",
                worker_index,
                poll_seconds,
                batch_size,
            )
            METRICS.set("generation_worker_heartbeat", 1, labels={"worker_index": worker_index})
            with ThreadPoolExecutor(max_workers=50, thread_name_prefix="generation") as executor:
                in_flight: dict[Future[bool], str] = {}
                last_capacity: tuple[int, int] | None = None
                while not stopping.is_set():
                    capacity = capacity_settings.current()
                    capacity_key = (capacity.enabled_workers, capacity.concurrency_per_worker)
                    if capacity_key != last_capacity:
                        _LOG.info(
                            "generation worker capacity applied index=%s enabled_workers=%s concurrency=%s active=%s",
                            worker_index,
                            capacity.enabled_workers,
                            capacity.concurrency_per_worker,
                            worker_index <= capacity.enabled_workers,
                        )
                        last_capacity = capacity_key
                    completed = {future for future in in_flight if future.done()}
                    for future in completed:
                        task_id = in_flight.pop(future)
                        try:
                            processed = future.result()
                            METRICS.inc(
                                "generation_tasks_processed_total",
                                labels={"outcome": "submitted" if processed else "skipped"},
                            )
                            if processed:
                                METRICS.set("generation_worker_last_success_timestamp", time.time(), labels={"worker_index": worker_index})
                        except Exception:
                            METRICS.inc("generation_tasks_processed_total", labels={"outcome": "error"})
                            _LOG.exception("generation task processing failed task_id=%s", task_id)
                    worker_enabled = worker_index <= capacity.enabled_workers
                    available = max(0, capacity.concurrency_per_worker - len(in_flight)) if worker_enabled else 0
                    scheduled = 0
                    if available > 0:
                        with engine.connect() as connection:
                            candidates = tuple(connection.execute(
                                _PENDING_TASKS,
                                {"limit": batch_size},
                            ))
                            oldest_age = connection.execute(_OLDEST_QUEUED).scalar_one_or_none()
                        METRICS.set("generation_queue_oldest_task_age_seconds", float(oldest_age or 0))
                        METRICS.set("generation_worker_in_flight", len(in_flight), labels={"worker_index": worker_index})
                        active_task_ids = set(in_flight.values())
                        for (
                            account_space_id,
                            task_id,
                            _route_id,
                            concurrency_group,
                            max_concurrency,
                            account_concurrency,
                        ) in candidates:
                            task_id_text = str(task_id)
                            if task_id_text in active_task_ids:
                                continue
                            future = executor.submit(
                                _process_candidate,
                                engine,
                                submitter,
                                str(account_space_id),
                                task_id_text,
                                int(account_concurrency),
                                str(concurrency_group),
                                int(max_concurrency),
                            )
                            in_flight[future] = task_id_text
                            active_task_ids.add(task_id_text)
                            scheduled += 1
                            if scheduled >= available:
                                break
                    if scheduled == 0:
                        if in_flight:
                            wait(tuple(in_flight), timeout=poll_seconds, return_when=FIRST_COMPLETED)
                        else:
                            stopping.wait(poll_seconds)
    finally:
        engine.dispose()
        if worker_index is not None:
            METRICS.set("generation_worker_heartbeat", 0, labels={"worker_index": worker_index})
        _LOG.info("generation worker stopped")


def _process_candidate(
    engine: Engine,
    submitter: object,
    account_space_id: str,
    task_id: str,
    account_concurrency: int,
    concurrency_group: str,
    max_concurrency: int,
) -> bool:
    task_key = f"generation-task:{task_id}"
    account_pool_key = f"generation-account-running:{account_space_id}"
    pool_key = f"generation-provider-pool:{concurrency_group}"
    with postgres_advisory_generation_dispatch_lock(
        engine,
        task_key,
        account_pool_key,
        account_concurrency,
        pool_key,
        max_concurrency,
    ) as slots:
        if slots is None:
            return False
        with engine.connect() as connection:
            eligibility = connection.execute(
                _DISPATCH_ELIGIBILITY,
                {"account_space_id": account_space_id, "task_id": task_id},
            ).one_or_none()
        if (
            eligibility is None
            or str(eligibility.status) != "queued"
            or int(eligibility.running_count) >= int(eligibility.execution_concurrency)
            or (
                int(eligibility.execution_concurrency) == 1
                and int(eligibility.earlier_queued_count) > 0
            )
        ):
            return False
        submitter.submit(account_space_id, task_id)  # type: ignore[attr-defined]
        return True


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


if __name__ == "__main__":
    run()

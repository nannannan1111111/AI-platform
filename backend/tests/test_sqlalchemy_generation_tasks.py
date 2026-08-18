from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.canvases import CanvasCreation, CanvasDeletion, CanvasNotFound, SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits
from app.generation import (
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationGlobalCapacityLimit,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTaskStatus,
    SqlAlchemyGenerationTasks,
)
from app.worker_capacity import SqlAlchemyWorkerCapacitySettings


def test_global_active_image_limit_is_dynamic_and_counts_across_accounts(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'global-generation-capacity.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url)
    first = accounts.register("global-first@example.com", "a-correct-horse-battery-staple")
    second = accounts.register("global-second@example.com", "a-correct-horse-battery-staple")
    now = datetime(2027, 8, 8, 12, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    for index, account_space_id in enumerate((first.account_space_id, second.account_space_id), start=1):
        credits.record_recharge(
            account_space_id,
            package.version_id,
            payment_reference=f"global-capacity-payment-{index}",
            occurred_at=now,
        )
    capacity = SqlAlchemyWorkerCapacitySettings.for_database_url(database_url, deployed_worker_limit=4)
    capacity.update(4, 5, 5, 10)
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=20,
    )

    def submission(registration, task_id: str, quantity: int) -> GenerationSubmission:
        return GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id=None,
            task_id=task_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=quantity,
            prompt="测试全站生图容量",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )

    tasks.submit(submission(first, "global-task-4", 4))
    with pytest.raises(GenerationGlobalCapacityLimit):
        tasks.submit(submission(second, "global-task-2", 2))
    assert credits.statement(second.account_space_id).frozen_credits == "0.0000"
    assert capacity.usage() == {
        "queued_image_units": 4,
        "running_image_units": 0,
        "active_image_units": 4,
    }

    capacity.update(4, 5, 6, 10)
    accepted = tasks.submit(submission(second, "global-task-2", 2))
    assert accepted.quantity == 2
    assert capacity.usage()["active_image_units"] == 6


def test_sqlalchemy_recent_canvas_tasks_include_terminal_tasks_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'recent-generation-tasks.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "recent-tasks@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-recent-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-recent-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )

    def submit(task_id: str, submitted_at: datetime):
        return tasks.submit(
            GenerationSubmission(
                user_id=registration.user_id,
                account_space_id=registration.account_space_id,
                canvas_id="canvas-recent-1",
                task_id=task_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="测试生成请求",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=submitted_at,
            )
        )

    submit("task-recent-1", now)
    submit("task-recent-2", now + timedelta(seconds=1))
    failed = tasks.transition(
        registration.account_space_id,
        "task-recent-2",
        GenerationFailed(
            reason="generation attempts exhausted",
            outcome_reference="generation-attempt:attempt-2",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    newest = submit("task-recent-3", now + timedelta(seconds=3))
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    assert restarted.recent_for_canvas(
        registration.account_space_id,
        "canvas-recent-1",
        limit=2,
    ) == (newest, failed)
    assert restarted.recent_for_canvas("another-account-space", "canvas-recent-1", limit=2) == ()


def test_sqlalchemy_recent_account_tasks_keep_deleted_canvas_history_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'account-generation-history.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "account-history@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 14, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-account-history-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(
        database_url,
        id_factory=iter(("canvas-active", "canvas-deleted")).__next__,
    )
    for title in ("保留画布", "待删除画布"):
        canvases.create(
            CanvasCreation(
                user_id=registration.user_id,
                account_space_id=registration.account_space_id,
                title=title,
                kind="classic",
                created_at=now,
            )
        )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )

    def submit(task_id: str, canvas_id: str, submitted_at: datetime):
        return tasks.submit(
            GenerationSubmission(
                user_id=registration.user_id,
                account_space_id=registration.account_space_id,
                canvas_id=canvas_id,
                task_id=task_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="测试生成请求",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=submitted_at,
            )
        )

    active_task = submit("task-active", "canvas-active", now)
    deleted_canvas_task = submit("task-deleted", "canvas-deleted", now + timedelta(seconds=1))
    canvases.delete(
        CanvasDeletion(
            account_space_id=registration.account_space_id,
            canvas_id="canvas-deleted",
            deleted_at=now + timedelta(seconds=2),
        )
    )
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    assert restarted.recent_for_account(registration.account_space_id, limit=2) == (
        deleted_canvas_task,
        active_task,
    )
    assert restarted.recent_for_account("another-account-space", limit=2) == ()


def test_sqlalchemy_generation_rejects_unknown_canvas_before_freezing_credits(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'unknown-canvas.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    with pytest.raises(CanvasNotFound):
        tasks.submit(
            GenerationSubmission(
                user_id=registration.user_id,
                account_space_id=registration.account_space_id,
                canvas_id="unknown-canvas",
                task_id="task-1",
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="测试生成请求",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=now,
            )
        )

    assert tuple(entry.kind for entry in credits.statement(registration.account_space_id).entries) == ("recharge",)


def test_sqlalchemy_standalone_generation_task_survives_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'standalone-generation.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "standalone@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 15, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-standalone-1",
        occurred_at=now,
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    created = tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id=None,
            task_id="standalone-task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="独立图片生成",
            params=GenerationParameters(
                aspect_ratio="3:4",
                resolution_tier="2k",
                output_format="jpeg",
            ),
            reference_media_ids=("reference-1", "reference-2"),
            mask_media_id="mask-1",
            submitted_at=now,
        )
    )
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    assert created.canvas_id is None
    assert created.params.quality == "auto"
    assert created.params.size == "1536x2048"
    assert created.params.resolution_tier == "2k"
    assert created.params.output_format == "jpeg"
    assert created.reference_media_ids == ("reference-1", "reference-2")
    assert created.mask_media_id == "mask-1"
    assert restarted.get(registration.account_space_id, "standalone-task-1") == created


def test_sqlalchemy_generation_task_survives_restart_with_ownership_and_frozen_credits(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-tasks.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    submission = GenerationSubmission(
        user_id=registration.user_id,
        account_space_id=registration.account_space_id,
        canvas_id="canvas-1",
        task_id="task-1",
        logical_model="gpt-image-2",
        output_spec="4k",
        quantity=1,
        prompt="一座漂浮在云海上的图书馆",
        params=GenerationParameters(aspect_ratio="16:9"),
        submitted_at=now,
    )

    task = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    ).submit(submission)
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )

    assert restarted.get(registration.account_space_id, "task-1") == task
    assert task.status is GenerationTaskStatus.QUEUED
    assert task.prompt == "一座漂浮在云海上的图书馆"
    assert task.params == GenerationParameters(aspect_ratio="16:9")
    assert credits.statement(registration.account_space_id).frozen_credits == "0.1500"


def test_sqlalchemy_generation_success_settles_after_task_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-task-transition.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id, package.version_id, payment_reference="payment-1", occurred_at=now
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )

    restarted.transition(
        registration.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-1", occurred_at=now),
    )
    task = restarted.transition(
        registration.account_space_id,
        "task-1",
        GenerationSucceeded(delivered_quantity=1, outcome_reference="outcome-1", occurred_at=now),
    )

    assert task.status is GenerationTaskStatus.SUCCEEDED
    assert task.delivered_quantity == 1
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.0000"


def test_sqlalchemy_generation_failure_releases_frozen_credits_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-task-failure.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id, package.version_id, payment_reference="payment-1", occurred_at=now
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    event = GenerationFailed(reason="provider failed", outcome_reference="failure-1", occurred_at=now)

    failed = restarted.transition(registration.account_space_id, "task-1", event)
    replay = restarted.transition(registration.account_space_id, "task-1", event)

    assert failed.status.value == "failed"
    assert replay == failed
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"


def test_sqlalchemy_generation_tasks_expire_twenty_minutes_after_dispatch_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-task-timeout.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "timeout@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-timeout-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-timeout-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="超时画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-timeout-1",
            task_id="task-timeout-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试五分钟超时",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    started_at = now + timedelta(hours=1)
    restarted.transition(
        registration.account_space_id,
        "task-timeout-1",
        GenerationDispatchStarted(occurred_at=started_at),
    )

    assert restarted.expire_due(started_at + timedelta(minutes=10) - timedelta(microseconds=1)) == ()
    expired = restarted.expire_due(started_at + timedelta(minutes=10))
    replay = restarted.expire_due(started_at + timedelta(minutes=11))

    assert len(expired) == 1
    assert expired[0].status is GenerationTaskStatus.FAILED
    assert expired[0].error == "generation task exceeded configured deadline"
    assert expired[0].outcome_reference == (f"generation-timeout:{registration.account_space_id}:task-timeout-1")
    assert replay == ()
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("release") == 1


def test_sqlalchemy_clear_history_persists_without_deleting_tasks(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-history-visibility.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "sql-history@example.com", "a-correct-horse-battery-staple"
    )
    now = datetime(2027, 8, 13, 10, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="2.00", credits="2.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="sql-history-payment",
        occurred_at=now,
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=3,
    )

    def submit(task_id: str) -> None:
        tasks.submit(
            GenerationSubmission(
                user_id=registration.user_id,
                account_space_id=registration.account_space_id,
                canvas_id=None,
                task_id=task_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="clear SQL history",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=now,
            )
        )

    submit("sql-terminal")
    tasks.transition(
        registration.account_space_id,
        "sql-terminal",
        GenerationFailed(reason="expected failure", outcome_reference="sql-failure", occurred_at=now),
    )
    submit("sql-active")

    assert tasks.clear_history(registration.account_space_id, cleared_at=now + timedelta(minutes=1)) == 1
    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=3,
    )

    assert [task.task_id for task in restarted.recent_for_account(registration.account_space_id, limit=20)] == [
        "sql-active"
    ]
    assert restarted.get(registration.account_space_id, "sql-terminal").status is GenerationTaskStatus.FAILED
    assert restarted.activity_summary(registration.account_space_id, since=None).total_tasks == 2
    assert restarted.clear_history(registration.account_space_id, cleared_at=now + timedelta(minutes=2)) == 0

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.accounts import InMemoryAccountAccess, SqlAlchemyAccountAccess
from app.canvases import InMemoryCanvases, SqlAlchemyCanvases
from app.credits import InMemoryCredits, SqlAlchemyCredits, SqlAlchemyModelPrices
from app.generation import (
    GenerationCancelled,
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationParameters,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTaskStatus,
    InMemoryGenerationTasks,
    SqlAlchemyGenerationTasks,
)
from app.http import create_app


def _admin_generation_context() -> tuple[
    TestClient,
    dict[str, str],
    dict[str, str],
    InMemoryGenerationTasks,
    InMemoryCredits,
    datetime,
]:
    now = datetime(2026, 8, 14, 14, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    admin_session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    artist_session = accounts.login("artist@example.com", "another-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={artist.account_space_id},
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        artist.account_space_id,
        package.version_id,
        payment_reference="admin-task-test-payment",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=3)
    for index, task_id in enumerate(("queued-task", "running-task")):
        tasks.submit(
            GenerationSubmission(
                user_id=artist.user_id,
                account_space_id=artist.account_space_id,
                canvas_id=None,
                task_id=task_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt=f"管理任务 {index + 1}",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=now + timedelta(seconds=index),
            )
        )
    tasks.transition(
        artist.account_space_id,
        "running-task",
        GenerationDispatchStarted(occurred_at=now + timedelta(seconds=2)),
    )

    def authorize_admin(token: str) -> None:
        if token != admin_session.access_token:
            raise PermissionError

    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=credits,
            generation_tasks=tasks,
            admin_authorizer=authorize_admin,
            clock=lambda: now + timedelta(minutes=1),
        )
    )
    return (
        client,
        {"Authorization": f"Bearer {admin_session.access_token}"},
        {"Authorization": f"Bearer {artist_session.access_token}"},
        tasks,
        credits,
        now,
    )


def test_administrator_lists_active_generation_tasks_across_accounts() -> None:
    client, admin_headers, artist_headers, _, _, _ = _admin_generation_context()

    response = client.get("/api/v1/admin/generation-tasks/active", headers=admin_headers)

    assert response.status_code == 200
    assert [(item["task_id"], item["status"]) for item in response.json()] == [
        ("queued-task", "queued"),
        ("running-task", "running"),
    ]
    assert {item["user_email"] for item in response.json()} == {"artist@example.com"}
    assert response.json()[1]["started_at"] == "2026-08-14T14:00:02Z"
    assert all("prompt" not in item for item in response.json())
    assert "管理任务 1" not in response.text
    assert "管理任务 2" not in response.text
    assert "credit_freeze_id" not in response.text
    assert "provider_task_id" not in response.text
    assert client.get("/api/v1/admin/generation-tasks/active", headers=artist_headers).status_code == 403


def test_administrator_cancels_active_task_and_refunds_frozen_credits_idempotently() -> None:
    client, admin_headers, _, tasks, credits, _ = _admin_generation_context()
    account_space_id = tasks.active_across_accounts()[0].account_space_id
    request = {"account_space_id": account_space_id}

    cancelled = client.post(
        "/api/v1/admin/generation-tasks/running-task/cancel",
        headers=admin_headers,
        json=request,
    )
    replay = client.post(
        "/api/v1/admin/generation-tasks/running-task/cancel",
        headers=admin_headers,
        json=request,
    )

    assert cancelled.status_code == replay.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["failure_message"] == "任务已由平台管理员取消，冻结额度已退回。"
    assert replay.json() == cancelled.json()
    assert tasks.get(account_space_id, "running-task").status is GenerationTaskStatus.CANCELLED
    statement = credits.statement(account_space_id)
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.1500"
    assert tuple(entry.kind for entry in statement.entries).count("release") == 1
    assert [item["task_id"] for item in client.get(
        "/api/v1/admin/generation-tasks/active", headers=admin_headers
    ).json()] == ["queued-task"]


def test_administrator_cannot_cancel_a_task_that_already_succeeded() -> None:
    client, admin_headers, _, tasks, credits, now = _admin_generation_context()
    account_space_id = tasks.active_across_accounts()[0].account_space_id
    tasks.transition(
        account_space_id,
        "running-task",
        GenerationSucceeded(
            delivered_quantity=1,
            outcome_reference="already-delivered",
            occurred_at=now + timedelta(minutes=1),
        ),
    )

    response = client.post(
        "/api/v1/admin/generation-tasks/running-task/cancel",
        headers=admin_headers,
        json={"account_space_id": account_space_id},
    )

    assert response.status_code == 409
    assert tasks.get(account_space_id, "running-task").status is GenerationTaskStatus.SUCCEEDED
    assert credits.statement(account_space_id).available_credits == "0.7000"


def test_sqlalchemy_active_task_listing_excludes_terminal_tasks(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'admin-active-tasks.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)
    SqlAlchemyModelPrices.for_database_url(database_url, clock=lambda: now).publish(
        "gpt-image-2",
        "4k",
        credits_per_result="0.1500",
        effective_from=now,
    )
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "sql-admin-task@example.com",
        "a-correct-horse-battery-staple",
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="sql-admin-task-payment",
        occurred_at=now,
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=3,
    )

    def submit(task_id: str, submitted_at: datetime) -> None:
        tasks.submit(GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id=None,
            task_id=task_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt=task_id,
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=submitted_at,
        ))

    submit("sql-active", now)
    submit("sql-terminal", now + timedelta(seconds=1))
    tasks.transition(
        registration.account_space_id,
        "sql-terminal",
        GenerationFailed(
            reason="test terminal",
            outcome_reference="test-terminal",
            occurred_at=now + timedelta(seconds=2),
        ),
    )

    restarted = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=3,
    )
    assert [task.task_id for task in restarted.active_across_accounts()] == ["sql-active"]
    cancelled = restarted.transition(
        registration.account_space_id,
        "sql-active",
        GenerationCancelled(
            reason="平台管理员手动取消任务",
            outcome_reference=f"cancel:{registration.account_space_id}:sql-active",
            occurred_at=now + timedelta(seconds=3),
        ),
    )
    assert cancelled.status is GenerationTaskStatus.CANCELLED
    assert restarted.active_across_accounts() == ()
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"

from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.worker_capacity import InMemoryWorkerCapacitySettings, SqlAlchemyWorkerCapacitySettings


def test_admin_reads_and_updates_generation_worker_capacity() -> None:
    capacity = InMemoryWorkerCapacitySettings(deployed_worker_limit=4)
    authorized: list[str] = []
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            worker_capacity=capacity,
            admin_authorizer=authorized.append,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}

    current = client.get("/api/v1/admin/generation-worker-capacity", headers=headers)
    updated = client.put(
        "/api/v1/admin/generation-worker-capacity",
        headers=headers,
        json={
            "enabled_workers": 4,
            "concurrency_per_worker": 10,
            "global_active_image_limit": 800,
            "task_deadline_minutes": 12,
        },
    )

    assert current.status_code == 200
    assert current.json()["total_concurrency"] == 20
    assert updated.status_code == 200
    assert updated.json()["total_concurrency"] == 40
    assert updated.json()["deployed_worker_limit"] == 4
    assert updated.json()["global_active_image_limit"] == 800
    assert updated.json()["task_deadline_minutes"] == 12
    assert updated.json()["active_image_units"] == 0
    assert authorized == ["admin-session", "admin-session"]


def test_admin_cannot_enable_more_workers_than_are_deployed() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            worker_capacity=InMemoryWorkerCapacitySettings(deployed_worker_limit=4),
            admin_authorizer=lambda _token: None,
        )
    )

    response = client.put(
        "/api/v1/admin/generation-worker-capacity",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "enabled_workers": 5,
            "concurrency_per_worker": 8,
            "global_active_image_limit": 500,
            "task_deadline_minutes": 10,
        },
    )

    assert response.status_code == 422
    assert "1 到 4" in response.json()["detail"]


def test_sqlalchemy_worker_capacity_survives_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'worker-capacity.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    settings = SqlAlchemyWorkerCapacitySettings.for_database_url(
        database_url,
        deployed_worker_limit=4,
    )
    settings.update(3, 10, 750, 15)
    restarted = SqlAlchemyWorkerCapacitySettings.for_database_url(
        database_url,
        deployed_worker_limit=4,
    )

    assert restarted.current().enabled_workers == 3
    assert restarted.current().total_concurrency == 30
    assert restarted.current().global_active_image_limit == 750
    assert restarted.current().task_deadline_minutes == 15

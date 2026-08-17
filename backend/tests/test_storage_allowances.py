from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.media import InMemoryStorageAllowances, SqlAlchemyStorageAllowances


def test_setting_global_storage_allowance_applies_to_existing_and_future_account_spaces() -> None:
    allowances = InMemoryStorageAllowances({"existing-account-space": 100})

    policy = allowances.set_global_limit(250)

    assert policy.limit_bytes == 250
    assert allowances.limit_bytes("existing-account-space") == 250
    assert allowances.limit_bytes("future-account-space") == 250


def test_setting_one_account_storage_allowance_does_not_change_other_accounts() -> None:
    allowances = InMemoryStorageAllowances({})
    allowances.set_global_limit(250)

    policy = allowances.set_account_limit("selected-account-space", 900)

    assert policy.account_space_id == "selected-account-space"
    assert policy.limit_bytes == 900
    assert allowances.global_limit_bytes() == 250
    assert allowances.limit_bytes("selected-account-space") == 900
    assert allowances.limit_bytes("other-account-space") == 250


def test_admin_can_set_global_storage_allowance_through_injected_authorizer() -> None:
    allowances = InMemoryStorageAllowances({})
    authorized_tokens: list[str] = []
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            storage_allowances=allowances,
            admin_authorizer=authorized_tokens.append,
        )
    )

    response = client.put(
        "/api/v1/admin/storage-allowance",
        headers={"Authorization": "Bearer admin-session"},
        json={"limit_bytes": 10_737_418_240},
    )

    assert response.status_code == 200
    assert response.json() == {"limit_bytes": 10_737_418_240}
    assert authorized_tokens == ["admin-session"]
    assert allowances.limit_bytes("any-account-space") == 10_737_418_240


def test_admin_storage_allowance_rejects_values_larger_than_database_capacity() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            storage_allowances=InMemoryStorageAllowances({}),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.put(
        "/api/v1/admin/storage-allowance",
        headers={"Authorization": "Bearer admin-session"},
        json={"limit_bytes": 9_223_372_036_854_775_808},
    )

    assert response.status_code == 422


def test_admin_searches_for_a_user_and_sets_only_that_users_storage_allowance() -> None:
    accounts = InMemoryAccountAccess()
    admin = accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    allowances = InMemoryStorageAllowances({})
    allowances.set_global_limit(500_000_000)
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            storage_allowances=allowances,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}

    response = client.put(
        f"/api/v1/admin/users/{artist.user_id}/storage-allowance",
        headers=headers,
        json={"limit_bytes": 2_000_000_000},
    )

    assert response.status_code == 200
    assert response.json() == {
        "account_space_id": artist.account_space_id,
        "limit_bytes": 2_000_000_000,
    }
    assert (
        client.get(
            f"/api/v1/admin/users/{artist.user_id}/storage-allowance",
            headers=headers,
        ).json()
        == response.json()
    )
    assert allowances.limit_bytes(artist.account_space_id) == 2_000_000_000
    assert allowances.limit_bytes(admin.account_space_id) == 500_000_000
    assert client.get("/api/v1/admin/storage-allowance", headers=headers).json() == {"limit_bytes": 500_000_000}


def test_sqlalchemy_global_storage_allowance_survives_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'storage-allowance.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    allowances = SqlAlchemyStorageAllowances.for_database_url(database_url)

    assert allowances.limit_bytes("existing-account-space") == 0
    assert allowances.set_global_limit(536_870_912).limit_bytes == 536_870_912

    restarted = SqlAlchemyStorageAllowances.for_database_url(database_url)

    assert restarted.limit_bytes("existing-account-space") == 536_870_912
    assert restarted.limit_bytes("future-account-space") == 536_870_912

    restarted.set_account_limit("existing-account-space", 1_500_000_000)
    restarted_again = SqlAlchemyStorageAllowances.for_database_url(database_url)

    assert restarted_again.limit_bytes("existing-account-space") == 1_500_000_000
    assert restarted_again.limit_bytes("future-account-space") == 536_870_912
    assert restarted_again.global_limit_bytes() == 536_870_912

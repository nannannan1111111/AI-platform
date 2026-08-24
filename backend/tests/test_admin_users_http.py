from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.account_generation_limits import InMemoryAccountGenerationLimits
from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits
from app.generation import GenerationActivitySummary
from app.http import create_app


class _ActivityTasks:
    def __init__(self) -> None:
        self.since_values: list[datetime | None] = []

    def activity_summary(self, account_space_id: str, *, since: datetime | None) -> GenerationActivitySummary:
        self.since_values.append(since)
        return GenerationActivitySummary(
            total_tasks=12,
            succeeded_tasks=8,
            failed_tasks=3,
            consumed_credit_units=125_000,
        )


def test_administrator_lists_every_registered_user_without_authentication_secrets() -> None:
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    admin = accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={admin.account_space_id, artist.account_space_id},
    )
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=credits,
            admin_authorizer=lambda token: None,
            clock=lambda: now,
        )
    )

    response = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "user_id": admin.user_id,
            "account_space_id": admin.account_space_id,
            "email": "admin@example.com",
            "email_verified": False,
            "registered_at": "2026-08-10T08:00:00Z",
            "available_credits": "0.0000",
            "frozen_credits": "0.0000",
            "generation_execution_concurrency": 2,
        },
        {
            "user_id": artist.user_id,
            "account_space_id": artist.account_space_id,
            "email": "artist@example.com",
            "email_verified": False,
            "registered_at": "2026-08-10T08:00:00Z",
            "available_credits": "0.0000",
            "frozen_credits": "0.0000",
            "generation_execution_concurrency": 2,
        },
    ]
    assert "password_hash" not in response.text
    assert "access_token" not in response.text


def test_administrator_finds_one_user_by_exact_email_without_listing_for_controls() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    client = TestClient(create_app(
        accounts,
        account_directory=accounts,
        credit_accounting=InMemoryCredits(account_space_ids={artist.account_space_id}),
        admin_authorizer=lambda token: None,
    ))

    response = client.get(
        "/api/v1/admin/users/by-email?email=ARTIST%40EXAMPLE.COM",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == artist.user_id
    assert response.json()["email"] == "artist@example.com"
    assert client.get(
        "/api/v1/admin/users/by-email?email=missing%40example.com",
        headers={"Authorization": f"Bearer {session.access_token}"},
    ).status_code == 404


def test_administrator_reads_user_activity_for_seven_thirty_and_all_time() -> None:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    admin = accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    tasks = _ActivityTasks()
    client = TestClient(create_app(
        accounts,
        account_directory=accounts,
        credit_accounting=InMemoryCredits(account_space_ids={admin.account_space_id}),
        generation_tasks=tasks,
        admin_authorizer=lambda token: None,
        clock=lambda: now,
    ))
    headers = {"Authorization": f"Bearer {session.access_token}"}

    seven = client.get("/api/v1/admin/user-activity?window=7d", headers=headers)
    thirty = client.get("/api/v1/admin/user-activity?window=30d", headers=headers)
    all_time = client.get("/api/v1/admin/user-activity?window=all", headers=headers)

    assert seven.status_code == thirty.status_code == all_time.status_code == 200
    assert seven.json()[0]["consumed_credits"] == "12.5000"
    assert seven.json()[0]["failed_tasks"] == 3
    assert tasks.since_values == [now - timedelta(days=7), now - timedelta(days=30), None]


def test_administrator_updates_one_users_generation_execution_concurrency() -> None:
    accounts = InMemoryAccountAccess()
    admin = accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(account_space_ids={admin.account_space_id, artist.account_space_id})
    limits = InMemoryAccountGenerationLimits()
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=credits,
            account_generation_limits=limits,
            admin_authorizer=lambda token: None,
        )
    )

    response = client.put(
        f"/api/v1/admin/users/{artist.user_id}/generation-limit",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={"execution_concurrency": 7},
    )

    assert response.status_code == 200
    assert response.json()["account_space_id"] == artist.account_space_id
    assert response.json()["execution_concurrency"] == 7
    users = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {session.access_token}"},
    ).json()
    artist_view = next(item for item in users if item["user_id"] == artist.user_id)
    assert artist_view["generation_execution_concurrency"] == 7


def test_user_generation_execution_concurrency_must_be_between_one_and_fifty() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=InMemoryCredits(account_space_ids={artist.account_space_id}),
            account_generation_limits=InMemoryAccountGenerationLimits(),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.put(
        f"/api/v1/admin/users/{artist.user_id}/generation-limit",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={"execution_concurrency": 51},
    )

    assert response.status_code == 422


def test_administrator_grants_permanent_credits_to_one_user_idempotently() -> None:
    now = datetime(2026, 8, 10, 8, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    admin = accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={admin.account_space_id, artist.account_space_id},
    )
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=credits,
            admin_authorizer=lambda token: None,
            clock=lambda: now,
        )
    )
    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Idempotency-Key": "support-ticket-1001",
    }

    created = client.post(
        f"/api/v1/admin/users/{artist.user_id}/credit-grants",
        headers=headers,
        json={"credits": "12.5000", "reason": "客服人工充值"},
    )
    replay = client.post(
        f"/api/v1/admin/users/{artist.user_id}/credit-grants",
        headers=headers,
        json={"credits": "12.5000", "reason": "客服人工充值"},
    )
    users = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert created.status_code == 201
    assert created.json()["kind"] == "admin_grant"
    assert created.json()["delta_available_credits"] == "12.5000"
    assert created.json()["available_credits_after"] == "12.5000"
    assert created.json()["reason"] == "客服人工充值"
    assert replay.status_code == 201
    assert replay.json() == created.json()
    artist_view = next(item for item in users.json() if item["user_id"] == artist.user_id)
    assert artist_view["available_credits"] == "12.5000"


def test_administrator_reads_one_users_recharge_records_without_payment_secrets() -> None:
    now = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    artist = accounts.register("artist@example.com", "another-correct-horse-battery-staple")
    session = accounts.login("admin@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={artist.account_space_id})
    package = credits.publish(
        "starter",
        payment_cny="10.00",
        credits="20.0000",
        effective_from=now,
    )
    credits.record_recharge(
        artist.account_space_id,
        package.version_id,
        payment_reference="payment:secret-provider-reference",
        occurred_at=now,
    )
    grant = credits.record_admin_grant(
        artist.account_space_id,
        "5.0000",
        grant_reference="admin-grant:private-idempotency-key",
        reason="客服补偿",
        occurred_at=now + timedelta(minutes=1),
    )
    credits.reverse(
        grant.posting_id,
        reversal_reference="reversal:private-reference",
        reason="录入错误",
        occurred_at=now + timedelta(minutes=2),
    )
    client = TestClient(
        create_app(
            accounts,
            account_directory=accounts,
            credit_accounting=credits,
            admin_authorizer=lambda token: None,
        )
    )

    response = client.get(
        f"/api/v1/admin/users/{artist.user_id}/recharge-records",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "posting_id": response.json()[0]["posting_id"],
            "occurred_at": "2026-08-11T15:02:00Z",
            "type": "reversal",
            "credits": "-5.0000",
            "reason": "录入错误",
            "status": "posted",
        },
        {
            "posting_id": grant.posting_id,
            "occurred_at": "2026-08-11T15:01:00Z",
            "type": "admin_recharge",
            "credits": "5.0000",
            "reason": "客服补偿",
            "status": "reversed",
        },
        {
            "posting_id": response.json()[2]["posting_id"],
            "occurred_at": "2026-08-11T15:00:00Z",
            "type": "payment_recharge",
            "credits": "20.0000",
            "reason": None,
            "status": "posted",
        },
    ]
    assert "secret-provider-reference" not in response.text
    assert "private-idempotency-key" not in response.text
    assert "private-reference" not in response.text

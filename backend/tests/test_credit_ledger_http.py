from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.http import create_app


def test_authenticated_ledger_route_returns_the_current_account_statement() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=InMemoryModelPrices(clock=lambda: now),
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    client = TestClient(create_app(accounts, credit_accounting=credits, clock=lambda: now))

    response = client.get(
        "/api/v1/credits/ledger",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["available_credits"] == "1.0000"
    assert body["frozen_credits"] == "0.0000"
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total_entries"] == 1
    assert body["total_pages"] == 1
    assert body["entries"][0]["kind"] == "recharge"
    assert body["entries"][0]["account_space_id"] == registration.account_space_id


def test_ledger_route_pages_newest_entries_first() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("paged@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("paged@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(account_space_ids={registration.account_space_id})
    for index in range(25):
        credits.record_admin_grant(
            registration.account_space_id,
            "1.0000",
            grant_reference=f"grant-{index}",
            reason="pagination test",
            occurred_at=now,
        )
    client = TestClient(create_app(accounts, credit_accounting=credits))
    headers = {"Authorization": f"Bearer {session.access_token}"}

    first = client.get("/api/v1/credits/ledger?page=1&page_size=20", headers=headers)
    second = client.get("/api/v1/credits/ledger?page=2&page_size=20", headers=headers)

    assert first.status_code == 200
    assert first.json()["total_entries"] == 25
    assert first.json()["total_pages"] == 2
    assert len(first.json()["entries"]) == 20
    assert first.json()["entries"][0]["reference"] == "grant-24"
    assert first.json()["entries"][-1]["reference"] == "grant-5"
    assert second.status_code == 200
    assert second.json()["page"] == 2
    assert [entry["reference"] for entry in second.json()["entries"]] == [
        "grant-4",
        "grant-3",
        "grant-2",
        "grant-1",
        "grant-0",
    ]


def test_ledger_route_rejects_invalid_pagination() -> None:
    client = TestClient(create_app(InMemoryAccountAccess(), credit_accounting=InMemoryCredits()))

    assert client.get("/api/v1/credits/ledger?page=0").status_code == 422
    assert client.get("/api/v1/credits/ledger?page_size=101").status_code == 422

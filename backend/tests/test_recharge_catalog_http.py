from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits
from app.http import create_app


def test_recharge_catalog_returns_only_the_latest_sellable_version_per_package_code() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids=set())
    first = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    second = credits.publish(
        "starter",
        payment_cny="2.00",
        credits="2.5000",
        effective_from=datetime(2026, 8, 8, 13, 1, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            accounts,
            recharge_packages=credits,
            clock=lambda: datetime(2026, 8, 8, 13, 2, tzinfo=UTC),
        )
    )

    response = client.get("/api/v1/recharge-packages")

    assert response.status_code == 200
    assert response.json() == [
        {
            "version_id": second.version_id,
            "package_code": "starter",
            "payment_cny": "2.00",
            "credits": "2.5000",
            "effective_from": "2026-08-08T13:01:00Z",
            "published_at": "2026-08-08T13:00:00Z",
        }
    ]
    assert first.version_id != second.version_id

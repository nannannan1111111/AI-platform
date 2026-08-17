from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.http import create_app


def test_admin_can_publish_a_versioned_recharge_package_through_injected_authorizer() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids=set())
    authorized_tokens: list[str] = []

    def authorize(token: str) -> None:
        authorized_tokens.append(token)

    client = TestClient(
        create_app(
            accounts,
            recharge_packages=credits,
            admin_authorizer=authorize,
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/admin/recharge-packages",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "package_code": "starter",
            "payment_cny": "1.00",
            "credits": "1.0000",
            "effective_from": "2026-08-08T13:01:00Z",
        },
    )

    assert response.status_code == 201
    assert authorized_tokens == ["admin-session"]
    assert response.json()["package_code"] == "starter"
    assert response.json()["payment_cny"] == "1.00"
    assert response.json()["credits"] == "1.0000"


def test_admin_can_publish_a_new_model_price_version_without_rewriting_history() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    prices = InMemoryModelPrices(clock=lambda: now)

    client = TestClient(
        create_app(
            accounts,
            model_prices=prices,
            admin_authorizer=lambda token: None,
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/admin/model-prices",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "credits_per_result": "0.2000",
            "max_reference_images": 6,
            "effective_from": "2026-08-08T13:01:00Z",
        },
    )

    assert response.status_code == 201
    assert response.json()["credits_per_result"] == "0.2000"
    assert response.json()["max_reference_images"] == 6
    assert (
        prices.effective_at("gpt-image-2", "4k", datetime(2026, 8, 8, 13, 1, tzinfo=UTC)).credits_per_result == "0.2000"
    )


def test_admin_can_delete_a_model_price_without_deleting_its_history() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    version = prices.publish(
        "gpt-image-2",
        "2k",
        credits_per_result="0.1000",
        effective_from=now,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            model_prices=prices,
            admin_authorizer=lambda token: None,
            clock=lambda: now,
        )
    )

    response = client.delete(
        f"/api/v1/admin/model-prices/{version.version_id}",
        headers={"Authorization": "Bearer admin-session"},
    )

    assert response.status_code == 204
    assert all(item["output_spec"] != "2k" for item in client.get("/api/v1/model-prices").json())
    assert prices.get_version(version.version_id) == version


def test_model_price_deletion_requires_platform_admin_authority() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    version = prices.catalog_at(now)[0]

    def reject(_: str) -> None:
        raise PermissionError

    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            model_prices=prices,
            admin_authorizer=reject,
            clock=lambda: now,
        )
    )

    response = client.delete(
        f"/api/v1/admin/model-prices/{version.version_id}",
        headers={"Authorization": "Bearer user-session"},
    )

    assert response.status_code == 403


def test_catalog_publication_rejects_a_token_without_platform_admin_authority() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids=set())

    def reject(_: str) -> None:
        raise PermissionError

    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            recharge_packages=credits,
            admin_authorizer=reject,
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/admin/recharge-packages",
        headers={"Authorization": "Bearer user-session"},
        json={
            "package_code": "starter",
            "payment_cny": "1.00",
            "credits": "1.0000",
            "effective_from": "2026-08-08T13:00:00Z",
        },
    )

    assert response.status_code == 403

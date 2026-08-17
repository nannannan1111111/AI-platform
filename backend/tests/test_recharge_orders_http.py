from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits
from app.http import create_app
from app.orders import InMemoryRechargeOrders, PaymentSuccess, RechargeOrderSubmission


def test_authenticated_recharge_order_routes_derive_ownership_from_bearer_session() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    client = TestClient(create_app(accounts, recharge_orders=orders, clock=lambda: now))
    headers = {
        "Authorization": f"Bearer {session.access_token}",
        "Idempotency-Key": "order-key-1",
    }

    response = client.post(
        "/api/v1/recharge-orders",
        headers=headers,
        json={
            "package_version_id": package.version_id,
            "payment_provider": "fakepay",
            "user_id": "attacker",
            "account_space_id": "attacker-space",
            "payment_cny": "0.01",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == registration.user_id
    assert body["account_space_id"] == registration.account_space_id
    assert body["payment_cny"] == "1.00"
    assert body["credits"] == "1.0000"
    assert (
        client.get(
            f"/api/v1/recharge-orders/{body['order_id']}",
            headers={"Authorization": f"Bearer {session.access_token}"},
        ).json()
        == body
    )
    assert client.get(
        "/api/v1/recharge-orders",
        headers={"Authorization": f"Bearer {session.access_token}"},
    ).json() == [body]


def test_verified_payment_notification_marks_order_paid_and_posts_credits_once() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    verified: list[tuple[str, str]] = []

    def verify(payment_provider: str, signature: str) -> None:
        verified.append((payment_provider, signature))

    client = TestClient(
        create_app(
            accounts,
            recharge_orders=orders,
            payment_notification_verifier=verify,
            clock=lambda: now,
        )
    )
    order = client.post(
        "/api/v1/recharge-orders",
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Idempotency-Key": "order-key-1",
        },
        json={"package_version_id": package.version_id, "payment_provider": "fakepay"},
    ).json()

    response = client.post(
        "/api/v1/payments/fakepay/notifications",
        headers={"X-Payment-Signature": "fake-signature"},
        json={
            "order_id": order["order_id"],
            "provider_event_id": "event-1",
            "paid_payment_cny": "1.00",
            "occurred_at": "2026-08-08T13:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert verified == [("fakepay", "fake-signature")]
    assert credits.statement(registration.account_space_id).available_credits == "1.0000"


def test_rejected_payment_signature_cannot_mark_order_paid_or_post_credits() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)

    def reject(_: str, __: str) -> None:
        raise PermissionError

    client = TestClient(
        create_app(
            accounts,
            recharge_orders=orders,
            payment_notification_verifier=reject,
            clock=lambda: now,
        )
    )
    order = client.post(
        "/api/v1/recharge-orders",
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Idempotency-Key": "order-key-1",
        },
        json={"package_version_id": package.version_id, "payment_provider": "fakepay"},
    ).json()

    response = client.post(
        "/api/v1/payments/fakepay/notifications",
        headers={"X-Payment-Signature": "invalid-signature"},
        json={
            "order_id": order["order_id"],
            "provider_event_id": "event-1",
            "paid_payment_cny": "1.00",
            "occurred_at": "2026-08-08T13:00:00Z",
        },
    )

    assert response.status_code == 401
    assert orders.get(registration.account_space_id, order["order_id"]).status.value == "pending"
    assert credits.statement(registration.account_space_id).available_credits == "0.0000"


def test_verified_chargeback_notification_reverses_the_paid_order() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    order = orders.create(
        RechargeOrderSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-1",
            created_at=now,
        )
    )
    orders.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    verified: list[tuple[str, str]] = []

    def verify(payment_provider: str, signature: str) -> None:
        verified.append((payment_provider, signature))

    client = TestClient(
        create_app(
            accounts,
            recharge_order_chargebacks=orders,
            chargeback_notification_verifier=verify,
        )
    )

    response = client.post(
        "/api/v1/payments/fakepay/chargebacks",
        headers={"X-Payment-Signature": "fake-signature"},
        json={
            "order_id": order.order_id,
            "provider_event_id": "chargeback-event-1",
            "charged_back_payment_cny": "1.00",
            "occurred_at": "2026-08-08T13:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "charged_back"
    assert verified == [("fakepay", "fake-signature")]
    assert credits.statement(registration.account_space_id).available_credits == "0.0000"


def test_rejected_chargeback_signature_cannot_reverse_the_paid_order() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    order = orders.create(
        RechargeOrderSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-1",
            created_at=now,
        )
    )
    paid = orders.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )

    def reject(_: str, __: str) -> None:
        raise PermissionError

    client = TestClient(
        create_app(
            accounts,
            recharge_order_chargebacks=orders,
            chargeback_notification_verifier=reject,
        )
    )

    response = client.post(
        "/api/v1/payments/fakepay/chargebacks",
        headers={"X-Payment-Signature": "invalid-signature"},
        json={
            "order_id": order.order_id,
            "provider_event_id": "chargeback-event-1",
            "charged_back_payment_cny": "1.00",
            "occurred_at": "2026-08-08T13:00:00Z",
        },
    )

    assert response.status_code == 401
    assert orders.get(registration.account_space_id, order.order_id) == paid
    statement = credits.statement(registration.account_space_id)
    assert statement.available_credits == "1.0000"
    assert tuple(entry.kind for entry in statement.entries) == ("recharge",)

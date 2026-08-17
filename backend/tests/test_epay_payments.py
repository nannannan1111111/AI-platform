from datetime import UTC, datetime
from hashlib import md5

import pytest
from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryCredits
from app.http import create_app
from app.model_routing import InMemoryProviderSecrets
from app.orders import InMemoryRechargeOrders
from app.payments import (
    InMemoryEpayPayments,
    InvalidPaymentSettings,
    PaymentMethod,
    PaymentSettingsUpdate,
    SqlAlchemyEpayPayments,
)


def _settings(*, enabled: bool = True, merchant_key: str = "merchant-secret") -> PaymentSettingsUpdate:
    return PaymentSettingsUpdate(
        enabled=enabled,
        gateway_url="https://pay.example.com/gateway",
        public_base_url="https://studio.example.com",
        merchant_id="10001",
        merchant_key=merchant_key,
        methods=(
            PaymentMethod("alipay", "支付宝"),
            PaymentMethod("wxpay", "微信支付"),
        ),
    )


def _signed(parameters: dict[str, str], key: str = "merchant-secret") -> dict[str, str]:
    values = {**parameters, "sign_type": "MD5"}
    unsigned = "&".join(
        f"{name}={values[name]}"
        for name in sorted(values)
        if name not in {"sign", "sign_type"} and values[name] != ""
    )
    values["sign"] = md5(f"{unsigned}{key}".encode(), usedforsecurity=False).hexdigest()
    return values


def test_payment_settings_require_https_and_a_key_before_enabling() -> None:
    payments = InMemoryEpayPayments()

    with pytest.raises(InvalidPaymentSettings, match="HTTPS"):
        payments.update(
            PaymentSettingsUpdate(
                enabled=False,
                gateway_url="http://pay.example.com",
                public_base_url="https://studio.example.com",
                merchant_id="10001",
                methods=(PaymentMethod("alipay", "支付宝"),),
            )
        )
    with pytest.raises(InvalidPaymentSettings, match="商户密钥"):
        payments.update(_settings(merchant_key=""))


def test_sql_payment_settings_store_the_merchant_key_outside_the_database(tmp_path) -> None:
    secrets = InMemoryProviderSecrets()
    payments = SqlAlchemyEpayPayments.for_database_url(
        f"sqlite+pysqlite:///{(tmp_path / 'payment-settings.db').as_posix()}",
        secrets,
        initialize_schema=True,
    )

    snapshot = payments.update(_settings())

    assert snapshot.configured is True
    assert snapshot.merchant_key_configured is True
    assert "merchant-secret" not in repr(snapshot)
    assert payments.available() == _settings().methods


def test_admin_payment_settings_api_never_returns_the_merchant_key() -> None:
    payments = InMemoryEpayPayments()
    authorized: list[str] = []
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            payment_methods=payments,
            epay_payments=payments,
            admin_authorizer=authorized.append,
        )
    )

    response = client.put(
        "/api/v1/admin/payment-settings",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "enabled": True,
            "gateway_url": "https://pay.example.com",
            "public_base_url": "https://studio.example.com",
            "merchant_id": "10001",
            "merchant_key": "merchant-secret",
            "methods": [{"payment_provider": "alipay", "display_name": "支付宝"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["merchant_key_configured"] is True
    assert "merchant-secret" not in response.text
    assert "merchant_key" not in response.json()
    assert client.get("/api/v1/payment-methods").json() == [
        {"payment_provider": "alipay", "display_name": "支付宝"}
    ]
    assert authorized == ["admin-session"]


def test_global_recharge_rate_has_dedicated_admin_and_public_apis() -> None:
    payments = InMemoryEpayPayments()
    authorized: list[str] = []
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            epay_payments=payments,
            admin_authorizer=authorized.append,
        )
    )

    updated = client.put(
        "/api/v1/admin/recharge-rate",
        headers={"Authorization": "Bearer admin-session"},
        json={"credits_per_cny": "2.5000"},
    )

    assert updated.status_code == 200
    assert updated.json()["credits_per_cny"] == "2.5000"
    assert updated.json()["preset_payment_cny"] == ["1.00", "2.00", "5.00", "10.00", "100.00"]
    assert client.get("/api/v1/recharge-rate").json()["credits_per_cny"] == "2.5000"
    assert authorized == ["admin-session"]


def test_direct_recharge_uses_global_rate_without_a_package_and_snapshots_the_quote() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("direct@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("direct@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    payments = InMemoryEpayPayments()
    payments.update(_settings())
    payments.update_recharge_rate("2.5000")
    client = TestClient(
        create_app(
            accounts,
            credit_accounting=credits,
            recharge_orders=orders,
            payment_methods=payments,
            epay_payments=payments,
            clock=lambda: now,
        )
    )

    created = client.post(
        "/api/v1/recharge-orders/direct",
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Idempotency-Key": "direct-order-1",
        },
        json={"payment_cny": "5.00", "payment_provider": "alipay"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["package_version_id"] is None
    assert body["package_code"] == "普通充值"
    assert body["payment_cny"] == "5.00"
    assert body["credits"] == "12.5000"
    assert body["checkout"]["parameters"]["money"] == "5.00"
    payments.update_recharge_rate("1.0000")
    notification = _signed(
        {
            "pid": "10001",
            "type": "alipay",
            "out_trade_no": body["order_id"],
            "trade_no": "epay-direct-1",
            "name": "普通充值",
            "money": "5.00",
            "trade_status": "TRADE_SUCCESS",
        }
    )

    assert client.post("/api/v1/payments/epay/notify", data=notification).text == "success"
    assert credits.statement(registration.account_space_id).available_credits == "12.5000"


def test_epay_checkout_and_signed_notification_credit_the_order_once() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="10.00", credits="12.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    payments = InMemoryEpayPayments()
    payments.update(_settings())
    client = TestClient(
        create_app(
            accounts,
            credit_accounting=credits,
            recharge_orders=orders,
            payment_methods=payments,
            epay_payments=payments,
            clock=lambda: now,
        )
    )

    created = client.post(
        "/api/v1/recharge-orders",
        headers={
            "Authorization": f"Bearer {session.access_token}",
            "Idempotency-Key": "epay-order-1",
        },
        json={"package_version_id": package.version_id, "payment_provider": "alipay"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["checkout"]["action_url"] == "https://pay.example.com/gateway/submit.php"
    assert body["checkout"]["parameters"]["money"] == "10.00"
    assert body["checkout"]["parameters"]["notify_url"].endswith("/api/v1/payments/epay/notify")
    notification = _signed(
        {
            "pid": "10001",
            "type": "alipay",
            "out_trade_no": body["order_id"],
            "trade_no": "epay-trade-1",
            "name": "starter 充值",
            "money": "10.00",
            "trade_status": "TRADE_SUCCESS",
        }
    )
    payments.update(_settings(enabled=False, merchant_key=""))

    first = client.post("/api/v1/payments/epay/notify", data=notification)
    replay = client.post("/api/v1/payments/epay/notify", data=notification)

    assert first.text == "success"
    assert replay.text == "success"
    assert orders.get(registration.account_space_id, body["order_id"]).status.value == "paid"
    assert credits.statement(registration.account_space_id).available_credits == "12.0000"


def test_epay_notification_rejects_a_bad_signature_without_crediting() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    payments = InMemoryEpayPayments()
    payments.update(_settings())
    client = TestClient(create_app(accounts, recharge_orders=orders, epay_payments=payments, clock=lambda: now))

    response = client.post(
        "/api/v1/payments/epay/notify",
        data={
            "pid": "10001",
            "type": "alipay",
            "out_trade_no": "missing-order",
            "trade_no": "epay-trade-bad",
            "money": "10.00",
            "trade_status": "TRADE_SUCCESS",
            "sign_type": "MD5",
            "sign": "0" * 32,
        },
    )

    assert response.text == "fail"
    assert credits.statement(registration.account_space_id).available_credits == "0.0000"

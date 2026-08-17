from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.payments import InMemoryPaymentMethods, PaymentMethod


def test_available_payment_methods_expose_only_checkout_display_information() -> None:
    methods = InMemoryPaymentMethods(
        (
            PaymentMethod(payment_provider="wechat", display_name="微信支付"),
            PaymentMethod(payment_provider="alipay", display_name="支付宝"),
        )
    )

    assert methods.available() == (
        PaymentMethod(payment_provider="wechat", display_name="微信支付"),
        PaymentMethod(payment_provider="alipay", display_name="支付宝"),
    )


def test_payment_method_catalog_is_public_and_contains_no_credentials() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            payment_methods=InMemoryPaymentMethods(
                (PaymentMethod(payment_provider="wechat", display_name="微信支付"),)
            ),
        )
    )

    response = client.get("/api/v1/payment-methods")

    assert response.status_code == 200
    assert response.json() == [{"payment_provider": "wechat", "display_name": "微信支付"}]

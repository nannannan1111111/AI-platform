from datetime import UTC, datetime

from app.http.application import (
    _AdminCreditGrant,
    _DirectRechargeOrderCreation,
    _ModelPricePublication,
    _PaymentChargebackNotification,
    _PaymentSuccessNotification,
    _ProviderCostRateReplacement,
    _RechargePackagePublication,
    _RechargeRateUpdate,
    _RedeemCodeCreate,
    _RunningHubUserPricePublication,
)


def test_decimal_request_fields_normalize_browser_numbers_to_strings() -> None:
    effective = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    assert _RechargePackagePublication(package_code="starter", payment_cny=10, credits=25, effective_from=effective).payment_cny == "10"
    assert _RedeemCodeCreate(credits=1.25).credits == "1.25"
    assert _ModelPricePublication(logical_model="gpt-image-2", output_spec="4k", credits_per_result=0.2, effective_from=effective).credits_per_result == "0.2"
    assert _ProviderCostRateReplacement(provider_currency="RMB", cost_per_image_yuan=0.07).cost_per_image_yuan == "0.07"
    assert _RunningHubUserPricePublication(credits_per_run=0.1, effective_from=effective).credits_per_run == "0.1"
    assert _DirectRechargeOrderCreation(payment_cny=20, payment_provider="epay").payment_cny == "20"
    assert _RechargeRateUpdate(credits_per_cny=2.5).credits_per_cny == "2.5"
    assert _AdminCreditGrant(credits=5, reason="test").credits == "5"
    assert _PaymentSuccessNotification(order_id="order-1", provider_event_id="event-1", paid_payment_cny=10, occurred_at=effective).paid_payment_cny == "10"
    assert _PaymentChargebackNotification(order_id="order-1", provider_event_id="event-2", charged_back_payment_cny=10, occurred_at=effective).charged_back_payment_cny == "10"

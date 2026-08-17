from datetime import UTC, datetime, timedelta

import pytest

from app.credits import InMemoryCredits
from app.orders import (
    InMemoryRechargeOrders,
    PaymentAmountMismatch,
    PaymentEventConflict,
    PaymentSuccess,
    RechargeOrderAlreadyExists,
    RechargeOrderNotFound,
    RechargeOrderPaymentAlreadyFinalized,
    RechargeOrderStatus,
    RechargeOrderSubmission,
)


def test_recharge_order_snapshots_the_server_package_and_is_idempotent() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    submission = RechargeOrderSubmission(
        user_id="user-1",
        account_space_id="account-space-1",
        package_version_id=package.version_id,
        payment_provider="fakepay",
        idempotency_key="order-key-1",
        created_at=now,
    )

    order = orders.create(submission)

    assert order.status is RechargeOrderStatus.PENDING
    assert order.package_version_id == package.version_id
    assert order.payment_cny == "1.00"
    assert order.credits == "1.0000"
    assert orders.create(submission) == order
    with pytest.raises(RechargeOrderAlreadyExists):
        orders.create(
            RechargeOrderSubmission(
                user_id="user-1",
                account_space_id="account-space-1",
                package_version_id="different-package",
                payment_provider="fakepay",
                idempotency_key="order-key-1",
                created_at=now,
            )
        )
    with pytest.raises(RechargeOrderNotFound):
        orders.get("another-account-space", order.order_id)


def test_recharge_orders_list_is_account_isolated_and_newest_first() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1", "account-space-2"},
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)

    first = orders.create(
        RechargeOrderSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-1",
            created_at=now,
        )
    )
    orders.create(
        RechargeOrderSubmission(
            user_id="user-2",
            account_space_id="account-space-2",
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="other-order-key",
            created_at=now + timedelta(minutes=1),
        )
    )
    newest = orders.create(
        RechargeOrderSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-2",
            created_at=now + timedelta(minutes=2),
        )
    )

    assert orders.list("account-space-1") == (newest, first)


def test_payment_success_is_amount_checked_idempotent_and_posts_credits_once() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    order = orders.create(
        RechargeOrderSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
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
            provider_event_id="event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )

    assert paid.status is RechargeOrderStatus.PAID
    assert paid.payment_reference == "payment:fakepay:event-1"
    assert (
        orders.record_payment_success(
            PaymentSuccess(
                order_id=order.order_id,
                payment_provider="fakepay",
                provider_event_id="event-1",
                paid_payment_cny="1.00",
                occurred_at=now,
            )
        )
        == paid
    )
    assert credits.statement("account-space-1").available_credits == "1.0000"

    with pytest.raises(RechargeOrderPaymentAlreadyFinalized):
        orders.record_payment_success(
            PaymentSuccess(
                order_id=order.order_id,
                payment_provider="fakepay",
                provider_event_id="event-2",
                paid_payment_cny="1.00",
                occurred_at=now,
            )
        )


def test_payment_success_rejects_wrong_amount_and_reused_provider_event_for_another_order() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = InMemoryRechargeOrders(credits, credits, clock=lambda: now)
    first = orders.create(
        RechargeOrderSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-1",
            created_at=now,
        )
    )
    second = orders.create(
        RechargeOrderSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="order-key-2",
            created_at=now,
        )
    )

    with pytest.raises(PaymentAmountMismatch):
        orders.record_payment_success(
            PaymentSuccess(
                order_id=first.order_id,
                payment_provider="fakepay",
                provider_event_id="event-1",
                paid_payment_cny="9.99",
                occurred_at=now,
            )
        )
    orders.record_payment_success(
        PaymentSuccess(
            order_id=first.order_id,
            payment_provider="fakepay",
            provider_event_id="event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    with pytest.raises(PaymentEventConflict):
        orders.record_payment_success(
            PaymentSuccess(
                order_id=second.order_id,
                payment_provider="fakepay",
                provider_event_id="event-1",
                paid_payment_cny="1.00",
                occurred_at=now,
            )
        )

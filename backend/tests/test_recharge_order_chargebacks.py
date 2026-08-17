from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.credits import InMemoryCredits, InsufficientCredits, SqlAlchemyCredits
from app.orders import (
    InMemoryRechargeOrders,
    PaymentChargeback,
    PaymentEventConflict,
    PaymentSuccess,
    RechargeOrderStatus,
    RechargeOrderSubmission,
    SqlAlchemyRechargeOrders,
)


def test_chargeback_reverses_paid_credits_even_when_the_account_becomes_negative() -> None:
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
            provider_event_id="payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    freeze = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=6,
        task_reference="task-1",
        occurred_at=now,
    )
    credits.settle(
        freeze.freeze_id,
        delivered_quantity=6,
        settlement_reference="settlement-1",
        occurred_at=now,
    )

    charged_back = orders.record_chargeback(
        PaymentChargeback(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="chargeback-event-1",
            charged_back_payment_cny="1.00",
            occurred_at=now,
        )
    )

    assert charged_back.status is RechargeOrderStatus.CHARGED_BACK
    assert charged_back.recharge_posting_id == paid.recharge_posting_id
    assert charged_back.chargeback_reference == "chargeback:fakepay:chargeback-event-1"
    assert credits.statement("account-space-1").available_credits == "-0.9000"
    with pytest.raises(InsufficientCredits):
        credits.freeze(
            "account-space-1",
            "gpt-image-2",
            "4k",
            quantity=1,
            task_reference="task-2",
            occurred_at=now,
        )


def test_replaying_the_same_chargeback_event_does_not_reverse_credits_twice() -> None:
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
    orders.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    event = PaymentChargeback(
        order_id=order.order_id,
        payment_provider="fakepay",
        provider_event_id="chargeback-event-1",
        charged_back_payment_cny="1.00",
        occurred_at=now,
    )

    charged_back = orders.record_chargeback(event)

    assert orders.record_chargeback(event) == charged_back
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries) == ("recharge", "reversal")


def test_sqlalchemy_chargeback_survives_restart_and_replays_without_a_second_reversal(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'chargebacks.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    orders = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=credits,
        credit_accounting=credits,
        clock=lambda: now,
    )
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
    event = PaymentChargeback(
        order_id=order.order_id,
        payment_provider="fakepay",
        provider_event_id="chargeback-event-1",
        charged_back_payment_cny="1.00",
        occurred_at=now,
    )

    restarted = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=SqlAlchemyCredits.for_database_url(database_url),
        credit_accounting=SqlAlchemyCredits.for_database_url(database_url),
    )
    charged_back = restarted.record_chargeback(event)
    replayed = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=SqlAlchemyCredits.for_database_url(database_url),
        credit_accounting=SqlAlchemyCredits.for_database_url(database_url),
    ).record_chargeback(event)

    with pytest.raises(PaymentEventConflict):
        restarted.record_chargeback(
            PaymentChargeback(
                order_id=order.order_id,
                payment_provider="fakepay",
                provider_event_id="chargeback-event-1",
                charged_back_payment_cny="9.99",
                occurred_at=now,
            )
        )

    assert paid.recharge_posting_id
    assert charged_back.status is RechargeOrderStatus.CHARGED_BACK
    assert replayed == charged_back
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)
    assert statement.available_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries) == ("recharge", "reversal")


def test_chargeback_event_id_cannot_be_replayed_with_a_different_amount() -> None:
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
    orders.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    orders.record_chargeback(
        PaymentChargeback(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="chargeback-event-1",
            charged_back_payment_cny="1.00",
            occurred_at=now,
        )
    )

    with pytest.raises(PaymentEventConflict):
        orders.record_chargeback(
            PaymentChargeback(
                order_id=order.order_id,
                payment_provider="fakepay",
                provider_event_id="chargeback-event-1",
                charged_back_payment_cny="9.99",
                occurred_at=now,
            )
        )

    assert tuple(entry.kind for entry in credits.statement("account-space-1").entries) == (
        "recharge",
        "reversal",
    )

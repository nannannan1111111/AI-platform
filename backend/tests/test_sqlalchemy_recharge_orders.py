from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.credits import SqlAlchemyCredits
from app.orders import PaymentSuccess, RechargeOrderStatus, RechargeOrderSubmission, SqlAlchemyRechargeOrders


def test_sqlalchemy_recharge_order_and_payment_survive_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'recharge-orders.db').as_posix()}"
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

    restarted = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        credit_accounting=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        clock=lambda: now,
    )
    assert restarted.get(registration.account_space_id, order.order_id) == order
    assert restarted.list(registration.account_space_id) == (order,)
    paid = restarted.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )

    assert paid.status is RechargeOrderStatus.PAID
    assert (
        restarted.record_payment_success(
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
    assert (
        SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id).available_credits
        == "1.0000"
    )

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.credits import SqlAlchemyCredits


def test_sqlalchemy_generation_freeze_can_be_partially_settled_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-credits.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime.now(UTC) + timedelta(minutes=1)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    freeze = credits.freeze(
        registration.account_space_id,
        "gpt-image-2",
        "4k",
        quantity=4,
        task_reference="task-1",
        occurred_at=now,
    )

    restarted = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    freeze_replay = restarted.freeze(
        registration.account_space_id,
        "gpt-image-2",
        "4k",
        quantity=4,
        task_reference="task-1",
        occurred_at=now,
    )
    settlement = restarted.settle(
        freeze.freeze_id,
        delivered_quantity=2,
        settlement_reference="settlement-1",
        occurred_at=now,
    )
    statement = restarted.statement(registration.account_space_id)

    assert settlement.delta_available_credits == "0.3000"
    assert freeze_replay == freeze
    assert settlement.delta_frozen_credits == "-0.6000"
    assert statement.available_credits == "0.7000"
    assert statement.frozen_credits == "0.0000"
    assert statement.entries[-2].kind == "freeze"
    assert statement.entries[-1].kind == "settlement"


def test_sqlalchemy_generation_release_survives_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-release.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime.now(UTC) + timedelta(minutes=1)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    freeze = credits.freeze(
        registration.account_space_id,
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="task-1",
        occurred_at=now,
    )

    release = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now).release(
        freeze.freeze_id,
        release_reference="release-1",
        reason="provider failed",
        occurred_at=now,
    )
    statement = SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id)

    assert release.delta_available_credits == "0.3000"
    assert release.delta_frozen_credits == "-0.3000"
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"
    assert statement.entries[-1] == release

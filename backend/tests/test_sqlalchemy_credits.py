from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.credits import SqlAlchemyCredits


def test_sqlalchemy_credits_preserve_packages_postings_and_balance_across_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'credits.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    posting = credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    restarted = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    replay = restarted.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    assert restarted.get_version(package.version_id) == package
    assert restarted.sellable_at(now) == (package,)
    assert replay == posting
    assert restarted.statement(registration.account_space_id).entries == (posting,)
    assert restarted.statement(registration.account_space_id).available_credits == "100.0000"
    session = SqlAlchemyAccountAccess.for_database_url(database_url).login(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    assert (
        SqlAlchemyAccountAccess.for_database_url(database_url).credit_balance(session.access_token).available_credits
        == "100.0000"
    )


def test_sqlalchemy_admin_credit_grant_is_auditable_and_idempotent_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'admin-credit-grant.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)

    posting = credits.record_admin_grant(
        registration.account_space_id,
        "25.0000",
        grant_reference="admin-grant:admin-1:ticket-1",
        reason="活动补发",
        occurred_at=now,
    )
    replay = SqlAlchemyCredits.for_database_url(database_url).record_admin_grant(
        registration.account_space_id,
        "25.0000",
        grant_reference="admin-grant:admin-1:ticket-1",
        reason="活动补发",
        occurred_at=now,
    )
    second = credits.record_admin_grant(
        registration.account_space_id,
        "5.0000",
        grant_reference="admin-grant:admin-1:ticket-2",
        reason="再次补发",
        occurred_at=now,
    )
    first_page = credits.statement_page(registration.account_space_id, page=1, page_size=1)
    second_page = credits.statement_page(registration.account_space_id, page=2, page_size=1)

    assert replay == posting
    assert posting.kind == "admin_grant"
    assert posting.package_version_id is None
    assert posting.reason == "活动补发"
    assert first_page.total_entries == 2
    assert first_page.total_pages == 2
    assert first_page.entries == (second,)
    assert second_page.entries == (posting,)
    assert (
        SqlAlchemyCredits.for_database_url(database_url).statement(registration.account_space_id).available_credits
        == "30.0000"
    )


def test_sqlalchemy_credit_reversal_remains_auditable_after_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'reversal.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "a-correct-horse-battery-staple",
    )
    now = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    recharge = credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    reversal = credits.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )

    restarted = SqlAlchemyCredits.for_database_url(database_url)
    replay = restarted.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )
    statement = restarted.statement(registration.account_space_id)

    assert replay == reversal
    assert statement.available_credits == "0.0000"
    assert statement.entries == (recharge, reversal)

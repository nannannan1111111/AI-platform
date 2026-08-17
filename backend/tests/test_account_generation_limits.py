from pathlib import Path

from alembic.config import Config

from alembic import command
from app.account_generation_limits import SqlAlchemyAccountGenerationLimits
from app.accounts import SqlAlchemyAccountAccess


def test_sqlalchemy_account_generation_limit_defaults_to_two_and_survives_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'account-generation-limits.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    account = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com",
        "another-correct-horse-battery-staple",
    )
    limits = SqlAlchemyAccountGenerationLimits.for_database_url(database_url)

    assert limits.current(account.account_space_id).execution_concurrency == 2
    assert limits.update(account.account_space_id, 8).execution_concurrency == 8

    restarted = SqlAlchemyAccountGenerationLimits.for_database_url(database_url)
    assert restarted.current(account.account_space_id).execution_concurrency == 8

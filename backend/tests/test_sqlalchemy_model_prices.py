from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.credits import SqlAlchemyModelPrices


def test_sqlalchemy_model_prices_seed_and_versions_survive_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'model-prices.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    prices = SqlAlchemyModelPrices.for_database_url(database_url, clock=lambda: now)

    initial = prices.effective_at("gpt-image-2", "4k", now)
    replacement = prices.publish(
        "gpt-image-2",
        "4k",
        credits_per_result="0.2000",
        effective_from=now + timedelta(days=1),
    )
    restarted = SqlAlchemyModelPrices.for_database_url(database_url, clock=lambda: now)

    assert initial.credits_per_result == "0.1500"
    assert initial.max_reference_images == 3
    assert restarted.effective_at("gpt-image-2", "4k", now) == initial
    assert restarted.effective_at("gpt-image-2", "4k", now + timedelta(days=1)) == replacement
    assert restarted.get_version(initial.version_id) == initial


def test_sqlalchemy_model_price_catalog_returns_current_versions(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'model-price-catalog.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    prices = SqlAlchemyModelPrices.for_database_url(database_url, clock=lambda: now)
    prices.publish(
        "gpt-image-2", "2k", credits_per_result="0.1000", effective_from=now, max_reference_images=7
    )

    catalog = prices.catalog_at(now)

    assert [(version.logical_model, version.output_spec, version.credits_per_result) for version in catalog] == [
        ("gpt-image-2", "2k", "0.1000"),
        ("gpt-image-2", "4k", "0.1500"),
    ]
    assert prices.effective_at("gpt-image-2", "2k", now).max_reference_images == 7

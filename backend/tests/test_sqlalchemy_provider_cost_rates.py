from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.model_routing import (
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProviderCreation,
    ProviderProtocol,
    SqlAlchemyModelRouting,
)
from app.provider_costs import SqlAlchemyProviderCostRates


def test_provider_cost_versions_survive_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'provider-cost-rates.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-source-a",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    rates = SqlAlchemyProviderCostRates.for_database_url(
        database_url,
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )

    first = rates.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    replacement = rates.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=150_000,
        effective_from=now + timedelta(days=1),
    )
    restarted = SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: now)

    assert restarted.effective_at(route.route_id, "4k", now) == first
    assert restarted.effective_at(route.route_id, "4k", now + timedelta(days=1)) == replacement
    assert restarted.versions(route.route_id, "4k") == (first, replacement)


def test_route_current_cost_replacement_survives_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'current-provider-cost.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    later = now + timedelta(seconds=1)
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-current", "route-current")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-current",
            display_name="来源 Current",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-current.example.com/v1",
            api_key="test-source-current",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    rates = SqlAlchemyProviderCostRates.for_database_url(
        database_url,
        id_factory=iter(("cost-current-1", "cost-current-2")).__next__,
        clock=iter((now, later)).__next__,
    )

    first = rates.replace(route.route_id, provider_currency="USD", cost_per_image_cents=12)
    second = rates.replace(route.route_id, provider_currency="USD", cost_per_image_cents=15)
    restarted = SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: later)

    assert restarted.current_at(route.route_id, later) == second
    assert restarted.versions_for_route(route.route_id) == (first, second)

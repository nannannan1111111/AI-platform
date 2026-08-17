from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.model_routing import (
    InMemoryProviderSecrets,
    ModelAvailability,
    ModelAvailabilityStatus,
    ModelRouteCreation,
    ModelRouteUpdate,
    ProbeResult,
    ProviderCreation,
    ProviderHasRoutes,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthNotFound,
    RouteHealthStatus,
    RouteProbeTarget,
    RoutingMode,
    RoutingPolicyUpdate,
    SqlAlchemyModelRouting,
)


class SuccessfulProbe:
    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        return ProbeResult(status=RouteHealthStatus.HEALTHY, total_latency_ms=230)


def test_model_routing_survives_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'model-routing.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    secrets = InMemoryProviderSecrets()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        secrets,
        id_factory=iter(("provider-1", "route-1", "route-2")).__next__,
        clock=lambda: now,
    )

    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1/",
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
            max_reference_images=7,
        )
    )

    restarted = SqlAlchemyModelRouting.for_database_url(database_url, secrets)

    assert restarted.list_providers() == (provider,)
    assert restarted.list_routes("gpt-image-2", "4k") == (route,)
    assert restarted.reference_image_limit("gpt-image-2", "4k") == 7
    restarted.update_route(ModelRouteUpdate(route.route_id, max_reference_images=9))
    assert SqlAlchemyModelRouting.for_database_url(database_url, secrets).reference_image_limit("gpt-image-2", "4k") == 9


def test_route_health_survives_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'route-health.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    secrets = InMemoryProviderSecrets()
    now = datetime(2026, 8, 8, tzinfo=UTC)
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        secrets,
        probe=SuccessfulProbe(),
        id_factory=iter(("provider-1", "route-1", "route-2")).__next__,
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

    health = routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))
    policy = routing.set_policy(RoutingPolicyUpdate("gpt-image-2", "4k", RoutingMode.PREFERRED, route.route_id))
    restarted = SqlAlchemyModelRouting.for_database_url(database_url, secrets, clock=lambda: now)

    assert restarted.route_health(route.route_id) == health
    assert restarted.list_routes()[0].health_status is RouteHealthStatus.HEALTHY
    assert restarted.routing_policy("gpt-image-2", "4k") == policy
    assert restarted.availability("gpt-image-2", "4k") == ModelAvailability(
        logical_model="gpt-image-2",
        output_spec="4k",
        status=ModelAvailabilityStatus.AVAILABLE,
    )
    assert restarted.availability("gpt-image-2", "2k") == ModelAvailability(
        logical_model="gpt-image-2",
        output_spec="2k",
        status=ModelAvailabilityStatus.MAINTENANCE,
    )
    assert restarted.select("gpt-image-2", "4k").route_id == route.route_id

    restarted.update_provider(ProviderUpdate(provider.provider_id, base_url="https://source-b.example.com/v1"))
    invalidated = SqlAlchemyModelRouting.for_database_url(database_url, secrets).list_routes()[0]

    assert invalidated.enabled is False
    assert invalidated.health_status is RouteHealthStatus.UNKNOWN
    with pytest.raises(RouteHealthNotFound):
        restarted.route_health(route.route_id)

    edited_route = restarted.update_route(
        ModelRouteUpdate(
            route.route_id,
            provider_model_name="gpt-image-2-2026",
            compatibility_group="gpt-image-2/4k/v2",
            priority=20,
        )
    )
    persisted_route = SqlAlchemyModelRouting.for_database_url(database_url, secrets).list_routes()[0]

    assert edited_route.provider_model_name == "gpt-image-2-2026"
    assert persisted_route == edited_route

    with pytest.raises(ProviderHasRoutes):
        restarted.delete_provider(provider.provider_id)

    restarted.delete_route(route.route_id)
    restarted.delete_route(route.route_id)
    after_deletion = SqlAlchemyModelRouting.for_database_url(database_url, secrets, clock=lambda: now)

    assert after_deletion.list_routes() == ()
    assert after_deletion.routing_policy("gpt-image-2", "4k").mode is RoutingMode.AUTOMATIC
    with pytest.raises(RouteHealthNotFound):
        after_deletion.route_health(route.route_id)

    replacement = restarted.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-2026",
            compatibility_group="gpt-image-2/4k/v2",
            priority=30,
        )
    )
    assert replacement.route_id != route.route_id
    assert restarted.list_routes() == (replacement,)
    restarted.delete_route(replacement.route_id)

    restarted.delete_provider(provider.provider_id)
    restarted.delete_provider(provider.provider_id)

    assert SqlAlchemyModelRouting.for_database_url(database_url, secrets).list_providers() == ()

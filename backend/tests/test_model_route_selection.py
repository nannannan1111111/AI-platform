from datetime import UTC, datetime, timedelta

import pytest

from app.model_routing import (
    ApiProviderNotFound,
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelAvailability,
    ModelAvailabilityStatus,
    ModelRouteCreation,
    ModelRouteUpdate,
    NoAvailableModelRoute,
    ProbeResult,
    ProviderCreation,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthNotFound,
    RouteHealthScheduler,
    RouteHealthStatus,
    RouteProbeTarget,
    RoutingMode,
    RoutingPolicyUpdate,
)


class ProbeBySource:
    def __init__(self) -> None:
        self.results = {
            "source-a.example.com": ProbeResult(RouteHealthStatus.HEALTHY, 420),
            "source-b.example.com": ProbeResult(RouteHealthStatus.HEALTHY, 120),
        }

    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        host = target.base_url.split("//", 1)[1].split("/", 1)[0]
        return self.results[host]


class RecordingProbe(ProbeBySource):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[RouteProbeTarget] = []

    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        self.calls.append(target)
        return super().probe(target)


def test_updating_provider_connection_disables_routes_and_invalidates_health() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=ProbeBySource(),
        id_factory=iter(("provider-a", "route-a")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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
    routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))

    updated = routing.update_provider(ProviderUpdate(provider.provider_id, base_url="https://source-b.example.com/v1"))

    assert updated.base_url == "https://source-b.example.com/v1"
    invalidated = routing.list_routes()[0]
    assert invalidated.enabled is False
    assert invalidated.health_status is RouteHealthStatus.UNKNOWN
    with pytest.raises(RouteHealthNotFound):
        routing.route_health(route.route_id)


def test_updating_a_disabled_route_mapping_invalidates_its_health() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=ProbeBySource(),
        id_factory=iter(("provider-a", "route-a")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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
    routing.check_route(route.route_id)

    updated = routing.update_route(
        ModelRouteUpdate(
            route.route_id,
            provider_model_name="gpt-image-2-2026",
            compatibility_group="gpt-image-2/4k/v2",
            priority=20,
        )
    )

    assert updated.provider_model_name == "gpt-image-2-2026"
    assert updated.compatibility_group == "gpt-image-2/4k/v2"
    assert updated.priority == 20
    assert updated.health_status is RouteHealthStatus.UNKNOWN
    with pytest.raises(RouteHealthNotFound):
        routing.route_health(route.route_id)


def test_deleting_a_route_hides_it_and_resets_its_preferred_policy() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=ProbeBySource(),
        id_factory=iter(("provider-a", "route-a", "route-replacement")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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
    routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))
    routing.set_policy(RoutingPolicyUpdate("gpt-image-2", "4k", RoutingMode.PREFERRED, route.route_id))

    routing.delete_route(route.route_id)
    routing.delete_route(route.route_id)

    assert routing.list_routes() == ()
    assert routing.routing_policy("gpt-image-2", "4k").mode is RoutingMode.AUTOMATIC
    with pytest.raises(RouteHealthNotFound):
        routing.route_health(route.route_id)
    with pytest.raises(NoAvailableModelRoute):
        routing.select("gpt-image-2", "4k")

    replacement = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    assert replacement.route_id != route.route_id
    assert routing.list_routes() == (replacement,)


def test_deleting_a_provider_requires_deleting_its_routes_first() -> None:
    now = datetime(2026, 8, 11, tzinfo=UTC)
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-a", "route-a")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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

    with pytest.raises(ValueError, match="必须先删除该来源的全部模型路由"):
        routing.delete_provider(provider.provider_id)

    routing.delete_route(route.route_id)
    routing.delete_provider(provider.provider_id)
    routing.delete_provider(provider.provider_id)

    assert routing.list_providers() == ()
    with pytest.raises(ApiProviderNotFound):
        routing.create_route(
            ModelRouteCreation(
                provider_id=provider.provider_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                provider_model_name="replacement",
                compatibility_group="gpt-image-2/4k/v1",
            )
        )


def test_automatic_routing_prefers_low_latency_and_admin_preference_safely_falls_back() -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    probe = ProbeBySource()
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=probe,
        id_factory=iter(("provider-a", "provider-b", "route-a", "route-b")).__next__,
        clock=lambda: now,
    )
    providers = [
        routing.create_provider(
            ProviderCreation(
                code=f"source-{suffix}",
                display_name=f"来源 {suffix.upper()}",
                protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
                base_url=f"https://source-{suffix}.example.com/v1",
                api_key=f"test-{suffix}",
            )
        )
        for suffix in ("a", "b")
    ]
    routes = [
        routing.create_route(
            ModelRouteCreation(
                provider_id=provider.provider_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                provider_model_name="gpt-image-2",
                compatibility_group="gpt-image-2/4k/v1",
            )
        )
        for provider in providers
    ]
    for provider, route in zip(providers, routes, strict=True):
        routing.check_route(route.route_id)
        routing.update_provider(ProviderUpdate(provider_id=provider.provider_id, enabled=True))
        routing.update_route(ModelRouteUpdate(route_id=route.route_id, enabled=True))

    automatic = routing.select("gpt-image-2", "4k")

    assert automatic.route_id == "route-b"
    assert automatic.selection_reason == "automatic"
    assert routing.availability("gpt-image-2", "4k") == ModelAvailability(
        logical_model="gpt-image-2",
        output_spec="4k",
        status=ModelAvailabilityStatus.AVAILABLE,
    )
    assert routing.availability("gpt-image-2", "2k") == ModelAvailability(
        logical_model="gpt-image-2",
        output_spec="2k",
        status=ModelAvailabilityStatus.MAINTENANCE,
    )

    routing.set_policy(
        RoutingPolicyUpdate(
            logical_model="gpt-image-2",
            output_spec="4k",
            mode=RoutingMode.PREFERRED,
            preferred_route_id="route-a",
        )
    )
    preferred = routing.select("gpt-image-2", "4k")
    assert preferred.route_id == "route-a"
    assert preferred.selection_reason == "preferred"

    probe.results["source-a.example.com"] = ProbeResult(
        RouteHealthStatus.UNHEALTHY,
        700,
        "upstream_unavailable",
    )
    routing.check_route("route-a")

    fallback = routing.select("gpt-image-2", "4k")
    assert fallback.route_id == "route-b"
    assert fallback.selection_reason == "preferred_fallback"


def test_completed_health_status_does_not_expire_between_daily_checks() -> None:
    current_time = [datetime(2026, 8, 10, tzinfo=UTC)]
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=ProbeBySource(),
        id_factory=iter(("provider-a", "route-a")).__next__,
        clock=lambda: current_time[0],
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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
    routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))

    current_time[0] += timedelta(days=30)

    assert routing.availability("gpt-image-2", "4k").status is ModelAvailabilityStatus.AVAILABLE
    assert routing.select("gpt-image-2", "4k").route_id == route.route_id


def test_route_health_scheduler_changes_status_only_after_each_completed_daily_check() -> None:
    current_time = [datetime(2026, 8, 10, 10, 0, tzinfo=UTC)]
    probe = RecordingProbe()
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=probe,
        id_factory=iter(("provider-a", "route-a")).__next__,
        clock=lambda: current_time[0],
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
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
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    scheduler = RouteHealthScheduler(routing, clock=lambda: current_time[0])

    assert scheduler.run_due()[0].status is RouteHealthStatus.HEALTHY
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))
    probe.results["source-a.example.com"] = ProbeResult(
        RouteHealthStatus.UNHEALTHY,
        900,
        "upstream_unavailable",
    )
    current_time[0] += timedelta(hours=23, minutes=59)

    assert scheduler.run_due() == ()
    assert routing.availability("gpt-image-2", "4k").status is ModelAvailabilityStatus.AVAILABLE

    current_time[0] += timedelta(minutes=1)

    assert scheduler.run_due()[0].status is RouteHealthStatus.UNHEALTHY
    assert routing.availability("gpt-image-2", "4k").status is ModelAvailabilityStatus.MAINTENANCE
    assert routing.list_routes()[0].enabled is True
    assert len(probe.calls) == 2

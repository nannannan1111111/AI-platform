from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.model_routing import (
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ProbeResult,
    ProviderProtocol,
    RouteHealthStatus,
    RouteProbeTarget,
    RoutingMode,
)


class SuccessfulProbe:
    def __init__(self) -> None:
        self.targets: list[RouteProbeTarget] = []

    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        self.targets.append(target)
        return ProbeResult(status=RouteHealthStatus.HEALTHY, total_latency_ms=184)


def test_admin_health_check_records_availability_and_latency_without_exposing_credentials() -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    probe = SuccessfulProbe()
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=probe,
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: now,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=routing,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    provider = client.post(
        "/api/v1/admin/providers",
        headers=headers,
        json={
            "code": "source-a",
            "display_name": "来源 A",
            "protocol": ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            "base_url": "https://source-a.example.com/v1",
            "api_key": "test-source-a",
        },
    ).json()
    route = client.post(
        "/api/v1/admin/image-model-routes",
        headers=headers,
        json={
            "provider_id": provider["provider_id"],
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "provider_model_name": "gpt-image-2",
            "compatibility_group": "gpt-image-2/4k/v1",
        },
    ).json()

    checked = client.post(
        f"/api/v1/admin/image-model-routes/{route['route_id']}/health-check",
        headers=headers,
    )

    assert checked.status_code == 200
    assert checked.json() == {
        "route_id": "route-1",
        "status": "healthy",
        "available": True,
        "total_latency_ms": 184,
        "ewma_latency_ms": 184,
        "p95_latency_ms": 184,
        "success_rate": 1.0,
        "sample_count": 1,
        "checked_at": "2026-08-08T00:00:00Z",
        "error_code": "",
    }
    assert "api_key" not in checked.text
    assert "secret_ref" not in checked.text
    assert probe.targets[0].base_url == "https://source-a.example.com/v1"
    assert probe.targets[0].provider_model_name == "gpt-image-2"

    snapshot = client.get(
        f"/api/v1/admin/image-model-routes/{route['route_id']}/health",
        headers=headers,
    )
    assert snapshot.status_code == 200
    assert snapshot.json() == checked.json()

    listed_route = client.get("/api/v1/admin/image-model-routes", headers=headers).json()[0]
    assert listed_route["health_status"] == "healthy"


def test_admin_enables_checked_route_and_sets_preferred_routing_policy() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=SuccessfulProbe(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=routing,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    provider = client.post(
        "/api/v1/admin/providers",
        headers=headers,
        json={
            "code": "source-a",
            "display_name": "来源 A",
            "protocol": "openai_compatible_images",
            "base_url": "https://source-a.example.com/v1",
            "api_key": "test-source-a",
        },
    ).json()
    route = client.post(
        "/api/v1/admin/image-model-routes",
        headers=headers,
        json={
            "provider_id": provider["provider_id"],
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "provider_model_name": "gpt-image-2",
            "compatibility_group": "gpt-image-2/4k/v1",
        },
    ).json()
    client.post(f"/api/v1/admin/image-model-routes/{route['route_id']}/health-check", headers=headers)

    enabled_provider = client.patch(
        f"/api/v1/admin/providers/{provider['provider_id']}",
        headers=headers,
        json={"enabled": True},
    )
    enabled_route = client.patch(
        f"/api/v1/admin/image-model-routes/{route['route_id']}",
        headers=headers,
        json={"enabled": True, "priority": 10},
    )
    policy = client.put(
        "/api/v1/admin/image-models/gpt-image-2/4k/routing-policy",
        headers=headers,
        json={"mode": RoutingMode.PREFERRED, "preferred_route_id": route["route_id"]},
    )

    assert enabled_provider.status_code == 200
    assert enabled_provider.json()["enabled"] is True
    assert enabled_route.status_code == 200
    assert enabled_route.json()["enabled"] is True
    assert enabled_route.json()["priority"] == 10
    assert policy.status_code == 200
    assert policy.json()["mode"] == "preferred"
    assert (
        client.get(
            "/api/v1/admin/image-models/gpt-image-2/4k/routing-policy",
            headers=headers,
        ).json()
        == policy.json()
    )

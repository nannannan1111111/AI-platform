from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.credits import InMemoryModelPrices
from app.http import create_app
from app.model_routing import (
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ModelRouteUpdate,
    ProbeResult,
    ProviderCreation,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthStatus,
    RouteProbeTarget,
)


class HealthyProbe:
    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        return ProbeResult(RouteHealthStatus.HEALTHY, 125)


def test_model_price_catalog_route_returns_current_user_visible_prices() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    second = prices.publish(
        "gpt-image-2",
        "2k",
        credits_per_result="0.1000",
        effective_from=now,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            model_prices=prices,
            clock=lambda: now,
        )
    )

    response = client.get("/api/v1/model-prices")

    assert response.status_code == 200
    assert [(item["logical_model"], item["output_spec"], item["credits_per_result"]) for item in response.json()] == [
        ("gpt-image-2", "2k", "0.1000"),
        ("gpt-image-2", "4k", "0.1500"),
    ]
    assert response.json()[0]["version_id"] == second.version_id


def test_image_model_catalog_combines_current_prices_with_safe_route_availability() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    prices.publish(
        "gpt-image-2",
        "2k",
        credits_per_result="0.1000",
        effective_from=now,
    )
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=HealthyProbe(),
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
            max_reference_images=6,
        )
    )
    routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            model_prices=prices,
            model_routing=routing,
            clock=lambda: now,
        )
    )

    response = client.get("/api/v1/image-models")

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "logical_model": "gpt-image-2",
                "output_specs": [
                    {
                        "output_spec": "2k",
                        "credits_per_result": "0.1000",
                        "max_reference_images": 3,
                        "status": "maintenance",
                    },
                    {
                        "output_spec": "4k",
                        "credits_per_result": "0.1500",
                        "max_reference_images": 6,
                        "status": "available",
                    },
                ],
            }
        ]
    }

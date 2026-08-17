from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.provider_costs import (
    InMemoryProviderCostRates,
    ProviderCostSummary,
    SqlAlchemyProviderCostRates,
)


class _ProviderCostSummaries:
    def summarize(self) -> tuple[ProviderCostSummary, ...]:
        return (
            ProviderCostSummary(
                provider_id="provider-1",
                provider_display_name="来源 A",
                logical_model="gpt-image-2",
                provider_currency="USD",
                submitted_attempts=2,
                submitted_images=3,
                total_cost_cents=36,
            ),
        )


def test_admin_reads_submitted_attempt_provider_cost_estimates() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            provider_cost_summaries=_ProviderCostSummaries(),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.get(
        "/api/v1/admin/provider-cost-summary",
        headers={"Authorization": "Bearer admin-session"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "provider_id": "provider-1",
            "provider_display_name": "来源 A",
            "logical_model": "gpt-image-2",
            "provider_currency": "USD",
            "submitted_attempts": 2,
            "submitted_images": 3,
            "total_cost_cents": 36,
        }
    ]


def test_admin_replaces_one_current_provider_cost_per_route_in_yuan() -> None:
    now = datetime(2026, 8, 11, 13, 0, tzinfo=UTC)
    rates = InMemoryProviderCostRates(
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            provider_cost_rates=rates,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}

    first = client.put(
        "/api/v1/admin/provider-cost-rates/route-1",
        headers=headers,
        json={"provider_currency": "rmb", "cost_per_image_yuan": "0.12"},
    )
    replacement = client.put(
        "/api/v1/admin/provider-cost-rates/route-1",
        headers=headers,
        json={"provider_currency": "RMB", "cost_per_image_yuan": "0.15"},
    )
    history = client.get(
        "/api/v1/admin/provider-cost-rates?route_id=route-1",
        headers=headers,
    )

    assert first.status_code == 200
    assert first.json() == {
        "version_id": "cost-rate-1",
        "route_id": "route-1",
        "version": 1,
        "provider_currency": "RMB",
        "cost_per_image_cents": 12,
        "cost_per_image_yuan": "0.12",
        "effective_from": "2026-08-11T13:00:00Z",
        "published_at": "2026-08-11T13:00:00Z",
    }
    assert replacement.status_code == 200
    assert replacement.json()["version"] == 2
    assert replacement.json()["cost_per_image_cents"] == 15
    assert replacement.json()["cost_per_image_yuan"] == "0.15"
    assert history.status_code == 200
    assert [version["version"] for version in history.json()] == [1, 2]
    assert [version["cost_per_image_cents"] for version in history.json()] == [12, 15]


def test_provider_cost_replacement_rejects_more_than_two_yuan_decimal_places() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            provider_cost_rates=InMemoryProviderCostRates(),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.put(
        "/api/v1/admin/provider-cost-rates/route-1",
        headers={"Authorization": "Bearer admin-session"},
        json={"provider_currency": "RMB", "cost_per_image_yuan": "0.001"},
    )

    assert response.status_code == 422


def test_admin_publishes_and_reads_immutable_provider_cost_history() -> None:
    now = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    authorized_tokens: list[str] = []
    rates = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            provider_cost_rates=rates,
            admin_authorizer=authorized_tokens.append,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}

    created = client.post(
        "/api/v1/admin/provider-cost-rates",
        headers=headers,
        json={
            "route_id": "route-1",
            "variant_code": "4k",
            "provider_currency": "usd",
            "cost_per_image_micros": 120_000,
            "effective_from": "2026-08-09T01:01:00Z",
        },
    )

    assert created.status_code == 201
    assert created.json() == {
        "version_id": "cost-rate-1",
        "route_id": "route-1",
        "variant_code": "4k",
        "version": 1,
        "provider_currency": "USD",
        "cost_per_image_micros": 120_000,
        "effective_from": "2026-08-09T01:01:00Z",
        "published_at": "2026-08-09T01:00:00Z",
    }

    listed = client.get(
        "/api/v1/admin/provider-cost-rates?route_id=route-1&variant_code=4k",
        headers=headers,
    )

    assert listed.status_code == 200
    assert listed.json() == [created.json()]
    assert authorized_tokens == ["admin-session", "admin-session"]


def test_provider_cost_publication_maps_invalid_parameters_to_422() -> None:
    now = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            provider_cost_rates=InMemoryProviderCostRates(clock=lambda: now),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.post(
        "/api/v1/admin/provider-cost-rates",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "route_id": "route-1",
            "variant_code": "4k",
            "provider_currency": "US",
            "cost_per_image_micros": 120_000,
            "effective_from": "2026-08-09T01:01:00Z",
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Provider 成本参数无效"}


def test_provider_cost_publication_maps_duplicate_effective_time_to_409() -> None:
    now = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            provider_cost_rates=InMemoryProviderCostRates(clock=lambda: now),
            admin_authorizer=lambda token: None,
        )
    )
    request = {
        "route_id": "route-1",
        "variant_code": "4k",
        "provider_currency": "USD",
        "cost_per_image_micros": 120_000,
        "effective_from": "2026-08-09T01:01:00Z",
    }
    headers = {"Authorization": "Bearer admin-session"}
    assert client.post("/api/v1/admin/provider-cost-rates", headers=headers, json=request).status_code == 201

    duplicate = client.post("/api/v1/admin/provider-cost-rates", headers=headers, json=request)

    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Provider 成本版本冲突"}


def test_provider_cost_publication_maps_an_unknown_route_to_404(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'provider-cost-http.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC)
    client = TestClient(
        create_app(
            InMemoryAccountAccess(clock=lambda: now),
            provider_cost_rates=SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: now),
            admin_authorizer=lambda token: None,
        )
    )

    response = client.post(
        "/api/v1/admin/provider-cost-rates",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "route_id": "unknown-route",
            "variant_code": "4k",
            "provider_currency": "USD",
            "cost_per_image_micros": 120_000,
            "effective_from": now.isoformat(),
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "模型路由不存在"}

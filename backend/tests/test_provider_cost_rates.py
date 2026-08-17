from datetime import UTC, datetime, timedelta

from app.provider_costs import InMemoryProviderCostRates, ProviderCostRate


def test_provider_cost_versions_take_effect_without_rewriting_history() -> None:
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    rates = InMemoryProviderCostRates(
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )

    first = rates.publish(
        "route-1",
        variant_code="4k",
        provider_currency="usd",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    replacement = rates.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=150_000,
        effective_from=now + timedelta(days=1),
    )

    assert first == ProviderCostRate(
        version_id="cost-rate-1",
        route_id="route-1",
        variant_code="4k",
        version=1,
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
        published_at=now,
    )
    assert replacement.version == 2
    assert rates.effective_at("route-1", "4k", now) == first
    assert rates.effective_at("route-1", "4k", now + timedelta(days=1)) == replacement
    assert rates.versions("route-1", "4k") == (first, replacement)

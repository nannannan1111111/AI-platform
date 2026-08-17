from datetime import UTC, datetime, timedelta

from app.credits import InMemoryModelPrices


def test_initial_model_price_is_seeded_and_later_versions_do_not_rewrite_history() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)

    initial = prices.effective_at("gpt-image-2", "4k", now)
    replacement = prices.publish(
        "gpt-image-2",
        "4k",
        credits_per_result="0.2000",
        effective_from=now + timedelta(days=1),
    )

    assert initial.credits_per_result == "0.1500"
    assert initial.max_reference_images == 3
    assert prices.effective_at("gpt-image-2", "4k", now) == initial
    assert prices.effective_at("gpt-image-2", "4k", now + timedelta(days=1)) == replacement
    assert prices.get_version(initial.version_id) == initial


def test_model_price_catalog_returns_current_version_for_each_model_spec() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    future = prices.publish(
        "gpt-image-2",
        "2k",
        credits_per_result="0.1000",
        effective_from=now,
    )

    catalog = prices.catalog_at(now)

    assert catalog == (
        prices.effective_at("gpt-image-2", "2k", now),
        prices.effective_at("gpt-image-2", "4k", now),
    )
    assert future in catalog


def test_model_price_versions_snapshot_the_reference_image_limit() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)

    version = prices.publish(
        "gpt-image-2",
        "reference-heavy",
        credits_per_result="0.3000",
        effective_from=now,
        max_reference_images=8,
    )

    assert version.max_reference_images == 8

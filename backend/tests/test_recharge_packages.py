from datetime import UTC, datetime, timedelta

import pytest

from app.credits import InMemoryCredits, InvalidEffectiveTime, PackageVersionConflict


def test_publishing_a_new_recharge_package_version_preserves_the_historical_offer() -> None:
    published_at = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    packages = InMemoryCredits(clock=lambda: published_at)
    initial = packages.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=published_at,
    )
    replacement = packages.publish(
        "standard",
        payment_cny="100.00",
        credits="120.0000",
        effective_from=published_at + timedelta(days=1),
    )

    assert packages.sellable_at(published_at) == (initial,)
    assert packages.sellable_at(published_at + timedelta(days=1)) == (replacement,)
    assert packages.get_version(initial.version_id) == initial


def test_recharge_package_amounts_use_fixed_decimal_precision() -> None:
    now = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    packages = InMemoryCredits(clock=lambda: now)

    version = packages.publish(
        "initial",
        payment_cny="1",
        credits="1",
        effective_from=now,
    )

    assert version.payment_cny == "1.00"
    assert version.credits == "1.0000"


def test_a_package_cannot_have_two_versions_with_the_same_effective_time() -> None:
    now = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    packages = InMemoryCredits(clock=lambda: now)
    packages.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )

    with pytest.raises(PackageVersionConflict):
        packages.publish(
            "standard",
            payment_cny="100.00",
            credits="120.0000",
            effective_from=now,
        )


def test_a_recharge_package_version_cannot_be_published_retroactively() -> None:
    now = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    packages = InMemoryCredits(clock=lambda: now)

    with pytest.raises(InvalidEffectiveTime):
        packages.publish(
            "standard",
            payment_cny="100.00",
            credits="100.0000",
            effective_from=now - timedelta(seconds=1),
        )

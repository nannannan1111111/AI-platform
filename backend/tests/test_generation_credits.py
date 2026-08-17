from datetime import UTC, datetime

import pytest

from app.credits import CreditFreezeAlreadyFinalized, InMemoryCredits, InMemoryModelPrices, InsufficientCredits


def test_generation_freeze_uses_the_effective_model_price_and_moves_available_credits() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    freeze = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="task-1",
        occurred_at=now,
    )

    assert freeze.unit_price == "0.1500"
    assert freeze.frozen_credits == "0.3000"
    assert freeze.available_credits_after == "0.7000"
    assert freeze.frozen_credits_after == "0.3000"
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.7000"
    assert statement.frozen_credits == "0.3000"
    assert statement.entries[-1].kind == "freeze"


def test_partial_settlement_consumes_delivered_results_and_releases_the_remainder() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    freeze = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=4,
        task_reference="task-1",
        occurred_at=now,
    )

    settlement = credits.settle(
        freeze.freeze_id,
        delivered_quantity=2,
        settlement_reference="settlement-1",
        occurred_at=now,
    )

    assert settlement.kind == "settlement"
    assert settlement.delta_available_credits == "0.3000"
    assert settlement.delta_frozen_credits == "-0.6000"
    assert settlement.available_credits_after == "0.7000"
    assert settlement.frozen_credits_after == "0.0000"
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.7000"
    assert statement.frozen_credits == "0.0000"


def test_releasing_a_failed_generation_restores_all_frozen_credits_with_a_reason() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    freeze = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="task-1",
        occurred_at=now,
    )

    release = credits.release(
        freeze.freeze_id,
        release_reference="release-1",
        reason="provider failed",
        occurred_at=now,
    )

    assert release.kind == "release"
    assert release.delta_available_credits == "0.3000"
    assert release.delta_frozen_credits == "-0.3000"
    assert release.available_credits_after == "1.0000"
    assert release.frozen_credits_after == "0.0000"
    assert release.reason == "provider failed"


def test_generation_freeze_is_idempotent_and_finalized_freezes_cannot_change_outcome() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    first = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="task-1",
        occurred_at=now,
    )
    replay = credits.freeze(
        "account-space-1",
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="task-1",
        occurred_at=now,
    )
    settled = credits.settle(
        first.freeze_id,
        delivered_quantity=2,
        settlement_reference="settlement-1",
        occurred_at=now,
    )
    settlement_replay = credits.settle(
        first.freeze_id,
        delivered_quantity=2,
        settlement_reference="settlement-1",
        occurred_at=now,
    )

    assert replay == first
    assert settlement_replay == settled
    with pytest.raises(CreditFreezeAlreadyFinalized):
        credits.release(
            first.freeze_id,
            release_reference="release-1",
            reason="too late",
            occurred_at=now,
        )


def test_freezing_more_than_available_credits_has_no_balance_side_effect() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    with pytest.raises(InsufficientCredits):
        credits.freeze(
            "account-space-1",
            "gpt-image-2",
            "4k",
            quantity=7,
            task_reference="task-1",
            occurred_at=now,
        )
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"

from datetime import UTC, datetime

import pytest

from app.credits import (
    InMemoryCredits,
    InvalidAuditReference,
    InvalidReversalReason,
    PostingAlreadyReversed,
    ReferenceConflict,
)


def test_recording_a_recharge_uses_the_published_package_version() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )

    posting = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    assert posting.delta_available_credits == "100.0000"
    assert posting.available_credits_after == "100.0000"
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "100.0000"
    assert statement.frozen_credits == "0.0000"
    assert statement.entries == (posting,)


def test_replaying_the_same_payment_reference_does_not_add_credits_twice() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )

    first = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    replay = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    assert replay == first
    assert credits.statement("account-space-1").available_credits == "100.0000"
    assert credits.statement("account-space-1").entries == (first,)


def test_reversing_a_recharge_appends_an_auditable_opposite_posting() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    recharge = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    reversal = credits.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )

    assert reversal.kind == "reversal"
    assert reversal.delta_available_credits == "-100.0000"
    assert reversal.available_credits_after == "0.0000"
    assert reversal.reverses_posting_id == recharge.posting_id
    assert reversal.reason == "payment charged back"
    assert credits.statement("account-space-1").entries == (recharge, reversal)


def test_payment_reference_cannot_be_reused_for_a_different_recharge() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1", "account-space-2"},
    )
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    with pytest.raises(ReferenceConflict):
        credits.record_recharge(
            "account-space-2",
            package.version_id,
            payment_reference="payment-1",
            occurred_at=now,
        )


def test_reversal_is_idempotent_and_an_original_posting_can_only_be_reversed_once() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    recharge = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    first = credits.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )

    replay = credits.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )

    assert replay == first
    with pytest.raises(PostingAlreadyReversed):
        credits.reverse(
            recharge.posting_id,
            reversal_reference="chargeback-2",
            reason="duplicate chargeback",
            occurred_at=now,
        )
    assert credits.statement("account-space-1").entries == (recharge, first)


def test_reversing_an_incorrect_reversal_restores_credits_without_rewriting_history() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    recharge = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    chargeback = credits.reverse(
        recharge.posting_id,
        reversal_reference="chargeback-1",
        reason="payment charged back",
        occurred_at=now,
    )

    correction = credits.reverse(
        chargeback.posting_id,
        reversal_reference="correction-1",
        reason="chargeback was incorrect",
        occurred_at=now,
    )

    assert correction.delta_available_credits == "100.0000"
    assert correction.reverses_posting_id == chargeback.posting_id
    assert credits.statement("account-space-1").available_credits == "100.0000"
    assert credits.statement("account-space-1").entries == (recharge, chargeback, correction)


def test_recharge_rejects_an_empty_audit_reference() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )

    with pytest.raises(InvalidAuditReference):
        credits.record_recharge(
            "account-space-1",
            package.version_id,
            payment_reference=" ",
            occurred_at=now,
        )


def test_reversal_rejects_an_empty_audit_reason() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish(
        "standard",
        payment_cny="100.00",
        credits="100.0000",
        effective_from=now,
    )
    recharge = credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )

    with pytest.raises(InvalidReversalReason):
        credits.reverse(
            recharge.posting_id,
            reversal_reference="chargeback-1",
            reason=" ",
            occurred_at=now,
        )

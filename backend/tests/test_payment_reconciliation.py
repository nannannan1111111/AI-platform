from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.reconcile_payment_events import PaymentEvent, compare_events, read_channel_events


def _event(
    *,
    event_type: str = "payment_success",
    event_id: str = "pay-1",
    order_id: str = "order-1",
    amount: int = 1000,
) -> PaymentEvent:
    return PaymentEvent(
        event_type=event_type,  # type: ignore[arg-type]
        payment_provider="epay",
        provider_event_id=event_id,
        order_id=order_id,
        amount_cny_units=amount,
        occurred_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


def test_reconciliation_accepts_exact_payment_and_chargeback_facts() -> None:
    events = (
        _event(),
        _event(event_type="chargeback", event_id="chargeback-1"),
    )

    assert compare_events(events, events) == ()


def test_reconciliation_reports_missing_amount_and_unsupported_refund_events() -> None:
    channel = (
        _event(amount=1001),
        _event(event_id="missing-1"),
        _event(event_type="partial_refund", event_id="refund-1", amount=500),
    )
    local = (
        _event(amount=1000),
        _event(event_id="local-only-1"),
    )

    kinds = {difference.kind for difference in compare_events(channel, local)}

    assert kinds == {
        "amount_mismatch",
        "missing_local_event",
        "unsupported_channel_event",
        "missing_channel_event",
    }


def test_channel_csv_requires_exact_money_timezone_and_unique_event_identity(tmp_path: Path) -> None:
    export = tmp_path / "events.csv"
    export.write_text(
        "event_type,payment_provider,provider_event_id,order_id,amount_cny,occurred_at\n"
        "payment_success,epay,event-1,order-1,10.001,2026-08-17T00:00:00Z\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at most 2 decimals"):
        read_channel_events(export)

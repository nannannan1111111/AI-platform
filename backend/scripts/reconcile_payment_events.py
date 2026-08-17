"""Compare a normalized payment-channel event export with immutable local facts."""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from sqlalchemy import create_engine, text

EventType = Literal["payment_success", "chargeback", "refund", "partial_refund"]
_SUPPORTED_LOCAL_EVENT_TYPES = {"payment_success", "chargeback"}


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """One exact-CNY payment-channel or local ledger event."""

    event_type: EventType
    payment_provider: str
    provider_event_id: str
    order_id: str
    amount_cny_units: int
    occurred_at: datetime

    @property
    def key(self) -> tuple[str, str, str]:
        """Return the provider-scoped immutable event identity."""
        return (self.event_type, self.payment_provider, self.provider_event_id)


@dataclass(frozen=True, slots=True)
class Difference:
    """A machine-readable reconciliation finding without credentials."""

    kind: str
    event_type: str
    payment_provider: str
    provider_event_id: str
    order_id: str
    detail: str


def read_channel_events(path: Path) -> tuple[PaymentEvent, ...]:
    """Read the reviewed normalized CSV contract and reject duplicate event identities."""
    required = {
        "event_type",
        "payment_provider",
        "provider_event_id",
        "order_id",
        "amount_cny",
        "occurred_at",
    }
    events: list[PaymentEvent] = []
    seen: set[tuple[str, str, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            event = _channel_event(row, line_number)
            if event.key in seen:
                raise ValueError(f"duplicate event identity at CSV line {line_number}")
            seen.add(event.key)
            events.append(event)
    return tuple(events)


def load_local_events(database_url: str, start: datetime, until: datetime) -> tuple[PaymentEvent, ...]:
    """Read local append-only payment and chargeback facts in a half-open UTC window."""
    if start >= until:
        raise ValueError("reconciliation start must be earlier than until")
    engine = create_engine(database_url)
    queries = (
        (
            "payment_success",
            "SELECT payment_provider, provider_event_id, order_id, "
            "paid_payment_cny_units AS amount_cny_units, occurred_at "
            "FROM payment_success_events WHERE occurred_at >= :start AND occurred_at < :until",
        ),
        (
            "chargeback",
            "SELECT payment_provider, provider_event_id, order_id, "
            "charged_back_payment_cny_units AS amount_cny_units, occurred_at "
            "FROM payment_chargeback_events WHERE occurred_at >= :start AND occurred_at < :until",
        ),
    )
    events: list[PaymentEvent] = []
    with engine.connect() as database:
        for event_type, statement in queries:
            rows = database.execute(text(statement), {"start": start, "until": until}).mappings()
            for row in rows:
                occurred_at = row["occurred_at"]
                if isinstance(occurred_at, str):
                    occurred_at = _utc_datetime(occurred_at)
                if isinstance(occurred_at, datetime) and occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
                if not isinstance(occurred_at, datetime):
                    raise RuntimeError("local payment event has an invalid timestamp")
                events.append(
                    PaymentEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        payment_provider=str(row["payment_provider"]),
                        provider_event_id=str(row["provider_event_id"]),
                        order_id=str(row["order_id"]),
                        amount_cny_units=int(row["amount_cny_units"]),
                        occurred_at=occurred_at.astimezone(UTC),
                    )
                )
    return tuple(events)


def compare_events(
    channel_events: tuple[PaymentEvent, ...],
    local_events: tuple[PaymentEvent, ...],
) -> tuple[Difference, ...]:
    """Return unsupported, missing, extra, order, and exact-amount differences."""
    channel = {event.key: event for event in channel_events}
    local = {event.key: event for event in local_events}
    differences: list[Difference] = []
    for event in channel_events:
        if event.event_type not in _SUPPORTED_LOCAL_EVENT_TYPES:
            differences.append(_difference("unsupported_channel_event", event, "requires approved manual handling"))
            continue
        counterpart = local.get(event.key)
        if counterpart is None:
            differences.append(_difference("missing_local_event", event, "channel event has no local fact"))
            continue
        if counterpart.order_id != event.order_id:
            differences.append(_difference("order_mismatch", event, f"local order is {counterpart.order_id}"))
        if counterpart.amount_cny_units != event.amount_cny_units:
            differences.append(
                _difference(
                    "amount_mismatch",
                    event,
                    f"channel={_format_cny(event.amount_cny_units)} local={_format_cny(counterpart.amount_cny_units)}",
                )
            )
    for key, event in local.items():
        if key not in channel:
            differences.append(_difference("missing_channel_event", event, "local fact is absent from channel export"))
    return tuple(sorted(differences, key=lambda item: (item.payment_provider, item.provider_event_id, item.kind)))


def _channel_event(row: dict[str, str | None], line_number: int) -> PaymentEvent:
    event_type = (row.get("event_type") or "").strip().casefold()
    if event_type not in {"payment_success", "chargeback", "refund", "partial_refund"}:
        raise ValueError(f"unsupported event_type at CSV line {line_number}")
    provider = (row.get("payment_provider") or "").strip().casefold()
    event_id = (row.get("provider_event_id") or "").strip()
    order_id = (row.get("order_id") or "").strip()
    if not provider or not event_id or not order_id:
        raise ValueError(f"blank event identity at CSV line {line_number}")
    return PaymentEvent(
        event_type=event_type,  # type: ignore[arg-type]
        payment_provider=provider,
        provider_event_id=event_id,
        order_id=order_id,
        amount_cny_units=_cny_units(row.get("amount_cny") or "", line_number),
        occurred_at=_utc_datetime(row.get("occurred_at") or "", line_number),
    )


def _cny_units(value: str, line_number: int) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount_cny at CSV line {line_number}") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError(f"amount_cny must be positive with at most 2 decimals at CSV line {line_number}")
    return int(amount * 100)


def _utc_datetime(value: str, line_number: int | None = None) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        suffix = f" at CSV line {line_number}" if line_number is not None else ""
        raise ValueError(f"invalid occurred_at{suffix}") from exc
    if parsed.tzinfo is None:
        suffix = f" at CSV line {line_number}" if line_number is not None else ""
        raise ValueError(f"occurred_at must include a timezone{suffix}")
    return parsed.astimezone(UTC)


def _format_cny(units: int) -> str:
    whole, fraction = divmod(units, 100)
    return f"{whole}.{fraction:02d}"


def _difference(kind: str, event: PaymentEvent, detail: str) -> Difference:
    return Difference(
        kind=kind,
        event_type=event.event_type,
        payment_provider=event.payment_provider,
        provider_event_id=event.provider_event_id,
        order_id=event.order_id,
        detail=detail,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel-csv", type=Path, required=True)
    parser.add_argument("--from", dest="start", required=True, help="inclusive RFC3339 timestamp")
    parser.add_argument("--until", required=True, help="exclusive RFC3339 timestamp")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    return parser.parse_args()


def main() -> int:
    """Print JSON findings and return 2 whenever operator attention is required."""
    arguments = _arguments()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    channel = read_channel_events(arguments.channel_csv)
    local = load_local_events(
        arguments.database_url,
        _utc_datetime(arguments.start),
        _utc_datetime(arguments.until),
    )
    differences = compare_events(channel, local)
    print(json.dumps([asdict(item) for item in differences], ensure_ascii=False, indent=2))
    return 2 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())

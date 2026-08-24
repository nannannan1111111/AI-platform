"""SQLAlchemy Adapter for the RechargeOrders interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.credits import CreditAccounting, RechargePackages
from app.credits._amounts import cny_units, credit_units, format_cny, format_credits
from app.orders.models import (
    DirectRechargeOrderSubmission,
    PaymentAmountMismatch,
    PaymentChargeback,
    PaymentEventConflict,
    PaymentProviderMismatch,
    PaymentSuccess,
    RechargeOrder,
    RechargeOrderAlreadyExists,
    RechargeOrderCancellationNotAllowed,
    RechargeOrderChargebackNotAllowed,
    RechargeOrderNotFound,
    RechargeOrderPaymentAlreadyFinalized,
    RechargeOrderStatus,
    RechargeOrderSubmission,
)

_metadata = MetaData()
_recharge_orders = Table(
    "recharge_orders",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("package_version_id", String(36), nullable=True),
    Column("package_code", String(64), nullable=False),
    Column("payment_cny_units", BigInteger, nullable=False),
    Column("credit_units", BigInteger, nullable=False),
    Column("payment_provider", String(64), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(32), nullable=True),
    Column("paid_at", DateTime(timezone=True), nullable=True),
    Column("payment_reference", String(255), nullable=True),
    Column("recharge_posting_id", String(36), nullable=True),
    Column("charged_back_at", DateTime(timezone=True), nullable=True),
    Column("chargeback_reference", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
_payment_events = Table(
    "payment_success_events",
    _metadata,
    Column("payment_provider", String(64), primary_key=True),
    Column("provider_event_id", String(255), primary_key=True),
    Column("order_id", String(36), nullable=False),
    Column("paid_payment_cny_units", BigInteger, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)
_chargeback_events = Table(
    "payment_chargeback_events",
    _metadata,
    Column("payment_provider", String(64), primary_key=True),
    Column("provider_event_id", String(255), primary_key=True),
    Column("order_id", String(36), nullable=False),
    Column("charged_back_payment_cny_units", BigInteger, nullable=False),
    Column("reversal_posting_id", String(36), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyRechargeOrders:
    """Persist recharge order snapshots and idempotent payment success events."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        packages: RechargePackages,
        credit_accounting: CreditAccounting,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._packages = packages
        self._credit_accounting = credit_accounting
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        packages: RechargePackages,
        credit_accounting: CreditAccounting,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyRechargeOrders:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            packages=packages,
            credit_accounting=credit_accounting,
            clock=clock,
        )

    def create(self, submission: RechargeOrderSubmission) -> RechargeOrder:
        with self._session_factory.begin() as database:
            existing_row = _by_idempotency(database, submission.account_space_id, submission.idempotency_key)
            if existing_row is not None:
                existing = _order_from_row(existing_row)
                if _matches_submission(existing, submission):
                    return existing
                raise RechargeOrderAlreadyExists(submission.idempotency_key)
            package = self._packages.get_version(submission.package_version_id)
            order = RechargeOrder(
                order_id=str(uuid4()),
                user_id=submission.user_id,
                account_space_id=submission.account_space_id,
                package_version_id=package.version_id,
                package_code=package.package_code,
                payment_cny=package.payment_cny,
                credits=package.credits,
                payment_provider=submission.payment_provider,
                idempotency_key=submission.idempotency_key,
                status=RechargeOrderStatus.PENDING,
                created_at=submission.created_at,
                updated_at=submission.created_at,
                expires_at=submission.created_at + timedelta(minutes=1),
            )
            try:
                database.execute(insert(_recharge_orders).values(**_order_values(order)))
            except IntegrityError as exc:
                raise RechargeOrderAlreadyExists(submission.idempotency_key) from exc
            return order

    def create_direct(self, submission: DirectRechargeOrderSubmission) -> RechargeOrder:
        """Persist one ordinary recharge quote without a package dependency."""
        payment_units = cny_units(submission.payment_cny)
        amount_units = credit_units(submission.credits)
        with self._session_factory.begin() as database:
            existing_row = _by_idempotency(database, submission.account_space_id, submission.idempotency_key)
            if existing_row is not None:
                existing = _order_from_row(existing_row)
                if _matches_direct_submission(existing, submission):
                    return existing
                raise RechargeOrderAlreadyExists(submission.idempotency_key)
            order = RechargeOrder(
                order_id=str(uuid4()),
                user_id=submission.user_id,
                account_space_id=submission.account_space_id,
                package_version_id=None,
                package_code="普通充值",
                payment_cny=format_cny(payment_units),
                credits=format_credits(amount_units),
                payment_provider=submission.payment_provider,
                idempotency_key=submission.idempotency_key,
                status=RechargeOrderStatus.PENDING,
                created_at=submission.created_at,
                updated_at=submission.created_at,
                expires_at=submission.created_at + timedelta(minutes=1),
            )
            try:
                database.execute(insert(_recharge_orders).values(**_order_values(order)))
            except IntegrityError as exc:
                raise RechargeOrderAlreadyExists(submission.idempotency_key) from exc
            return order

    def get(self, account_space_id: str, order_id: str) -> RechargeOrder:
        with self._session_factory.begin() as database:
            row = _order_row(database, order_id, account_space_id=account_space_id, for_update=True)
            if row is None:
                raise RechargeOrderNotFound(order_id)
            return _expire_if_due(database, _order_from_row(row), self._clock())

    def list(self, account_space_id: str) -> tuple[RechargeOrder, ...]:
        """按最新创建优先读取账户空间拥有的充值订单。"""
        with self._session_factory.begin() as database:
            rows = database.execute(
                select(_recharge_orders)
                .where(_recharge_orders.c.account_space_id == account_space_id)
                .order_by(_recharge_orders.c.created_at.desc(), _recharge_orders.c.id.desc())
            ).mappings()
            now = self._clock()
            return tuple(_expire_if_due(database, _order_from_row(row), now) for row in rows)

    def cancel(self, account_space_id: str, order_id: str, *, occurred_at: datetime) -> RechargeOrder:
        """Persist an owner-requested cancellation while the order is pending."""
        with self._session_factory.begin() as database:
            row = _order_row(database, order_id, account_space_id=account_space_id, for_update=True)
            if row is None:
                raise RechargeOrderNotFound(order_id)
            order = _expire_if_due(database, _order_from_row(row), occurred_at)
            if order.status is RechargeOrderStatus.CANCELLED:
                return order
            if order.status is not RechargeOrderStatus.PENDING:
                raise RechargeOrderCancellationNotAllowed(order_id)
            cancelled = replace(
                order,
                status=RechargeOrderStatus.CANCELLED,
                updated_at=occurred_at,
                cancelled_at=occurred_at,
                cancellation_reason="user_cancelled",
            )
            _update_order(database, cancelled)
            return cancelled

    def record_payment_success(self, event: PaymentSuccess) -> RechargeOrder:
        with self._session_factory.begin() as database:
            event_row = (
                database.execute(
                    select(_payment_events).where(
                        _payment_events.c.payment_provider == event.payment_provider,
                        _payment_events.c.provider_event_id == event.provider_event_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if event_row is not None:
                if str(event_row["order_id"]) != event.order_id:
                    raise PaymentEventConflict(event.provider_event_id)
                order_row = _order_row(database, event.order_id)
                if order_row is None:
                    raise RechargeOrderNotFound(event.order_id)
                return _order_from_row(order_row)
            order_row = _order_row(database, event.order_id, for_update=True)
            if order_row is None:
                raise RechargeOrderNotFound(event.order_id)
            order = _order_from_row(order_row)
            if order.status is RechargeOrderStatus.PAID:
                raise RechargeOrderPaymentAlreadyFinalized(order.order_id)
            if order.payment_provider != event.payment_provider:
                raise PaymentProviderMismatch(order.order_id)
            paid_units = cny_units(event.paid_payment_cny)
            if cny_units(order.payment_cny) != paid_units:
                raise PaymentAmountMismatch(order.order_id)
            payment_reference = f"payment:{event.payment_provider}:{event.provider_event_id}"
            if order.package_version_id is None:
                recharge = self._credit_accounting.record_direct_recharge(
                    order.account_space_id,
                    order.credits,
                    payment_reference=payment_reference,
                    occurred_at=event.occurred_at,
                )
            else:
                recharge = self._credit_accounting.record_recharge(
                    order.account_space_id,
                    order.package_version_id,
                    payment_reference=payment_reference,
                    occurred_at=event.occurred_at,
                )
            paid = replace(
                order,
                status=RechargeOrderStatus.PAID,
                updated_at=event.occurred_at,
                paid_at=event.occurred_at,
                payment_reference=payment_reference,
                recharge_posting_id=recharge.posting_id,
            )
            database.execute(
                insert(_payment_events).values(
                    payment_provider=event.payment_provider,
                    provider_event_id=event.provider_event_id,
                    order_id=order.order_id,
                    paid_payment_cny_units=paid_units,
                    occurred_at=event.occurred_at,
                )
            )
            database.execute(
                update(_recharge_orders).where(_recharge_orders.c.id == order.order_id).values(**_order_values(paid))
            )
            return paid

    def record_chargeback(self, event: PaymentChargeback) -> RechargeOrder:
        """Persist one verified full chargeback and reverse its recharge posting."""
        with self._session_factory.begin() as database:
            event_row = (
                database.execute(
                    select(_chargeback_events).where(
                        _chargeback_events.c.payment_provider == event.payment_provider,
                        _chargeback_events.c.provider_event_id == event.provider_event_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if event_row is not None:
                if str(event_row["order_id"]) != event.order_id or int(
                    event_row["charged_back_payment_cny_units"]
                ) != cny_units(event.charged_back_payment_cny):
                    raise PaymentEventConflict(event.provider_event_id)
                order_row = _order_row(database, event.order_id)
                if order_row is None:
                    raise RechargeOrderNotFound(event.order_id)
                return _order_from_row(order_row)
            order_row = _order_row(database, event.order_id, for_update=True)
            if order_row is None:
                raise RechargeOrderNotFound(event.order_id)
            order = _order_from_row(order_row)
            if order.status is not RechargeOrderStatus.PAID or not order.recharge_posting_id:
                raise RechargeOrderChargebackNotAllowed(order.order_id)
            if order.payment_provider != event.payment_provider:
                raise PaymentProviderMismatch(order.order_id)
            charged_back_units = cny_units(event.charged_back_payment_cny)
            if cny_units(order.payment_cny) != charged_back_units:
                raise PaymentAmountMismatch(order.order_id)
            chargeback_reference = f"chargeback:{event.payment_provider}:{event.provider_event_id}"
            reversal = self._credit_accounting.reverse(
                order.recharge_posting_id,
                reversal_reference=chargeback_reference,
                reason="payment chargeback",
                occurred_at=event.occurred_at,
            )
            charged_back = replace(
                order,
                status=RechargeOrderStatus.CHARGED_BACK,
                updated_at=event.occurred_at,
                charged_back_at=event.occurred_at,
                chargeback_reference=chargeback_reference,
            )
            database.execute(
                insert(_chargeback_events).values(
                    payment_provider=event.payment_provider,
                    provider_event_id=event.provider_event_id,
                    order_id=order.order_id,
                    charged_back_payment_cny_units=charged_back_units,
                    reversal_posting_id=reversal.posting_id,
                    occurred_at=event.occurred_at,
                )
            )
            database.execute(
                update(_recharge_orders)
                .where(_recharge_orders.c.id == order.order_id)
                .values(**_order_values(charged_back))
            )
            return charged_back


def _by_idempotency(database: Session, account_space_id: str, idempotency_key: str) -> Any:
    return (
        database.execute(
            select(_recharge_orders).where(
                _recharge_orders.c.account_space_id == account_space_id,
                _recharge_orders.c.idempotency_key == idempotency_key,
            )
        )
        .mappings()
        .one_or_none()
    )


def _order_row(
    database: Session,
    order_id: str,
    *,
    account_space_id: str | None = None,
    for_update: bool = False,
) -> Any:
    query = select(_recharge_orders).where(_recharge_orders.c.id == order_id)
    if account_space_id is not None:
        query = query.where(_recharge_orders.c.account_space_id == account_space_id)
    if for_update:
        query = query.with_for_update()
    return database.execute(query).mappings().one_or_none()


def _order_values(order: RechargeOrder) -> dict[str, Any]:
    return {
        "id": order.order_id,
        "user_id": order.user_id,
        "account_space_id": order.account_space_id,
        "package_version_id": order.package_version_id,
        "package_code": order.package_code,
        "payment_cny_units": cny_units(order.payment_cny),
        "credit_units": credit_units(order.credits),
        "payment_provider": order.payment_provider,
        "idempotency_key": order.idempotency_key,
        "status": order.status.value,
        "expires_at": order.expires_at or (order.created_at + timedelta(minutes=1)),
        "cancelled_at": order.cancelled_at,
        "cancellation_reason": order.cancellation_reason or None,
        "paid_at": order.paid_at,
        "payment_reference": order.payment_reference or None,
        "recharge_posting_id": order.recharge_posting_id or None,
        "charged_back_at": order.charged_back_at,
        "chargeback_reference": order.chargeback_reference or None,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def _order_from_row(row: Any) -> RechargeOrder:
    def aware(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    created_at = aware(row["created_at"])
    updated_at = aware(row["updated_at"])
    if created_at is None or updated_at is None:
        raise RuntimeError("充值订单时间不能为空")
    return RechargeOrder(
        order_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        package_version_id=None if row["package_version_id"] is None else str(row["package_version_id"]),
        package_code=str(row["package_code"]),
        payment_cny=format_cny(int(row["payment_cny_units"])),
        credits=format_credits(int(row["credit_units"])),
        payment_provider=str(row["payment_provider"]),
        idempotency_key=str(row["idempotency_key"]),
        status=RechargeOrderStatus(str(row["status"])),
        created_at=created_at,
        updated_at=updated_at,
        paid_at=aware(row["paid_at"]),
        payment_reference=str(row["payment_reference"] or ""),
        recharge_posting_id=str(row["recharge_posting_id"] or ""),
        charged_back_at=aware(row["charged_back_at"]),
        chargeback_reference=str(row["chargeback_reference"] or ""),
        expires_at=aware(row["expires_at"]) or created_at,
        cancelled_at=aware(row["cancelled_at"]),
        cancellation_reason=str(row["cancellation_reason"] or ""),
    )


def _matches_submission(order: RechargeOrder, submission: RechargeOrderSubmission) -> bool:
    return (
        order.user_id == submission.user_id
        and order.package_version_id == submission.package_version_id
        and order.payment_provider == submission.payment_provider
    )


def _matches_direct_submission(order: RechargeOrder, submission: DirectRechargeOrderSubmission) -> bool:
    return (
        order.user_id == submission.user_id
        and order.package_version_id is None
        and order.payment_cny == submission.payment_cny
        and order.credits == submission.credits
        and order.payment_provider == submission.payment_provider
    )


def _expire_if_due(database: Session, order: RechargeOrder, occurred_at: datetime) -> RechargeOrder:
    if order.status is not RechargeOrderStatus.PENDING or not order.expires_at or occurred_at < order.expires_at:
        return order
    expired = replace(
        order,
        status=RechargeOrderStatus.EXPIRED,
        updated_at=occurred_at,
        cancelled_at=occurred_at,
        cancellation_reason="expired",
    )
    _update_order(database, expired)
    return expired


def _update_order(database: Session, order: RechargeOrder) -> None:
    database.execute(
        update(_recharge_orders).where(_recharge_orders.c.id == order.order_id).values(**_order_values(order))
    )

"""RechargeOrders Interface 的内存 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from app.credits import CreditAccounting, RechargePackages
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


class InMemoryRechargeOrders:
    """在单进程内保存充值订单与幂等键。"""

    def __init__(
        self,
        packages: RechargePackages,
        credit_accounting: CreditAccounting,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._packages = packages
        self._credit_accounting = credit_accounting
        self._clock = clock or (lambda: datetime.now(UTC))
        self._orders_by_id: dict[str, RechargeOrder] = {}
        self._orders_by_idempotency: dict[tuple[str, str], RechargeOrder] = {}
        self._orders_by_payment_event: dict[tuple[str, str], RechargeOrder] = {}
        self._orders_by_chargeback_event: dict[tuple[str, str], tuple[PaymentChargeback, RechargeOrder]] = {}
        self._lock = RLock()

    def create(self, submission: RechargeOrderSubmission) -> RechargeOrder:
        """创建固化服务端充值包版本的待支付订单。"""
        idempotency = (submission.account_space_id, submission.idempotency_key)
        with self._lock:
            existing = self._orders_by_idempotency.get(idempotency)
            if existing is not None:
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
            self._orders_by_id[order.order_id] = order
            self._orders_by_idempotency[idempotency] = order
            return order

    def create_direct(self, submission: DirectRechargeOrderSubmission) -> RechargeOrder:
        """创建固化普通充值金额与全局比例计算结果的待支付订单。"""
        idempotency = (submission.account_space_id, submission.idempotency_key)
        with self._lock:
            existing = self._orders_by_idempotency.get(idempotency)
            if existing is not None:
                if _matches_direct_submission(existing, submission):
                    return existing
                raise RechargeOrderAlreadyExists(submission.idempotency_key)
            order = RechargeOrder(
                order_id=str(uuid4()),
                user_id=submission.user_id,
                account_space_id=submission.account_space_id,
                package_version_id=None,
                package_code="普通充值",
                payment_cny=submission.payment_cny,
                credits=submission.credits,
                payment_provider=submission.payment_provider,
                idempotency_key=submission.idempotency_key,
                status=RechargeOrderStatus.PENDING,
                created_at=submission.created_at,
                updated_at=submission.created_at,
                expires_at=submission.created_at + timedelta(minutes=1),
            )
            self._orders_by_id[order.order_id] = order
            self._orders_by_idempotency[idempotency] = order
            return order

    def get(self, account_space_id: str, order_id: str) -> RechargeOrder:
        """读取账户空间拥有的充值订单。"""
        with self._lock:
            order = self._orders_by_id.get(order_id)
        if order is None or order.account_space_id != account_space_id:
            raise RechargeOrderNotFound(order_id)
        with self._lock:
            return self._expire_if_due(order, self._clock())

    def list(self, account_space_id: str) -> tuple[RechargeOrder, ...]:
        """按最新创建优先读取账户空间拥有的充值订单。"""
        with self._lock:
            owned = tuple(order for order in self._orders_by_id.values() if order.account_space_id == account_space_id)
        with self._lock:
            now = self._clock()
            owned = tuple(self._expire_if_due(order, now) for order in owned)
        return tuple(sorted(owned, key=lambda order: (order.created_at, order.order_id), reverse=True))

    def cancel(self, account_space_id: str, order_id: str, *, occurred_at: datetime) -> RechargeOrder:
        """取消当前账户空间仍在有效期内的待支付订单。"""
        with self._lock:
            order = self._orders_by_id.get(order_id)
            if order is None or order.account_space_id != account_space_id:
                raise RechargeOrderNotFound(order_id)
            order = self._expire_if_due(order, occurred_at)
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
            self._store(cancelled)
            return cancelled

    def record_payment_success(self, event: PaymentSuccess) -> RechargeOrder:
        """验证成功通知并为订单入账一次充值额度。"""
        event_key = (event.payment_provider, event.provider_event_id)
        with self._lock:
            existing_event = self._orders_by_payment_event.get(event_key)
            if existing_event is not None:
                if existing_event.order_id == event.order_id:
                    return existing_event
                raise PaymentEventConflict(event.provider_event_id)
            order = self._orders_by_id.get(event.order_id)
            if order is None:
                raise RechargeOrderNotFound(event.order_id)
            if order.status is RechargeOrderStatus.PAID:
                raise RechargeOrderPaymentAlreadyFinalized(order.order_id)
            if order.payment_provider != event.payment_provider:
                raise PaymentProviderMismatch(order.order_id)
            if order.payment_cny != event.paid_payment_cny:
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
            self._orders_by_id[order.order_id] = paid
            self._orders_by_idempotency[(order.account_space_id, order.idempotency_key)] = paid
            self._orders_by_payment_event[event_key] = paid
            return paid

    def record_chargeback(self, event: PaymentChargeback) -> RechargeOrder:
        """冲销已到账订单的全部充值额度。"""
        event_key = (event.payment_provider, event.provider_event_id)
        with self._lock:
            existing_chargeback = self._orders_by_chargeback_event.get(event_key)
            if existing_chargeback is not None:
                existing_event, existing_order = existing_chargeback
                if (
                    existing_event.order_id == event.order_id
                    and existing_event.charged_back_payment_cny == event.charged_back_payment_cny
                ):
                    return existing_order
                raise PaymentEventConflict(event.provider_event_id)
            order = self._orders_by_id.get(event.order_id)
            if order is None:
                raise RechargeOrderNotFound(event.order_id)
            if order.status is not RechargeOrderStatus.PAID or not order.recharge_posting_id:
                raise RechargeOrderChargebackNotAllowed(order.order_id)
            if order.payment_provider != event.payment_provider:
                raise PaymentProviderMismatch(order.order_id)
            if order.payment_cny != event.charged_back_payment_cny:
                raise PaymentAmountMismatch(order.order_id)
            chargeback_reference = f"chargeback:{event.payment_provider}:{event.provider_event_id}"
            self._credit_accounting.reverse(
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
            self._orders_by_id[order.order_id] = charged_back
            self._orders_by_idempotency[(order.account_space_id, order.idempotency_key)] = charged_back
            self._orders_by_chargeback_event[event_key] = (event, charged_back)
            return charged_back

    def _expire_if_due(self, order: RechargeOrder, occurred_at: datetime) -> RechargeOrder:
        if order.status is not RechargeOrderStatus.PENDING or not order.expires_at or occurred_at < order.expires_at:
            return order
        expired = replace(
            order,
            status=RechargeOrderStatus.EXPIRED,
            updated_at=occurred_at,
            cancelled_at=occurred_at,
            cancellation_reason="expired",
        )
        self._store(expired)
        return expired

    def _store(self, order: RechargeOrder) -> None:
        self._orders_by_id[order.order_id] = order
        self._orders_by_idempotency[(order.account_space_id, order.idempotency_key)] = order


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

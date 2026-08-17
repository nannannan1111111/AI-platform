"""支付途径目录与易支付网关 Module 的公开 Interface。"""

from collections.abc import Mapping
from typing import Protocol

from app.orders import RechargeOrder
from app.payments.models import (
    PaymentCheckout,
    PaymentMethod,
    PaymentSettingsSnapshot,
    PaymentSettingsUpdate,
    RechargeQuote,
    RechargeRateSnapshot,
    VerifiedPaymentNotification,
)


class PaymentMethods(Protocol):
    """提供当前可用于创建充值订单的支付途径。"""

    def available(self) -> tuple[PaymentMethod, ...]:
        """按运营展示顺序返回不含任何凭据的支付途径。"""


class EpayPayments(PaymentMethods, Protocol):
    """管理并使用一个易支付兼容网关。"""

    def current(self) -> PaymentSettingsSnapshot:
        """返回不含商户密钥的当前设置。"""

    def update(self, command: PaymentSettingsUpdate) -> PaymentSettingsSnapshot:
        """验证并保存设置，仅在提供新密钥时轮换密钥。"""

    def current_recharge_rate(self) -> RechargeRateSnapshot:
        """返回用户可见的普通充值全局换算比例。"""

    def update_recharge_rate(self, credits_per_cny: str) -> RechargeRateSnapshot:
        """验证并保存普通充值每人民币一元兑换的额度。"""

    def quote_recharge(self, payment_cny: str) -> RechargeQuote:
        """按当前全局比例生成普通充值金额与额度快照。"""

    def create_checkout(self, order: RechargeOrder) -> PaymentCheckout:
        """为已固化金额的待支付充值订单生成表单。"""

    def verify_notification(self, parameters: Mapping[str, str]) -> VerifiedPaymentNotification:
        """验证网关通知并返回可供订单 Module 入账的去敏事件。"""

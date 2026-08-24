"""充值订单 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.orders.models import (
    DirectRechargeOrderSubmission,
    PaymentChargeback,
    PaymentSuccess,
    RechargeOrder,
    RechargeOrderSubmission,
)


class RechargeOrders(Protocol):
    """创建并读取个人账户空间拥有的充值订单。"""

    def create(self, submission: RechargeOrderSubmission) -> RechargeOrder:
        """按服务端充值包快照创建可安全重放的待支付订单。"""

    def create_direct(self, submission: DirectRechargeOrderSubmission) -> RechargeOrder:
        """按全局比例报价快照创建不依赖充值包的普通充值订单。"""

    def get(self, account_space_id: str, order_id: str) -> RechargeOrder:
        """读取账户空间拥有的订单；其他账户按不存在处理。"""

    def list(self, account_space_id: str) -> tuple[RechargeOrder, ...]:
        """按创建时间和订单标识倒序读取账户空间拥有的订单。"""

    def cancel(self, account_space_id: str, order_id: str, *, occurred_at: datetime) -> RechargeOrder:
        """取消仍在有效期内的待支付订单。"""

    def record_payment_success(self, event: PaymentSuccess) -> RechargeOrder:
        """校验支付成功通知并完成一次额度入账；重复通知可安全重放。"""


class RechargeOrderChargebacks(Protocol):
    """记录支付渠道发起的整笔拒付。"""

    def record_chargeback(self, event: PaymentChargeback) -> RechargeOrder:
        """冲销原充值账务记录；允许结果额度为负。"""

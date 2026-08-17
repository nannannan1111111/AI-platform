"""充值订单 Module 的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RechargeOrderStatus(StrEnum):
    """充值订单的支付生命周期状态。"""

    PENDING = "pending"
    PAID = "paid"
    CHARGED_BACK = "charged_back"


class RechargeOrderAlreadyExists(ValueError):
    """幂等键已经属于参数不同的充值订单。"""


class RechargeOrderNotFound(LookupError):
    """充值订单不存在或不属于请求的个人账户空间。"""


class PaymentAmountMismatch(ValueError):
    """支付通知金额与订单固化金额不一致。"""


class PaymentProviderMismatch(ValueError):
    """支付通知渠道与订单渠道不一致。"""


class PaymentEventConflict(ValueError):
    """同一支付渠道事件标识已经属于另一订单。"""


class RechargeOrderPaymentAlreadyFinalized(ValueError):
    """订单已经到账，不能用另一支付事件改写。"""


class RechargeOrderChargebackNotAllowed(ValueError):
    """充值订单当前状态不允许记录拒付。"""


@dataclass(frozen=True, slots=True)
class RechargeOrderSubmission:
    """一次由登录用户发起的充值订单创建请求。"""

    user_id: str
    account_space_id: str
    package_version_id: str
    payment_provider: str
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DirectRechargeOrderSubmission:
    """一次按全局换算比例固化金额与额度的普通充值请求。"""

    user_id: str
    account_space_id: str
    payment_cny: str
    credits: str
    payment_provider: str
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentSuccess:
    """经过渠道验签的支付成功通知。"""

    order_id: str
    payment_provider: str
    provider_event_id: str
    paid_payment_cny: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentChargeback:
    """经过渠道验签的整笔支付拒付通知。"""

    order_id: str
    payment_provider: str
    provider_event_id: str
    charged_back_payment_cny: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RechargeOrder:
    """固化充值包金额与到账额度的订单快照。"""

    order_id: str
    user_id: str
    account_space_id: str
    package_version_id: str | None
    package_code: str
    payment_cny: str
    credits: str
    payment_provider: str
    idempotency_key: str
    status: RechargeOrderStatus
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    payment_reference: str = ""
    recharge_posting_id: str = ""
    charged_back_at: datetime | None = None
    chargeback_reference: str = ""

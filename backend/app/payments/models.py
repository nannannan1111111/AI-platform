"""支付途径、管理设置与易支付交互的公开领域模型。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PaymentMethod:
    """充值下单时可选择的支付途径显示信息。"""

    payment_provider: str
    display_name: str


class InvalidPaymentSettings(ValueError):
    """支付设置不完整或不安全。"""


class PaymentGatewayUnavailable(RuntimeError):
    """支付网关未启用或当前配置不可用。"""


class UnsupportedPaymentMethod(ValueError):
    """请求的支付方式未由管理员开放。"""


class InvalidPaymentNotification(ValueError):
    """易支付通知缺少必需字段、验签失败或与当前配置不匹配。"""


@dataclass(frozen=True, slots=True)
class PaymentSettingsSnapshot:
    """不包含商户密钥的管理员支付设置投影。"""

    configured: bool
    enabled: bool
    gateway_url: str
    public_base_url: str
    merchant_id: str
    merchant_key_configured: bool
    methods: tuple[PaymentMethod, ...]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaymentSettingsUpdate:
    """易支付兼容网关的完整非密钥设置与可选密钥轮换。"""

    enabled: bool
    gateway_url: str
    public_base_url: str
    merchant_id: str
    merchant_key: str = ""
    methods: tuple[PaymentMethod, ...] = ()


@dataclass(frozen=True, slots=True)
class RechargeRateSnapshot:
    """普通充值的全局人民币到额度换算设置。"""

    credits_per_cny: str
    preset_payment_cny: tuple[str, ...]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class RechargeQuote:
    """按当前全局比例计算并固化的普通充值报价。"""

    payment_cny: str
    credits: str
    credits_per_cny: str


@dataclass(frozen=True, slots=True)
class PaymentCheckout:
    """用户浏览器向易支付网关提交的已签名表单。"""

    action_url: str
    method: str
    parameters: dict[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedPaymentNotification:
    """已通过易支付 MD5 签名与商户身份校验的通知。"""

    order_id: str
    payment_provider: str
    provider_event_id: str
    paid_payment_cny: str
    trade_status: str

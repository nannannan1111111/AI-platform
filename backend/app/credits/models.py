"""充值包与额度账务的公开结果模型。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


class UnknownRechargePackageVersion(LookupError):
    """充值包版本不存在。"""


class InvalidAmount(ValueError):
    """人民币或额度金额不是受支持的正数精度。"""


class InvalidEffectiveTime(ValueError):
    """充值包生效时间缺少时区或早于发布时间。"""


class InvalidAuditReference(ValueError):
    """额度账务引用为空或超出存储上限。"""


class InvalidReversalReason(ValueError):
    """冲销原因为空或超出存储上限。"""


class UnknownAccountSpace(LookupError):
    """个人账户空间不存在。"""


class UnknownCreditPosting(LookupError):
    """额度账务记录不存在。"""


class ReferenceConflict(ValueError):
    """幂等引用已经属于另一笔额度账务记录。"""


class PostingAlreadyReversed(ValueError):
    """额度账务记录已经存在反向记录。"""


class PackageVersionConflict(ValueError):
    """同一充值包在相同生效时间已经存在版本。"""


class UnknownModelPriceVersion(LookupError):
    """模型价格版本不存在。"""


class ModelPriceConflict(ValueError):
    """同一模型规格在相同生效时间已经存在价格版本。"""


class InvalidModelReferenceLimit(ValueError):
    """模型允许的参考图数量不在平台支持范围内。"""


class InsufficientCredits(ValueError):
    """个人账户空间的可用额度不足以冻结生成任务。"""


class UnknownCreditFreeze(LookupError):
    """额度冻结不存在。"""


class CreditFreezeAlreadyFinalized(ValueError):
    """额度冻结已经结算或释放。"""


@dataclass(frozen=True, slots=True)
class RechargePackageVersion:
    """一个不可改写的充值包版本。"""

    version_id: str
    package_code: str
    payment_cny: str
    credits: str
    effective_from: datetime
    published_at: datetime


@dataclass(frozen=True, slots=True)
class ModelPriceVersion:
    """一个不可改写的逻辑模型价格版本。"""

    version_id: str
    logical_model: str
    output_spec: str
    credits_per_result: str
    effective_from: datetime
    published_at: datetime
    max_reference_images: int = 3


@dataclass(frozen=True, slots=True)
class CreditPosting:
    """一次不可改写的额度账务记录。"""

    posting_id: str
    account_space_id: str
    kind: Literal["recharge", "admin_grant", "reversal", "freeze", "settlement", "release"]
    delta_available_credits: str
    available_credits_after: str
    package_version_id: str | None
    reference: str
    reverses_posting_id: str | None
    reason: str | None
    occurred_at: datetime
    delta_frozen_credits: str = "0.0000"
    frozen_credits_after: str = "0.0000"
    model_price_version_id: str | None = None
    generation_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CreditStatement:
    """个人账户空间的当前额度与完整账务记录。"""

    available_credits: str
    frozen_credits: str
    entries: tuple[CreditPosting, ...]


@dataclass(frozen=True, slots=True)
class CreditStatementPage:
    """个人账户空间的余额与一页倒序账务记录。"""

    available_credits: str
    frozen_credits: str
    entries: tuple[CreditPosting, ...]
    page: int
    page_size: int
    total_entries: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class CreditFreeze:
    """一次冻结中的生成任务额度。"""

    freeze_id: str
    account_space_id: str
    task_reference: str
    model_price_version_id: str
    logical_model: str
    output_spec: str
    quantity: int
    unit_price: str
    frozen_credits: str
    available_credits_after: str
    frozen_credits_after: str
    occurred_at: datetime

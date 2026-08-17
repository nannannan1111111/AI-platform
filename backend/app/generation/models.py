"""SaaS 生成任务公开模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GenerationTaskStatus(StrEnum):
    """SaaS 生成任务生命周期状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """返回任务是否已经结束。"""
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class GenerationTaskAlreadyExists(ValueError):
    """任务引用已经属于参数不同的任务。"""


class GenerationTaskNotFound(LookupError):
    """任务不存在或不属于请求的个人账户空间。"""


class GenerationConcurrencyLimit(ValueError):
    """个人账户空间的排队及生成中图片名额已达到上限。"""


class GenerationGlobalCapacityLimit(ValueError):
    """全站排队及生成中图片名额已达到管理员设置的上限。"""


class InvalidGenerationRequest(ValueError):
    """生成任务请求快照不完整或包含不支持的成品参数。"""


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    """生成任务提交时固化的用户可选成品参数。"""

    aspect_ratio: str
    quality: str = "auto"
    size: str = ""
    resolution_tier: str = ""
    output_format: str = ""
    operation: str = "auto"
    input_fidelity: str = "auto"


@dataclass(frozen=True, slots=True)
class GenerationSubmission:
    """一次 SaaS 生成任务提交。"""

    user_id: str
    account_space_id: str
    canvas_id: str | None
    task_id: str
    logical_model: str
    output_spec: str
    quantity: int
    prompt: str
    params: GenerationParameters
    submitted_at: datetime
    reference_media_ids: tuple[str, ...] = ()
    mask_media_id: str = ""
    selected_route_id: str = ""
    route_selection_reason: str = ""


@dataclass(frozen=True, slots=True)
class GenerationTask:
    """返回给调用方的归属明确的任务快照。"""

    task_id: str
    user_id: str
    account_space_id: str
    canvas_id: str | None
    logical_model: str
    output_spec: str
    quantity: int
    prompt: str
    params: GenerationParameters
    credit_freeze_id: str
    model_price_version_id: str
    frozen_credits: str
    status: GenerationTaskStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    reference_media_ids: tuple[str, ...] = ()
    mask_media_id: str = ""
    selected_route_id: str = ""
    route_selection_reason: str = ""
    provider_task_id: str = ""
    delivered_quantity: int | None = None
    error: str = ""
    outcome_reference: str = ""


@dataclass(frozen=True, slots=True)
class GenerationActivitySummary:
    """Administrator-safe aggregate of one account's generation activity."""

    total_tasks: int
    succeeded_tasks: int
    failed_tasks: int
    consumed_credit_units: int


@dataclass(frozen=True, slots=True)
class GenerationStarted:
    """上游已接受任务的生命周期 transition。"""

    provider_task_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationDispatchStarted:
    """Worker 已占用用户执行名额，即将向 Provider 发送请求。"""

    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationSucceeded:
    """任务成功并交付部分或全部结果的 transition。"""

    delivered_quantity: int
    outcome_reference: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationFailed:
    """任务失败并释放冻结额度的 transition。"""

    reason: str
    outcome_reference: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationCancelled:
    """任务取消并释放冻结额度的 transition。"""

    reason: str
    outcome_reference: str
    occurred_at: datetime


type GenerationTransition = (
    GenerationDispatchStarted
    | GenerationStarted
    | GenerationSucceeded
    | GenerationFailed
    | GenerationCancelled
)

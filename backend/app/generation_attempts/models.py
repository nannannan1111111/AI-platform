"""生成尝试 Module 的公开模型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GenerationAttemptStatus(StrEnum):
    """生成尝试的提交阶段生命周期状态。"""

    CREATED = "created"
    SUBMITTING = "submitting"
    PROVIDER_PENDING = "provider_pending"
    UNKNOWN = "unknown"
    FAILED = "failed"


class GenerationAttemptConflict(ValueError):
    """生成尝试与任务固化路由或已有尝试冲突。"""


class GenerationAttemptNotFound(LookupError):
    """生成尝试不存在或不属于请求的个人账户空间。"""


@dataclass(frozen=True, slots=True)
class GenerationAttemptPreparation:
    """在外部提交前预备生成尝试的命令。"""

    account_space_id: str
    task_id: str
    route_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    """一次固定到模型路由且可安全恢复的生成尝试。"""

    attempt_id: str
    task_id: str
    attempt_no: int
    route_id: str
    provider_idempotency_key: str
    status: GenerationAttemptStatus
    created_at: datetime
    updated_at: datetime
    provider_cost_rate_id: str = ""
    provider_task_id: str = ""
    error_code: str = ""
    error: str = ""
    submitted_at: datetime | None = None
    accepted_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AttemptSubmissionStarted:
    """Provider 请求开始发送。"""

    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptAccepted:
    """Provider 明确受理请求。"""

    provider_task_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptSubmissionUnknown:
    """请求已发送但无法确认 Provider 是否受理。"""

    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptRejected:
    """Provider 明确未受理请求。"""

    error_code: str
    reason: str
    occurred_at: datetime


type GenerationAttemptTransition = (
    AttemptSubmissionStarted | AttemptAccepted | AttemptSubmissionUnknown | AttemptRejected
)

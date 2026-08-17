"""生成尝试 Module 的内部 API 来源提交 seam。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.generation_results import GenerationImageContent


@dataclass(frozen=True, slots=True)
class ProviderReferenceImage:
    """An account-checked reference image ready for the provider wire protocol."""

    filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ProviderGenerationRequest:
    """提交给已固化模型路由的去凭据生成请求。"""

    route_id: str
    provider_idempotency_key: str
    prompt: str
    aspect_ratio: str
    quantity: int
    output_spec: str
    quality: str = "auto"
    size: str = ""
    resolution_tier: str = ""
    output_format: str = ""
    operation: str = "auto"
    input_fidelity: str = "auto"
    reference_images: tuple[ProviderReferenceImage, ...] = ()
    mask: ProviderReferenceImage | None = None
    on_image: Callable[[GenerationImageContent], None] | None = None
    should_continue: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class ProviderSubmissionAccepted:
    """API 来源明确受理生成请求。"""

    provider_task_id: str


@dataclass(frozen=True, slots=True)
class ProviderSubmissionCompleted:
    """Provider returned final image bytes in the submission response."""

    provider_task_id: str
    images: tuple[GenerationImageContent, ...]


@dataclass(frozen=True, slots=True)
class ProviderSubmissionDeliveryFailed:
    """Provider accepted the request, but no final image could be delivered."""

    provider_task_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderSubmissionRejected:
    """API 来源明确拒绝生成请求。"""

    error_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderSubmissionUnknown:
    """无法确认 API 来源是否受理生成请求。"""

    reason: str


@dataclass(frozen=True, slots=True)
class ProviderGenerationResolutionRequest:
    """用于核实未知提交且不触发新生成的稳定身份。"""

    route_id: str
    provider_idempotency_key: str
    provider_task_id: str


@dataclass(frozen=True, slots=True)
class ProviderResolutionAccepted:
    """API 来源确认原提交已经受理。"""

    provider_task_id: str


@dataclass(frozen=True, slots=True)
class ProviderResolutionRejected:
    """API 来源确认原提交没有被受理。"""

    error_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderResolutionUnknown:
    """API 来源仍无法确认原提交是否受理。"""

    reason: str


class ProviderGenerationResolutions(Protocol):
    """核实一次状态未知的原提交，不创建新的上游生成。"""

    def resolve(
        self, request: ProviderGenerationResolutionRequest
    ) -> ProviderResolutionAccepted | ProviderResolutionRejected | ProviderResolutionUnknown:
        """返回原提交的可确认结果。"""


class ProviderGenerationSubmissions(Protocol):
    """向已选择的 API 来源提交一次固化生成请求。"""

    def submit(
        self, request: ProviderGenerationRequest
    ) -> (
        ProviderSubmissionAccepted
        | ProviderSubmissionCompleted
        | ProviderSubmissionDeliveryFailed
        | ProviderSubmissionRejected
        | ProviderSubmissionUnknown
    ):
        """返回 API 来源对提交请求的明确结果。"""

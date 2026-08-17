"""账户公开 Interface 的 FastAPI Adapter。"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import struct
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import PurePosixPath
from tempfile import TemporaryFile
from typing import Annotated
from urllib.parse import parse_qsl, unquote

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.account_generation_limits import AccountGenerationLimits
from app.accounts import (
    AccountAccess,
    AccountDirectory,
    CurrentUser,
    EmailAlreadyRegistered,
    EmailDeliveryFailed,
    EmailVerificationUnavailable,
    InvalidCredentials,
    InvalidEmail,
    InvalidEmailVerification,
    InvalidPasswordReset,
    InvalidSession,
    PasswordResetUnavailable,
    RegisteredUser,
    WeakPassword,
)
from app.assets import (
    InvalidPersonalAsset,
    PersonalAssetConflict,
    PersonalAssetNotFound,
    PersonalAssetRename,
    PersonalAssets,
    PersonalAssetSave,
)
from app.auth_abuse import (
    AuthAbusePolicies,
    AuthAbuseProtection,
    AuthAction,
    ClientIpResolver,
    RateLimitBackendUnavailable,
    RateLimitSubject,
)
from app.canvases import (
    CanvasCreation,
    CanvasDeletion,
    Canvases,
    CanvasNotFound,
    CanvasSave,
    CanvasVersionConflict,
    InvalidCanvas,
)
from app.credits import (
    CreditAccounting,
    InsufficientCredits,
    InvalidAmount,
    InvalidAuditReference,
    InvalidEffectiveTime,
    InvalidModelReferenceLimit,
    InvalidReversalReason,
    ModelPriceConflict,
    ModelPrices,
    PackageVersionConflict,
    RechargePackages,
    ReferenceConflict,
    UnknownModelPriceVersion,
    UnknownRechargePackageVersion,
)
from app.email_settings import EmailSettings, EmailSettingsUpdate, InvalidEmailSettings
from app.generation import (
    GenerationCancelled,
    GenerationConcurrencyLimit,
    GenerationGlobalCapacityLimit,
    GenerationParameters,
    GenerationSubmission,
    GenerationTask,
    GenerationTaskAlreadyExists,
    GenerationTaskNotFound,
    GenerationTasks,
    GenerationTaskStatus,
    InvalidGenerationRequest,
    is_generation_timeout,
)
from app.generation_attempts import (
    GenerationAttemptNotFound,
    GenerationAttemptReconciliations,
    GenerationAttemptSubmissions,
)
from app.http.security import HttpSecuritySettings, install_http_security
from app.media import (
    MAX_STORAGE_ALLOWANCE_BYTES,
    CanvasMediaUpload,
    GeneratedMedia,
    GeneratedMediaNotDeletable,
    GeneratedMediaNotFound,
    GeneratedMediaNotRetainable,
    GeneratedMediaRecord,
    GeneratedMediaState,
    InvalidGeneratedMedia,
    MediaContentStore,
    MediaObjectDeletionFailed,
    MediaObjectPromotionFailed,
    StorageAllowanceExceeded,
    StorageAllowances,
)
from app.media._validation import validated_canvas_image_mime
from app.model_routing import (
    ApiProviderNotFound,
    ImageResponseMode,
    InvalidModelRoute,
    InvalidProviderConfiguration,
    InvalidRoutingPolicy,
    ModelRouteConflict,
    ModelRouteCreation,
    ModelRouteNotFound,
    ModelRouteUpdate,
    ModelRouting,
    NoAvailableModelRoute,
    ProviderCodeConflict,
    ProviderCreation,
    ProviderHasRoutes,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthNotFound,
    RouteProbeUnavailable,
    RoutingMode,
    RoutingPolicyUpdate,
)
from app.observability import METRICS, RequestObservabilityMiddleware, metrics_response
from app.orders import (
    DirectRechargeOrderSubmission,
    PaymentAmountMismatch,
    PaymentChargeback,
    PaymentEventConflict,
    PaymentProviderMismatch,
    PaymentSuccess,
    RechargeOrderAlreadyExists,
    RechargeOrderChargebackNotAllowed,
    RechargeOrderChargebacks,
    RechargeOrderNotFound,
    RechargeOrderPaymentAlreadyFinalized,
    RechargeOrders,
    RechargeOrderSubmission,
)
from app.payments import (
    EpayPayments,
    InvalidPaymentNotification,
    InvalidPaymentSettings,
    PaymentGatewayUnavailable,
    PaymentMethod,
    PaymentMethods,
    PaymentSettingsUpdate,
    UnsupportedPaymentMethod,
)
from app.platform_content import InvalidPlatformContent, PlatformContentSettings, PlatformContentUpdate
from app.prompt_assets import (
    InvalidPromptAsset,
    PromptAssetNotFound,
    PromptAssets,
    PromptCategoryCreate,
    PromptItemSave,
    PromptLibraryCreate,
)
from app.provider_costs import (
    InvalidProviderCostRate,
    ProviderCostRate,
    ProviderCostRateConflict,
    ProviderCostRateNotFound,
    ProviderCostRates,
    ProviderCostRouteNotFound,
    ProviderCostSummaries,
)
from app.reference_media import (
    InvalidReferenceMedia,
    ReferenceMedia,
    ReferenceMediaExpired,
    ReferenceMediaNotFound,
    ReferenceMediaOrigin,
    ReferenceMediaRecord,
    ReferenceMediaUpload,
)
from app.runninghub_capabilities import (
    InvalidRunningHubCapability,
    RunningHubCapabilities,
    RunningHubCapabilityInput,
    RunningHubCapabilityNotFound,
    RunningHubCapabilityPublication,
    RunningHubCapabilityUpdate,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubUserPriceConflict,
    RunningHubUserPricePublication,
)
from app.user_llm import (
    InvalidUserLLMProvider,
    UserLLMCompletion,
    UserLLMProvider,
    UserLLMProviderNotFound,
    UserLLMProviders,
    UserLLMProviderSave,
    UserLLMUpstreamError,
)
from app.webui import mount_web_ui
from app.worker_capacity import InvalidWorkerCapacity, WorkerCapacitySettings

_WORKFLOW_MAX_JSON_BYTES = 10 * 1024 * 1024
_WORKFLOW_MAX_MANIFEST_BYTES = 1024 * 1024
_WORKFLOW_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_WORKFLOW_MAX_COMPRESSION_RATIO = 200
_WORKFLOW_COMPRESSION_RATIO_MIN_BYTES = 1024 * 1024
_LOG = logging.getLogger(__name__)
_REGISTRATION_ACCEPTED = {"detail": "注册请求已受理；若邮箱可用，验证邮件将发送到该地址"}
_PASSWORD_RESET_ACCEPTED = {"detail": "密码重置请求已受理；若邮箱可用，重置邮件将发送到该地址"}
_WORKFLOW_MEDIA_URL_KEYS = {
    "url",
    "path",
    "src",
    "uri",
    "output",
    "output_url",
    "outputUrl",
    "preview_url",
    "previewUrl",
}


class _Credentials(BaseModel):
    """邮箱密码请求。"""

    email: str
    password: str


class _VerificationToken(BaseModel):
    """邮箱验证请求。"""

    token: str


class _PasswordChange(BaseModel):
    """已登录用户修改密码请求。"""

    current_password: str
    new_password: str


class _PasswordResetRequest(BaseModel):
    """不泄露账户是否存在的密码重置邮件请求。"""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)


class _PasswordReset(BaseModel):
    """一次性令牌与新密码。"""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=512)
    new_password: str


class _GenerationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aspect_ratio: str
    quality: str = "auto"
    size: str = ""
    resolution_tier: str = ""
    output_format: str = ""
    operation: str = "auto"
    input_fidelity: str = "auto"


class _GenerationSubmission(BaseModel):
    task_id: str
    canvas_id: str | None = None
    logical_model: str
    output_spec: str
    quantity: int = Field(ge=1, le=5)
    prompt: str
    params: _GenerationParameters
    reference_media_ids: list[str] = Field(default_factory=list, max_length=16)
    mask_media_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def reject_user_supplied_routing(cls, value: object) -> object:
        """用户只能选择逻辑模型和规格，不能指定平台来源配置。"""
        if isinstance(value, Mapping):
            forbidden = {"api_key", "base_url", "endpoint", "provider", "provider_id", "route_id"}
            if any(str(key).casefold() in forbidden for key in value):
                raise ValueError("用户不能指定 API 来源、地址、凭据或模型路由")
        return value


class _AdminGenerationCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_space_id: str = Field(min_length=1, max_length=255)


class _MediaArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_ids: list[str] = Field(min_length=1, max_length=100)


class _CanvasCreation(BaseModel):
    title: str = "未命名画布"
    kind: str = "classic"


class _CanvasSave(BaseModel):
    expected_version: int
    document: dict[str, object]
    title: str | None = None


class _PersonalAssetSave(BaseModel):
    media_id: str
    display_name: str
    idempotency_key: str


class _PersonalAssetRename(BaseModel):
    display_name: str


class _PromptName(BaseModel):
    name: str


class _PromptCategoryNew(_PromptName):
    library_id: str = "system"


class _PromptItemBody(BaseModel):
    library_id: str = "system"
    name: str
    positive: str
    negative: str = ""
    category: str = "custom"
    scene: str = ""
    params: dict[str, object] = Field(default_factory=dict)


class _PromptItemsDelete(BaseModel):
    ids: list[str]


class _UserLLMProviderBody(BaseModel):
    code: str
    display_name: str
    base_url: str
    api_key: str = ""
    models: list[str]
    enabled: bool = True


class _CanvasLLMBody(BaseModel):
    message: str = Field(max_length=100_000)
    model: str = ""
    provider: str = ""
    system_prompt: str = Field(default="", max_length=100_000)
    messages: list[dict[str, object]] = Field(default_factory=list, max_length=200)
    images: list[str] = Field(default_factory=list, max_length=20)
    videos: list[str] = Field(default_factory=list, max_length=20)


class _RechargePackagePublication(BaseModel):
    package_code: str
    payment_cny: str
    credits: str
    effective_from: datetime


class _ModelPricePublication(BaseModel):
    logical_model: str
    output_spec: str
    credits_per_result: str
    effective_from: datetime
    max_reference_images: int = Field(default=3, ge=0, le=16)


class _ProviderCostRatePublication(BaseModel):
    route_id: str
    variant_code: str
    provider_currency: str
    cost_per_image_micros: int
    effective_from: datetime


class _ProviderCostRateReplacement(BaseModel):
    provider_currency: str
    cost_per_image_yuan: str


def _yuan_to_cents(value: str) -> int:
    try:
        yuan = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise InvalidProviderCostRate("Provider 单张成本无效") from exc
    cents = yuan * 100
    if not yuan.is_finite() or yuan < 0 or cents != cents.to_integral_value():
        raise InvalidProviderCostRate("Provider 单张成本必须是非负金额，且最多保留两位小数")
    return int(cents)


class _RunningHubCapabilityPublication(BaseModel):
    name: str
    workflow_id: str
    input_capabilities: tuple[RunningHubInputCapability, ...]
    available: bool


class _RunningHubCapabilityUpdate(BaseModel):
    name: str | None = None
    workflow_id: str | None = None
    input_capabilities: tuple[RunningHubInputCapability, ...] | None = None
    available: bool | None = None


class _RunningHubCapabilityInput(BaseModel):
    input_key: str
    label: str
    kind: RunningHubInputCapability
    required: bool


class _RunningHubInputSchemaPublication(BaseModel):
    inputs: tuple[_RunningHubCapabilityInput, ...]


class _RunningHubUserPricePublication(BaseModel):
    credits_per_run: str
    effective_from: datetime


class _StorageAllowanceUpdate(BaseModel):
    limit_bytes: int = Field(ge=0, le=MAX_STORAGE_ALLOWANCE_BYTES)


class _AccountGenerationLimitUpdate(BaseModel):
    execution_concurrency: int = Field(ge=1, le=20)


class _WorkerCapacityUpdate(BaseModel):
    enabled_workers: int = Field(ge=1, le=64)
    concurrency_per_worker: int = Field(ge=1, le=50)
    global_active_image_limit: int = Field(ge=1, le=100_000)
    task_deadline_minutes: int = Field(ge=1, le=120)


class _EmailSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_base_url: str
    smtp_host: str
    smtp_port: int = Field(ge=1, le=65535)
    smtp_sender: str
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"
    smtp_timeout_seconds: float = Field(ge=1, le=120)


class _ProviderCreation(BaseModel):
    code: str
    display_name: str
    protocol: ProviderProtocol
    base_url: str
    api_key: str
    image_response_mode: ImageResponseMode = ImageResponseMode.AUTO
    concurrency_group: str = ""
    max_concurrency: int = Field(default=20, ge=1, le=1000)
    request_timeout_seconds: int = Field(default=600, ge=60, le=1800)


class _ProviderUpdate(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    image_response_mode: ImageResponseMode | None = None
    concurrency_group: str | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)
    request_timeout_seconds: int | None = Field(default=None, ge=60, le=1800)


class _ModelRouteCreation(BaseModel):
    provider_id: str
    logical_model: str
    output_spec: str
    provider_model_name: str
    compatibility_group: str
    priority: int = Field(default=100, ge=0, le=10_000)
    max_reference_images: int = Field(default=3, ge=0, le=16)


class _ModelRouteUpdate(BaseModel):
    provider_model_name: str | None = None
    compatibility_group: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None
    max_reference_images: int | None = Field(default=None, ge=0, le=16)


class _RoutingPolicyUpdate(BaseModel):
    mode: RoutingMode
    preferred_route_id: str = ""


class _RechargeOrderCreation(BaseModel):
    package_version_id: str
    payment_provider: str


class _DirectRechargeOrderCreation(BaseModel):
    payment_cny: str
    payment_provider: str


class _RechargeRateUpdate(BaseModel):
    credits_per_cny: str


class _PaymentMethodSettings(BaseModel):
    payment_provider: str
    display_name: str


class _PaymentGatewaySettingsUpdate(BaseModel):
    enabled: bool = False
    gateway_url: str
    public_base_url: str
    merchant_id: str
    merchant_key: str = ""
    methods: list[_PaymentMethodSettings]


class _AdminCreditGrant(BaseModel):
    credits: str
    reason: str


class _PaymentSuccessNotification(BaseModel):
    order_id: str
    provider_event_id: str
    paid_payment_cny: str
    occurred_at: datetime


class _PaymentChargebackNotification(BaseModel):
    order_id: str
    provider_event_id: str
    charged_back_payment_cny: str
    occurred_at: datetime


def _bearer_token(authorization: str | None) -> str:
    """从标准 Bearer 头提取不透明访问令牌。"""
    scheme, separator, token = (authorization or "").partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")
    return token


def _enforce_auth_limit(
    protection: AuthAbuseProtection | None,
    action: AuthAction,
    subjects: tuple[RateLimitSubject, ...],
) -> None:
    if protection is None:
        return
    try:
        decision = protection.consume(action, subjects)
    except RateLimitBackendUnavailable as exc:
        _LOG.error("authentication rate-limit backend unavailable", extra={"security_event": "auth_limit_error"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证保护服务暂时不可用，请稍后重试",
        ) from exc
    if decision.allowed:
        return
    _LOG.warning(
        "authentication request rate limited",
        extra={
            "security_event": "auth_rate_limited",
            "auth_action": action.value,
            "blocked_scopes": decision.blocked_scopes,
            "retry_after_seconds": decision.retry_after_seconds,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="请求过于频繁，请稍后重试",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _reset_auth_limit(
    protection: AuthAbuseProtection | None,
    action: AuthAction,
    scope: str,
    subject_value: str,
) -> None:
    if protection is None:
        return
    try:
        protection.reset(action, scope, subject_value)
    except RateLimitBackendUnavailable as exc:
        _LOG.error("authentication rate-limit backend unavailable", extra={"security_event": "auth_limit_error"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证保护服务暂时不可用，请稍后重试",
        ) from exc


def _canvas_media_ids(value: object) -> tuple[str, ...]:
    """读取画布文档显式声明的规范媒体标识字段。"""
    found: set[str] = set()

    def scan(current: object) -> None:
        if isinstance(current, Mapping):
            for key, item in current.items():
                if key in {"media_id", "mediaId"} and isinstance(item, str) and item.strip():
                    found.add(item.strip())
                scan(item)
        elif isinstance(current, list):
            for item in current:
                scan(item)

    scan(value)
    return tuple(sorted(found))


def _public_generation_task(task: GenerationTask) -> dict[str, object]:
    """Project a generation task onto the user-safe HTTP representation."""
    delivered_quantity = task.delivered_quantity or 0
    partial_delivery = task.status is GenerationTaskStatus.SUCCEEDED and delivered_quantity < task.quantity
    return {
        "task_id": task.task_id,
        "canvas_id": task.canvas_id,
        "logical_model": task.logical_model,
        "output_spec": task.output_spec,
        "quantity": task.quantity,
        "prompt": task.prompt,
        "params": {
            "aspect_ratio": task.params.aspect_ratio,
            "quality": task.params.quality,
            **({"size": task.params.size} if task.params.size else {}),
            **({"resolution_tier": task.params.resolution_tier} if task.params.resolution_tier else {}),
            **({"output_format": task.params.output_format} if task.params.output_format else {}),
            **({"operation": task.params.operation} if task.params.operation != "auto" else {}),
            **({"input_fidelity": task.params.input_fidelity} if task.params.input_fidelity != "auto" else {}),
        },
        "reference_media_count": len(task.reference_media_ids),
        "mask_media_present": bool(task.mask_media_id),
        "frozen_credits": task.frozen_credits,
        "status": task.status,
        "failure_message": (
            "生成超过管理员设置的任务时限，已按超时结束，冻结额度已退回。"
            if task.status is GenerationTaskStatus.FAILED and is_generation_timeout(task)
            else f"图片生成失败：{_safe_generation_failure(task.error)}。冻结额度已恢复。"
            if task.status is GenerationTaskStatus.FAILED
            else "任务已由平台管理员取消，冻结额度已退回。"
            if task.status is GenerationTaskStatus.CANCELLED
            else None
        ),
        "partial_delivery": partial_delivery,
        "undelivered_quantity": task.quantity - delivered_quantity if partial_delivery else 0,
        "completion_message": (
            f"上游仅完成 {delivered_quantity}/{task.quantity} 张，未返回的请求未交付。" if partial_delivery else None
        ),
        "delivered_quantity": task.delivered_quantity,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _public_generated_media(media: GeneratedMediaRecord) -> dict[str, object]:
    """Project generated media without storage or ownership internals."""
    return {
        "media_id": media.media_id,
        "task_id": media.task_id,
        "kind": media.kind,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "state": media.state,
        "created_at": media.created_at,
        "expires_at": media.expires_at,
        "retained_at": media.retained_at,
    }


def _safe_generation_failure(value: str) -> str:
    message = re.sub(r"https?://\S+", "<url>", value.strip(), flags=re.IGNORECASE)
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", message)
    message = re.sub(r"(?i)(?:api[_ -]?key|token|secret)[=: -]+\S+", "credential=<redacted>", message)
    return re.sub(r"\s+", " ", message)[:240] or "上游未返回具体原因"


def _validated_canvas_workflow(value: object) -> dict[str, object]:
    """Accept one bounded smart-canvas workflow document from an untrusted local file."""
    if not isinstance(value, Mapping):
        raise ValueError("工作流文件格式无效")
    embedded_workflow = value.get("workflow")
    source: Mapping[object, object] = embedded_workflow if isinstance(embedded_workflow, Mapping) else value
    nodes = source.get("nodes")
    connections = source.get("connections", [])
    if not isinstance(nodes, list) or not nodes or len(nodes) > 500:
        raise ValueError("工作流需要包含 1 至 500 个节点")
    if not isinstance(connections, list) or len(connections) > 2000:
        raise ValueError("工作流连线数量无效")
    return {
        "format": "infinite-smart-canvas-workflow",
        "version": 1,
        "canvas_type": "smart",
        "exported_at": value.get("exported_at"),
        "nodes": nodes,
        "connections": connections,
    }


def _workflow_media_ids(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def scan(current: object) -> None:
        if isinstance(current, Mapping):
            for key, item in current.items():
                if key in {"media_id", "mediaId"} and isinstance(item, str) and item.strip():
                    found.add(item.strip())
                scan(item)
        elif isinstance(current, list):
            for item in current:
                scan(item)

    scan(value)
    return tuple(sorted(found))


def _image_dimensions(content: bytes) -> tuple[int, int] | None:
    """Read supported image dimensions without trusting browser metadata."""
    if len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n") and content[12:16] == b"IHDR":
        width, height = struct.unpack(">II", content[16:24])
        return (width, height) if width and height else None
    if content.startswith(b"\xff\xd8\xff"):
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(content):
                break
            segment_length = int.from_bytes(content[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(content):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(content[offset + 3 : offset + 5], "big")
                width = int.from_bytes(content[offset + 5 : offset + 7], "big")
                return (width, height) if width and height else None
            offset += segment_length
    if len(content) >= 30 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        if content[12:16] == b"VP8X":
            width = 1 + int.from_bytes(content[24:27], "little")
            height = 1 + int.from_bytes(content[27:30], "little")
            return width, height
        if content[12:16] == b"VP8L" and content[20] == 0x2F:
            bits = int.from_bytes(content[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if content[12:16] == b"VP8 " and content[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", content[26:30])
            return width & 0x3FFF, height & 0x3FFF
    return None


def _rewritten_workflow_media(value: object, replacements: Mapping[str, str]) -> object:
    if isinstance(value, list):
        return [_rewritten_workflow_media(item, replacements) for item in value]
    if not isinstance(value, Mapping):
        return value
    rewritten = {key: _rewritten_workflow_media(item, replacements) for key, item in value.items()}
    old_id = value.get("media_id") or value.get("mediaId")
    if isinstance(old_id, str) and old_id in replacements:
        media_id = replacements[old_id]
        rewritten["media_id"] = media_id
        if "mediaId" in rewritten:
            rewritten["mediaId"] = media_id
        content_url = f"/api/v1/media/{media_id}/content"
        rewritten["url"] = content_url
        for key in _WORKFLOW_MEDIA_URL_KEYS:
            if key in rewritten:
                rewritten[key] = content_url
    return rewritten


def _provider_cost_rate_projection(version: ProviderCostRate) -> dict[str, object]:
    """Expose route cost versions in whole cents without leaking the legacy storage unit."""
    return {
        "version_id": version.version_id,
        "route_id": version.route_id,
        "version": version.version,
        "provider_currency": version.provider_currency,
        "cost_per_image_cents": version.cost_per_image_micros // 10_000,
        "cost_per_image_yuan": f"{version.cost_per_image_micros / 1_000_000:.6f}".rstrip("0").rstrip("."),
        "effective_from": version.effective_from,
        "published_at": version.published_at,
    }


def _public_reference_media(media: ReferenceMediaRecord) -> dict[str, object]:
    """Project reference metadata without ownership or object-store internals."""
    return {
        "media_id": media.media_id,
        "original_name": media.original_name,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "expires_at": media.expires_at.isoformat().replace("+00:00", "Z"),
        "preview_url": f"/api/v1/reference-media/{media.media_id}/content",
    }


def _model_reference_image_limit(
    model_routing: ModelRouting | None,
    logical_model: str,
    output_spec: str,
    *,
    fallback: int = 3,
) -> int:
    if model_routing is None:
        return fallback
    reader = getattr(model_routing, "reference_image_limit", None)
    return fallback if reader is None else int(reader(logical_model, output_spec))


def create_app(
    accounts: AccountAccess,
    *,
    account_directory: AccountDirectory | None = None,
    credit_accounting: CreditAccounting | None = None,
    generation_tasks: GenerationTasks | None = None,
    generation_attempt_submissions: GenerationAttemptSubmissions | None = None,
    generation_submission_deferred: bool = False,
    generation_attempt_reconciliations: GenerationAttemptReconciliations | None = None,
    generated_media: GeneratedMedia | None = None,
    media_content: MediaContentStore | None = None,
    reference_media: ReferenceMedia | None = None,
    storage_allowances: StorageAllowances | None = None,
    account_generation_limits: AccountGenerationLimits | None = None,
    personal_assets: PersonalAssets | None = None,
    prompt_assets: PromptAssets | None = None,
    user_llm_providers: UserLLMProviders | None = None,
    canvases: Canvases | None = None,
    model_prices: ModelPrices | None = None,
    recharge_packages: RechargePackages | None = None,
    recharge_orders: RechargeOrders | None = None,
    recharge_order_chargebacks: RechargeOrderChargebacks | None = None,
    payment_methods: PaymentMethods | None = None,
    epay_payments: EpayPayments | None = None,
    model_routing: ModelRouting | None = None,
    provider_cost_rates: ProviderCostRates | None = None,
    provider_cost_summaries: ProviderCostSummaries | None = None,
    runninghub_capabilities: RunningHubCapabilities | None = None,
    worker_capacity: WorkerCapacitySettings | None = None,
    email_settings: EmailSettings | None = None,
    platform_content: PlatformContentSettings | None = None,
    admin_authorizer: Callable[[str], None] | None = None,
    payment_notification_verifier: Callable[[str, str], None] | None = None,
    chargeback_notification_verifier: Callable[[str, str], None] | None = None,
    auth_abuse_protection: AuthAbuseProtection | None = None,
    auth_abuse_policies: AuthAbusePolicies | None = None,
    client_ip_resolver: ClientIpResolver | None = None,
    http_security: HttpSecuritySettings | None = None,
    metrics_token: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """创建只依赖账户 Interface 的 FastAPI 应用。"""
    app = FastAPI(title="乐云工坊 SaaS")
    install_http_security(app, http_security or HttpSecuritySettings())
    app.add_middleware(RequestObservabilityMiddleware, metrics=METRICS)
    abuse_policies = auth_abuse_policies or AuthAbusePolicies.defaults()
    resolve_client_ip = client_ip_resolver or ClientIpResolver()
    app.state.auth_abuse_protection = auth_abuse_protection

    @app.get("/healthz", include_in_schema=False)
    def health_check() -> dict[str, str]:
        """Report that the HTTP process is ready to receive requests."""
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> PlainTextResponse:
        """Expose Prometheus metrics only to the explicitly configured scraper."""
        return metrics_response(request, token=metrics_token, metrics=METRICS)

    @app.post("/api/v1/auth/register", status_code=status.HTTP_202_ACCEPTED)
    def register(credentials: _Credentials, request: Request) -> dict[str, str]:
        _enforce_auth_limit(
            auth_abuse_protection,
            AuthAction.REGISTER,
            (
                RateLimitSubject("ip", resolve_client_ip(request), abuse_policies.register_ip),
            ),
        )
        try:
            accounts.register(credentials.email, credentials.password)
        except EmailAlreadyRegistered:
            return dict(_REGISTRATION_ACCEPTED)
        except InvalidEmail as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="邮箱格式无效") from exc
        except WeakPassword as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="密码至少需要 12 个字符"
            ) from exc
        except EmailDeliveryFailed:
            _LOG.error(
                "registration verification delivery failed",
                extra={"security_event": "registration_delivery_failed"},
            )
        return dict(_REGISTRATION_ACCEPTED)

    @app.post("/api/v1/auth/login")
    def login(credentials: _Credentials, request: Request) -> dict[str, str]:
        normalized_email = credentials.email.strip().casefold()
        _enforce_auth_limit(
            auth_abuse_protection,
            AuthAction.LOGIN,
            (
                RateLimitSubject("email", normalized_email, abuse_policies.login_email),
                RateLimitSubject("ip", resolve_client_ip(request), abuse_policies.login_ip),
            ),
        )
        try:
            session = accounts.login(credentials.email, credentials.password)
        except (InvalidCredentials, KeyError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误") from exc
        _reset_auth_limit(auth_abuse_protection, AuthAction.LOGIN, "email", normalized_email)
        return {"access_token": session.access_token, "token_type": "bearer"}

    @app.get("/api/v1/auth/me")
    def current_user(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        token = _bearer_token(authorization)
        try:
            current = accounts.current_user(token)
        except InvalidSession as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
        response: dict[str, object] = asdict(current)
        if generated_media is not None:
            response["storage_allowance"] = asdict(generated_media.storage_allowance(current.account_space_id))
        return response

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(authorization: Annotated[str | None, Header()] = None) -> None:
        token = _bearer_token(authorization)
        accounts.logout(token)

    if user_llm_providers is not None:

        def llm_current(authorization: str | None) -> CurrentUser:
            try:
                return accounts.current_user(_bearer_token(authorization))
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc

        def llm_public(provider: UserLLMProvider) -> dict[str, object]:
            data: dict[str, object] = asdict(provider)
            data.pop("account_space_id", None)
            return data

        @app.get("/api/v1/llm-providers")
        def list_user_llm_providers(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            current = llm_current(authorization)
            return [llm_public(provider) for provider in user_llm_providers.list(current.account_space_id)]

        @app.post("/api/v1/llm-providers", status_code=status.HTTP_201_CREATED)
        def create_user_llm_provider(
            request: _UserLLMProviderBody,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            current = llm_current(authorization)
            try:
                return llm_public(
                    user_llm_providers.create(
                        UserLLMProviderSave(
                            account_space_id=current.account_space_id,
                            code=request.code,
                            display_name=request.display_name,
                            base_url=request.base_url,
                            models=tuple(request.models),
                            enabled=request.enabled,
                            api_key=request.api_key,
                        )
                    )
                )
            except InvalidUserLLMProvider as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

        @app.patch("/api/v1/llm-providers/{provider_id}")
        def update_user_llm_provider(
            provider_id: str,
            request: _UserLLMProviderBody,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            current = llm_current(authorization)
            try:
                return llm_public(
                    user_llm_providers.update(
                        provider_id,
                        UserLLMProviderSave(
                            account_space_id=current.account_space_id,
                            code=request.code,
                            display_name=request.display_name,
                            base_url=request.base_url,
                            models=tuple(request.models),
                            enabled=request.enabled,
                            api_key=request.api_key,
                        ),
                    )
                )
            except UserLLMProviderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM Provider 不存在") from exc
            except InvalidUserLLMProvider as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

        @app.delete("/api/v1/llm-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_user_llm_provider(
            provider_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> None:
            current = llm_current(authorization)
            try:
                user_llm_providers.delete(current.account_space_id, provider_id)
            except UserLLMProviderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM Provider 不存在") from exc

        @app.post("/api/v1/canvas-llm")
        def canvas_llm(
            request: _CanvasLLMBody,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, str]:
            current = llm_current(authorization)
            try:
                text = user_llm_providers.complete(
                    UserLLMCompletion(
                        account_space_id=current.account_space_id,
                        provider_code=request.provider,
                        model=request.model,
                        message=request.message,
                        system_prompt=request.system_prompt,
                        messages=tuple(request.messages),
                        images=tuple(request.images),
                        videos=tuple(request.videos),
                    )
                )
            except UserLLMProviderNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="请先在 LLM 设置中配置可用的 Provider"
                ) from exc
            except InvalidUserLLMProvider as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            except UserLLMUpstreamError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            return {"text": text}

    @app.post("/api/v1/auth/verify-email", status_code=status.HTTP_204_NO_CONTENT)
    def verify_email(verification: _VerificationToken) -> None:
        try:
            accounts.verify_email(verification.token)
        except InvalidEmailVerification as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="邮箱验证链接无效或已过期",
            ) from exc

    @app.post("/api/v1/auth/email-verification", status_code=status.HTTP_204_NO_CONTENT)
    def request_email_verification(authorization: Annotated[str | None, Header()] = None) -> None:
        token = _bearer_token(authorization)
        try:
            current = accounts.current_user(token)
            _enforce_auth_limit(
                auth_abuse_protection,
                AuthAction.EMAIL_VERIFICATION,
                (
                    RateLimitSubject(
                        "account",
                        current.user_id,
                        abuse_policies.email_verification_account,
                    ),
                ),
            )
            accounts.request_email_verification(token)
        except InvalidSession as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
        except EmailVerificationUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="邮箱验证服务尚未配置") from exc
        except EmailDeliveryFailed as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="验证邮件发送失败，请稍后重试") from exc

    @app.post("/api/v1/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
    def change_password(
        passwords: _PasswordChange,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        token = _bearer_token(authorization)
        try:
            accounts.change_password(token, passwords.current_password, passwords.new_password)
        except InvalidSession as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
        except InvalidCredentials as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误") from exc
        except WeakPassword as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="新密码至少需要 12 个字符"
            ) from exc

    @app.post("/api/v1/auth/password-reset", status_code=status.HTTP_202_ACCEPTED)
    def request_password_reset(
        reset: _PasswordResetRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        normalized_email = reset.email.strip().casefold()
        _enforce_auth_limit(
            auth_abuse_protection,
            AuthAction.PASSWORD_RESET,
            (
                RateLimitSubject("email", normalized_email, abuse_policies.password_reset_email),
                RateLimitSubject("ip", resolve_client_ip(request), abuse_policies.password_reset_ip),
            ),
        )
        def deliver_without_account_disclosure() -> None:
            try:
                accounts.request_password_reset(reset.email)
            except (PasswordResetUnavailable, EmailDeliveryFailed):
                _LOG.error(
                    "password reset delivery failed",
                    extra={"security_event": "password_reset_delivery_failed"},
                )

        background_tasks.add_task(deliver_without_account_disclosure)
        return dict(_PASSWORD_RESET_ACCEPTED)

    @app.post("/api/v1/auth/reset-password", status_code=status.HTTP_204_NO_CONTENT)
    def reset_password(reset: _PasswordReset) -> None:
        try:
            accounts.reset_password(reset.token, reset.new_password)
        except InvalidPasswordReset as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="密码重置链接无效或已过期",
            ) from exc
        except WeakPassword as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="新密码至少需要 12 个字符",
            ) from exc

    @app.get("/api/v1/credits/balance")
    def credit_balance(authorization: Annotated[str | None, Header()] = None) -> dict[str, str]:
        token = _bearer_token(authorization)
        try:
            return asdict(accounts.credit_balance(token))
        except InvalidSession as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc

    if platform_content is not None:

        @app.get("/api/v1/platform-content")
        def get_platform_content(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            try:
                accounts.current_user(_bearer_token(authorization))
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            snapshot = asdict(platform_content.current())
            snapshot["announcement_image_url"] = (
                "/api/v1/platform-content/announcement/image" if snapshot.pop("announcement_image_configured") else ""
            )
            snapshot["support_image_url"] = (
                "/api/v1/platform-content/support/image" if snapshot.pop("support_image_configured") else ""
            )
            return snapshot

        @app.get("/api/v1/platform-content/{kind}/image")
        def get_platform_content_image(
            kind: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            try:
                accounts.current_user(_bearer_token(authorization))
                content, mime_type = platform_content.image(kind)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容图片不存在") from exc
            return Response(content=content, media_type=mime_type, headers={"Cache-Control": "no-store"})

        if admin_authorizer is not None:

            @app.put("/api/v1/admin/platform-content")
            async def update_platform_content(
                announcement_text: Annotated[str, File()] = "",
                support_text: Annotated[str, File()] = "",
                remove_announcement_image: Annotated[bool, File()] = False,
                remove_support_image: Annotated[bool, File()] = False,
                announcement_image: Annotated[UploadFile | None, File()] = None,
                support_image: Annotated[UploadFile | None, File()] = None,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                _require_platform_admin(authorization, admin_authorizer)
                try:
                    settings = platform_content.update(
                        PlatformContentUpdate(
                            announcement_text=announcement_text,
                            support_text=support_text,
                            announcement_image=await announcement_image.read() if announcement_image else None,
                            announcement_image_mime=announcement_image.content_type or "" if announcement_image else "",
                            support_image=await support_image.read() if support_image else None,
                            support_image_mime=support_image.content_type or "" if support_image else "",
                            remove_announcement_image=remove_announcement_image,
                            remove_support_image=remove_support_image,
                        )
                    )
                except InvalidPlatformContent as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                return asdict(settings)

    if credit_accounting is not None:

        @app.get("/api/v1/credits/ledger")
        def credit_ledger(
            page: Annotated[int, Query(ge=1)] = 1,
            page_size: Annotated[int, Query(ge=1, le=100)] = 20,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                statement = credit_accounting.statement_page(
                    current.account_space_id,
                    page=page,
                    page_size=page_size,
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return asdict(statement)

    if account_directory is not None and credit_accounting is not None and admin_authorizer is not None:

        def admin_user_projection(user: RegisteredUser) -> dict[str, object]:
            statement = credit_accounting.statement(user.account_space_id)
            generation_limit = (
                account_generation_limits.current(user.account_space_id)
                if account_generation_limits is not None
                else None
            )
            return {
                **asdict(user),
                "available_credits": statement.available_credits,
                "frozen_credits": statement.frozen_credits,
                "generation_execution_concurrency": generation_limit.execution_concurrency
                if generation_limit is not None
                else 2,
            }

        @app.get("/api/v1/admin/users")
        def list_registered_users(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                admin_authorizer(token)
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            result: list[dict[str, object]] = []
            for user in account_directory.list_registered_users():
                result.append(admin_user_projection(user))
            return result

        @app.get("/api/v1/admin/users/by-email")
        def registered_user_by_email(
            email: Annotated[str, Query(min_length=3, max_length=320)],
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                return admin_user_projection(account_directory.registered_user_by_email(email))
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该邮箱用户") from exc

        if generation_tasks is not None:

            def admin_generation_task_projection(
                task: GenerationTask,
                emails_by_user_id: Mapping[str, str],
            ) -> dict[str, object]:
                return {
                    **_public_generation_task(task),
                    "user_id": task.user_id,
                    "account_space_id": task.account_space_id,
                    "user_email": emails_by_user_id.get(task.user_id, ""),
                    "started_at": task.started_at,
                }

            @app.get("/api/v1/admin/generation-tasks/active")
            def active_admin_generation_tasks(
                authorization: Annotated[str | None, Header()] = None,
            ) -> list[dict[str, object]]:
                _require_platform_admin(authorization, admin_authorizer)
                active_tasks = generation_tasks.active_across_accounts()
                emails_by_user_id: dict[str, str] = {}
                for user_id in {task.user_id for task in active_tasks}:
                    try:
                        emails_by_user_id[user_id] = account_directory.registered_user(user_id).email
                    except KeyError:
                        pass
                return [admin_generation_task_projection(task, emails_by_user_id) for task in active_tasks]

            @app.post("/api/v1/admin/generation-tasks/{task_id}/cancel")
            def cancel_admin_generation_task(
                task_id: str,
                request: _AdminGenerationCancellation,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                _require_platform_admin(authorization, admin_authorizer)
                try:
                    task = generation_tasks.get(request.account_space_id, task_id)
                    if task.status is GenerationTaskStatus.CANCELLED:
                        cancelled = task
                    elif task.status.is_terminal:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="任务已经结束，不能取消或退款",
                        )
                    else:
                        cancelled = generation_tasks.transition(
                            request.account_space_id,
                            task_id,
                            GenerationCancelled(
                                reason="平台管理员手动取消任务",
                                outcome_reference=f"cancel:{request.account_space_id}:{task_id}",
                                occurred_at=(clock or (lambda: datetime.now(UTC)))(),
                            ),
                        )
                except GenerationTaskNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="任务状态已经变化，请刷新后重试",
                    ) from exc
                try:
                    user_email = account_directory.registered_user(cancelled.user_id).email
                except KeyError:
                    user_email = ""
                return admin_generation_task_projection(cancelled, {cancelled.user_id: user_email})

            @app.get("/api/v1/admin/user-activity")
            def user_activity_statistics(
                window: Annotated[str, Query(pattern="^(7d|30d|all)$")] = "7d",
                authorization: Annotated[str | None, Header()] = None,
            ) -> list[dict[str, object]]:
                _require_platform_admin(authorization, admin_authorizer)
                now = (clock or (lambda: datetime.now(UTC)))()
                since = None if window == "all" else now - timedelta(days=7 if window == "7d" else 30)
                result: list[dict[str, object]] = []
                for user in account_directory.list_registered_users():
                    activity = generation_tasks.activity_summary(user.account_space_id, since=since)
                    statement = credit_accounting.statement(user.account_space_id)
                    whole, fraction = divmod(activity.consumed_credit_units, 10_000)
                    result.append(
                        {
                            "user_id": user.user_id,
                            "email": user.email,
                            "registered_at": user.registered_at,
                            "available_credits": statement.available_credits,
                            "consumed_credits": f"{whole}.{fraction:04d}",
                            "total_tasks": activity.total_tasks,
                            "succeeded_tasks": activity.succeeded_tasks,
                            "failed_tasks": activity.failed_tasks,
                        }
                    )
                return result

        if account_generation_limits is not None:

            @app.put("/api/v1/admin/users/{user_id}/generation-limit")
            def update_user_generation_limit(
                user_id: str,
                request: _AccountGenerationLimitUpdate,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                token = _bearer_token(authorization)
                try:
                    admin_authorizer(token)
                    target = account_directory.registered_user(user_id)
                    limit = account_generation_limits.update(
                        target.account_space_id,
                        request.execution_concurrency,
                    )
                except PermissionError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="需要平台管理员权限",
                    ) from exc
                except KeyError as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=str(exc),
                    ) from exc
                return asdict(limit)

        @app.post("/api/v1/admin/users/{user_id}/credit-grants", status_code=status.HTTP_201_CREATED)
        def grant_user_credits(
            user_id: str,
            request: _AdminCreditGrant,
            authorization: Annotated[str | None, Header()] = None,
            idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            if not idempotency_key:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="需要 Idempotency-Key")
            try:
                admin_authorizer(token)
                administrator = accounts.current_user(token)
                target = account_directory.registered_user(user_id)
                posting = credit_accounting.record_admin_grant(
                    target.account_space_id,
                    request.credits,
                    grant_reference=f"admin-grant:{administrator.user_id}:{idempotency_key}",
                    reason=request.reason,
                    occurred_at=(clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
            except ReferenceConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="人工充值幂等键冲突") from exc
            except (InvalidAmount, InvalidAuditReference, InvalidReversalReason) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="人工充值参数无效"
                ) from exc
            return asdict(posting)

        @app.get("/api/v1/admin/users/{user_id}/recharge-records")
        def user_recharge_records(
            user_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                admin_authorizer(token)
                target = account_directory.registered_user(user_id)
                statement = credit_accounting.statement(target.account_space_id)
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
            recharge_postings = {
                posting.posting_id: posting
                for posting in statement.entries
                if posting.kind in {"recharge", "admin_grant"}
            }
            reversals = {
                posting.reverses_posting_id: posting
                for posting in statement.entries
                if posting.kind == "reversal" and posting.reverses_posting_id in recharge_postings
            }
            relevant = [
                posting
                for posting in statement.entries
                if posting.posting_id in recharge_postings
                or (posting.kind == "reversal" and posting.reverses_posting_id in recharge_postings)
            ]
            result: list[dict[str, object]] = []
            for posting in sorted(relevant, key=lambda item: item.occurred_at, reverse=True):
                posting_type = {
                    "recharge": "payment_recharge",
                    "admin_grant": "admin_recharge",
                    "reversal": "reversal",
                }[posting.kind]
                result.append(
                    {
                        "posting_id": posting.posting_id,
                        "occurred_at": posting.occurred_at,
                        "type": posting_type,
                        "credits": posting.delta_available_credits,
                        "reason": posting.reason,
                        "status": "reversed" if posting.posting_id in reversals else "posted",
                    }
                )
            return result

    if email_settings is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/email-settings")
        def get_email_settings(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            return asdict(email_settings.current())

        @app.put("/api/v1/admin/email-settings")
        def update_email_settings(
            request: _EmailSettingsUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                settings = email_settings.update(
                    EmailSettingsUpdate(
                        public_base_url=request.public_base_url,
                        smtp_host=request.smtp_host,
                        smtp_port=request.smtp_port,
                        smtp_sender=request.smtp_sender,
                        smtp_username=request.smtp_username,
                        smtp_password=request.smtp_password,
                        smtp_security=request.smtp_security,
                        smtp_timeout_seconds=request.smtp_timeout_seconds,
                    )
                )
            except InvalidEmailSettings as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return asdict(settings)

    if recharge_packages is not None:

        @app.get("/api/v1/recharge-packages")
        def recharge_catalog() -> list[dict[str, object]]:
            at = (clock or (lambda: datetime.now(UTC)))()
            return [asdict(package) for package in recharge_packages.sellable_at(at)]

    if model_prices is not None:

        @app.get("/api/v1/model-prices")
        def model_price_catalog() -> list[dict[str, object]]:
            at = (clock or (lambda: datetime.now(UTC)))()
            return [asdict(version) for version in model_prices.catalog_at(at)]

    if model_prices is not None and model_routing is not None:

        @app.get("/api/v1/image-models")
        def image_model_catalog() -> dict[str, object]:
            at = (clock or (lambda: datetime.now(UTC)))()
            specifications_by_model: dict[str, list[dict[str, object]]] = {}
            for version in model_prices.catalog_at(at):
                availability = model_routing.availability(version.logical_model, version.output_spec)
                specifications_by_model.setdefault(version.logical_model, []).append(
                    {
                        "output_spec": version.output_spec,
                        "credits_per_result": version.credits_per_result,
                        "status": availability.status.value,
                        "max_reference_images": _model_reference_image_limit(
                            model_routing,
                            version.logical_model,
                            version.output_spec,
                            fallback=version.max_reference_images,
                        ),
                    }
                )
            data = [
                {"logical_model": logical_model, "output_specs": output_specs}
                for logical_model, output_specs in sorted(specifications_by_model.items())
            ]
            return {"data": data}

    if runninghub_capabilities is not None:

        @app.get("/api/v1/runninghub-capabilities")
        def runninghub_capability_catalog(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return {"data": [asdict(capability) for capability in runninghub_capabilities.catalog()]}

    if payment_methods is not None:

        @app.get("/api/v1/payment-methods")
        def payment_method_catalog() -> list[dict[str, str]]:
            return [asdict(method) for method in payment_methods.available()]

    if epay_payments is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/recharge-rate")
        def get_admin_recharge_rate(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            return asdict(epay_payments.current_recharge_rate())

        @app.put("/api/v1/admin/recharge-rate")
        def update_admin_recharge_rate(
            request: _RechargeRateUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                return asdict(epay_payments.update_recharge_rate(request.credits_per_cny))
            except InvalidPaymentSettings as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

        @app.get("/api/v1/admin/payment-settings")
        def get_payment_settings(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            return asdict(epay_payments.current())

        @app.put("/api/v1/admin/payment-settings")
        def update_payment_settings(
            request: _PaymentGatewaySettingsUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                snapshot = epay_payments.update(
                    PaymentSettingsUpdate(
                        enabled=request.enabled,
                        gateway_url=request.gateway_url,
                        public_base_url=request.public_base_url,
                        merchant_id=request.merchant_id,
                        merchant_key=request.merchant_key,
                        methods=tuple(
                            PaymentMethod(method.payment_provider, method.display_name) for method in request.methods
                        ),
                    )
                )
            except InvalidPaymentSettings as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return asdict(snapshot)

    if epay_payments is not None:

        @app.get("/api/v1/recharge-rate")
        def get_recharge_rate() -> dict[str, object]:
            """Expose the ordinary recharge ratio and fixed amount choices without gateway secrets."""
            return asdict(epay_payments.current_recharge_rate())

    if recharge_packages is not None and admin_authorizer is not None:

        @app.post("/api/v1/admin/recharge-packages", status_code=status.HTTP_201_CREATED)
        def publish_recharge_package(
            request: _RechargePackagePublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                admin_authorizer(token)
                package = recharge_packages.publish(
                    request.package_code,
                    payment_cny=request.payment_cny,
                    credits=request.credits,
                    effective_from=request.effective_from,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            except PackageVersionConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="充值包版本冲突") from exc
            except (InvalidAmount, InvalidEffectiveTime) as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="充值包参数无效") from exc
            return asdict(package)

    if model_prices is not None and admin_authorizer is not None:

        @app.post("/api/v1/admin/model-prices", status_code=status.HTTP_201_CREATED)
        def publish_model_price(
            request: _ModelPricePublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                admin_authorizer(token)
                version = model_prices.publish(
                    request.logical_model,
                    request.output_spec,
                    credits_per_result=request.credits_per_result,
                    effective_from=request.effective_from,
                    max_reference_images=request.max_reference_images,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            except ModelPriceConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型价格版本冲突") from exc
            except InvalidAmount as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="模型价格必须大于 0，且最多保留 4 位小数",
                ) from exc
            except InvalidModelReferenceLimit as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="参考图上限必须是 0–16 之间的整数",
                ) from exc
            except InvalidEffectiveTime as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="生效时间不能早于当前时间，请重新选择",
                ) from exc
            return asdict(version)

        @app.delete("/api/v1/admin/model-prices/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_model_price(
            version_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                model_prices.delete(version_id, (clock or (lambda: datetime.now(UTC)))())
            except UnknownModelPriceVersion as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型价格不存在") from exc
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    if provider_cost_summaries is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/provider-cost-summary")
        def provider_cost_summary(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            return [asdict(summary) for summary in provider_cost_summaries.summarize()]

    if provider_cost_rates is not None and admin_authorizer is not None:

        @app.put("/api/v1/admin/provider-cost-rates/{route_id}")
        def replace_provider_cost_rate(
            route_id: str,
            request: _ProviderCostRateReplacement,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                version = provider_cost_rates.replace(
                    route_id,
                    provider_currency=request.provider_currency,
                    cost_per_image_cents=_yuan_to_cents(request.cost_per_image_yuan),
                )
            except InvalidProviderCostRate as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Provider 成本参数无效"
                ) from exc
            except ProviderCostRateConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider 成本版本冲突") from exc
            except ProviderCostRouteNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型路由不存在") from exc
            return _provider_cost_rate_projection(version)

        @app.post("/api/v1/admin/provider-cost-rates", status_code=status.HTTP_201_CREATED)
        def publish_provider_cost_rate(
            request: _ProviderCostRatePublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                version = provider_cost_rates.publish(
                    request.route_id,
                    variant_code=request.variant_code,
                    provider_currency=request.provider_currency,
                    cost_per_image_micros=request.cost_per_image_micros,
                    effective_from=request.effective_from,
                )
            except InvalidProviderCostRate as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Provider 成本参数无效"
                ) from exc
            except ProviderCostRateConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider 成本版本冲突") from exc
            except ProviderCostRouteNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型路由不存在") from exc
            return asdict(version)

        @app.get("/api/v1/admin/provider-cost-rates")
        def provider_cost_rate_history(
            route_id: str,
            variant_code: str = "",
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            if not variant_code:
                return [
                    _provider_cost_rate_projection(version)
                    for version in provider_cost_rates.versions_for_route(route_id)
                ]
            return [asdict(version) for version in provider_cost_rates.versions(route_id, variant_code)]

    if runninghub_capabilities is not None and admin_authorizer is not None:

        @app.post("/api/v1/admin/runninghub-capabilities", status_code=status.HTTP_201_CREATED)
        def publish_runninghub_capability(
            request: _RunningHubCapabilityPublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                capability = runninghub_capabilities.publish(
                    RunningHubCapabilityPublication(
                        name=request.name,
                        workflow_id=request.workflow_id,
                        input_capabilities=request.input_capabilities,
                        available=request.available,
                    )
                )
            except InvalidRunningHubCapability as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 能力参数无效",
                ) from exc
            return asdict(capability)

        @app.get("/api/v1/admin/runninghub-capabilities")
        def list_runninghub_capabilities_for_administration(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            return [asdict(capability) for capability in runninghub_capabilities.list_for_administration()]

        @app.post(
            "/api/v1/admin/runninghub-capabilities/{capability_id}/input-schema-versions",
            status_code=status.HTTP_201_CREATED,
        )
        def publish_runninghub_input_schema(
            capability_id: str,
            request: _RunningHubInputSchemaPublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                version = runninghub_capabilities.publish_input_schema(
                    RunningHubInputSchemaPublication(
                        capability_id=capability_id,
                        inputs=tuple(
                            RunningHubCapabilityInput(
                                input_key=item.input_key,
                                label=item.label,
                                kind=item.kind,
                                required=item.required,
                            )
                            for item in request.inputs
                        ),
                    )
                )
            except RunningHubCapabilityNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="RunningHub 能力不存在",
                ) from exc
            except InvalidRunningHubCapability as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 输入 schema 参数无效",
                ) from exc
            return asdict(version)

        @app.get("/api/v1/admin/runninghub-capabilities/{capability_id}/input-schema-versions")
        def list_runninghub_input_schema_versions(
            capability_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                versions = runninghub_capabilities.input_schema_versions(capability_id)
            except RunningHubCapabilityNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="RunningHub 能力不存在",
                ) from exc
            except InvalidRunningHubCapability as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 能力标识无效",
                ) from exc
            return [asdict(version) for version in versions]

        @app.post(
            "/api/v1/admin/runninghub-capabilities/{capability_id}/price-versions",
            status_code=status.HTTP_201_CREATED,
        )
        def publish_runninghub_user_price(
            capability_id: str,
            request: _RunningHubUserPricePublication,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                version = runninghub_capabilities.publish_user_price(
                    RunningHubUserPricePublication(
                        capability_id=capability_id,
                        credits_per_run=request.credits_per_run,
                        effective_from=request.effective_from,
                    )
                )
            except RunningHubCapabilityNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="RunningHub 能力不存在",
                ) from exc
            except RunningHubUserPriceConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="RunningHub 用户价格版本冲突",
                ) from exc
            except (InvalidRunningHubCapability, InvalidAmount, InvalidEffectiveTime) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 用户价格参数无效",
                ) from exc
            return asdict(version)

        @app.get("/api/v1/admin/runninghub-capabilities/{capability_id}/price-versions")
        def list_runninghub_user_price_versions(
            capability_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                versions = runninghub_capabilities.user_price_versions(capability_id)
            except RunningHubCapabilityNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="RunningHub 能力不存在",
                ) from exc
            except InvalidRunningHubCapability as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 能力标识无效",
                ) from exc
            return [asdict(version) for version in versions]

        @app.patch("/api/v1/admin/runninghub-capabilities/{capability_id}")
        def update_runninghub_capability(
            capability_id: str,
            request: _RunningHubCapabilityUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                capability = runninghub_capabilities.update(
                    RunningHubCapabilityUpdate(
                        capability_id=capability_id,
                        name=request.name,
                        workflow_id=request.workflow_id,
                        input_capabilities=request.input_capabilities,
                        available=request.available,
                    )
                )
            except RunningHubCapabilityNotFound as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="RunningHub 能力不存在",
                ) from exc
            except InvalidRunningHubCapability as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="RunningHub 能力参数无效",
                ) from exc
            return asdict(capability)

    if storage_allowances is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/storage-allowance")
        def get_global_storage_allowance(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, int]:
            _require_platform_admin(authorization, admin_authorizer)
            return {"limit_bytes": storage_allowances.global_limit_bytes()}

        @app.put("/api/v1/admin/storage-allowance")
        def set_global_storage_allowance(
            request: _StorageAllowanceUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, int]:
            token = _bearer_token(authorization)
            try:
                admin_authorizer(token)
                policy = storage_allowances.set_global_limit(request.limit_bytes)
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc
            return asdict(policy)

        if account_directory is not None:

            @app.get("/api/v1/admin/users/{user_id}/storage-allowance")
            def get_user_storage_allowance(
                user_id: str,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                _require_platform_admin(authorization, admin_authorizer)
                try:
                    target = account_directory.registered_user(user_id)
                except KeyError as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
                return {
                    "account_space_id": target.account_space_id,
                    "limit_bytes": storage_allowances.limit_bytes(target.account_space_id),
                }

            @app.put("/api/v1/admin/users/{user_id}/storage-allowance")
            def set_user_storage_allowance(
                user_id: str,
                request: _StorageAllowanceUpdate,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                _require_platform_admin(authorization, admin_authorizer)
                try:
                    target = account_directory.registered_user(user_id)
                    policy = storage_allowances.set_account_limit(target.account_space_id, request.limit_bytes)
                except KeyError as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
                return asdict(policy)

    if worker_capacity is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/generation-worker-capacity")
        def get_generation_worker_capacity(
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            return {**asdict(worker_capacity.current()), **worker_capacity.usage()}

        @app.put("/api/v1/admin/generation-worker-capacity")
        def update_generation_worker_capacity(
            request: _WorkerCapacityUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                capacity = worker_capacity.update(
                    request.enabled_workers,
                    request.concurrency_per_worker,
                    request.global_active_image_limit,
                    request.task_deadline_minutes,
                )
            except InvalidWorkerCapacity as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return {**asdict(capacity), **worker_capacity.usage()}

    if model_routing is not None and admin_authorizer is not None:

        @app.get("/api/v1/admin/providers")
        def list_api_providers(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            return [asdict(provider) for provider in model_routing.list_providers()]

        @app.post("/api/v1/admin/providers", status_code=status.HTTP_201_CREATED)
        def create_api_provider(
            request: _ProviderCreation,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                provider = model_routing.create_provider(
                    ProviderCreation(
                        code=request.code,
                        display_name=request.display_name,
                        protocol=request.protocol,
                        base_url=request.base_url,
                        api_key=request.api_key,
                        image_response_mode=request.image_response_mode,
                        concurrency_group=request.concurrency_group,
                        max_concurrency=request.max_concurrency,
                        request_timeout_seconds=request.request_timeout_seconds,
                    )
                )
            except ProviderCodeConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="API 来源代码已存在") from exc
            except InvalidProviderConfiguration as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return asdict(provider)

        @app.delete("/api/v1/admin/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_api_provider(
            provider_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                model_routing.delete_provider(provider_id)
            except ApiProviderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API 来源不存在") from exc
            except ProviderHasRoutes as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.patch("/api/v1/admin/providers/{provider_id}")
        def update_api_provider(
            provider_id: str,
            request: _ProviderUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                provider = model_routing.update_provider(
                    ProviderUpdate(
                        provider_id=provider_id,
                        display_name=request.display_name,
                        base_url=request.base_url,
                        api_key=request.api_key,
                        enabled=request.enabled,
                        image_response_mode=request.image_response_mode,
                        concurrency_group=request.concurrency_group,
                        max_concurrency=request.max_concurrency,
                        request_timeout_seconds=request.request_timeout_seconds,
                    )
                )
            except ApiProviderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API 来源不存在") from exc
            except InvalidProviderConfiguration as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return asdict(provider)

        @app.get("/api/v1/admin/image-model-routes")
        def list_image_model_routes(
            logical_model: str = "",
            output_spec: str = "",
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            _require_platform_admin(authorization, admin_authorizer)
            return [
                {
                    **asdict(route),
                    "max_reference_images": _model_reference_image_limit(
                        model_routing, route.logical_model, route.output_spec
                    ),
                }
                for route in model_routing.list_routes(logical_model, output_spec)
            ]

        @app.post("/api/v1/admin/image-model-routes", status_code=status.HTTP_201_CREATED)
        def create_image_model_route(
            request: _ModelRouteCreation,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                route = model_routing.create_route(
                    ModelRouteCreation(
                        provider_id=request.provider_id,
                        logical_model=request.logical_model,
                        output_spec=request.output_spec,
                        provider_model_name=request.provider_model_name,
                        compatibility_group=request.compatibility_group,
                        priority=request.priority,
                        max_reference_images=request.max_reference_images,
                    )
                )
            except ApiProviderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API 来源不存在") from exc
            except ModelRouteConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型路由已存在") from exc
            except (InvalidModelRoute, InvalidProviderConfiguration) as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return {
                **asdict(route),
                "max_reference_images": _model_reference_image_limit(
                    model_routing, route.logical_model, route.output_spec
                ),
            }

        @app.patch("/api/v1/admin/image-model-routes/{route_id}")
        def update_image_model_route(
            route_id: str,
            request: _ModelRouteUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                route = model_routing.update_route(
                    ModelRouteUpdate(
                        route_id=route_id,
                        provider_model_name=request.provider_model_name,
                        compatibility_group=request.compatibility_group,
                        priority=request.priority,
                        enabled=request.enabled,
                        max_reference_images=request.max_reference_images,
                    )
                )
            except ModelRouteNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型路由不存在") from exc
            except ModelRouteConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型路由已存在") from exc
            except InvalidModelRoute as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            return {
                **asdict(route),
                "max_reference_images": _model_reference_image_limit(
                    model_routing, route.logical_model, route.output_spec
                ),
            }

        @app.delete("/api/v1/admin/image-model-routes/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_image_model_route(
            route_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                model_routing.delete_route(route_id)
            except ModelRouteNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型路由不存在") from exc
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.post("/api/v1/admin/image-model-routes/{route_id}/health-check")
        def check_image_model_route(
            route_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                return asdict(model_routing.check_route(route_id))
            except ModelRouteNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型路由不存在") from exc
            except RouteProbeUnavailable as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="来源检测暂不可用") from exc

        @app.get("/api/v1/admin/image-model-routes/{route_id}/health")
        def image_model_route_health(
            route_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                return asdict(model_routing.route_health(route_id))
            except RouteHealthNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="路由尚无健康检测结果") from exc

        @app.get("/api/v1/admin/image-models/{logical_model}/{output_spec}/routing-policy")
        def image_model_routing_policy(
            logical_model: str,
            output_spec: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            return asdict(model_routing.routing_policy(logical_model, output_spec))

        @app.put("/api/v1/admin/image-models/{logical_model}/{output_spec}/routing-policy")
        def set_image_model_routing_policy(
            logical_model: str,
            output_spec: str,
            request: _RoutingPolicyUpdate,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            _require_platform_admin(authorization, admin_authorizer)
            try:
                policy = model_routing.set_policy(
                    RoutingPolicyUpdate(
                        logical_model=logical_model,
                        output_spec=output_spec,
                        mode=request.mode,
                        preferred_route_id=request.preferred_route_id,
                    )
                )
            except InvalidRoutingPolicy as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
            return asdict(policy)

    if recharge_orders is not None:

        @app.get("/api/v1/recharge-orders")
        def list_recharge_orders(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                orders = recharge_orders.list(current.account_space_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [asdict(order) for order in orders]

        @app.post("/api/v1/recharge-orders", status_code=status.HTTP_201_CREATED)
        def create_recharge_order(
            request: _RechargeOrderCreation,
            authorization: Annotated[str | None, Header()] = None,
            idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            if not idempotency_key:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="需要 Idempotency-Key",
                )
            try:
                current = accounts.current_user(token)
                if epay_payments is not None and request.payment_provider not in {
                    method.payment_provider for method in epay_payments.available()
                }:
                    raise UnsupportedPaymentMethod(request.payment_provider)
                order = recharge_orders.create(
                    RechargeOrderSubmission(
                        user_id=current.user_id,
                        account_space_id=current.account_space_id,
                        package_version_id=request.package_version_id,
                        payment_provider=request.payment_provider,
                        idempotency_key=idempotency_key,
                        created_at=(clock or (lambda: datetime.now(UTC)))(),
                    )
                )
                checkout = epay_payments.create_checkout(order) if epay_payments is not None else None
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except UnsupportedPaymentMethod as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="支付方式未开放") from exc
            except PaymentGatewayUnavailable as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            except RechargeOrderAlreadyExists as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="订单幂等键冲突") from exc
            except UnknownRechargePackageVersion as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="充值包版本无效") from exc
            result = asdict(order)
            if checkout is not None:
                result["checkout"] = asdict(checkout)
            return result

        if epay_payments is not None:

            @app.post("/api/v1/recharge-orders/direct", status_code=status.HTTP_201_CREATED)
            def create_direct_recharge_order(
                request: _DirectRechargeOrderCreation,
                authorization: Annotated[str | None, Header()] = None,
                idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
            ) -> dict[str, object]:
                token = _bearer_token(authorization)
                if not idempotency_key:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="需要 Idempotency-Key",
                    )
                try:
                    current = accounts.current_user(token)
                    if request.payment_provider not in {
                        method.payment_provider for method in epay_payments.available()
                    }:
                        raise UnsupportedPaymentMethod(request.payment_provider)
                    quote = epay_payments.quote_recharge(request.payment_cny)
                    order = recharge_orders.create_direct(
                        DirectRechargeOrderSubmission(
                            user_id=current.user_id,
                            account_space_id=current.account_space_id,
                            payment_cny=quote.payment_cny,
                            credits=quote.credits,
                            payment_provider=request.payment_provider,
                            idempotency_key=idempotency_key,
                            created_at=(clock or (lambda: datetime.now(UTC)))(),
                        )
                    )
                    checkout = epay_payments.create_checkout(order)
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except UnsupportedPaymentMethod as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="支付方式未开放"
                    ) from exc
                except InvalidPaymentSettings as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                except PaymentGatewayUnavailable as exc:
                    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
                except RechargeOrderAlreadyExists as exc:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="订单幂等键冲突") from exc
                result = asdict(order)
                result["checkout"] = asdict(checkout)
                return result

        @app.get("/api/v1/recharge-orders/{order_id}")
        def get_recharge_order(
            order_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                order = recharge_orders.get(current.account_space_id, order_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except RechargeOrderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="充值订单不存在") from exc
            return asdict(order)

    if recharge_orders is not None and epay_payments is not None:

        @app.api_route(
            "/api/v1/payments/epay/notify",
            methods=["GET", "POST"],
            response_class=PlainTextResponse,
        )
        async def epay_notification(request: Request) -> PlainTextResponse:
            METRICS.inc("payment_notifications_total", labels={"provider": "epay"})
            parameters = {key: value for key, value in request.query_params.items()}
            if request.method == "POST":
                try:
                    content_length = int(request.headers.get("content-length", "0"))
                    if content_length > 64 * 1024:
                        return PlainTextResponse("fail")
                    body = await request.body()
                    if len(body) > 64 * 1024:
                        return PlainTextResponse("fail")
                    parameters.update(parse_qsl(body.decode(), keep_blank_values=True, max_num_fields=100))
                except (UnicodeDecodeError, ValueError):
                    return PlainTextResponse("fail")
            try:
                verified = epay_payments.verify_notification(parameters)
                if verified.trade_status != "TRADE_SUCCESS":
                    return PlainTextResponse("success")
                recharge_orders.record_payment_success(
                    PaymentSuccess(
                        order_id=verified.order_id,
                        payment_provider=verified.payment_provider,
                        provider_event_id=verified.provider_event_id,
                        paid_payment_cny=verified.paid_payment_cny,
                        occurred_at=(clock or (lambda: datetime.now(UTC)))(),
                    )
                )
            except (
                InvalidPaymentNotification,
                PaymentGatewayUnavailable,
                RechargeOrderNotFound,
                PaymentAmountMismatch,
                PaymentProviderMismatch,
                PaymentEventConflict,
                RechargeOrderPaymentAlreadyFinalized,
                InvalidAmount,
            ):
                return PlainTextResponse("fail")
            return PlainTextResponse("success")

    if recharge_orders is not None and payment_notification_verifier is not None:

        @app.post("/api/v1/payments/{payment_provider}/notifications")
        def payment_notification(
            payment_provider: str,
            request: _PaymentSuccessNotification,
            payment_signature: Annotated[str | None, Header(alias="X-Payment-Signature")] = None,
        ) -> dict[str, object]:
            if not payment_signature:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="支付通知验签失败")
            try:
                payment_notification_verifier(payment_provider, payment_signature)
                order = recharge_orders.record_payment_success(
                    PaymentSuccess(
                        order_id=request.order_id,
                        payment_provider=payment_provider,
                        provider_event_id=request.provider_event_id,
                        paid_payment_cny=request.paid_payment_cny,
                        occurred_at=request.occurred_at,
                    )
                )
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="支付通知验签失败") from exc
            except RechargeOrderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="充值订单不存在") from exc
            except (PaymentAmountMismatch, PaymentProviderMismatch, InvalidAmount) as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="支付通知不匹配") from exc
            except (PaymentEventConflict, RechargeOrderPaymentAlreadyFinalized) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="支付通知冲突") from exc
            return asdict(order)

    if recharge_order_chargebacks is not None and chargeback_notification_verifier is not None:

        @app.post("/api/v1/payments/{payment_provider}/chargebacks")
        def chargeback_notification(
            payment_provider: str,
            request: _PaymentChargebackNotification,
            payment_signature: Annotated[str | None, Header(alias="X-Payment-Signature")] = None,
        ) -> dict[str, object]:
            if not payment_signature:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="拒付通知验签失败")
            try:
                chargeback_notification_verifier(payment_provider, payment_signature)
                order = recharge_order_chargebacks.record_chargeback(
                    PaymentChargeback(
                        order_id=request.order_id,
                        payment_provider=payment_provider,
                        provider_event_id=request.provider_event_id,
                        charged_back_payment_cny=request.charged_back_payment_cny,
                        occurred_at=request.occurred_at,
                    )
                )
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="拒付通知验签失败") from exc
            except RechargeOrderNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="充值订单不存在") from exc
            except (PaymentAmountMismatch, PaymentProviderMismatch, InvalidAmount) as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="拒付通知不匹配") from exc
            except (PaymentEventConflict, RechargeOrderChargebackNotAllowed) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="拒付通知冲突") from exc
            return asdict(order)

    if prompt_assets is not None:

        def prompt_response(current: CurrentUser, **extra: object) -> dict[str, object]:
            return {"library": prompt_assets.projection(current.account_space_id), **extra}

        def prompt_current(authorization: str | None) -> CurrentUser:
            try:
                return accounts.current_user(_bearer_token(authorization))
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc

        def prompt_error(exc: Exception) -> HTTPException:
            if isinstance(exc, PromptAssetNotFound):
                return HTTPException(status_code=404, detail="提示词资产不存在")
            return HTTPException(status_code=422, detail=str(exc))

        @app.get("/api/v1/prompt-libraries")
        def list_prompt_libraries(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
            current = prompt_current(authorization)
            return prompt_response(current)

        @app.post("/api/v1/prompt-libraries", status_code=201)
        def create_prompt_library(
            request: _PromptName, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.create_library(PromptLibraryCreate(current.account_space_id, request.name))
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, prompt_library=item)

        @app.patch("/api/v1/prompt-libraries/{library_id}")
        def rename_prompt_library(
            library_id: str, request: _PromptName, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.rename_library(current.account_space_id, library_id, request.name)
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, prompt_library=item)

        @app.delete("/api/v1/prompt-libraries/{library_id}")
        def delete_prompt_library(
            library_id: str, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                prompt_assets.delete_library(current.account_space_id, library_id)
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current)

        @app.post("/api/v1/prompt-libraries/categories", status_code=201)
        def create_prompt_category(
            request: _PromptCategoryNew, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.create_category(
                    PromptCategoryCreate(current.account_space_id, request.library_id, request.name)
                )
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, category=item)

        @app.patch("/api/v1/prompt-libraries/categories/{category_id}")
        def rename_prompt_category(
            category_id: str, request: _PromptName, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.rename_category(current.account_space_id, category_id, request.name)
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, category=item)

        @app.delete("/api/v1/prompt-libraries/categories/{category_id}")
        def delete_prompt_category(
            category_id: str, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                prompt_assets.delete_category(current.account_space_id, category_id)
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current)

        def prompt_command(current: CurrentUser, request: _PromptItemBody) -> PromptItemSave:
            return PromptItemSave(
                current.account_space_id,
                request.library_id,
                request.name,
                request.positive,
                request.negative,
                request.category,
                request.scene,
                request.params,
            )

        @app.post("/api/v1/prompt-libraries/items", status_code=201)
        def create_prompt_item(
            request: _PromptItemBody, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.create_item(prompt_command(current, request))
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, item=item)

        @app.patch("/api/v1/prompt-libraries/items/{item_id}")
        def update_prompt_item(
            item_id: str, request: _PromptItemBody, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            try:
                item = prompt_assets.update_item(current.account_space_id, item_id, prompt_command(current, request))
            except (InvalidPromptAsset, PromptAssetNotFound) as exc:
                raise prompt_error(exc) from exc
            return prompt_response(current, item=item)

        @app.delete("/api/v1/prompt-libraries/items/{item_id}")
        def delete_prompt_item(
            item_id: str, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            prompt_assets.delete_items(current.account_space_id, (item_id,))
            return prompt_response(current)

        @app.post("/api/v1/prompt-libraries/items/delete")
        def delete_prompt_items(
            request: _PromptItemsDelete, authorization: Annotated[str | None, Header()] = None
        ) -> dict[str, object]:
            current = prompt_current(authorization)
            prompt_assets.delete_items(current.account_space_id, tuple(request.ids))
            return prompt_response(current)

    if personal_assets is not None:

        @app.post("/api/v1/personal-assets", status_code=status.HTTP_201_CREATED)
        def save_personal_asset(
            request: _PersonalAssetSave,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                asset = personal_assets.save_generated_media(
                    PersonalAssetSave(
                        user_id=current.user_id,
                        account_space_id=current.account_space_id,
                        media_id=request.media_id,
                        display_name=request.display_name,
                        idempotency_key=request.idempotency_key,
                        saved_at=(clock or (lambda: datetime.now(UTC)))(),
                    )
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GeneratedMediaNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
            except InvalidPersonalAsset as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="个人资产参数无效"
                ) from exc
            except (PersonalAssetConflict, GeneratedMediaNotRetainable, StorageAllowanceExceeded) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="个人资产当前无法保存") from exc
            except MediaObjectPromotionFailed as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用") from exc
            return asdict(asset)

        @app.get("/api/v1/personal-assets")
        def list_personal_assets(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [asdict(asset) for asset in personal_assets.list(current.account_space_id)]

        @app.patch("/api/v1/personal-assets/{asset_id}")
        def rename_personal_asset(
            asset_id: str,
            request: _PersonalAssetRename,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                asset = personal_assets.rename(
                    PersonalAssetRename(
                        account_space_id=current.account_space_id,
                        asset_id=asset_id,
                        display_name=request.display_name,
                    )
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except PersonalAssetNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="个人资产不存在") from exc
            except InvalidPersonalAsset as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="个人资产参数无效"
                ) from exc
            return asdict(asset)

        @app.delete("/api/v1/personal-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
        def remove_personal_asset(
            asset_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> None:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                personal_assets.remove(
                    current.account_space_id,
                    asset_id,
                    (clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except PersonalAssetNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="个人资产不存在") from exc
            except MediaObjectDeletionFailed as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用") from exc

    if canvases is not None:

        @app.post("/api/v1/canvases", status_code=status.HTTP_201_CREATED)
        def create_canvas(
            request: _CanvasCreation,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                canvas = canvases.create(
                    CanvasCreation(
                        user_id=current.user_id,
                        account_space_id=current.account_space_id,
                        title=request.title,
                        kind=request.kind,
                        created_at=(clock or (lambda: datetime.now(UTC)))(),
                    )
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except InvalidCanvas as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="画布参数无效") from exc
            return asdict(canvas)

        @app.get("/api/v1/canvases/{canvas_id}")
        def get_canvas(
            canvas_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                canvas = canvases.get(current.account_space_id, canvas_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except CanvasNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
            return asdict(canvas)

        if generated_media is not None and media_content is not None:

            @app.post("/api/v1/canvases/{canvas_id}/media", status_code=status.HTTP_201_CREATED)
            async def upload_canvas_media(
                canvas_id: str,
                files: Annotated[list[UploadFile], File()],
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                if not 1 <= len(files) <= 20:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="每次需要上传 1 至 20 张图片",
                    )
                token = _bearer_token(authorization)
                uploaded_at = (clock or (lambda: datetime.now(UTC)))()
                try:
                    current = accounts.current_user(token)
                    canvases.get(current.account_space_id, canvas_id)
                    uploaded = []
                    for file in files:
                        original_name = (file.filename or "canvas-image").replace("\\", "/").rsplit("/", 1)[-1]
                        media = generated_media.upload_to_canvas(
                            CanvasMediaUpload(
                                user_id=current.user_id,
                                account_space_id=current.account_space_id,
                                canvas_id=canvas_id,
                                original_name=original_name,
                                declared_mime_type=file.content_type or "application/octet-stream",
                                content=await file.read(),
                                created_at=uploaded_at,
                            )
                        )
                        uploaded.append(
                            {
                                "media_id": media.media_id,
                                "name": original_name,
                                "kind": "image",
                                "mime_type": media.mime_type,
                                "url": f"/api/v1/media/{media.media_id}/content",
                            }
                        )
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except CanvasNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
                except InvalidGeneratedMedia as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                except StorageAllowanceExceeded as exc:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="个人存储空间不足") from exc
                except (MediaObjectPromotionFailed, MediaObjectDeletionFailed) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用"
                    ) from exc
                return {"files": uploaded}

            @app.post("/api/v1/canvases/{canvas_id}/workflows/export")
            def export_canvas_workflow(
                canvas_id: str,
                request: dict[str, object],
                authorization: Annotated[str | None, Header()] = None,
            ) -> Response:
                """Download a portable local ZIP containing a smart workflow and owned image bytes."""
                token = _bearer_token(authorization)
                try:
                    current = accounts.current_user(token)
                    canvas = canvases.get(current.account_space_id, canvas_id)
                    if canvas.kind.value != "smart":
                        raise InvalidCanvas("仅智能画布支持工作流导入导出")
                    workflow = _validated_canvas_workflow(request)
                    resources: list[tuple[GeneratedMediaRecord, str]] = []
                    for media_id in _workflow_media_ids(workflow):
                        media = generated_media.get(current.account_space_id, media_id)
                        extension = {
                            "image/png": "png",
                            "image/jpeg": "jpg",
                            "image/webp": "webp",
                        }.get(media.mime_type)
                        if extension is None or media.state not in {
                            GeneratedMediaState.TEMPORARY,
                            GeneratedMediaState.PERSISTENT,
                        }:
                            raise GeneratedMediaNotFound(media_id)
                        resources.append((media, extension))
                    manifest_resources: list[dict[str, object]] = []
                    manifest: dict[str, object] = {"version": 1, "resources": manifest_resources}
                    archive_file = TemporaryFile()
                    try:
                        with zipfile.ZipFile(archive_file, "w", zipfile.ZIP_DEFLATED) as archive:
                            archive.writestr(
                                "workflow.json",
                                json.dumps(workflow, ensure_ascii=False, indent=2).encode("utf-8"),
                            )
                            for media, extension in resources:
                                content = media_content.read(media.object_key)
                                path = f"resources/{media.media_id}.{extension}"
                                archive.writestr(path, content)
                                manifest_resources.append(
                                    {
                                        "media_id": media.media_id,
                                        "path": path,
                                        "name": f"{media.media_id}.{extension}",
                                        "mime_type": media.mime_type,
                                        "size_bytes": len(content),
                                    }
                                )
                                del content
                            archive.writestr(
                                "manifest.json",
                                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                            )
                        archive_file.seek(0)
                    except Exception:
                        archive_file.close()
                        raise
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except CanvasNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
                except InvalidCanvas as exc:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
                except (GeneratedMediaNotFound, FileNotFoundError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="工作流资源不可用"
                    ) from exc
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

                def stream_archive() -> Iterator[bytes]:
                    try:
                        while chunk := archive_file.read(1024 * 1024):
                            yield chunk
                    finally:
                        archive_file.close()

                return StreamingResponse(
                    stream_archive(),
                    media_type="application/zip",
                    headers={
                        "Cache-Control": "private, no-store",
                        "Content-Disposition": 'attachment; filename="smart-canvas-workflow.zip"',
                    },
                )

            @app.post("/api/v1/canvases/{canvas_id}/workflows/import")
            async def import_canvas_workflow(
                canvas_id: str,
                file: Annotated[UploadFile, File()],
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                """Import a local JSON/ZIP workflow and retain packaged images to the destination canvas."""
                token = _bearer_token(authorization)
                imported_at = (clock or (lambda: datetime.now(UTC)))()
                try:
                    current = accounts.current_user(token)
                    canvas = canvases.get(current.account_space_id, canvas_id)
                    if canvas.kind.value != "smart":
                        raise InvalidCanvas("仅智能画布支持工作流导入导出")
                    await file.seek(0)
                    is_zip = zipfile.is_zipfile(file.file)
                    await file.seek(0)
                    packaged: list[tuple[str, str, str, zipfile.ZipInfo]] = []
                    if is_zip:
                        with zipfile.ZipFile(file.file) as archive:
                            archive_names: set[str] = set()
                            for info in archive.infolist():
                                normalized_name = info.filename.replace("\\", "/")
                                archive_path = PurePosixPath(normalized_name)
                                if (
                                    not normalized_name
                                    or "\x00" in normalized_name
                                    or archive_path.is_absolute()
                                    or ".." in archive_path.parts
                                    or (archive_path.parts and archive_path.parts[0].endswith(":"))
                                ):
                                    raise ValueError("工作流 ZIP 包含不安全路径")
                                if info.flag_bits & 1:
                                    raise ValueError("不支持加密工作流 ZIP")
                                if normalized_name in archive_names:
                                    raise ValueError("工作流 ZIP 包含重复路径")
                                archive_names.add(normalized_name)
                                if (
                                    info.file_size > _WORKFLOW_COMPRESSION_RATIO_MIN_BYTES
                                    and info.file_size / max(info.compress_size, 1) > _WORKFLOW_MAX_COMPRESSION_RATIO
                                ):
                                    raise ValueError("工作流 ZIP 压缩比异常")
                            if "workflow.json" not in archive.namelist() or "manifest.json" not in archive.namelist():
                                raise ValueError("工作流 ZIP 缺少 workflow.json 或 manifest.json")
                            workflow_info = archive.getinfo("workflow.json")
                            manifest_info = archive.getinfo("manifest.json")
                            if workflow_info.flag_bits & 1 or manifest_info.flag_bits & 1:
                                raise ValueError("不支持加密工作流 ZIP")
                            if workflow_info.file_size > _WORKFLOW_MAX_JSON_BYTES:
                                raise ValueError("工作流 JSON 超过 10MB")
                            if manifest_info.file_size > _WORKFLOW_MAX_MANIFEST_BYTES:
                                raise ValueError("工作流资源清单超过 1MB")
                            workflow = _validated_canvas_workflow(
                                json.loads(archive.read(workflow_info).decode("utf-8"))
                            )
                            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                            entries = manifest.get("resources", []) if isinstance(manifest, Mapping) else []
                            if not isinstance(entries, list):
                                raise ValueError("工作流资源清单无效")
                            referenced_ids = set(_workflow_media_ids(workflow))
                            packaged_ids: set[str] = set()
                            for entry in entries:
                                if not isinstance(entry, Mapping):
                                    raise ValueError("工作流资源清单无效")
                                old_id = str(entry.get("media_id") or "").strip()
                                path = PurePosixPath(str(entry.get("path") or ""))
                                mime_type = str(entry.get("mime_type") or "").lower()
                                if (
                                    not old_id
                                    or old_id not in referenced_ids
                                    or path.is_absolute()
                                    or ".." in path.parts
                                    or old_id in packaged_ids
                                    or path.as_posix() not in archive_names
                                ):
                                    raise ValueError("工作流资源路径无效")
                                info = archive.getinfo(path.as_posix())
                                if info.file_size > _WORKFLOW_MAX_IMAGE_BYTES:
                                    raise ValueError("单个工作流图片不能超过 50MB")
                                name = str(entry.get("name") or path.name).replace("\\", "/").rsplit("/", 1)[-1]
                                packaged.append((old_id, name, mime_type, info))
                                packaged_ids.add(old_id)

                            for media_id in referenced_ids - packaged_ids:
                                generated_media.get(current.account_space_id, media_id)

                            unique_import_sizes: dict[str, int] = {}
                            for _, name, mime_type, info in packaged:
                                content = archive.read(info)
                                upload = CanvasMediaUpload(
                                    user_id=current.user_id,
                                    account_space_id=current.account_space_id,
                                    canvas_id=canvas_id,
                                    original_name=name,
                                    declared_mime_type=mime_type,
                                    content=content,
                                    created_at=imported_at,
                                )
                                validated_canvas_image_mime(upload)
                                unique_import_sizes.setdefault(sha256(content).hexdigest(), len(content))
                                del upload, content
                            if (
                                sum(unique_import_sizes.values())
                                > generated_media.storage_allowance(current.account_space_id).available_bytes
                            ):
                                raise StorageAllowanceExceeded(current.account_space_id)

                            replacements = {}
                            for old_id, name, mime_type, info in packaged:
                                content = archive.read(info)
                                upload = CanvasMediaUpload(
                                    user_id=current.user_id,
                                    account_space_id=current.account_space_id,
                                    canvas_id=canvas_id,
                                    original_name=name,
                                    declared_mime_type=mime_type,
                                    content=content,
                                    created_at=imported_at,
                                )
                                replacements[old_id] = generated_media.upload_to_canvas(upload).media_id
                                del upload, content
                    else:
                        raw = await file.read(_WORKFLOW_MAX_JSON_BYTES + 1)
                        if not raw:
                            raise ValueError("工作流文件为空")
                        if len(raw) > _WORKFLOW_MAX_JSON_BYTES:
                            raise ValueError("工作流 JSON 超过 10MB")
                        workflow = _validated_canvas_workflow(json.loads(raw.decode("utf-8")))

                    if not is_zip:
                        for media_id in set(_workflow_media_ids(workflow)):
                            generated_media.get(current.account_space_id, media_id)
                        replacements = {}
                    return _rewritten_workflow_media(workflow, replacements)  # type: ignore[return-value]
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except CanvasNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
                except InvalidCanvas as exc:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
                except GeneratedMediaNotFound as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="JSON 中的图片不属于当前账户，请导入包含资源的 ZIP",
                    ) from exc
                except StorageAllowanceExceeded as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT, detail="个人存储空间不足，无法导入工作流"
                    ) from exc
                except InvalidGeneratedMedia as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    zipfile.BadZipFile,
                ) as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                except (MediaObjectPromotionFailed, MediaObjectDeletionFailed) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用"
                    ) from exc

        @app.get("/api/v1/canvases")
        def list_canvases(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [asdict(canvas) for canvas in canvases.list(current.account_space_id)]

        @app.put("/api/v1/canvases/{canvas_id}")
        def save_canvas(
            canvas_id: str,
            request: _CanvasSave,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                saved_at = (clock or (lambda: datetime.now(UTC)))()
                canvas = canvases.save(
                    CanvasSave(
                        account_space_id=current.account_space_id,
                        canvas_id=canvas_id,
                        expected_version=request.expected_version,
                        document=request.document,
                        title=request.title,
                        saved_at=saved_at,
                    )
                )
                if generated_media is not None:
                    try:
                        generated_media.reconcile_canvas_references(
                            current.account_space_id,
                            canvas_id,
                            _canvas_media_ids(request.document),
                            saved_at,
                        )
                    except Exception:
                        # 画布已经保存；协调失败只暂时保留旧引用，避免产生悬空媒体。
                        pass
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except CanvasNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
            except CanvasVersionConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="画布版本冲突") from exc
            except InvalidCanvas as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="画布参数无效") from exc
            return asdict(canvas)

        @app.delete("/api/v1/canvases/{canvas_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_canvas(
            canvas_id: str,
            confirm_running_tasks: Annotated[bool, Query()] = False,
            authorization: Annotated[str | None, Header()] = None,
        ) -> None:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                canvases.get(current.account_space_id, canvas_id)
                if (
                    generation_tasks is not None
                    and generation_tasks.active_for_canvas(current.account_space_id, canvas_id)
                    and not confirm_running_tasks
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "confirm_required": True,
                            "message": (
                                "画布仍有生成任务运行；永久删除后任务继续执行，但结果不再回到该画布。是否继续？"
                            ),
                        },
                    )
                deleted_at = (clock or (lambda: datetime.now(UTC)))()
                canvases.delete(
                    CanvasDeletion(
                        account_space_id=current.account_space_id,
                        canvas_id=canvas_id,
                        deleted_at=deleted_at,
                    )
                )
                if generated_media is not None:
                    try:
                        generated_media.reconcile_canvas_references(
                            current.account_space_id,
                            canvas_id,
                            (),
                            deleted_at,
                        )
                    except Exception:
                        # 画布删除已经生效；协调失败保留旧引用，避免误删仍可能被其他位置使用的媒体。
                        pass
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except CanvasNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc

    if reference_media is not None:

        def store_reference_media_upload(
            *,
            authorization: str | None,
            original_name: str,
            declared_mime_type: str,
            content: bytes,
        ) -> dict[str, object]:
            """Validate and store a reference image received through either HTTP upload shape."""
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                media = reference_media.upload(
                    ReferenceMediaUpload(
                        user_id=current.user_id,
                        account_space_id=current.account_space_id,
                        original_name=original_name,
                        declared_mime_type=declared_mime_type,
                        content=content,
                        created_at=(clock or (lambda: datetime.now(UTC)))(),
                    )
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except InvalidReferenceMedia as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                ) from exc
            return _public_reference_media(media)

        @app.post("/api/v1/reference-media", status_code=status.HTTP_201_CREATED)
        async def upload_reference_media(
            file: Annotated[UploadFile, File()],
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            """Store one authenticated temporary reference image without exposing its object key."""
            return store_reference_media_upload(
                authorization=authorization,
                original_name=file.filename or "reference-image",
                declared_mime_type=file.content_type or "application/octet-stream",
                content=await file.read(),
            )

        @app.post("/api/v1/reference-media/content", status_code=status.HTTP_201_CREATED)
        async def upload_reference_media_content(
            request: Request,
            authorization: Annotated[str | None, Header()] = None,
            encoded_file_name: Annotated[str | None, Header(alias="X-Reference-Filename")] = None,
        ) -> dict[str, object]:
            """Store raw image bytes so browsers need not rely on multipart boundary parsing."""
            declared_mime_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
            return store_reference_media_upload(
                authorization=authorization,
                original_name=unquote(encoded_file_name or "reference-image", errors="replace"),
                declared_mime_type=declared_mime_type,
                content=await request.body(),
            )

        @app.get("/api/v1/reference-media/recent")
        def recent_reference_media(
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                recent = reference_media.list_recent(
                    current.account_space_id,
                    at=(clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [_public_reference_media(media) for media in recent]

        @app.delete("/api/v1/reference-media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_reference_media(
            media_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                reference_media.delete(current.account_space_id, media_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except ReferenceMediaNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="参考图片不存在") from exc
            except MediaObjectDeletionFailed as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用") from exc
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/api/v1/reference-media/{media_id}/content")
        def reference_media_content(
            media_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            """Return reference bytes only after account ownership and expiry checks."""
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                available = reference_media.read(
                    current.account_space_id,
                    media_id,
                    at=(clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except (ReferenceMediaNotFound, ReferenceMediaExpired) as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="参考图片不存在") from exc
            return Response(
                content=available.content,
                media_type=available.media.mime_type,
                headers={"Cache-Control": "private, no-store"},
            )

    if generation_tasks is not None:

        @app.post("/api/v1/generation-tasks", status_code=status.HTTP_202_ACCEPTED)
        def submit_generation_task(
            request: _GenerationSubmission,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                submitted_at = (clock or (lambda: datetime.now(UTC)))()
                if (
                    generated_media is not None
                    and generated_media.storage_allowance(current.account_space_id).available_bytes < 10_000_000
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="个人存储空间不足 10MB，请清理后再生成",
                    )
                requested_reference_media_ids = [
                    *request.reference_media_ids,
                    *([request.mask_media_id] if request.mask_media_id else []),
                ]
                if model_prices is not None:
                    selected_model = model_prices.effective_at(request.logical_model, request.output_spec, submitted_at)
                    reference_image_limit = _model_reference_image_limit(
                        model_routing,
                        request.logical_model,
                        request.output_spec,
                        fallback=selected_model.max_reference_images,
                    )
                    if len(request.reference_media_ids) > reference_image_limit:
                        raise InvalidGenerationRequest("reference media exceeds the selected model limit")
                if requested_reference_media_ids:
                    if reference_media is None:
                        raise InvalidGenerationRequest("reference media is not configured")
                    available_reference_media = {}
                    for media_id in requested_reference_media_ids:
                        try:
                            available_reference_media[media_id] = reference_media.read(
                                current.account_space_id, media_id, at=submitted_at
                            )
                        except (ReferenceMediaNotFound, ReferenceMediaExpired) as exc:
                            raise InvalidGenerationRequest("reference media is unavailable") from exc
                    if request.mask_media_id:
                        source = available_reference_media[request.reference_media_ids[0]]
                        mask = available_reference_media[request.mask_media_id]
                        if mask.media.mime_type != "image/png":
                            raise InvalidGenerationRequest("inpaint mask must be a PNG image")
                        source_dimensions = _image_dimensions(source.content)
                        mask_dimensions = _image_dimensions(mask.content)
                        if source_dimensions is None or mask_dimensions is None or source_dimensions != mask_dimensions:
                            raise InvalidGenerationRequest("inpaint mask dimensions must match the first source image")
                selection = (
                    None if model_routing is None else model_routing.select(request.logical_model, request.output_spec)
                )
                task = generation_tasks.submit(
                    GenerationSubmission(
                        user_id=current.user_id,
                        account_space_id=current.account_space_id,
                        canvas_id=request.canvas_id,
                        task_id=request.task_id,
                        logical_model=request.logical_model,
                        output_spec=request.output_spec,
                        quantity=request.quantity,
                        prompt=request.prompt,
                        params=GenerationParameters(
                            aspect_ratio=request.params.aspect_ratio,
                            quality=request.params.quality,
                            size=request.params.size,
                            resolution_tier=request.params.resolution_tier,
                            output_format=request.params.output_format,
                            operation=request.params.operation,
                            input_fidelity=request.params.input_fidelity,
                        ),
                        submitted_at=submitted_at,
                        reference_media_ids=tuple(request.reference_media_ids),
                        mask_media_id=request.mask_media_id,
                        selected_route_id="" if selection is None else selection.route_id,
                        route_selection_reason="" if selection is None else selection.selection_reason,
                    )
                )
                if generation_attempt_submissions is not None:
                    try:
                        generation_attempt_submissions.submit(current.account_space_id, task.task_id)
                    except ProviderCostRateNotFound:
                        pass
                    task = generation_tasks.get(current.account_space_id, task.task_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GenerationTaskAlreadyExists as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务参数冲突") from exc
            except CanvasNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="画布不存在") from exc
            except GenerationConcurrencyLimit as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前账户的排队或生成中图片已达到 20 张上限，请等待现有图片完成后再提交",
                ) from exc
            except GenerationGlobalCapacityLimit as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="当前全站生图队列已满，请稍后再提交",
                ) from exc
            except InsufficientCredits as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="可用额度不足以冻结本次生成任务，请减少生成数量或充值后再提交",
                ) from exc
            except InvalidGenerationRequest as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="生成任务参数无效"
                ) from exc
            except NoAvailableModelRoute as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="模型当前没有可用来源"
                ) from exc
            return _public_generation_task(task)

        @app.get("/api/v1/generation-tasks/recent")
        def recent_account_generation_tasks(
            limit: Annotated[int, Query(ge=1, le=100)] = 20,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [
                _public_generation_task(task)
                for task in generation_tasks.recent_for_account(
                    current.account_space_id,
                    limit=limit,
                )
            ]

        if generation_attempt_submissions is not None or generation_submission_deferred:

            @app.post("/api/v1/generation-tasks/{task_id}/retry")
            def retry_generation_task(
                task_id: str,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                token = _bearer_token(authorization)
                try:
                    current = accounts.current_user(token)
                    task = generation_tasks.get(current.account_space_id, task_id)
                    if task.status is GenerationTaskStatus.RUNNING and not task.provider_task_id:
                        return _public_generation_task(task)
                    if task.status is not GenerationTaskStatus.QUEUED:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="当前任务不能重新尝试",
                        )
                    if generation_attempt_submissions is not None:
                        try:
                            generation_attempt_submissions.submit(current.account_space_id, task_id)
                        except ProviderCostRateNotFound:
                            pass
                    task = generation_tasks.get(current.account_space_id, task_id)
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except GenerationTaskNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc
                return _public_generation_task(task)

        if generation_attempt_reconciliations is not None:

            @app.post("/api/v1/generation-tasks/{task_id}/reconcile")
            def reconcile_generation_task(
                task_id: str,
                authorization: Annotated[str | None, Header()] = None,
            ) -> dict[str, object]:
                token = _bearer_token(authorization)
                try:
                    current = accounts.current_user(token)
                    generation_tasks.get(current.account_space_id, task_id)
                    try:
                        generation_attempt_reconciliations.reconcile(current.account_space_id, task_id)
                    except GenerationAttemptNotFound:
                        pass
                    task = generation_tasks.get(current.account_space_id, task_id)
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except GenerationTaskNotFound as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc
                return _public_generation_task(task)

        @app.get("/api/v1/generation-tasks/{task_id}")
        def get_generation_task(
            task_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                task = generation_tasks.get(current.account_space_id, task_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GenerationTaskNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc
            return _public_generation_task(task)

        @app.get("/api/v1/generation-tasks/{task_id}/events", include_in_schema=False)
        async def stream_generation_task(
            task_id: str,
            request: Request,
            authorization: Annotated[str | None, Header()] = None,
        ) -> StreamingResponse:
            """Keep one authenticated response open until the task reaches a terminal state."""
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                generation_tasks.get(current.account_space_id, task_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GenerationTaskNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc

            async def events() -> AsyncIterator[str]:
                previous_payload = ""
                previous_media_payload = ""
                while not await request.is_disconnected():
                    try:
                        task = await asyncio.to_thread(generation_tasks.get, current.account_space_id, task_id)
                    except GenerationTaskNotFound:
                        yield 'event: error\ndata: {"detail":"task not found"}\n\n'
                        return
                    payload = json.dumps(
                        _public_generation_task(task),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
                    )
                    emitted = payload != previous_payload
                    if emitted:
                        yield f"event: task\ndata: {payload}\n\n"
                        previous_payload = payload
                    if generated_media is not None:
                        media_payload = json.dumps(
                            [
                                _public_generated_media(item)
                                for item in await asyncio.to_thread(
                                    generated_media.list_for_task,
                                    current.account_space_id,
                                    task_id,
                                )
                            ],
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
                        )
                        if media_payload != previous_media_payload:
                            yield f"event: media\ndata: {media_payload}\n\n"
                            previous_media_payload = media_payload
                            emitted = True
                    if not emitted:
                        yield ": keep-alive\n\n"
                    if task.status.is_terminal:
                        return
                    await asyncio.sleep(1.5)

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
            )

        @app.get("/api/v1/canvases/{canvas_id}/generation-tasks/active")
        def active_generation_tasks(
            canvas_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [
                _public_generation_task(task)
                for task in generation_tasks.active_for_canvas(current.account_space_id, canvas_id)
            ]

        @app.get("/api/v1/canvases/{canvas_id}/generation-tasks/recent")
        def recent_generation_tasks(
            canvas_id: str,
            limit: Annotated[int, Query(ge=1, le=100)] = 20,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            return [
                _public_generation_task(task)
                for task in generation_tasks.recent_for_canvas(
                    current.account_space_id,
                    canvas_id,
                    limit=limit,
                )
            ]

    if generated_media is not None:
        if media_content is not None:

            @app.post("/api/v1/media/archive")
            def download_generated_media_archive(
                request: _MediaArchiveRequest,
                authorization: Annotated[str | None, Header()] = None,
            ) -> Response:
                token = _bearer_token(authorization)
                requested_at = (clock or (lambda: datetime.now(UTC)))()
                try:
                    current = accounts.current_user(token)
                    generated_media.expire_due(requested_at)
                    archive_buffer = io.BytesIO()
                    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for index, media_id in enumerate(request.media_ids, start=1):
                            media = generated_media.get(current.account_space_id, media_id)
                            if media.state not in {
                                GeneratedMediaState.TEMPORARY,
                                GeneratedMediaState.PERSISTENT,
                            } or (
                                media.state is GeneratedMediaState.TEMPORARY
                                and media.expires_at is not None
                                and media.expires_at <= requested_at
                            ):
                                raise GeneratedMediaNotFound(media_id)
                            extension = {
                                "image/png": "png",
                                "image/jpeg": "jpg",
                                "image/webp": "webp",
                            }.get(media.mime_type)
                            if extension is None:
                                raise GeneratedMediaNotFound(media_id)
                            filename = f"{media.created_at.astimezone(UTC):%Y%m%d-%H%M%S}-{index:02d}.{extension}"
                            archive.writestr(filename, media_content.read(media.object_key))
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except (GeneratedMediaNotFound, FileNotFoundError) as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
                downloaded_at = requested_at.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
                return Response(
                    content=archive_buffer.getvalue(),
                    media_type="application/zip",
                    headers={
                        "Cache-Control": "private, no-store",
                        "Content-Disposition": f'attachment; filename="generated-images-{downloaded_at}.zip"',
                        "X-Content-Type-Options": "nosniff",
                    },
                )

        @app.post("/api/v1/media/{media_id}/retain-to-canvas")
        def retain_generated_media_to_canvas(
            media_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                media = generated_media.retain_to_canvas(
                    current.account_space_id,
                    media_id,
                    (clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GeneratedMediaNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
            except (GeneratedMediaNotRetainable, StorageAllowanceExceeded) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="媒体当前无法保留") from exc
            except MediaObjectPromotionFailed as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用") from exc
            return _public_generated_media(media)

        @app.get("/api/v1/generation-tasks/{task_id}/media")
        def generation_task_media(
            task_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> list[dict[str, object]]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                media = generated_media.list_for_task(current.account_space_id, task_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GenerationTaskNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在") from exc
            return [_public_generated_media(item) for item in media]

        @app.get("/api/v1/media/{media_id}")
        def get_generated_media(
            media_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> dict[str, object]:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                media = generated_media.get(current.account_space_id, media_id)
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GeneratedMediaNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
            return _public_generated_media(media)

        @app.delete("/api/v1/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_generated_media(
            media_id: str,
            authorization: Annotated[str | None, Header()] = None,
        ) -> Response:
            token = _bearer_token(authorization)
            try:
                current = accounts.current_user(token)
                generated_media.delete(
                    current.account_space_id,
                    media_id,
                    (clock or (lambda: datetime.now(UTC)))(),
                )
            except InvalidSession as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
            except GeneratedMediaNotFound as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
            except GeneratedMediaNotDeletable as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="媒体已被资产或画布引用") from exc
            except MediaObjectDeletionFailed as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="媒体存储暂不可用") from exc
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        if media_content is not None:
            if reference_media is not None:

                @app.post(
                    "/api/v1/media/{media_id}/use-as-reference",
                    status_code=status.HTTP_201_CREATED,
                )
                def use_generated_media_as_reference(
                    media_id: str,
                    authorization: Annotated[str | None, Header()] = None,
                ) -> dict[str, object]:
                    token = _bearer_token(authorization)
                    requested_at = (clock or (lambda: datetime.now(UTC)))()
                    try:
                        current = accounts.current_user(token)
                        generated_media.expire_due(requested_at)
                        media = generated_media.get(current.account_space_id, media_id)
                        if media.state not in {
                            GeneratedMediaState.TEMPORARY,
                            GeneratedMediaState.PERSISTENT,
                        } or media.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                            raise GeneratedMediaNotFound(media_id)
                        extension = {
                            "image/png": "png",
                            "image/jpeg": "jpg",
                            "image/webp": "webp",
                        }[media.mime_type]
                        reference = reference_media.upload(
                            ReferenceMediaUpload(
                                user_id=current.user_id,
                                account_space_id=current.account_space_id,
                                original_name=f"generated-{media.created_at.astimezone(UTC):%Y%m%d-%H%M%S}.{extension}",
                                declared_mime_type=media.mime_type,
                                content=media_content.read(media.object_key),
                                created_at=requested_at,
                                origin=ReferenceMediaOrigin.CANVAS,
                            )
                        )
                    except InvalidSession as exc:
                        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                    except (GeneratedMediaNotFound, FileNotFoundError) as exc:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
                    except InvalidReferenceMedia as exc:
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
                    return _public_reference_media(reference)

            @app.get("/api/v1/media/{media_id}/content")
            def get_generated_media_content(
                media_id: str,
                authorization: Annotated[str | None, Header()] = None,
            ) -> Response:
                token = _bearer_token(authorization)
                try:
                    current = accounts.current_user(token)
                    requested_at = (clock or (lambda: datetime.now(UTC)))()
                    generated_media.expire_due(requested_at)
                    media = generated_media.get(current.account_space_id, media_id)
                    if media.state not in {
                        GeneratedMediaState.TEMPORARY,
                        GeneratedMediaState.PERSISTENT,
                    } or (
                        media.state is GeneratedMediaState.TEMPORARY
                        and media.expires_at is not None
                        and media.expires_at <= requested_at
                    ):
                        raise GeneratedMediaNotFound(media_id)
                    content = media_content.read(media.object_key)
                except InvalidSession as exc:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
                except (GeneratedMediaNotFound, FileNotFoundError) as exc:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="媒体不存在") from exc
                return Response(
                    content=content,
                    media_type=media.mime_type,
                    headers={
                        "Cache-Control": "private, no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )

    mount_web_ui(app)
    return app


def _require_platform_admin(
    authorization: str | None,
    admin_authorizer: Callable[[str], None],
) -> None:
    """统一执行平台管理员鉴权并隐藏授权实现。"""
    token = _bearer_token(authorization)
    try:
        admin_authorizer(token)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限") from exc

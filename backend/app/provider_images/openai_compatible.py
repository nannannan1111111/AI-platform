"""OpenAI-compatible direct image submission Adapter."""

import base64
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace

import httpx

from app.generation_attempts._provider import (
    ProviderGenerationRequest,
    ProviderSubmissionAccepted,
    ProviderSubmissionCompleted,
    ProviderSubmissionDeliveryFailed,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
)
from app.generation_results import GenerationImageContent
from app.model_routing._generation_targets import ProviderGenerationTargets
from app.model_routing.models import ImageResponseMode

_SIZES = {
    ("1:1", "1k"): "1024x1024",
    ("1:1", "2k"): "2048x2048",
    ("1:1", "4k"): "4096x4096",
    ("16:9", "1k"): "1280x720",
    ("16:9", "2k"): "2048x1152",
    ("16:9", "4k"): "3840x2160",
    ("9:16", "1k"): "720x1280",
    ("9:16", "2k"): "1152x2048",
    ("9:16", "4k"): "2160x3840",
}
HostResolver = Callable[[str], Iterable[str]]
_REDIRECT_LIMIT = 5
_DEFAULT_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_PARALLEL_IMAGE_REQUESTS = 5
_ASYNC_POLL_SECONDS = 5.0
_IMAGE_URL_KEYS = (
    "url",
    "image",
    "image_url",
    "imageUrl",
    "output_url",
    "outputUrl",
    "result_url",
    "resultUrl",
    "download_url",
    "downloadUrl",
    "asset_url",
    "assetUrl",
    "output",
)
_IMAGE_BASE64_KEYS = ("b64_json", "base64", "image_base64", "imageBase64")
_IMAGE_CONTAINER_KEYS = ("data", "result", "results", "images", "image", "output", "outputs", "items", "files")
_TASK_ID_KEYS = ("task_id", "taskId", "submit_id", "submitId")
_TASK_FAILED_STATUSES = {
    "FAILURE",
    "FAILED",
    "FAIL",
    "ERROR",
    "ERRORED",
    "CANCELED",
    "CANCELLED",
    "TIMEOUT",
    "REJECTED",
    "EXPIRED",
}
_TASK_PENDING_STATUSES = {
    "PENDING",
    "PROCESSING",
    "QUEUED",
    "RUNNING",
    "SUBMITTED",
    "IN_PROGRESS",
    "IN-PROGRESS",
}
_SAFE_UPSTREAM_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,246}").fullmatch
_SAFE_PROVIDER_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}").fullmatch
_LOG = logging.getLogger(__name__)


class OpenAICompatibleImageSubmissions:
    """Hide route resolution and the legacy Images response contract behind one seam."""

    def __init__(
        self,
        targets: ProviderGenerationTargets,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | float = 170.0,
        resolver: HostResolver | None = None,
        max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure route resolution, isolated HTTP transport, and download safety limits."""
        self._targets = targets
        self._transport = transport
        self._timeout = timeout
        self._resolver = resolver or _resolve_host
        self._sleeper = sleeper
        self._monotonic = monotonic
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be positive")
        self._max_image_bytes = max_image_bytes

    def submit(
        self, request: ProviderGenerationRequest
    ) -> (
        ProviderSubmissionAccepted
        | ProviderSubmissionCompleted
        | ProviderSubmissionDeliveryFailed
        | ProviderSubmissionRejected
        | ProviderSubmissionUnknown
    ):
        """Submit a direct-result image request and normalize returned bytes."""
        if not 1 <= request.quantity <= _MAX_PARALLEL_IMAGE_REQUESTS:
            return ProviderSubmissionRejected(
                error_code="invalid_quantity",
                reason="image generation quantity must be between 1 and 5",
            )
        if request.quantity > 1:
            return self._submit_parallel_images(request)
        target = self._targets.resolve(request.route_id)
        absolute_deadline = self._monotonic() + float(target.request_timeout_seconds)
        resolution_tier = request.resolution_tier.strip().lower()
        if not resolution_tier and request.output_spec.strip().lower() in {"1k", "2k", "4k"}:
            resolution_tier = request.output_spec.strip().lower()
        size = request.size
        if not size:
            try:
                size = _SIZES[(request.aspect_ratio, resolution_tier)]
            except KeyError:
                return ProviderSubmissionRejected(
                    error_code="invalid_resolution",
                    reason="a supported generation resolution is required",
                )
        body: dict[str, object] = {
            "model": target.provider_model_name,
            "prompt": request.prompt,
            "size": size,
            "n": request.quantity,
        }
        if request.quality != "auto":
            body["quality"] = request.quality
        if request.output_format:
            body["output_format"] = request.output_format
        if _uses_streaming(target.image_response_mode, target.provider_model_name):
            body["stream"] = True
        if not _is_gpt_image_2(target.provider_model_name):
            body["response_format"] = "url"
        has_edit_inputs = bool(request.reference_images or request.mask)
        expected_operation = "inpaint" if request.mask else "edit" if request.reference_images else "generate"
        if request.operation not in {"auto", expected_operation}:
            return ProviderSubmissionRejected(
                error_code="invalid_operation",
                reason="image operation does not match its edit inputs",
            )
        if has_edit_inputs and request.input_fidelity != "auto":
            body["input_fidelity"] = request.input_fidelity
        elif not has_edit_inputs and request.input_fidelity != "auto":
            return ProviderSubmissionRejected(
                error_code="invalid_input_fidelity",
                reason="input fidelity requires at least one edit image",
            )
        endpoint = f"{target.base_url.rstrip('/')}/images/{'edits' if has_edit_inputs else 'generations'}"
        images: list[GenerationImageContent] = []
        provider_accepted = False
        upstream_request_id = ""
        provider_task_id = f"direct:{request.provider_idempotency_key}"
        response_was_sse = False
        try:
            timeout = self._timeout
            if isinstance(timeout, (int, float)):
                timeout = max(float(timeout), float(target.request_timeout_seconds))
            with httpx.Client(transport=self._transport, timeout=timeout) as client:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {target.api_key}",
                }
                if has_edit_inputs:
                    files = [
                        (
                            "image",
                            (image.filename, image.content, image.mime_type),
                        )
                        for image in request.reference_images
                    ]
                    if request.mask is not None:
                        files.append(
                            (
                                "mask",
                                (
                                    request.mask.filename,
                                    request.mask.content,
                                    request.mask.mime_type,
                                ),
                            )
                        )
                    response_context = client.stream(
                        "POST",
                        endpoint,
                        headers=headers,
                        data={key: _multipart_form_value(value) for key, value in body.items()},
                        files=files,
                    )
                else:
                    response_context = client.stream(
                        "POST",
                        endpoint,
                        headers={**headers, "Content-Type": "application/json"},
                        json=body,
                    )
                with response_context as response:
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    response_was_sse = content_type == "text/event-stream"
                    upstream_request_id = _safe_upstream_request_id(
                        response.headers.get("x-request-id") or response.headers.get("x-client-request-id")
                    )
                    if upstream_request_id:
                        provider_task_id = f"request:{upstream_request_id}"
                    _LOG.info(
                        "image provider response started route_id=%s status_code=%s content_type=%s "
                        "upstream_request_id=%s",
                        request.route_id,
                        response.status_code,
                        content_type or "missing",
                        upstream_request_id or "missing",
                    )
                    if 400 <= response.status_code < 500:
                        failure = _provider_failure(response, api_key=target.api_key)
                        return ProviderSubmissionRejected(
                            error_code=failure[0],
                            reason=failure[1],
                        )
                    if response.status_code >= 500:
                        failure = _provider_failure(response, api_key=target.api_key)
                        if (
                            failure[0] != "provider_rejected"
                            or failure[1] != f"image provider rejected the request (HTTP {response.status_code})"
                        ):
                            return ProviderSubmissionRejected(error_code=failure[0], reason=failure[1])
                        return ProviderSubmissionUnknown(reason="image provider submission status is unknown")
                    response.raise_for_status()
                    provider_accepted = True
                    streamed_image_index = 0

                    def receive_stream_payload(stream_payload: dict[str, object]) -> None:
                        nonlocal streamed_image_index
                        if request.on_image is None:
                            return
                        event_images: list[GenerationImageContent] = []
                        _append_payload_images(
                            client,
                            stream_payload,
                            event_images,
                            result_reference_prefix=request.provider_idempotency_key,
                            result_reference_offset=streamed_image_index,
                            requested_format=request.output_format,
                            resolver=self._resolver,
                            max_image_bytes=self._max_image_bytes,
                            route_id=request.route_id,
                        )
                        for image in event_images:
                            request.on_image(image)
                        streamed_image_index += len(event_images)

                    payload = _response_payload(
                        response,
                        on_completed=receive_stream_payload,
                        deadline=absolute_deadline,
                        monotonic=self._monotonic,
                        should_continue=request.should_continue,
                    )
                if not _has_image_reference(payload):
                    status = _task_status(payload)
                    if status in _TASK_FAILED_STATUSES or _payload_explicitly_failed(payload):
                        failure = _failure_from_payload(payload)
                        if response_was_sse:
                            return _accepted_delivery_failure(
                                payload=payload,
                                upstream_request_id=upstream_request_id,
                                fallback_provider_task_id=provider_task_id,
                                failure=failure,
                                route_id=request.route_id,
                                status=status,
                                api_key=target.api_key,
                            )
                        return ProviderSubmissionRejected(error_code=failure[0], reason=failure[1])
                    task_id = _task_id(payload)
                    if task_id:
                        provider_task_id = _safe_provider_task_id(task_id) or provider_task_id
                        _LOG.info(
                            "image provider returned an asynchronous task; polling result without resubmission "
                            "route_id=%s response_keys=%s",
                            request.route_id,
                            _response_keys(payload),
                        )
                        payload = _poll_async_image_payload(
                            client,
                            base_url=target.base_url,
                            task_id=task_id,
                            headers=headers,
                            sleeper=self._sleeper,
                            deadline=absolute_deadline,
                            monotonic=self._monotonic,
                            should_continue=request.should_continue,
                        )
                        status = _task_status(payload)
                        if status in _TASK_FAILED_STATUSES or _payload_explicitly_failed(payload):
                            failure = _failure_from_payload(payload)
                            return _accepted_delivery_failure(
                                payload=payload,
                                upstream_request_id=upstream_request_id,
                                fallback_provider_task_id=provider_task_id,
                                failure=failure,
                                route_id=request.route_id,
                                status=status,
                                api_key=target.api_key,
                            )
                    if not _has_image_reference(payload) and _payload_still_processing(payload):
                        return ProviderSubmissionAccepted(provider_task_id=provider_task_id)
                    if not _has_image_reference(payload):
                        _LOG.warning(
                            "image provider response contained no supported image fields route_id=%s response_keys=%s",
                            request.route_id,
                            _response_keys(payload),
                        )
                _append_payload_images(
                    client,
                    payload,
                    images,
                    result_reference_prefix=request.provider_idempotency_key,
                    requested_format=request.output_format,
                    resolver=self._resolver,
                    max_image_bytes=self._max_image_bytes,
                    route_id=request.route_id,
                )
                if len(images) > request.quantity:
                    raise ValueError("image provider returned more images than requested")
        except httpx.TransportError as exc:
            _LOG.warning(
                "image provider transport failed route_id=%s error_type=%s error_detail=%s",
                request.route_id,
                type(exc).__name__,
                _safe_transport_error_detail(exc),
            )
            if provider_accepted:
                return ProviderSubmissionAccepted(provider_task_id=provider_task_id)
            return ProviderSubmissionUnknown(reason="image provider submission status is unknown")
        except (httpx.HTTPStatusError, ValueError) as exc:
            _LOG.warning(
                "image provider response was not deliverable route_id=%s error_type=%s reason=%s",
                request.route_id,
                type(exc).__name__,
                str(exc),
            )
            if provider_accepted:
                return ProviderSubmissionDeliveryFailed(
                    provider_task_id=provider_task_id,
                    reason="image provider returned no deliverable images",
                )
            return ProviderSubmissionUnknown(reason="image provider returned no valid images")
        if not images:
            return ProviderSubmissionDeliveryFailed(
                provider_task_id=provider_task_id,
                reason="image provider returned no deliverable images",
            )
        return ProviderSubmissionCompleted(
            provider_task_id=provider_task_id,
            images=tuple(images),
        )

    def _submit_parallel_images(
        self,
        request: ProviderGenerationRequest,
    ) -> (
        ProviderSubmissionAccepted
        | ProviderSubmissionCompleted
        | ProviderSubmissionDeliveryFailed
        | ProviderSubmissionRejected
        | ProviderSubmissionUnknown
    ):
        """Run one independent n=1 request at a time and deliver each completion immediately."""
        completed_images: list[GenerationImageContent] = []
        failures: list[ProviderSubmissionDeliveryFailed | ProviderSubmissionRejected | ProviderSubmissionUnknown] = []
        pending: ProviderSubmissionAccepted | None = None
        for request_index in range(1, request.quantity + 1):
            if request.should_continue is not None and not request.should_continue():
                _LOG.info(
                    "sequential image submission stopped because the generation task is no longer active "
                    "route_id=%s next_request_index=%s",
                    request.route_id,
                    request_index,
                )
                break
            try:
                result = self.submit(
                    replace(
                        request,
                        provider_idempotency_key=f"{request.provider_idempotency_key}:request-{request_index}",
                        quantity=1,
                        on_image=None,
                    )
                )
            except Exception:
                _LOG.exception(
                    "sequential image result delivery failed route_id=%s request_index=%s",
                    request.route_id,
                    request_index,
                )
                failures.append(
                    ProviderSubmissionDeliveryFailed(
                        provider_task_id=f"direct:{request.provider_idempotency_key}:request-{request_index}",
                        reason="image provider result delivery failed",
                    )
                )
                continue
            if isinstance(result, ProviderSubmissionCompleted):
                _LOG.info(
                    "sequential image request completed route_id=%s request_index=%s image_count=%s",
                    request.route_id,
                    request_index,
                    len(result.images),
                )
                for image_index, image in enumerate(result.images, start=1):
                    suffix = str(request_index) if len(result.images) == 1 else f"{request_index}-{image_index}"
                    normalized = GenerationImageContent(
                        result_reference=f"{request.provider_idempotency_key}:{suffix}",
                        mime_type=image.mime_type,
                        content=image.content,
                    )
                    completed_images.append(normalized)
                    if request.on_image is not None:
                        request.on_image(normalized)
            elif isinstance(result, ProviderSubmissionAccepted):
                pending = result
                _LOG.info(
                    "sequential image request remains accepted and pending route_id=%s request_index=%s",
                    request.route_id,
                    request_index,
                )
                break
            else:
                _LOG.warning(
                    "sequential image request did not deliver an image route_id=%s request_index=%s outcome=%s",
                    request.route_id,
                    request_index,
                    type(result).__name__,
                )
                failures.append(result)
        if completed_images and len(completed_images) < request.quantity:
            _LOG.warning(
                "sequential image submission completed partially route_id=%s requested=%s delivered=%s failures=%s",
                request.route_id,
                request.quantity,
                len(completed_images),
                len(failures),
            )
        if pending is not None:
            return pending
        if completed_images:
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=tuple(completed_images),
            )
        if failures:
            return failures[0]
        return ProviderSubmissionDeliveryFailed(
            provider_task_id=f"direct:{request.provider_idempotency_key}",
            reason="image provider returned no deliverable images",
        )


def _is_gpt_image_2(model: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "-", model.strip().lower()).strip("-")
    return (
        normalized == "gpt-image-2"
        or normalized.startswith("gpt-image-2-")
        or normalized.endswith("-gpt-image-2")
        or "-gpt-image-2-" in normalized
    )


def _should_stream_image_response(model: str) -> bool:
    """Request one long-lived response for every GPT-Image-2 generation."""
    return _is_gpt_image_2(model)


def _uses_streaming(mode: ImageResponseMode, model: str) -> bool:
    if mode is ImageResponseMode.SSE:
        return True
    if mode in {ImageResponseMode.SYNC_JSON, ImageResponseMode.ASYNC_TASK}:
        return False
    return _should_stream_image_response(model)


def _multipart_form_value(value: object) -> str:
    """Serialize booleans using JSON spelling expected by compatible image APIs."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _safe_transport_error_detail(exc: httpx.TransportError) -> str:
    detail = re.sub(r"https?://\S+", "<url>", str(exc), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", detail).strip()[:240] or "unavailable"


def _response_payload(
    response: httpx.Response,
    *,
    on_completed: Callable[[dict[str, object]], None] | None = None,
    deadline: float,
    monotonic: Callable[[], float],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, object]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "text/event-stream":
        return _stream_image_payload(
            response,
            on_completed=on_completed,
            deadline=deadline,
            monotonic=monotonic,
            should_continue=should_continue,
        )
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        _ensure_request_active(
            deadline=deadline,
            monotonic=monotonic,
            should_continue=should_continue,
        )
        chunks.append(chunk)
    _ensure_request_active(deadline=deadline, monotonic=monotonic, should_continue=should_continue)
    raw = json.loads(b"".join(chunks))
    if not isinstance(raw, dict):
        raise ValueError("image provider JSON response must be an object")
    return raw


def _ensure_request_active(
    *,
    deadline: float,
    monotonic: Callable[[], float],
    should_continue: Callable[[], bool] | None,
) -> None:
    if should_continue is not None and not should_continue():
        raise httpx.ReadTimeout("generation task is no longer active")
    if monotonic() >= deadline:
        raise httpx.ReadTimeout("image provider request exceeded its absolute timeout")


def _failure_from_payload(payload: object) -> tuple[str, str]:
    current = payload
    if isinstance(payload, dict):
        for key in ("error", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict) and any(
                name in nested for name in ("code", "error_code", "message", "msg", "detail", "reason")
            ):
                current = nested
                break
    if not isinstance(current, dict):
        return "provider_rejected", "image provider rejected the request"
    raw_code = str(current.get("code") or current.get("error_code") or "provider_rejected").strip().lower()
    code = re.sub(r"[^a-z0-9_-]+", "_", raw_code).strip("_")[:64] or "provider_rejected"
    reason = str(
        current.get("message") or current.get("msg") or current.get("detail") or current.get("reason") or ""
    ).strip()
    reason = re.sub(r"https?://\S+", "<url>", reason, flags=re.IGNORECASE)
    reason = re.sub(r"\s+", " ", reason)[:240]
    return code, reason or "image provider rejected the request"


def _safe_upstream_request_id(value: str | None) -> str:
    request_id = str(value or "").strip()
    return request_id if _SAFE_UPSTREAM_REQUEST_ID(request_id) is not None else ""


def _safe_provider_task_id(value: str | None) -> str:
    task_id = str(value or "").strip()
    return task_id if _SAFE_PROVIDER_TASK_ID(task_id) is not None else ""


def _accepted_delivery_failure(
    *,
    payload: object,
    upstream_request_id: str,
    fallback_provider_task_id: str,
    failure: tuple[str, str],
    route_id: str,
    status: str,
    api_key: str,
) -> ProviderSubmissionDeliveryFailed:
    task_id = _safe_provider_task_id(_task_id(payload))
    provider_task_id = task_id or (
        f"request:{upstream_request_id}" if upstream_request_id else fallback_provider_task_id
    )
    reference_detail = (
        f"上游任务 ID：{task_id}"
        if task_id
        else f"上游请求 ID：{upstream_request_id}"
        if upstream_request_id
        else "上游请求标识不可用"
    )
    failure_reason = failure[1].replace(api_key, "<redacted>") if api_key else failure[1]
    reason = f"上游明确失败：{failure_reason}；{reference_detail}"
    _LOG.warning(
        "image provider accepted request but reported failure route_id=%s error_code=%s status=%s "
        "provider_task_id=%s upstream_request_id=%s",
        route_id,
        failure[0],
        status or "missing",
        task_id or "missing",
        upstream_request_id or "missing",
    )
    return ProviderSubmissionDeliveryFailed(provider_task_id=provider_task_id, reason=reason)


def _payload_explicitly_failed(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    status = _task_status(payload)
    if status in _TASK_FAILED_STATUSES:
        return True
    if payload.get("success") is False or payload.get("ok") is False:
        return True
    code = payload.get("code") or payload.get("error_code")
    if code not in (None, "", 0, "0", 200, "200") and any(
        payload.get(key) for key in ("message", "msg", "detail", "reason")
    ):
        return True
    error = payload.get("error")
    return isinstance(error, dict) or (isinstance(error, str) and bool(error.strip()))


def _provider_failure(response: httpx.Response, *, api_key: str) -> tuple[str, str]:
    try:
        response.read()
        payload = response.json()
    except (ValueError, httpx.HTTPError):
        return "provider_rejected", f"image provider rejected the request (HTTP {response.status_code})"
    code, reason = _failure_from_payload(payload)
    if api_key:
        reason = reason.replace(api_key, "<redacted>")
    return code, reason


def _stream_image_payload(
    response: httpx.Response,
    *,
    on_completed: Callable[[dict[str, object]], None] | None = None,
    deadline: float,
    monotonic: Callable[[], float],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Read heartbeat-delimited SSE and collect every completed image event."""
    event_name = ""
    data_lines: list[str] = []
    fallback: dict[str, object] | None = None
    failure_payload: dict[str, object] | None = None
    completed_payloads: list[dict[str, object]] = []
    bare_lines: list[str] = []
    last_payload: dict[str, object] | None = None

    def consume_event() -> None:
        nonlocal event_name, data_lines, fallback, failure_payload, last_payload
        data = "\n".join(data_lines).strip()
        current_event = event_name.strip().lower()
        event_name = ""
        data_lines = []
        if not data or data == "[DONE]":
            return
        try:
            raw = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("image provider stream contained invalid JSON") from exc
        if not isinstance(raw, dict):
            return
        last_payload = raw
        payload_type = str(raw.get("type") or current_event).strip().lower()
        if payload_type == "error" or current_event == "error":
            failure_payload = raw
            return
        if "partial_image" in payload_type or "partial-image" in payload_type:
            return
        if _has_image_reference(raw):
            fallback = raw
            if payload_type.endswith(".completed") or current_event.endswith(".completed"):
                completed_payloads.append(raw)
                if on_completed is not None:
                    on_completed(raw)

    for line in response.iter_lines():
        _ensure_request_active(
            deadline=deadline,
            monotonic=monotonic,
            should_continue=should_continue,
        )
        if not line:
            consume_event()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not event_name and not data_lines:
            # Some compatible gateways label an ordinary JSON response as
            # text/event-stream. Preserve that payload instead of treating a
            # paid, successful response as an empty stream.
            bare_lines.append(line.strip())
    _ensure_request_active(deadline=deadline, monotonic=monotonic, should_continue=should_continue)
    consume_event()
    if completed_payloads:
        return {"data": completed_payloads}
    if fallback is not None and failure_payload is None:
        return fallback
    if failure_payload is not None:
        return failure_payload
    if bare_lines:
        try:
            raw = json.loads("\n".join(bare_lines))
        except json.JSONDecodeError as exc:
            raise ValueError("image provider stream contained invalid JSON") from exc
        if isinstance(raw, dict):
            return raw
    if last_payload is not None:
        return last_payload
    raise ValueError("image provider stream ended without a final image")


def _poll_async_image_payload(
    client: httpx.Client,
    *,
    base_url: str,
    task_id: str,
    headers: dict[str, str],
    sleeper: Callable[[float], None],
    deadline: float,
    monotonic: Callable[[], float],
    should_continue: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Poll a provider-owned task using GET only; never repeat the paid POST."""
    encoded_task_id = urllib.parse.quote(task_id, safe="")
    candidates = (
        f"{base_url.rstrip('/')}/images/tasks/{encoded_task_id}",
        f"{base_url.rstrip('/')}/tasks/{encoded_task_id}",
        f"{base_url.rstrip('/')}/images/generations/{encoded_task_id}",
    )
    selected_url = ""
    while True:
        _ensure_request_active(deadline=deadline, monotonic=monotonic, should_continue=should_continue)
        urls = (selected_url,) if selected_url else candidates
        for url in urls:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in {404, 405} and not selected_url:
                    continue
                if response.status_code >= 500:
                    selected_url = url
                    break
                response.raise_for_status()
                selected_url = url
                payload = _response_payload(
                    response,
                    deadline=deadline,
                    monotonic=monotonic,
                    should_continue=should_continue,
                )
                if (
                    _has_image_reference(payload)
                    or _task_status(payload) in _TASK_FAILED_STATUSES
                    or _payload_explicitly_failed(payload)
                ):
                    return payload
                break
        remaining = deadline - monotonic()
        if remaining <= 0:
            _ensure_request_active(deadline=deadline, monotonic=monotonic, should_continue=should_continue)
        sleeper(min(_ASYNC_POLL_SECONDS, remaining))


def _base64_images(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key in _IMAGE_BASE64_KEYS:
            yield from _string_values(value.get(key))
        for key in _IMAGE_CONTAINER_KEYS:
            if key in value:
                yield from _base64_images(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _base64_images(item)


def _image_urls(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key in _IMAGE_URL_KEYS:
            yield from _string_values(value.get(key))
        for key in _IMAGE_CONTAINER_KEYS:
            if key in value:
                yield from _image_urls(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _image_urls(item)


def _string_values(value: object) -> Iterator[str]:
    if isinstance(value, str) and value.strip():
        yield value.strip()
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                yield item.strip()


def _has_image_reference(payload: object) -> bool:
    return next(_base64_images(payload), None) is not None or next(_image_urls(payload), None) is not None


def _task_id(value: object, depth: int = 0) -> str:
    if depth > 8 or not isinstance(value, dict):
        return ""
    for key in _TASK_ID_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, (str, int)):
            text = str(candidate).strip()
            if text and len(text) <= 255:
                return text
    candidate_id = value.get("id")
    if isinstance(candidate_id, str) and candidate_id.lower().startswith("task") and len(candidate_id) <= 255:
        return candidate_id.strip()
    for key in _IMAGE_CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, list):
            for item in nested:
                found = _task_id(item, depth + 1)
                if found:
                    return found
        else:
            found = _task_id(nested, depth + 1)
            if found:
                return found
    return ""


def _task_status(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    data = value.get("data")
    current = data if isinstance(data, dict) else value
    return str(current.get("status") or current.get("task_status") or "").strip().upper()


def _payload_still_processing(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if _task_status(value) in _TASK_PENDING_STATUSES:
        return True
    event_type = str(value.get("type") or value.get("event") or "").strip().upper()
    return any(
        event_type.endswith(suffix)
        for suffix in (".PENDING", ".PROCESSING", ".QUEUED", ".RUNNING", ".SUBMITTED", ".IN_PROGRESS")
    )


def _response_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(str(key)[:64] for key in value)[:16])


def _decode_base64_image(value: str) -> bytes:
    encoded = value.strip()
    if encoded.lower().startswith("data:image/"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ValueError("invalid image data URL")
    compact = re.sub(r"\s+", "", encoded)
    try:
        return base64.b64decode(compact, validate=True)
    except ValueError:
        padded = compact.replace("-", "+").replace("_", "/")
        padded += "=" * (-len(padded) % 4)
        return base64.b64decode(padded, validate=True)


def _append_payload_images(
    client: httpx.Client,
    payload: object,
    images: list[GenerationImageContent],
    *,
    result_reference_prefix: str,
    result_reference_offset: int = 0,
    requested_format: str,
    resolver: HostResolver,
    max_image_bytes: int,
    route_id: str,
) -> None:
    seen: set[tuple[str, str]] = set()
    for encoded in _base64_images(payload):
        identity = ("base64", encoded)
        if identity in seen:
            continue
        seen.add(identity)
        content = _decode_base64_image(encoded)
        _log_requested_format_mismatch(requested_format, content, route_id=route_id)
        images.append(
            GenerationImageContent(
                f"{result_reference_prefix}:{result_reference_offset + len(images) + 1}",
                _image_mime_type(content),
                content,
            )
        )
    for url in _image_urls(payload):
        identity = ("url", url)
        if identity in seen:
            continue
        seen.add(identity)
        content = (
            _decode_base64_image(url)
            if url.lower().startswith("data:image/")
            else _download_image(client, url, resolver=resolver, max_bytes=max_image_bytes)
        )
        _log_requested_format_mismatch(requested_format, content, route_id=route_id)
        images.append(
            GenerationImageContent(
                f"{result_reference_prefix}:{result_reference_offset + len(images) + 1}",
                _image_mime_type(content),
                content,
            )
        )


def _image_mime_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported image signature")


def _log_requested_format_mismatch(output_format: str, content: bytes, *, route_id: str) -> None:
    expected_mime_type = {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(output_format)
    actual_mime_type = _image_mime_type(content)
    if expected_mime_type is not None and actual_mime_type != expected_mime_type:
        _LOG.warning(
            "image provider ignored the requested output format route_id=%s requested=%s actual=%s",
            route_id,
            expected_mime_type,
            actual_mime_type,
        )


def _validate_public_https_url(url: str, *, resolver: HostResolver) -> str:
    text = str(url or "").strip()
    if not text or any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("invalid image URL")
    try:
        parsed = urllib.parse.urlsplit(text)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid image URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid image URL")
    try:
        addresses = tuple(resolver(hostname))
        if not addresses or not all(ipaddress.ip_address(address.split("%", 1)[0]).is_global for address in addresses):
            raise ValueError("invalid image URL")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid image URL") from exc
    return text


def _download_image(
    client: httpx.Client,
    url: str,
    *,
    resolver: HostResolver,
    max_bytes: int,
) -> bytes:
    current_url = url
    for _redirect in range(_REDIRECT_LIMIT + 1):
        safe_url = _validate_public_https_url(current_url, resolver=resolver)
        with client.stream("GET", safe_url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("invalid image redirect")
                current_url = urllib.parse.urljoin(safe_url, location)
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("image exceeds byte limit")
                chunks.append(chunk)
            content = b"".join(chunks)
            declared_mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            actual_mime = _image_mime_type(content)
            normalized_declared_mime = "image/jpeg" if declared_mime == "image/jpg" else declared_mime
            if normalized_declared_mime not in {"", "application/octet-stream", "binary/octet-stream", actual_mime}:
                raise ValueError("image MIME type does not match its signature")
            return content
    raise ValueError("too many image redirects")


def _resolve_host(hostname: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))

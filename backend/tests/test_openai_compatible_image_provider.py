import base64
import json
from unittest.mock import patch

import httpx

from app.generation_attempts._provider import (
    ProviderGenerationRequest,
    ProviderReferenceImage,
    ProviderSubmissionAccepted,
    ProviderSubmissionCompleted,
    ProviderSubmissionDeliveryFailed,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
)
from app.generation_results import GenerationImageContent
from app.model_routing import (
    ImageResponseMode,
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProviderCreation,
    ProviderProtocol,
)
from app.provider_images import OpenAICompatibleImageSubmissions
from app.provider_images.openai_compatible import _uses_streaming

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\xff\xd9"
_WEBP_BYTES = b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00"


def test_image2_alias_uses_long_lived_response_mode() -> None:
    assert _uses_streaming(ImageResponseMode.AUTO, "image2") is True
    assert _uses_streaming(ImageResponseMode.AUTO, "image-2-pro") is True
    assert _uses_streaming(ImageResponseMode.AUTO, "image21") is False


def test_sync_json_mode_does_not_request_sse_for_gpt_image_2() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-sync", "route-sync")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="sync-images",
            display_name="Sync images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
            image_response_mode=ImageResponseMode.SYNC_JSON,
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="1k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/1k/v1",
        )
    )
    requested_body: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_body.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode()}]})

    with patch("app.provider_images.openai_compatible._LOG.info") as log_info:
        result = OpenAICompatibleImageSubmissions(
            routing,
            transport=httpx.MockTransport(upstream),
        ).submit(
            ProviderGenerationRequest(
                route_id=route.route_id,
                provider_idempotency_key="attempt-sync",
                prompt="a paper-cut fox",
                aspect_ratio="1:1",
                quantity=1,
                output_spec="1k",
            )
        )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert "stream" not in requested_body
    normalized_calls = [call for call in log_info.call_args_list if "response normalized" in call.args[0]]
    assert len(normalized_calls) == 1
    message, *args = normalized_calls[0].args
    diagnostic = message % tuple(args)
    assert "image provider response normalized" in diagnostic
    assert "image_count=1" in diagnostic
    assert "provider-secret" not in diagnostic
    assert "a paper-cut fox" not in diagnostic
    assert "api.example.invalid" not in diagnostic


def test_sse_mode_requests_stream_for_non_gpt_model() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-sse", "route-sse")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="sse-images",
            display_name="SSE images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
            image_response_mode=ImageResponseMode.SSE,
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="custom-image",
            output_spec="1k",
            provider_model_name="custom-image",
            compatibility_group="custom-image/1k/v1",
        )
    )
    requested_body: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_body.update(json.loads(request.content))
        event = json.dumps({"type": "image_generation.completed", "b64_json": base64.b64encode(_PNG_BYTES).decode()})
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=f"data: {event}\n\ndata: [DONE]\n\n"
        )

    result = OpenAICompatibleImageSubmissions(routing, transport=httpx.MockTransport(upstream)).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-sse",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert requested_body["stream"] is True


def test_sse_error_after_http_200_is_accepted_delivery_failure_with_request_id() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-sse-error", "route-sse-error")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="sse-error-images",
            display_name="SSE error images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
            image_response_mode=ImageResponseMode.SSE,
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    requests: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        event = json.dumps({"type": "error", "error": {"message": "Upstream request failed"}})
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/event-stream",
                "x-request-id": "9cd5e26f-69b5-4e18-abfd-9af3e708b5a0",
            },
            content=f"event: error\ndata: {event}\n\n",
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-sse-error",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
        )
    )

    assert result == ProviderSubmissionDeliveryFailed(
        provider_task_id="request:9cd5e26f-69b5-4e18-abfd-9af3e708b5a0",
        reason=("上游明确失败：Upstream request failed；上游请求 ID：9cd5e26f-69b5-4e18-abfd-9af3e708b5a0"),
    )
    assert requests == [("POST", "https://api.example.invalid/v1/images/generations")]
    assert "provider-secret" not in repr(result)
    assert "api.example.invalid" not in repr(result)


def test_openai_compatible_submission_passes_explicit_openai_image_size_and_quality() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.example.invalid/v1/images/generations"
        assert json.loads(request.content) == {
            "model": "gpt-image-2",
            "prompt": "wide landscape",
            "size": "2048x1152",
            "n": 1,
            "stream": True,
            "quality": "high",
            "output_format": "webp",
        }
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_WEBP_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-explicit-size",
            prompt="wide landscape",
            aspect_ratio="16:9",
            quantity=1,
            output_spec="4k",
            quality="high",
            size="2048x1152",
            output_format="webp",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)


def test_custom_route_spec_uses_the_user_selected_resolution_instead_of_forcing_clarity() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="模型2通用版",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/model-2/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["size"] == "2048x1152"
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-custom-spec",
            prompt="wide landscape",
            aspect_ratio="16:9",
            quantity=1,
            output_spec="模型2通用版",
            resolution_tier="2k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)


def test_openai_compatible_submission_sends_reference_images_and_a_separate_mask_to_edits() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="1k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.example.invalid/v1/images/edits"
        assert request.headers["Authorization"] == "Bearer provider-secret"
        assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
        body = request.content
        for name, value in {
            "model": b"gpt-image-2",
            "prompt": b"keep the reference composition",
            "size": b"1024x1024",
            "n": b"1",
            "quality": b"high",
            "output_format": b"jpeg",
            "input_fidelity": b"high",
            "stream": b"true",
        }.items():
            assert f'name="{name}"'.encode() in body
            assert value in body
        assert body.count(b'name="image"') == 2
        assert body.count(b'name="mask"') == 1
        assert b'filename="first.png"' in body
        assert b'filename="second.jpg"' in body
        assert b'filename="selection.png"' in body
        assert _PNG_BYTES in body
        assert _JPEG_BYTES in body
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_JPEG_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-edit",
            prompt="keep the reference composition",
            aspect_ratio="1:1",
            quantity=2,
            output_spec="1k",
            quality="high",
            output_format="jpeg",
            operation="inpaint",
            input_fidelity="high",
            reference_images=(
                ProviderReferenceImage(filename="first.png", mime_type="image/png", content=_PNG_BYTES),
                ProviderReferenceImage(filename="second.jpg", mime_type="image/jpeg", content=_JPEG_BYTES),
            ),
            mask=ProviderReferenceImage(
                filename="selection.png",
                mime_type="image/png",
                content=_PNG_BYTES,
            ),
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert [image.content for image in result.images] == [_JPEG_BYTES, _JPEG_BYTES]


def test_openai_compatible_submission_maps_the_legacy_request_and_base64_result() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.example.invalid/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer provider-secret"
        assert "provider-secret" not in repr(request)
        assert json.loads(request.content) == {
            "model": "gpt-image-2",
            "prompt": "a paper-cut fox",
            "size": "3840x2160",
            "n": 1,
            "stream": True,
        }
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-1",
            prompt="a paper-cut fox",
            aspect_ratio="16:9",
            quantity=1,
            output_spec="4k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert result.provider_task_id == "direct:attempt-key-1"
    assert [(image.result_reference, image.mime_type, image.content) for image in result.images] == [
        ("attempt-key-1:1", "image/png", _PNG_BYTES)
    ]


def test_openai_compatible_submission_uses_one_n1_request_per_requested_image() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="2k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/2k/v1",
        )
    )
    requests: list[dict[str, object]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"result": {"images": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]}},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-2",
            prompt="five paper-cut foxes",
            aspect_ratio="9:16",
            quantity=5,
            output_spec="2k",
        )
    )

    assert len(requests) == 5
    assert requests == [
        {
            "model": "upstream-image-1",
            "prompt": "five paper-cut foxes",
            "size": "1152x2048",
            "response_format": "url",
            "n": 1,
        }
        for _ in range(5)
    ]
    assert sorted(image.result_reference for image in result.images) == [
        f"attempt-key-2:{index}" for index in range(1, 6)
    ]


def test_openai_compatible_submission_rejects_more_than_five_requests_before_posting() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="2k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/2k/v1",
        )
    )
    request_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-too-many",
            prompt="six images",
            aspect_ratio="1:1",
            quantity=6,
            output_spec="2k",
        )
    )

    assert result == ProviderSubmissionRejected(
        error_code="invalid_quantity",
        reason="image generation quantity must be between 1 and 5",
    )
    assert request_count == 0


def test_openai_compatible_submission_detects_jpeg_content_from_its_signature() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_JPEG_BYTES).decode("ascii")}]},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-jpeg",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result.images[0].mime_type == "image/jpeg"
    assert result.images[0].content == _JPEG_BYTES


def test_openai_compatible_submission_detects_webp_content_from_its_signature() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_WEBP_BYTES).decode("ascii")}]},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-webp",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result.images[0].mime_type == "image/webp"
    assert result.images[0].content == _WEBP_BYTES


def test_openai_compatible_submission_maps_an_explicit_upstream_rejection_without_leaking_details() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": {"message": "provider-secret rejected at https://internal.invalid"}},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-rejected",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result == ProviderSubmissionRejected(
        error_code="provider_rejected",
        reason="<redacted> rejected at <url>",
    )
    assert "provider-secret" not in repr(result)
    assert "internal.invalid" not in repr(result)


def test_openai_compatible_submission_runs_every_requested_image_request() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )
    call_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-partial",
            prompt="two paper-cut foxes",
            aspect_ratio="1:1",
            quantity=2,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert call_count == 2
    assert len(result.images) == 2


def test_openai_compatible_submission_immediately_returns_an_upstream_json_failure() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="model-2",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/model-2/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"success": False, "error": {"code": "content_policy", "message": "prompt rejected"}},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-upstream-failure",
            prompt="rejected prompt",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="model-2",
            resolution_tier="1k",
        )
    )

    assert result == ProviderSubmissionRejected(error_code="content_policy", reason="prompt rejected")


def test_openai_compatible_submission_maps_timeout_to_unknown_without_retrying() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )
    request_count = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise httpx.ReadTimeout("provider-secret timed out", request=request)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(timeout),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-timeout",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=2,
            output_spec="1k",
        )
    )

    assert result == ProviderSubmissionUnknown(reason="image provider submission status is unknown")
    assert request_count == 2
    assert "provider-secret" not in repr(result)


def test_openai_compatible_submission_maps_connection_failure_to_unknown() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def connection_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("provider-secret connection failed", request=request)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(connection_failure),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-connect",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result == ProviderSubmissionUnknown(reason="image provider submission status is unknown")
    assert "provider-secret" not in repr(result)


def test_openai_compatible_submission_maps_upstream_server_error_to_unknown() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="provider-secret unavailable")),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-server-error",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result == ProviderSubmissionUnknown(reason="image provider submission status is unknown")
    assert "provider-secret" not in repr(result)


def test_openai_compatible_submission_maps_invalid_json_to_unknown() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="provider-secret is not json")),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-json",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)
    assert "provider-secret" not in repr(result)


def test_openai_compatible_submission_maps_a_response_without_images_to_unknown() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [], "result": {}})),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-empty",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)


def test_openai_compatible_submission_downloads_a_public_https_image_without_provider_credentials() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/result.png"}]})
        assert request.url == "https://cdn.example.invalid/result.png"
        assert "Authorization" not in request.headers
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=_PNG_BYTES)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-url",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert [(image.result_reference, image.mime_type, image.content) for image in result.images] == [
        ("attempt-key-url:1", "image/png", _PNG_BYTES)
    ]


def test_openai_compatible_submission_rejects_a_redirect_to_a_private_address() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )
    requested_urls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/result"}]})
        if request.url.host == "cdn.example.invalid":
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/internal.png"})
        raise AssertionError("private redirect target must not be requested")

    def resolver(hostname: str) -> tuple[str, ...]:
        return ("93.184.216.34",) if hostname == "cdn.example.invalid" else ("127.0.0.1",)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=resolver,
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-private-redirect",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)
    assert requested_urls == [
        "https://api.example.invalid/images/generations",
        "https://cdn.example.invalid/result",
    ]


def test_openai_compatible_submission_rejects_a_remote_image_over_the_byte_limit() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/result.png"}]})
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=_PNG_BYTES)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
        max_image_bytes=len(_PNG_BYTES) - 1,
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-large",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)


def test_openai_compatible_submission_rejects_remote_content_without_an_image_signature() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/result"}]})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>error</html>")

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-html",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)


def test_openai_compatible_submission_rejects_a_remote_mime_signature_mismatch() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/result"}]})
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=_PNG_BYTES)

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-mismatch",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)


def test_openai_compatible_submission_maps_remote_image_download_failure_to_unknown() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example.invalid/missing"}]})
        return httpx.Response(404, text="provider-secret remote detail")

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-download-failure",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionDeliveryFailed)
    assert "provider-secret" not in repr(result)


def test_openai_compatible_submission_restores_legacy_image_field_aliases() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": {"result": {"outputs": [{"image_base64": base64.b64encode(_PNG_BYTES).decode()}]}}},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-alias",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
            output_format="png",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].content == _PNG_BYTES


def test_openai_compatible_submission_polls_an_async_task_without_repeating_the_paid_post() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    requested: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"task_id": "task-123", "status": "processing"}})
        if request.url.path.endswith("/images/tasks/task-123"):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
            )
        raise AssertionError(str(request.url))

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
        sleeper=lambda _seconds: None,
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-async",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
            output_format="jpeg",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert [method for method, _url in requested].count("POST") == 1
    assert requested == [
        ("POST", "https://api.example.invalid/v1/images/generations"),
        ("GET", "https://api.example.invalid/v1/images/tasks/task-123"),
    ]


def test_async_task_keeps_polling_past_the_legacy_attempt_limit_until_completion() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
            request_timeout_seconds=600,
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    elapsed = [0.0]
    poll_count = [0]
    post_count = [0]

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_count[0] += 1
            return httpx.Response(200, json={"data": {"task_id": "slow-task", "status": "processing"}})
        poll_count[0] += 1
        if poll_count[0] <= 50:
            return httpx.Response(200, json={"data": {"task_id": "slow-task", "status": "processing"}})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]})

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
        sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        monotonic=lambda: elapsed[0],
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-slow-async",
            prompt="a slow multi-reference composition",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert post_count == [1]
    assert poll_count == [51]
    assert elapsed == [250.0]


def test_processing_response_without_queryable_task_id_remains_accepted() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"status": "processing"})),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-processing",
            prompt="a slow multi-reference composition",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
        )
    )

    assert result == ProviderSubmissionAccepted(provider_task_id="direct:attempt-key-processing")


def test_async_task_failure_is_accepted_delivery_failure_without_repeating_post() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    requested: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, str(request.url)))
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"task_id": "task-123", "status": "processing"}})
        return httpx.Response(
            200,
            json={"data": {"task_id": "task-123", "status": "failed", "message": "Upstream request failed"}},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        sleeper=lambda _seconds: None,
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-async-failed",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
        )
    )

    assert result == ProviderSubmissionDeliveryFailed(
        provider_task_id="task-123",
        reason=("上游明确失败：Upstream request failed；上游任务 ID：task-123"),
    )
    assert [method for method, _url in requested].count("POST") == 1
    assert requested == [
        ("POST", "https://api.example.invalid/v1/images/generations"),
        ("GET", "https://api.example.invalid/v1/images/tasks/task-123"),
    ]


def test_sse_task_id_is_preserved_for_read_only_result_polling() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="1k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/1k/v1",
        )
    )
    requested: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, str(request.url)))
        if request.method == "POST":
            event = json.dumps({"type": "image_generation.queued", "task_id": "task-sse"})
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=f"data: {event}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
        resolver=lambda hostname: ("93.184.216.34",),
        sleeper=lambda _seconds: None,
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-sse-task",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert [method for method, _url in requested].count("POST") == 1
    assert requested[1] == ("GET", "https://api.example.invalid/v1/images/tasks/task-sse")


def test_openai_compatible_submission_keeps_actual_format_when_provider_ignores_requested_format() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode()}]},
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-format-fallback",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
            output_format="jpeg",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].content == _PNG_BYTES


def test_originboost_submission_uses_heartbeat_stream_and_reads_completed_event() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="originboost-images",
            display_name="OriginBoost images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.originboostai.com/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    requested_bodies: list[dict[str, object]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_bodies.append(json.loads(request.content))
        completed = json.dumps(
            {
                "type": "image_generation.completed",
                "b64_json": base64.b64encode(_PNG_BYTES).decode(),
                "output_format": "png",
            }
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f":\n\nevent: image_generation.completed\ndata: {completed}\n\ndata: [DONE]\n\n",
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-originboost-stream",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
            size="2880x2880",
            output_format="png",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert requested_bodies == [
        {
            "model": "gpt-image-2",
            "prompt": "a paper-cut fox",
            "size": "2880x2880",
            "n": 1,
            "output_format": "png",
            "stream": True,
        }
    ]
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].content == _PNG_BYTES


def test_originboost_uses_one_independent_stream_per_requested_image() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="originboost-images",
            display_name="OriginBoost images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.originboostai.com/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    requested_bodies: list[dict[str, object]] = []
    completed_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        requested_bodies.append(json.loads(request.content))
        nonlocal completed_count
        completed_count += 1
        image = _PNG_BYTES + bytes((completed_count,))
        event = json.dumps({"type": "image_generation.completed", "b64_json": base64.b64encode(image).decode()})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f"event: image_generation.completed\ndata: {event}\n\ndata: [DONE]\n\n",
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-originboost-multiple",
            prompt="two paper-cut foxes",
            aspect_ratio="1:1",
            quantity=2,
            output_spec="4k",
            size="2880x2880",
            output_format="png",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert len(requested_bodies) == 2
    assert all(body["n"] == 1 and body["stream"] is True for body in requested_bodies)
    assert len(result.images) == 2


def test_multiple_images_are_submitted_sequentially() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )
    active = 0
    maximum_active = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
            )
        finally:
            active -= 1

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-sequential",
            prompt="four paper-cut foxes",
            aspect_ratio="1:1",
            quantity=4,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert len(result.images) == 4
    assert maximum_active == 1


def test_stream_parser_accepts_plain_json_mislabeled_as_event_stream() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="1k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/1k/v1",
        )
    )
    payload = {"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]}

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=json.dumps(payload),
            )
        ),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-json-stream",
            prompt="a paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert isinstance(result, ProviderSubmissionCompleted)
    assert result.images[0].content == _PNG_BYTES


def test_sequential_submission_stops_when_one_paid_request_is_still_processing() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="originboost-images",
            display_name="OriginBoost images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.originboostai.com/v1",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    call_count = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content='event: image_generation.in_progress\ndata: {"type":"image_generation.in_progress"}\n\ndata: [DONE]\n\n',
            )
        event = json.dumps({"type": "image_generation.completed", "b64_json": base64.b64encode(_PNG_BYTES).decode()})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f"event: image_generation.completed\ndata: {event}\n\ndata: [DONE]\n\n",
        )

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-originboost-partial",
            prompt="two paper-cut foxes",
            aspect_ratio="1:1",
            quantity=2,
            output_spec="4k",
            size="2880x2880",
            output_format="png",
        )
    )

    assert call_count == 1
    assert result == ProviderSubmissionAccepted(provider_task_id="direct:attempt-key-originboost-partial:request-1")


def test_sequential_submission_stops_before_the_next_request_when_task_is_inactive() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="image-1",
            output_spec="1k",
            provider_model_name="upstream-image-1",
            compatibility_group="image-1/1k/v1",
        )
    )
    calls = 0
    active = [True]

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    def receive(_image: GenerationImageContent) -> None:
        active[0] = False

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(upstream),
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-stop-sequence",
            prompt="four paper-cut foxes",
            aspect_ratio="1:1",
            quantity=4,
            output_spec="1k",
            on_image=receive,
            should_continue=lambda: active[0],
        )
    )

    assert calls == 1
    assert isinstance(result, ProviderSubmissionCompleted)
    assert len(result.images) == 1


def test_sse_heartbeats_do_not_extend_the_absolute_provider_timeout() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets(), id_factory=iter(("provider-1", "route-1")).__next__)
    provider = routing.create_provider(
        ProviderCreation(
            code="primary-images",
            display_name="Primary images",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://api.example.invalid",
            api_key="provider-secret",
            request_timeout_seconds=600,
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="1k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/1k/v1",
        )
    )
    elapsed = [0.0]

    class HeartbeatStream(httpx.SyncByteStream):
        def __iter__(self):
            elapsed[0] = 601.0
            yield b": heartbeat\n\n"

    result = OpenAICompatibleImageSubmissions(
        routing,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=HeartbeatStream(),
            )
        ),
        monotonic=lambda: elapsed[0],
    ).submit(
        ProviderGenerationRequest(
            route_id=route.route_id,
            provider_idempotency_key="attempt-key-absolute-timeout",
            prompt="a slow paper-cut fox",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="1k",
        )
    )

    assert result == ProviderSubmissionAccepted(provider_task_id="direct:attempt-key-absolute-timeout")

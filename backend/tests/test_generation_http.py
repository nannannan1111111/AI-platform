import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.generation import GenerationDispatchStarted, GenerationFailed, InMemoryGenerationTasks
from app.generation_attempts import (
    GenerationAttemptReconciler,
    GenerationAttemptSubmitter,
    InMemoryGenerationAttempts,
)
from app.generation_attempts._provider import (
    ProviderGenerationRequest,
    ProviderGenerationResolutionRequest,
    ProviderResolutionAccepted,
    ProviderResolutionRejected,
    ProviderSubmissionAccepted,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
)
from app.generation_results import GenerationImageDelivery
from app.http import create_app
from app.media import FileSystemMediaObjects, InMemoryGeneratedMedia, InMemoryStorageAllowances
from app.model_routing import (
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ModelRouteUpdate,
    ProbeResult,
    ProviderCreation,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthStatus,
    RouteProbeTarget,
)
from app.provider_costs import InMemoryProviderCostRates
from app.provider_images import OpenAICompatibleImageSubmissions
from app.reference_media import InMemoryReferenceMedia

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class HealthyProbe:
    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        return ProbeResult(RouteHealthStatus.HEALTHY, 125)


class AcceptingGenerationProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ProviderGenerationRequest] = []

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        self.calls += 1
        self.requests.append(request)
        return ProviderSubmissionAccepted(provider_task_id="provider-task-1")


class GenerationProviderMustNotBeCalled:
    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        raise AssertionError("provider must not be called without an effective cost rate")


class UnknownGenerationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionUnknown:
        self.calls += 1
        return ProviderSubmissionUnknown(reason="provider submission status is unknown")


class RejectThenAcceptGenerationProvider:
    def __init__(self) -> None:
        self._submissions = 0

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionRejected | ProviderSubmissionAccepted:
        self._submissions += 1
        if self._submissions == 1:
            return ProviderSubmissionRejected(error_code="not_accepted", reason="provider-secret-detail")
        return ProviderSubmissionAccepted(provider_task_id="provider-task-retried")


class RejectingGenerationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionRejected:
        self.calls += 1
        return ProviderSubmissionRejected(error_code="not_accepted", reason="provider-secret-detail")


class AcceptingProviderResolutions:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionAccepted:
        return ProviderResolutionAccepted(provider_task_id="provider-task-reconciled")


class ProviderResolutionMustNotBeCalled:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionAccepted:
        raise AssertionError("provider resolution must not be called without an attempt")


class RejectThenBecomeUnknownGenerationProvider:
    def __init__(self) -> None:
        self._submissions = 0

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionRejected | ProviderSubmissionUnknown:
        self._submissions += 1
        if self._submissions == 1:
            return ProviderSubmissionRejected(error_code="not_accepted", reason="provider-secret-detail")
        return ProviderSubmissionUnknown(reason="provider-secret-detail")


class RejectingProviderResolutions:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionRejected:
        return ProviderResolutionRejected(error_code="not_accepted", reason="provider-secret-detail")


def test_top_level_generation_submission_does_not_require_a_canvas() -> None:
    now = datetime(2026, 8, 9, 9, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("standalone-http@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("standalone-http@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-standalone-http-1",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "standalone-task-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "独立图片生成",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 202
    assert response.json()["canvas_id"] is None
    assert response.json()["status"] == "queued"

    tasks.transition(
        registration.account_space_id,
        "standalone-task-1",
        GenerationFailed(reason="test terminal state", outcome_reference="test-terminal-1", occurred_at=now),
    )
    with client.stream(
        "GET",
        "/api/v1/generation-tasks/standalone-task-1/events",
        headers={"Authorization": f"Bearer {session.access_token}"},
    ) as stream:
        event_body = stream.read().decode()

    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: task" in event_body
    assert "event: media" not in event_body
    assert '"status":"failed"' in event_body


def _rolled_back_test_clear_generation_history_is_account_scoped_and_keeps_active_tasks() -> None:
    now = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    first = accounts.register("clear-history-first@example.com", "a-correct-horse-battery-staple")
    second = accounts.register("clear-history-second@example.com", "a-correct-horse-battery-staple")
    first_session = accounts.login("clear-history-first@example.com", "a-correct-horse-battery-staple")
    second_session = accounts.login("clear-history-second@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={first.account_space_id, second.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="2.00", credits="2.0000", effective_from=now)
    for index, account_space_id in enumerate((first.account_space_id, second.account_space_id), start=1):
        credits.record_recharge(
            account_space_id,
            package.version_id,
            payment_reference=f"clear-history-payment-{index}",
            occurred_at=now,
        )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=3)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))
    first_headers = {"Authorization": f"Bearer {first_session.access_token}"}
    second_headers = {"Authorization": f"Bearer {second_session.access_token}"}

    def submit(headers: dict[str, str], task_id: str) -> None:
        response = client.post(
            "/api/v1/generation-tasks",
            headers=headers,
            json={
                "task_id": task_id,
                "logical_model": "gpt-image-2",
                "output_spec": "4k",
                "quantity": 1,
                "prompt": "clear history test",
                "params": {"aspect_ratio": "1:1"},
            },
        )
        assert response.status_code == 202

    submit(first_headers, "first-terminal")
    tasks.transition(
        first.account_space_id,
        "first-terminal",
        GenerationFailed(reason="expected failure", outcome_reference="first-failure", occurred_at=now),
    )
    submit(first_headers, "first-active")
    submit(second_headers, "second-terminal")
    tasks.transition(
        second.account_space_id,
        "second-terminal",
        GenerationFailed(reason="expected failure", outcome_reference="second-failure", occurred_at=now),
    )

    cleared = client.delete("/api/v1/generation-tasks/history", headers=first_headers)
    first_recent = client.get("/api/v1/generation-tasks/recent", headers=first_headers)
    second_recent = client.get("/api/v1/generation-tasks/recent", headers=second_headers)

    assert cleared.status_code == 200
    assert cleared.json() == {"cleared_tasks": 1}
    assert [item["task_id"] for item in first_recent.json()] == ["first-active"]
    assert [item["task_id"] for item in second_recent.json()] == ["second-terminal"]
    assert client.get("/api/v1/generation-tasks/first-terminal", headers=first_headers).status_code == 200
    assert client.delete("/api/v1/generation-tasks/history", headers=first_headers).json() == {
        "cleared_tasks": 0
    }


def test_generation_submission_explains_when_the_account_activity_limit_is_reached() -> None:
    now = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("busy-artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("busy-artist@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-busy-artist-1",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=1)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))
    headers = {"Authorization": f"Bearer {session.access_token}"}
    request = {
        "logical_model": "gpt-image-2",
        "output_spec": "4k",
        "quantity": 1,
        "prompt": "一座漂浮在云海上的图书馆",
        "params": {"aspect_ratio": "1:1"},
    }

    first = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={**request, "task_id": "busy-task-1"},
    )
    blocked = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={**request, "task_id": "busy-task-2"},
    )

    assert first.status_code == 202
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "当前账户的排队或生成中图片已达到 20 张上限，请等待现有图片完成后再提交"
    statement = credits.statement(registration.account_space_id)
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.1500"


def test_generation_submission_explains_when_available_credits_are_insufficient() -> None:
    now = datetime(2026, 8, 11, 10, 5, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("zero-balance@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("zero-balance@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=InMemoryModelPrices(clock=lambda: now),
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "unfunded-task-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一片发光的森林",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "可用额度不足以冻结本次生成任务，请减少生成数量或充值后再提交"
    assert tasks.recent_for_account(registration.account_space_id, limit=20) == ()


def test_generation_is_not_submitted_when_less_than_ten_megabytes_remain() -> None:
    now = datetime(2026, 8, 9, 9, 35, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("storage-gate@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("storage-gate@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=FileSystemMediaObjects(Path.cwd() / ".pytest-tmp-storage-gate"),
        storage_allowances=InMemoryStorageAllowances({registration.account_space_id: 9_999_999}),
    )
    client = TestClient(create_app(accounts, generation_tasks=tasks, generated_media=media, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "must-not-exist",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "must not reach generation",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "个人存储空间不足 10MB，请清理后再生成"
    assert tasks.recent_for_account(registration.account_space_id, limit=20) == ()


def test_ten_decimal_megabytes_passes_the_storage_submission_gate() -> None:
    now = datetime(2026, 8, 9, 9, 40, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("storage-boundary@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("storage-boundary@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=InMemoryModelPrices(clock=lambda: now),
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=FileSystemMediaObjects(Path.cwd() / ".pytest-tmp-storage-boundary"),
        storage_allowances=InMemoryStorageAllowances({registration.account_space_id: 10_000_000}),
    )
    client = TestClient(create_app(accounts, generation_tasks=tasks, generated_media=media, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "boundary-task",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "must pass storage gate",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "可用额度不足以冻结本次生成任务，请减少生成数量或充值后再提交"


def test_top_level_generation_snapshots_available_reference_image_and_mask() -> None:
    now = datetime(2026, 8, 9, 9, 45, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("reference-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("reference-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-reference-task-1",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            reference_media=InMemoryReferenceMedia(
                id_factory=iter(("reference-1", "mask-1")).__next__,
            ),
            clock=lambda: now,
        )
    )
    upload = client.post(
        "/api/v1/reference-media",
        headers={"Authorization": f"Bearer {session.access_token}"},
        files={"file": ("reference.png", _PNG_BYTES, "image/png")},
    )
    mask_upload = client.post(
        "/api/v1/reference-media",
        headers={"Authorization": f"Bearer {session.access_token}"},
        files={"file": ("reference_mask.png", _PNG_BYTES, "image/png")},
    )

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "reference-task-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "Use the reference composition",
            "params": {"aspect_ratio": "16:9", "quality": "high"},
            "reference_media_ids": [upload.json()["media_id"]],
            "mask_media_id": mask_upload.json()["media_id"],
        },
    )

    assert response.status_code == 202
    assert response.json()["params"] == {"aspect_ratio": "16:9", "quality": "high"}
    assert response.json()["reference_media_count"] == 1
    assert response.json()["mask_media_present"] is True
    assert "reference_media_ids" not in response.json()
    assert "mask_media_id" not in response.json()
    task = tasks.get(registration.account_space_id, "reference-task-1")
    assert task.reference_media_ids == ("reference-1",)
    assert task.mask_media_id == "mask-1"


def test_generation_rejects_reference_images_above_the_selected_model_limit() -> None:
    now = datetime(2026, 8, 9, 9, 48, tzinfo=UTC)
    seed_time = now - timedelta(minutes=1)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("reference-limit@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("reference-limit@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: seed_time)
    prices.publish(
        "gpt-image-2",
        "4k",
        credits_per_result="0.1500",
        effective_from=now,
        max_reference_images=8,
    )
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-reference-limit", "route-reference-limit")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="reference-limit-source",
            display_name="Reference limit source",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://reference-limit.example.com/v1",
            api_key="test-reference-limit",
        )
    )
    routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
            max_reference_images=1,
        )
    )
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-reference-limit",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    references = InMemoryReferenceMedia(id_factory=iter(("reference-1", "reference-2")).__next__)
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            reference_media=references,
            model_prices=prices,
            model_routing=routing,
            clock=lambda: now,
        )
    )
    reference_ids = []
    for name in ("first.png", "second.png"):
        upload = client.post(
            "/api/v1/reference-media",
            headers={"Authorization": f"Bearer {session.access_token}"},
            files={"file": (name, _PNG_BYTES, "image/png")},
        )
        assert upload.status_code == 201
        reference_ids.append(upload.json()["media_id"])

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "reference-limit-task",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "Use both references",
            "params": {"aspect_ratio": "1:1", "operation": "edit"},
            "reference_media_ids": reference_ids,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "生成任务参数无效"


def test_top_level_inpaint_exposes_safe_edit_parameters() -> None:
    now = datetime(2026, 8, 9, 9, 50, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("inpaint@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("inpaint@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id}, model_prices=prices)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(registration.account_space_id, package.version_id, payment_reference="inpaint", occurred_at=now)
    media = InMemoryReferenceMedia(id_factory=iter(("source-1", "mask-1")).__next__)
    client = TestClient(create_app(
        accounts,
        generation_tasks=InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2),
        reference_media=media,
        clock=lambda: now,
    ))
    headers = {"Authorization": f"Bearer {session.access_token}"}
    source = client.post("/api/v1/reference-media", headers=headers, files={"file": ("source.png", _PNG_BYTES, "image/png")})
    mask = client.post("/api/v1/reference-media", headers=headers, files={"file": ("mask.png", _PNG_BYTES, "image/png")})

    response = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "inpaint-http-1", "logical_model": "gpt-image-2", "output_spec": "4k",
            "quantity": 1, "prompt": "replace the selected object",
            "params": {"aspect_ratio": "1:1", "operation": "inpaint", "input_fidelity": "high"},
            "reference_media_ids": [source.json()["media_id"]], "mask_media_id": mask.json()["media_id"],
        },
    )

    assert response.status_code == 202
    assert response.json()["params"]["operation"] == "inpaint"
    assert response.json()["params"]["input_fidelity"] == "high"


def test_top_level_inpaint_rejects_a_non_png_mask() -> None:
    now = datetime(2026, 8, 9, 9, 55, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("bad-mask@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("bad-mask@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id}, model_prices=prices)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(registration.account_space_id, package.version_id, payment_reference="bad-mask", occurred_at=now)
    media = InMemoryReferenceMedia(id_factory=iter(("source-1", "mask-1")).__next__)
    client = TestClient(create_app(
        accounts,
        generation_tasks=InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2),
        reference_media=media,
        clock=lambda: now,
    ))
    headers = {"Authorization": f"Bearer {session.access_token}"}
    source = client.post("/api/v1/reference-media", headers=headers, files={"file": ("source.png", _PNG_BYTES, "image/png")})
    mask = client.post("/api/v1/reference-media", headers=headers, files={"file": ("mask.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")})

    response = client.post(
        "/api/v1/generation-tasks", headers=headers,
        json={"task_id": "bad-mask-1", "logical_model": "gpt-image-2", "output_spec": "4k", "quantity": 1,
              "prompt": "replace", "params": {"aspect_ratio": "1:1", "operation": "inpaint"},
              "reference_media_ids": [source.json()["media_id"]], "mask_media_id": mask.json()["media_id"]},
    )

    assert response.status_code == 422


def test_generation_submission_returns_running_after_provider_acceptance() -> None:
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("running-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("running-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-running-task-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(tasks, provider_cost_rates=costs, id_factory=lambda: "attempt-1")
    provider = AcceptingGenerationProvider()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=1),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {
                "aspect_ratio": "4:3",
                "resolution_tier": "4k",
                "output_format": "jpeg",
            },
        },
    )

    retry = client.post(
        "/api/v1/generation-tasks/task-1/retry",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert retry.status_code == 409
    assert retry.json() == {"detail": "当前任务不能重新尝试"}
    assert provider.calls == 1
    assert provider.requests[0].size == "3264x2448"
    assert provider.requests[0].quality == "auto"
    assert provider.requests[0].output_format == "jpeg"
    assert "provider-task-1" not in response.text
    assert (
        client.get(
            "/api/v1/generation-tasks/task-1",
            headers={"Authorization": f"Bearer {session.access_token}"},
        ).json()
        == response.json()
    )


def test_openai_compatible_http_submission_returns_delivered_media_content(tmp_path: Path) -> None:
    now = datetime(2026, 8, 9, 10, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("completed-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("completed-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-completed-task-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="Completed generation canvas",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(tasks, provider_cost_rates=costs, id_factory=lambda: "attempt-1")
    routing = _healthy_routing(now)

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://source-a.example.com/v1/images/generations"
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode("ascii")}]},
        )

    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({registration.account_space_id: 100 * 1024 * 1024}),
        id_factory=lambda: "media-1",
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        OpenAICompatibleImageSubmissions(routing, transport=httpx.MockTransport(upstream)),
        image_delivery=GenerationImageDelivery(tasks, media, objects),
        clock=lambda: now + timedelta(seconds=1),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            generation_attempt_submissions=submitter,
            generated_media=media,
            media_content=objects,
            model_routing=routing,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}

    response = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "a paper-cut fox",
            "params": {"aspect_ratio": "16:9"},
        },
    )
    media_response = client.get("/api/v1/generation-tasks/task-1/media", headers=headers)
    content_response = client.get("/api/v1/media/media-1/content", headers=headers)
    with client.stream(
        "GET",
        "/api/v1/generation-tasks/task-1/events",
        headers=headers,
    ) as stream:
        event_body = stream.read().decode()

    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"
    assert response.json()["delivered_quantity"] == 1
    assert media_response.status_code == 200
    assert [item["media_id"] for item in media_response.json()] == ["media-1"]
    assert content_response.status_code == 200
    assert content_response.content == _PNG_BYTES
    assert "event: media" in event_body
    assert '"media_id":"media-1"' in event_body
    public_text = response.text + media_response.text
    assert "test-source-a" not in public_text
    assert "source-a.example.com" not in public_text
    assert "route-1" not in public_text


def test_user_cannot_retry_an_explicitly_rejected_generation_attempt() -> None:
    now = datetime(2026, 8, 9, 11, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("retry-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("retry-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-retry-task-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=costs,
        id_factory=iter(("attempt-1", "attempt-2")).__next__,
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectThenAcceptGenerationProvider(),
        clock=lambda: now + timedelta(seconds=1),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    accounts.register("other-retry-task@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other-retry-task@example.com", "a-correct-horse-battery-staple")
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
    request = {
        "task_id": "task-1",
        "canvas_id": "canvas-1",
        "logical_model": "gpt-image-2",
        "output_spec": "4k",
        "quantity": 1,
        "prompt": "一座漂浮在云海上的图书馆",
        "params": {"aspect_ratio": "16:9"},
    }

    created = client.post("/api/v1/generation-tasks", headers=headers, json=request)
    hidden = client.post("/api/v1/generation-tasks/task-1/retry", headers=other_headers)
    retried = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)

    assert created.status_code == 202
    assert created.json()["status"] == "failed"
    assert hidden.status_code == 404
    assert retried.status_code == 409
    assert credits.statement(registration.account_space_id).frozen_credits == "0.0000"
    assert submitter._provider_submissions._submissions == 1
    assert "provider-secret-detail" not in created.text


def test_explicit_rejection_fails_immediately_and_calls_provider_once() -> None:
    now = datetime(2026, 8, 9, 11, 45, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("retry-failure@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("retry-failure@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-retry-failure-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=costs,
        id_factory=iter(("attempt-1", "attempt-2")).__next__,
    )
    provider = RejectingGenerationProvider()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=1),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    request = {
        "task_id": "task-1",
        "canvas_id": "canvas-1",
        "logical_model": "gpt-image-2",
        "output_spec": "4k",
        "quantity": 1,
        "prompt": "一座漂浮在云海上的图书馆",
        "params": {"aspect_ratio": "16:9"},
    }

    created = client.post("/api/v1/generation-tasks", headers=headers, json=request)
    retried = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)
    replay = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)

    assert created.status_code == 202
    assert created.json()["status"] == "failed"
    assert retried.status_code == 409
    assert replay.status_code == 409
    assert provider.calls == 1
    assert credits.statement(registration.account_space_id).frozen_credits == "0.0000"
    assert "provider-secret-detail" not in created.text


def test_user_can_reconcile_an_unknown_generation_attempt_to_running() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("reconcile-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("reconcile-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-reconcile-task-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(tasks, provider_cost_rates=costs, id_factory=lambda: "attempt-1")
    provider = UnknownGenerationProvider()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=1),
    )
    reconciler = GenerationAttemptReconciler(
        attempts,
        AcceptingProviderResolutions(),
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=2),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            generation_attempt_reconciliations=reconciler,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    accounts.register("other-reconcile-task@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other-reconcile-task@example.com", "a-correct-horse-battery-staple")
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
    created = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {"aspect_ratio": "16:9"},
        },
    )

    hidden = client.post("/api/v1/generation-tasks/task-1/reconcile", headers=other_headers)
    retried = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)
    response = client.post("/api/v1/generation-tasks/task-1/reconcile", headers=headers)
    replay = client.post("/api/v1/generation-tasks/task-1/reconcile", headers=headers)

    assert created.status_code == 202
    assert created.json()["status"] == "running"
    assert hidden.status_code == 404
    assert retried.status_code == 200
    assert retried.json() == created.json()
    assert provider.calls == 1
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert replay.json() == response.json()
    assert response.json() == client.get("/api/v1/generation-tasks/task-1", headers=headers).json()
    assert "provider-task-reconciled" not in response.text


def test_rejected_submission_fails_before_reconciliation_and_is_not_retried() -> None:
    now = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("reconcile-failure@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("reconcile-failure@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-reconcile-failure-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=costs,
        id_factory=iter(("attempt-1", "attempt-2")).__next__,
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectThenBecomeUnknownGenerationProvider(),
        clock=lambda: now + timedelta(seconds=1),
    )
    reconciler = GenerationAttemptReconciler(
        attempts,
        RejectingProviderResolutions(),
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=2),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            generation_attempt_reconciliations=reconciler,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    request = {
        "task_id": "task-1",
        "canvas_id": "canvas-1",
        "logical_model": "gpt-image-2",
        "output_spec": "4k",
        "quantity": 1,
        "prompt": "一座漂浮在云海上的图书馆",
        "params": {"aspect_ratio": "16:9"},
    }

    first = client.post("/api/v1/generation-tasks", headers=headers, json=request)
    second = client.post("/api/v1/generation-tasks", headers=headers, json=request)
    reconciled = client.post("/api/v1/generation-tasks/task-1/reconcile", headers=headers)
    retried_after_failure = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)

    assert first.status_code == 202
    assert first.json()["status"] == "failed"
    assert second.status_code == 202
    assert second.json()["status"] == "failed"
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "failed"
    assert retried_after_failure.status_code == 409
    assert retried_after_failure.json() == {"detail": "当前任务不能重新尝试"}
    assert "credential=<redacted>" in reconciled.json()["failure_message"]
    assert credits.statement(registration.account_space_id).frozen_credits == "0.0000"
    assert "provider-secret-detail" not in reconciled.text


def test_generation_submission_stays_queued_when_provider_cost_is_not_configured() -> None:
    now = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("queued-task@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("queued-task@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-queued-task-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    costs = InMemoryProviderCostRates(clock=lambda: now)
    attempts = InMemoryGenerationAttempts(tasks, provider_cost_rates=costs)
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        GenerationProviderMustNotBeCalled(),
        clock=lambda: now + timedelta(seconds=1),
    )
    reconciler = GenerationAttemptReconciler(
        attempts,
        ProviderResolutionMustNotBeCalled(),
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=2),
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            model_routing=_healthy_routing(now),
            generation_attempt_submissions=submitter,
            generation_attempt_reconciliations=reconciler,
            clock=lambda: now,
        ),
        raise_server_exceptions=False,
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}

    response = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {"aspect_ratio": "1:1"},
        },
    )
    retried = client.post("/api/v1/generation-tasks/task-1/retry", headers=headers)
    reconciled = client.post("/api/v1/generation-tasks/task-1/reconcile", headers=headers)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert retried.status_code == 200
    assert retried.json() == response.json()
    assert reconciled.status_code == 200
    assert reconciled.json() == response.json()
    assert credits.statement(registration.account_space_id).frozen_credits == "0.1500"


def test_recent_generation_tasks_expose_safe_persistent_failure_messages() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("failure-notice@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("failure-notice@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-failure-notice-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    client = TestClient(
        create_app(accounts, generation_tasks=tasks, model_routing=_healthy_routing(now), clock=lambda: now)
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    submitted = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {"aspect_ratio": "16:9"},
        },
    )
    assert submitted.status_code == 202
    tasks.transition(
        registration.account_space_id,
        "task-1",
        GenerationFailed(
            reason="provider failed with route=secret-route and api_key=provider-secret",
            outcome_reference="generation-attempt:internal-attempt-2",
            occurred_at=now,
        ),
    )

    response = client.get(
        "/api/v1/canvases/canvas-1/generation-tasks/recent?limit=20",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "failed"
    assert "credential=<redacted>" in body[0]["failure_message"]
    assert client.get("/api/v1/generation-tasks/task-1", headers=headers).json() == body[0]
    assert "provider-secret" not in response.text
    assert "secret-route" not in response.text
    assert "internal-attempt-2" not in response.text

    submitted_timeout = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-timeout",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "测试五分钟超时",
            "params": {"aspect_ratio": "16:9"},
        },
    )
    assert submitted_timeout.status_code == 202
    tasks.transition(
        registration.account_space_id,
        "task-timeout",
        GenerationDispatchStarted(occurred_at=now),
    )
    tasks.expire_due(now + timedelta(minutes=10))

    timeout_response = client.get("/api/v1/generation-tasks/task-timeout", headers=headers)

    assert timeout_response.status_code == 200
    assert timeout_response.json()["failure_message"] == "生成超过管理员设置的任务时限，已按超时结束，冻结额度已退回。"


def test_account_generation_history_keeps_tasks_after_their_canvas_is_deleted() -> None:
    now = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("deleted-canvas-history@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("deleted-canvas-history@example.com", "a-correct-horse-battery-staple")
    accounts.register("other-history@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other-history@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-deleted-canvas-history-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-deleted")
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generation_tasks=tasks,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
    created_canvas = client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "即将删除的画布", "kind": "classic"},
    )
    submitted = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-deleted-canvas",
            "canvas_id": "canvas-deleted",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {"aspect_ratio": "16:9"},
        },
    )
    deleted = client.delete(
        "/api/v1/canvases/canvas-deleted?confirm_running_tasks=true",
        headers=headers,
    )

    response = client.get("/api/v1/generation-tasks/recent?limit=20", headers=headers)

    assert created_canvas.status_code == 201
    assert submitted.status_code == 202
    assert deleted.status_code == 204
    assert client.get("/api/v1/canvases", headers=headers).json() == []
    assert response.status_code == 200
    assert response.json() == [submitted.json()]
    assert client.get("/api/v1/generation-tasks/recent?limit=20", headers=other_headers).json() == []
    assert "account_space_id" not in response.text
    assert "model_price_version_id" not in response.text
    assert "selected_route_id" not in response.text


def test_generation_rejects_an_unknown_canvas_before_freezing_credits() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases()
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    routing = _healthy_routing(now)
    client = TestClient(create_app(accounts, generation_tasks=tasks, model_routing=routing, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-1",
            "canvas_id": "unknown-canvas",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "测试生成请求",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 404
    assert tuple(entry.kind for entry in credits.statement(registration.account_space_id).entries) == ("recharge",)


def test_authenticated_generation_routes_derive_ownership_from_bearer_session() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    client = TestClient(
        create_app(accounts, generation_tasks=tasks, model_routing=_healthy_routing(now), clock=lambda: now)
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}

    response = client.post(
        "/api/v1/generation-tasks",
        headers=headers,
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {
                "aspect_ratio": "4:3",
                "resolution_tier": "4k",
                "output_format": "jpeg",
            },
            "user_id": "attacker",
            "account_space_id": "attacker-space",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "task_id",
        "canvas_id",
        "logical_model",
        "output_spec",
        "quantity",
        "prompt",
        "params",
        "reference_media_count",
        "mask_media_present",
        "frozen_credits",
        "status",
        "failure_message",
        "partial_delivery",
        "undelivered_quantity",
        "completion_message",
        "delivered_quantity",
        "created_at",
        "updated_at",
    }
    assert body["task_id"] == "task-1"
    assert body["prompt"] == "一座漂浮在云海上的图书馆"
    assert body["params"] == {
        "aspect_ratio": "4:3",
        "quality": "auto",
        "size": "3264x2448",
        "resolution_tier": "4k",
        "output_format": "jpeg",
    }
    assert body["reference_media_count"] == 0
    assert body["mask_media_present"] is False
    assert body["failure_message"] is None
    assert body["partial_delivery"] is False
    assert body["undelivered_quantity"] == 0
    assert body["completion_message"] is None
    assert client.get("/api/v1/generation-tasks/task-1", headers=headers).json() == body
    assert client.get("/api/v1/canvases/canvas-1/generation-tasks/active", headers=headers).json() == [body]


@pytest.mark.parametrize(
    ("prompt", "aspect_ratio", "size"),
    (
        (" \n ", "16:9", "2048x1152"),
        ("一座漂浮在云海上的图书馆", "4:3", ""),
        ("一座漂浮在云海上的图书馆", "16:9", "4096x4096"),
        ("一座漂浮在云海上的图书馆", "1:1", "2048x1152"),
        ("一座漂浮在云海上的图书馆", "3:2", ""),
    ),
)
def test_generation_rejects_invalid_request_snapshot_before_freezing_credits(
    prompt: str,
    aspect_ratio: str,
    size: str,
) -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={registration.account_space_id},
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": prompt,
            "params": {"aspect_ratio": aspect_ratio, "size": size},
        },
    )

    assert response.status_code == 422
    assert credits.statement(registration.account_space_id).frozen_credits == "0.0000"


def test_generation_rejects_unknown_request_parameters() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    tasks = InMemoryGenerationTasks(
        InMemoryCredits(clock=lambda: now),
        canvases=InMemoryCanvases(),
        max_active_tasks=2,
    )
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "一座漂浮在云海上的图书馆",
            "params": {"aspect_ratio": "16:9", "provider_id": "user-provider"},
        },
    )

    assert response.status_code == 422


def test_generation_rejects_more_than_five_images_at_the_http_boundary() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("five-images@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("five-images@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={registration.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-too-many",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 6,
            "prompt": "six images",
            "params": {"aspect_ratio": "1:1"},
        },
    )

    assert response.status_code == 422
    assert tasks.recent_for_account(registration.account_space_id, limit=20) == ()


def test_generation_submission_rejects_user_supplied_api_routing() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now)
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    client = TestClient(create_app(accounts, generation_tasks=tasks, clock=lambda: now))

    response = client.post(
        "/api/v1/generation-tasks",
        headers={"Authorization": f"Bearer {session.access_token}"},
        json={
            "task_id": "task-1",
            "canvas_id": "canvas-1",
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "quantity": 1,
            "prompt": "测试生成请求",
            "params": {"aspect_ratio": "1:1"},
            "base_url": "https://user-selected.example/v1",
            "api_key": "must-not-be-accepted",
            "route_id": "user-route",
        },
    )

    assert response.status_code == 422


def _healthy_routing(now: datetime) -> InMemoryModelRouting:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=HealthyProbe(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-source-a",
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
    routing.check_route(route.route_id)
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    routing.update_route(ModelRouteUpdate(route.route_id, enabled=True))
    return routing

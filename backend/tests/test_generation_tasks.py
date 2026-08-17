from datetime import UTC, datetime, timedelta

import pytest

from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.generation import (
    GenerationCancelled,
    GenerationConcurrencyLimit,
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTaskAlreadyExists,
    GenerationTaskNotFound,
    GenerationTaskStatus,
    InMemoryGenerationTasks,
    InvalidGenerationRequest,
)


def test_submit_creates_a_user_and_canvas_owned_task_and_freezes_its_credits() -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id="user-1",
            account_space_id="account-space-1",
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)

    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="一座漂浮在云海上的图书馆",
            params=GenerationParameters(aspect_ratio="16:9"),
            submitted_at=now,
        )
    )

    assert task.status is GenerationTaskStatus.QUEUED
    assert task.user_id == "user-1"
    assert task.account_space_id == "account-space-1"
    assert task.canvas_id == "canvas-1"
    assert task.task_id == "task-1"
    assert task.prompt == "一座漂浮在云海上的图书馆"
    assert task.params == GenerationParameters(aspect_ratio="16:9")
    assert task.model_price_version_id
    assert task.frozen_credits == "0.1500"
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_submit_creates_an_account_owned_task_without_a_canvas() -> None:
    tasks, credits, now = _credits_and_tasks()

    task = tasks.submit(_submission(now, canvas_id=None))

    assert task.status is GenerationTaskStatus.QUEUED
    assert task.user_id == "user-1"
    assert task.account_space_id == "account-space-1"
    assert task.canvas_id is None
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_submission_freezes_quality_and_reference_media_ids() -> None:
    tasks, _, now = _credits_and_tasks()

    task = tasks.submit(
        _submission(
            now,
            quality="high",
            reference_media_ids=("reference-1", "reference-2"),
        )
    )

    assert task.params == GenerationParameters(aspect_ratio="16:9", quality="high")
    assert task.reference_media_ids == ("reference-1", "reference-2")
    with pytest.raises(GenerationTaskAlreadyExists):
        tasks.submit(_submission(now, quality="low", reference_media_ids=("reference-1",)))


def test_inpaint_requires_source_mask_and_preserves_edit_parameters() -> None:
    tasks, _, now = _credits_and_tasks()

    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="inpaint-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="Replace the selected sign with a red sign",
            params=GenerationParameters(
                aspect_ratio="1:1",
                operation="inpaint",
                input_fidelity="high",
            ),
            submitted_at=now,
            reference_media_ids=("source-1",),
            mask_media_id="mask-1",
        )
    )

    assert task.params.operation == "inpaint"
    assert task.params.input_fidelity == "high"


def test_inpaint_is_rejected_for_non_gpt_image_2_models() -> None:
    tasks, _, now = _credits_and_tasks()

    with pytest.raises(InvalidGenerationRequest):
        tasks.submit(
            GenerationSubmission(
                user_id="user-1", account_space_id="account-space-1", canvas_id="canvas-1",
                task_id="wrong-model", logical_model="other-image-model", output_spec="4k", quantity=1,
                prompt="replace the selection", params=GenerationParameters(aspect_ratio="1:1", operation="inpaint"),
                submitted_at=now, reference_media_ids=("source-1",), mask_media_id="mask-1",
            )
        )


@pytest.mark.parametrize(
    ("operation", "references", "mask"),
    (
        ("inpaint", (), ""),
        ("inpaint", ("source-1",), ""),
        ("edit", (), ""),
        ("generate", ("source-1",), ""),
    ),
)
def test_explicit_image_operation_must_match_its_inputs(
    operation: str,
    references: tuple[str, ...],
    mask: str,
) -> None:
    tasks, _, now = _credits_and_tasks()

    with pytest.raises(InvalidGenerationRequest):
        tasks.submit(
            GenerationSubmission(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id="canvas-1",
                task_id="invalid-operation",
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="Edit this image",
                params=GenerationParameters(aspect_ratio="1:1", operation=operation),
                submitted_at=now,
                reference_media_ids=references,
                mask_media_id=mask,
            )
        )


@pytest.mark.parametrize(
    ("resolution_tier", "aspect_ratio", "expected_size"),
    (
        ("1k", "1:1", "1024x1024"),
        ("1k", "4:3", "1024x768"),
        ("1k", "16:9", "1280x720"),
        ("1k", "3:4", "768x1024"),
        ("1k", "9:16", "720x1280"),
        ("2k", "1:1", "2048x2048"),
        ("2k", "4:3", "2048x1536"),
        ("2k", "16:9", "2048x1152"),
        ("2k", "3:4", "1536x2048"),
        ("2k", "9:16", "1152x2048"),
        ("4k", "1:1", "2880x2880"),
        ("4k", "4:3", "3264x2448"),
        ("4k", "16:9", "3840x2160"),
        ("4k", "3:4", "2448x3264"),
        ("4k", "9:16", "2160x3840"),
    ),
)
def test_submission_derives_openai_size_from_resolution_tier_and_aspect_ratio(
    resolution_tier: str,
    aspect_ratio: str,
    expected_size: str,
) -> None:
    tasks, _, now = _credits_and_tasks()

    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="一座漂浮在云海上的图书馆",
            params=GenerationParameters(
                aspect_ratio=aspect_ratio,
                resolution_tier=resolution_tier,
                output_format="webp",
            ),
            submitted_at=now,
        )
    )

    assert task.params == GenerationParameters(
        aspect_ratio=aspect_ratio,
        quality="auto",
        size=expected_size,
        resolution_tier=resolution_tier,
        output_format="webp",
    )


def _credits_and_tasks(
    *,
    max_active_tasks: int = 2,
    deadline_minutes: list[int] | None = None,
) -> tuple[InMemoryGenerationTasks, InMemoryCredits, datetime]:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=now,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=iter(("canvas-1", "canvas-2")).__next__)
    for title in ("生成画布一", "生成画布二"):
        canvases.create(
            CanvasCreation(
                user_id="user-1",
                account_space_id="account-space-1",
                title=title,
                kind="classic",
                created_at=now,
            )
        )
    return (
        InMemoryGenerationTasks(
            credits,
            canvases=canvases,
            max_active_tasks=max_active_tasks,
            deadline=(
                (lambda: timedelta(minutes=deadline_minutes[0]))
                if deadline_minutes is not None
                else (lambda: timedelta(minutes=10))
            ),
        ),
        credits,
        now,
    )


def _submission(
    now: datetime,
    *,
    task_id: str = "task-1",
    canvas_id: str | None = "canvas-1",
    quantity: int = 1,
    prompt: str = "一座漂浮在云海上的图书馆",
    aspect_ratio: str = "16:9",
    quality: str = "auto",
    reference_media_ids: tuple[str, ...] = (),
    mask_media_id: str = "",
) -> GenerationSubmission:
    return GenerationSubmission(
        user_id="user-1",
        account_space_id="account-space-1",
        canvas_id=canvas_id,
        task_id=task_id,
        logical_model="gpt-image-2",
        output_spec="4k",
        quantity=quantity,
        prompt=prompt,
        params=GenerationParameters(aspect_ratio=aspect_ratio, quality=quality),
        reference_media_ids=reference_media_ids,
        mask_media_id=mask_media_id,
        submitted_at=now,
    )


def test_identical_submission_is_idempotent_but_conflicting_parameters_are_rejected() -> None:
    tasks, credits, now = _credits_and_tasks()
    first = tasks.submit(_submission(now))

    assert tasks.submit(_submission(now)) == first
    assert credits.statement("account-space-1").frozen_credits == "0.1500"

    with pytest.raises(GenerationTaskAlreadyExists):
        tasks.submit(_submission(now, quantity=2))
    with pytest.raises(GenerationTaskAlreadyExists):
        tasks.submit(_submission(now, prompt="一座海底图书馆"))
    with pytest.raises(GenerationTaskAlreadyExists):
        tasks.submit(_submission(now, aspect_ratio="1:1"))
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_invalid_request_snapshot_is_rejected_before_credits_are_frozen() -> None:
    tasks, credits, now = _credits_and_tasks()

    with pytest.raises(InvalidGenerationRequest):
        tasks.submit(_submission(now, prompt=" \n "))
    with pytest.raises(InvalidGenerationRequest):
        tasks.submit(_submission(now, aspect_ratio="4:3"))
    with pytest.raises(InvalidGenerationRequest, match="最多生成 5 张"):
        tasks.submit(_submission(now, quantity=6))

    assert credits.statement("account-space-1").frozen_credits == "0.0000"


@pytest.mark.parametrize(
    ("reference_media_ids", "mask_media_id"),
    (
        ((), "mask-1"),
        (("reference-1",), "reference-1"),
    ),
)
def test_invalid_mask_relationship_is_rejected_before_credits_are_frozen(
    reference_media_ids: tuple[str, ...],
    mask_media_id: str,
) -> None:
    tasks, credits, now = _credits_and_tasks()

    with pytest.raises(InvalidGenerationRequest):
        tasks.submit(
            _submission(
                now,
                reference_media_ids=reference_media_ids,
                mask_media_id=mask_media_id,
            )
        )

    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_concurrency_limit_rejects_new_task_without_freezing_more_credits() -> None:
    tasks, credits, now = _credits_and_tasks(max_active_tasks=1)
    tasks.submit(_submission(now, task_id="task-1"))

    with pytest.raises(GenerationConcurrencyLimit):
        tasks.submit(_submission(now, task_id="task-2"))

    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.1500"


def test_active_image_limit_counts_requested_quantity_and_releases_capacity_at_terminal_state() -> None:
    tasks, _, now = _credits_and_tasks(max_active_tasks=5)
    tasks.submit(_submission(now, task_id="task-4", quantity=4))
    tasks.submit(_submission(now, task_id="task-5", quantity=1))

    with pytest.raises(GenerationConcurrencyLimit):
        tasks.submit(_submission(now, task_id="task-overflow", quantity=1))

    tasks.transition(
        "account-space-1",
        "task-5",
        GenerationFailed(reason="provider failed", outcome_reference="failure-5", occurred_at=now),
    )


def test_submission_accepts_custom_pixel_size_on_sixteen_pixel_grid() -> None:
    tasks, _, now = _credits_and_tasks()

    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="custom-size-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="一座漂浮在云海上的图书馆",
            params=GenerationParameters(
                aspect_ratio="custom",
                size="1280x768",
                output_format="png",
            ),
            submitted_at=now,
        )
    )

    assert task.params.size == "1280x768"
    assert task.params.aspect_ratio == "custom"
    assert task.params.resolution_tier == ""


@pytest.mark.parametrize("size", ("255x1024", "1025x1024", "8208x1024", "1024x"))
def test_submission_rejects_invalid_custom_pixel_size(size: str) -> None:
    tasks, credits, now = _credits_and_tasks()

    with pytest.raises(InvalidGenerationRequest, match="自定义像素尺寸不对"):
        tasks.submit(
            GenerationSubmission(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id="canvas-1",
                task_id=f"invalid-{size}",
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="一座漂浮在云海上的图书馆",
                params=GenerationParameters(aspect_ratio="custom", size=size),
                submitted_at=now,
            )
        )

    assert credits.statement("account-space-1").frozen_credits == "0.0000"
    assert tasks.submit(_submission(now, task_id="task-after-release", quantity=1)).quantity == 1


def test_quantity_larger_than_remaining_active_image_capacity_is_rejected() -> None:
    tasks, _, now = _credits_and_tasks(max_active_tasks=5)
    tasks.submit(_submission(now, task_id="task-4", quantity=4))

    with pytest.raises(GenerationConcurrencyLimit):
        tasks.submit(_submission(now, task_id="task-2", quantity=2))


def test_queued_task_does_not_expire_before_provider_dispatch() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now))

    assert tasks.expire_due(now + timedelta(days=1)) == ()
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_running_task_expires_ten_minutes_after_provider_dispatch_and_releases_once() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now))
    started_at = now + timedelta(hours=1)
    tasks.transition("account-space-1", "task-1", GenerationDispatchStarted(occurred_at=started_at))

    assert tasks.expire_due(started_at + timedelta(minutes=10) - timedelta(seconds=1)) == ()

    expired = tasks.expire_due(started_at + timedelta(minutes=10))
    replay = tasks.expire_due(started_at + timedelta(minutes=20))

    assert len(expired) == 1
    assert expired[0].status is GenerationTaskStatus.FAILED
    assert expired[0].error == "generation task exceeded configured deadline"
    assert expired[0].outcome_reference == "generation-timeout:account-space-1:task-1"
    assert expired[0].updated_at == started_at + timedelta(minutes=10)
    assert replay == ()
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("release") == 1


def test_running_task_uses_the_current_administrator_deadline() -> None:
    deadline_minutes = [10]
    tasks, _, now = _credits_and_tasks(deadline_minutes=deadline_minutes)
    tasks.submit(_submission(now))
    started_at = now + timedelta(hours=1)
    tasks.transition("account-space-1", "task-1", GenerationDispatchStarted(occurred_at=started_at))

    deadline_minutes[0] = 3

    assert tasks.expire_due(started_at + timedelta(minutes=3) - timedelta(seconds=1)) == ()
    assert len(tasks.expire_due(started_at + timedelta(minutes=3))) == 1


def test_running_task_uses_dispatch_time_for_the_ten_minute_deadline() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now))
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now + timedelta(minutes=4)),
    )

    assert tasks.expire_due(now + timedelta(minutes=10)) == ()
    expired = tasks.expire_due(now + timedelta(minutes=14))

    assert len(expired) == 1
    assert expired[0].status is GenerationTaskStatus.FAILED
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_started_transition_marks_the_queued_task_as_running() -> None:
    tasks, _, now = _credits_and_tasks()
    tasks.submit(_submission(now))
    started_at = datetime(2026, 8, 8, 13, 1, tzinfo=UTC)

    task = tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=started_at),
    )

    assert task.status is GenerationTaskStatus.RUNNING
    assert task.provider_task_id == "provider-task-1"
    assert task.started_at == started_at
    assert task.updated_at == started_at
    assert tasks.get("account-space-1", "task-1") == task


def test_succeeded_transition_settles_delivered_quantity_and_releases_remainder() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now, quantity=2))
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    finished_at = datetime(2026, 8, 8, 13, 2, tzinfo=UTC)

    task = tasks.transition(
        "account-space-1",
        "task-1",
        GenerationSucceeded(
            delivered_quantity=1,
            outcome_reference="outcome-1",
            occurred_at=finished_at,
        ),
    )

    assert task.status is GenerationTaskStatus.SUCCEEDED
    assert task.delivered_quantity == 1
    assert task.updated_at == finished_at
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.0000"


def test_failed_transition_releases_all_frozen_credits_and_records_reason() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now, quantity=2))
    failed_at = datetime(2026, 8, 8, 13, 3, tzinfo=UTC)

    task = tasks.transition(
        "account-space-1",
        "task-1",
        GenerationFailed(
            reason="provider failed",
            outcome_reference="failure-1",
            occurred_at=failed_at,
        ),
    )

    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "provider failed"
    assert task.updated_at == failed_at
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"


def test_terminal_outcome_is_idempotent_but_cannot_be_changed() -> None:
    tasks, credits, now = _credits_and_tasks()
    tasks.submit(_submission(now))
    event = GenerationFailed(
        reason="provider failed",
        outcome_reference="failure-1",
        occurred_at=now,
    )

    first = tasks.transition("account-space-1", "task-1", event)
    replay = tasks.transition("account-space-1", "task-1", event)

    assert replay == first
    assert credits.statement("account-space-1").entries[-1].kind == "release"
    with pytest.raises(ValueError):
        tasks.transition(
            "account-space-1",
            "task-1",
            GenerationFailed(
                reason="different reason",
                outcome_reference="failure-2",
                occurred_at=now,
            ),
        )


def test_lookup_is_account_scoped_and_active_canvas_tasks_exclude_terminal_tasks() -> None:
    tasks, _, now = _credits_and_tasks()
    first = tasks.submit(_submission(now, task_id="task-1"))
    tasks.submit(_submission(now, task_id="task-2"))
    tasks.transition(
        "account-space-1",
        "task-2",
        GenerationCancelled(
            reason="user cancelled",
            outcome_reference="cancellation-1",
            occurred_at=now,
        ),
    )

    with pytest.raises(GenerationTaskNotFound):
        tasks.get("another-account-space", "task-1")
    assert tasks.active_across_accounts() == (first,)
    assert tasks.active_for_canvas("account-space-1", "canvas-1") == (first,)
    assert tasks.active_for_canvas("another-account-space", "canvas-1") == ()


def test_recent_canvas_tasks_include_terminal_tasks_newest_first_with_a_limit() -> None:
    tasks, _, now = _credits_and_tasks()
    tasks.submit(_submission(now, task_id="task-1"))
    tasks.submit(_submission(now + timedelta(seconds=1), task_id="task-2"))
    failed = tasks.transition(
        "account-space-1",
        "task-2",
        GenerationFailed(
            reason="generation attempts exhausted",
            outcome_reference="generation-attempt:attempt-2",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    newest = tasks.submit(_submission(now + timedelta(seconds=3), task_id="task-3"))

    assert tasks.recent_for_canvas("account-space-1", "canvas-1", limit=2) == (newest, failed)
    assert tasks.recent_for_canvas("another-account-space", "canvas-1", limit=2) == ()


def test_recent_account_tasks_span_canvases_and_include_terminal_tasks_with_a_global_limit() -> None:
    tasks, _, now = _credits_and_tasks(max_active_tasks=3)
    tasks.submit(_submission(now, task_id="task-1", canvas_id="canvas-1"))
    tasks.submit(_submission(now + timedelta(seconds=1), task_id="task-2", canvas_id="canvas-2"))
    failed = tasks.transition(
        "account-space-1",
        "task-2",
        GenerationFailed(
            reason="generation attempts exhausted",
            outcome_reference="generation-attempt:attempt-2",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    newest = tasks.submit(_submission(now + timedelta(seconds=3), task_id="task-3", canvas_id="canvas-1"))

    assert tasks.recent_for_account("account-space-1", limit=2) == (newest, failed)
    assert tasks.recent_for_account("another-account-space", limit=2) == ()

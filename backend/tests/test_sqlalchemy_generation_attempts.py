from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.canvases import CanvasCreation, SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits
from app.generation import GenerationParameters, GenerationSubmission, SqlAlchemyGenerationTasks
from app.generation_attempts import (
    AttemptAccepted,
    AttemptRejected,
    AttemptSubmissionStarted,
    AttemptSubmissionUnknown,
    GenerationAttemptPreparation,
    GenerationAttemptStatus,
    GenerationAttemptSubmitter,
    SqlAlchemyGenerationAttempts,
)
from app.generation_attempts._provider import ProviderGenerationRequest, ProviderSubmissionAccepted
from app.model_routing import (
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProviderCreation,
    ProviderProtocol,
    SqlAlchemyModelRouting,
)
from app.provider_costs import SqlAlchemyProviderCostRates


class AcceptingProviderSubmissions:
    def __init__(self) -> None:
        self.requests: list[ProviderGenerationRequest] = []

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        self.requests.append(request)
        return ProviderSubmissionAccepted(provider_task_id="provider-task-2")


def test_submitter_creates_a_second_sqlalchemy_attempt_after_confirmed_failure(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-attempt-retry.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, clock=lambda: now)
    registration = accounts.register("attempt-retry@example.com", "a-correct-horse-battery-staple")
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-retry-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-retry-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-retry-1", "route-retry-1")).__next__,
        clock=lambda: now,
    )
    provider_source = routing.create_provider(
        ProviderCreation(
            code="source-retry",
            display_name="来源 Retry",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-retry.example.com/v1",
            api_key="test-source-retry",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider_source.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    task = tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-retry-1",
            task_id="task-retry-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id=route.route_id,
            route_selection_reason="automatic",
        )
    )
    costs = SqlAlchemyProviderCostRates.for_database_url(
        database_url,
        id_factory=iter(("cost-retry-1", "cost-retry-2")).__next__,
        clock=lambda: now,
    )
    first_cost = costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    second_cost = costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=130_000,
        effective_from=now + timedelta(seconds=30),
    )
    attempts = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=tasks,
        provider_cost_rates=costs,
        id_factory=iter(("attempt-retry-1", "attempt-retry-2")).__next__,
    )
    first = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id=registration.account_space_id,
            task_id=task.task_id,
            route_id=route.route_id,
            occurred_at=now,
        )
    )
    attempts.transition(
        registration.account_space_id,
        first.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    failed = attempts.transition(
        registration.account_space_id,
        first.attempt_id,
        AttemptRejected(
            error_code="not_accepted",
            reason="provider confirmed the request was not accepted",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    provider = AcceptingProviderSubmissions()
    frozen_before_retry = credits.statement(registration.account_space_id).frozen_credits

    retried = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(minutes=1),
    ).submit(registration.account_space_id, task.task_id)

    assert failed.provider_cost_rate_id == first_cost.version_id
    assert retried.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert retried.attempt_id == "attempt-retry-2"
    assert retried.attempt_no == 2
    assert retried.route_id == task.selected_route_id
    assert retried.provider_idempotency_key != failed.provider_idempotency_key
    assert retried.provider_cost_rate_id == second_cost.version_id
    assert attempts.for_task(registration.account_space_id, task.task_id) == (failed, retried)
    assert provider.requests[0].provider_idempotency_key == retried.provider_idempotency_key
    persisted_task = tasks.get(registration.account_space_id, task.task_id)
    assert persisted_task.model_price_version_id == task.model_price_version_id
    assert persisted_task.status.value == "running"
    assert persisted_task.provider_task_id == retried.provider_task_id
    assert credits.statement(registration.account_space_id).frozen_credits == frozen_before_retry


def test_generation_attempt_survives_sqlalchemy_adapter_restart_and_replay(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-attempts.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, clock=lambda: now)
    registration = accounts.register("attempts@example.com", "a-correct-horse-battery-staple")
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        InMemoryProviderSecrets(),
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
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    task = tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id=route.route_id,
            route_selection_reason="automatic",
        )
    )
    costs = SqlAlchemyProviderCostRates.for_database_url(
        database_url,
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )
    first_cost = costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=tasks,
        provider_cost_rates=costs,
        id_factory=lambda: "attempt-1",
    )
    prepared = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id=registration.account_space_id,
            task_id=task.task_id,
            route_id=route.route_id,
            occurred_at=now,
        )
    )
    assert prepared.provider_cost_rate_id == first_cost.version_id
    costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=130_000,
        effective_from=now + timedelta(seconds=30),
    )
    attempts.transition(
        registration.account_space_id,
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown_event = AttemptSubmissionUnknown(
        reason="submission status is unknown",
        occurred_at=now + timedelta(seconds=2),
    )
    unknown = attempts.transition(registration.account_space_id, prepared.attempt_id, unknown_event)
    restarted_tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    restarted = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        provider_cost_rates=SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: now),
    )

    assert restarted.for_task(registration.account_space_id, task.task_id) == (unknown,)
    assert restarted.transition(registration.account_space_id, prepared.attempt_id, unknown_event) == unknown
    assert (
        restarted.prepare(
            GenerationAttemptPreparation(
                account_space_id=registration.account_space_id,
                task_id=task.task_id,
                route_id=route.route_id,
                occurred_at=now + timedelta(minutes=1),
            )
        )
        == unknown
    )
    accepted_event = AttemptAccepted(
        provider_task_id="provider-task-1",
        occurred_at=now + timedelta(seconds=3),
    )

    accepted = restarted.transition(registration.account_space_id, prepared.attempt_id, accepted_event)

    assert accepted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert accepted.attempt_id == unknown.attempt_id
    assert accepted.route_id == unknown.route_id
    assert accepted.provider_idempotency_key == unknown.provider_idempotency_key
    assert accepted.provider_cost_rate_id == first_cost.version_id
    assert accepted.provider_task_id == "provider-task-1"
    assert accepted.error == ""
    assert restarted.transition(registration.account_space_id, prepared.attempt_id, accepted_event) == accepted
    reloaded = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        provider_cost_rates=SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: now),
    )
    assert reloaded.for_task(registration.account_space_id, task.task_id) == (accepted,)

    with create_engine(database_url).begin() as database:
        database.execute(
            text("UPDATE image_generation_attempts SET provider_cost_rate_id = NULL WHERE id = :attempt_id"),
            {"attempt_id": prepared.attempt_id},
        )
    legacy = reloaded.for_task(registration.account_space_id, task.task_id)
    assert legacy[0].provider_cost_rate_id == ""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.accounts import InvalidEmailVerification, InvalidSession, SqlAlchemyAccountAccess
from app.assets import PersonalAssetSave, SqlAlchemyPersonalAssets
from app.auth_abuse import AuthAction, RateLimitPolicy, RateLimitSubject, SqlAlchemyAuthAbuseProtection
from app.canvases import CanvasCreation, CanvasSave, SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits, SqlAlchemyModelPrices
from app.generation import (
    GenerationFailed,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    SqlAlchemyGenerationTasks,
)
from app.generation_attempts import (
    AttemptRejected,
    AttemptSubmissionStarted,
    AttemptSubmissionUnknown,
    GenerationAttemptPreparation,
    SqlAlchemyGenerationAttempts,
)
from app.generation_results import GenerationOutput, GenerationOutputReceiver
from app.media import (
    GeneratedMediaRegistration,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
    SqlAlchemyGeneratedMedia,
)
from app.model_routing import (
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProviderCreation,
    ProviderProtocol,
    SqlAlchemyModelRouting,
)
from app.orders import PaymentChargeback, PaymentSuccess, RechargeOrderSubmission, SqlAlchemyRechargeOrders
from app.provider_costs import SqlAlchemyProviderCostRates
from app.runninghub_capabilities import (
    RunningHubCapabilityInput,
    RunningHubCapabilityPublication,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubUserPricePublication,
    SqlAlchemyRunningHubCapabilities,
)


class _RecordingVerificationDelivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))


def test_postgresql_can_apply_all_alembic_migrations() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    backend_root = Path(__file__).parents[1]
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_postgresql_sqlalchemy_account_credit_pricing_and_generation_flows() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    now = datetime(2027, 8, 8, 13, 0, tzinfo=UTC)
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, clock=lambda: now)
    registration = accounts.register("postgres-flow@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("postgres-flow@example.com", "a-correct-horse-battery-staple")
    assert accounts.current_user(session.access_token).account_space_id == registration.account_space_id
    assert accounts.credit_balance(session.access_token).available_credits == "0.0000"

    prices = SqlAlchemyModelPrices.for_database_url(database_url, clock=lambda: now)
    initial = prices.effective_at("gpt-image-2", "4k", now)
    replacement = prices.publish(
        "gpt-image-2",
        "4k",
        credits_per_result="0.2000",
        effective_from=now + timedelta(days=1),
    )
    assert initial.credits_per_result == "0.1500"
    assert prices.catalog_at(now) == (initial,)
    assert prices.effective_at("gpt-image-2", "4k", now + timedelta(days=1)) == replacement

    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    recharge = credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="postgres-payment-1",
        occurred_at=now,
    )
    assert (
        credits.record_recharge(
            registration.account_space_id,
            package.version_id,
            payment_reference="postgres-payment-1",
            occurred_at=now,
        )
        == recharge
    )
    reversible = credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="postgres-payment-2",
        occurred_at=now,
    )
    reversal = credits.reverse(
        reversible.posting_id,
        reversal_reference="postgres-reversal-1",
        reason="payment reversed",
        occurred_at=now,
    )
    assert reversal.reverses_posting_id == reversible.posting_id
    assert credits.get_version(package.version_id) == package
    assert credits.sellable_at(now) == (package,)
    orders = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=credits,
        credit_accounting=credits,
        clock=lambda: now,
    )
    order = orders.create(
        RechargeOrderSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            package_version_id=package.version_id,
            payment_provider="fakepay",
            idempotency_key="postgres-order-key-1",
            created_at=now,
        )
    )
    restarted_orders = SqlAlchemyRechargeOrders.for_database_url(
        database_url,
        packages=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        credit_accounting=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        clock=lambda: now,
    )
    assert restarted_orders.get(registration.account_space_id, order.order_id) == order
    assert restarted_orders.list(registration.account_space_id) == (order,)
    paid_order = restarted_orders.record_payment_success(
        PaymentSuccess(
            order_id=order.order_id,
            payment_provider="fakepay",
            provider_event_id="postgres-payment-event-1",
            paid_payment_cny="1.00",
            occurred_at=now,
        )
    )
    assert paid_order.status.value == "paid"
    assert (
        restarted_orders.record_payment_success(
            PaymentSuccess(
                order_id=order.order_id,
                payment_provider="fakepay",
                provider_event_id="postgres-payment-event-1",
                paid_payment_cny="1.00",
                occurred_at=now,
            )
        )
        == paid_order
    )
    chargeback = PaymentChargeback(
        order_id=order.order_id,
        payment_provider="fakepay",
        provider_event_id="postgres-chargeback-event-1",
        charged_back_payment_cny="1.00",
        occurred_at=now,
    )
    charged_back_order = restarted_orders.record_chargeback(chargeback)
    assert charged_back_order.status.value == "charged_back"
    assert restarted_orders.record_chargeback(chargeback) == charged_back_order
    freeze = credits.freeze(
        registration.account_space_id,
        "gpt-image-2",
        "4k",
        quantity=2,
        task_reference="postgres-freeze-1",
        occurred_at=now,
    )
    restarted_credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    settlement = restarted_credits.settle(
        freeze.freeze_id,
        delivered_quantity=1,
        settlement_reference="postgres-settlement-1",
        occurred_at=now,
    )
    assert settlement.delta_available_credits == "0.1500"
    release_freeze = restarted_credits.freeze(
        registration.account_space_id,
        "gpt-image-2",
        "4k",
        quantity=1,
        task_reference="postgres-freeze-2",
        occurred_at=now,
    )
    release = restarted_credits.release(
        release_freeze.freeze_id,
        release_reference="postgres-release-1",
        reason="provider failed",
        occurred_at=now,
    )
    assert release.kind == "release"
    assert restarted_credits.statement(registration.account_space_id).frozen_credits == "0.0000"

    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    postgres_canvas = canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="PostgreSQL 生成画布",
            kind="classic",
            created_at=now,
        )
    )
    saved_canvas = canvases.save(
        CanvasSave(
            account_space_id=registration.account_space_id,
            canvas_id=postgres_canvas.canvas_id,
            expected_version=1,
            title="PostgreSQL 已保存画布",
            document={
                "nodes": [{"id": "postgres-node-1", "type": "image"}],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
            saved_at=now,
        )
    )
    assert (
        SqlAlchemyCanvases.for_database_url(database_url).get(registration.account_space_id, postgres_canvas.canvas_id)
        == saved_canvas
    )
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        InMemoryProviderSecrets(),
        id_factory=iter(("postgres-provider-1", "postgres-route-1")).__next__,
        clock=lambda: now,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="postgres-source-a",
            display_name="PostgreSQL 来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://postgres-source-a.example.com/v1",
            api_key="test-postgres-source-a",
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
    provider_costs = SqlAlchemyProviderCostRates.for_database_url(
        database_url,
        id_factory=iter(("postgres-cost-rate-1", "postgres-cost-rate-2")).__next__,
        clock=lambda: now,
    )
    first_cost = provider_costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    replacement_cost = provider_costs.publish(
        route.route_id,
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=150_000,
        effective_from=now + timedelta(days=1),
    )
    restarted_provider_costs = SqlAlchemyProviderCostRates.for_database_url(database_url, clock=lambda: now)
    assert restarted_provider_costs.effective_at(route.route_id, "4k", now) == first_cost
    assert restarted_provider_costs.effective_at(route.route_id, "4k", now + timedelta(days=1)) == replacement_cost
    runninghub_capabilities = SqlAlchemyRunningHubCapabilities.for_database_url(
        database_url,
        id_factory=iter(
            (
                "postgres-runninghub-capability-1",
                "postgres-runninghub-schema-1",
                "postgres-runninghub-price-1",
            )
        ).__next__,
        clock=lambda: now,
    )
    runninghub_capability = runninghub_capabilities.publish(
        RunningHubCapabilityPublication(
            name="PostgreSQL 商品摄影",
            workflow_id="postgres-internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT, RunningHubInputCapability.IMAGE),
            available=True,
        )
    )
    runninghub_input_schema = runninghub_capabilities.publish_input_schema(
        RunningHubInputSchemaPublication(
            capability_id=runninghub_capability.capability_id,
            inputs=(
                RunningHubCapabilityInput("prompt", "提示词", RunningHubInputCapability.TEXT, True),
                RunningHubCapabilityInput(
                    "reference_image",
                    "参考图",
                    RunningHubInputCapability.IMAGE,
                    False,
                ),
            ),
        )
    )
    runninghub_user_price = runninghub_capabilities.publish_user_price(
        RunningHubUserPricePublication(
            capability_id=runninghub_capability.capability_id,
            credits_per_run="0.1000",
            effective_from=now,
        )
    )
    restarted_runninghub_capabilities = SqlAlchemyRunningHubCapabilities.for_database_url(
        database_url,
        clock=lambda: now,
    )
    restarted_runninghub_capability = restarted_runninghub_capabilities.list_for_administration()[0]
    assert restarted_runninghub_capability.capability_id == runninghub_capability.capability_id
    assert restarted_runninghub_capabilities.input_schema_versions(runninghub_capability.capability_id) == (
        runninghub_input_schema,
    )
    assert restarted_runninghub_capabilities.user_price_versions(runninghub_capability.capability_id) == (
        runninghub_user_price,
    )
    public_runninghub_capability = restarted_runninghub_capabilities.catalog()[0]
    assert public_runninghub_capability.capability_id == runninghub_capability.capability_id
    assert public_runninghub_capability.input_schema is not None
    assert public_runninghub_capability.input_schema.schema_version_id == runninghub_input_schema.schema_version_id
    assert public_runninghub_capability.input_schema.inputs == runninghub_input_schema.inputs
    assert public_runninghub_capability.credits_per_run == "0.1000"
    assert not hasattr(public_runninghub_capability, "workflow_id")
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=restarted_credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    submitted = tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="postgres-task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id=route.route_id,
            route_selection_reason="automatic",
        )
    )
    attempts = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=tasks,
        provider_cost_rates=restarted_provider_costs,
        id_factory=lambda: "postgres-attempt-1",
    )
    prepared_attempt = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id=registration.account_space_id,
            task_id=submitted.task_id,
            route_id=route.route_id,
            occurred_at=now,
        )
    )
    assert prepared_attempt.provider_cost_rate_id == first_cost.version_id
    attempts.transition(
        registration.account_space_id,
        prepared_attempt.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown_attempt = attempts.transition(
        registration.account_space_id,
        prepared_attempt.attempt_id,
        AttemptSubmissionUnknown(
            reason="postgres submission status is unknown",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    restarted_tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now),
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    assert restarted_tasks.get(registration.account_space_id, submitted.task_id) == submitted
    restarted_attempts = SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        provider_cost_rates=restarted_provider_costs,
    )
    assert restarted_attempts.for_task(registration.account_space_id, submitted.task_id) == (unknown_attempt,)
    rejected_event = AttemptRejected(
        error_code="not_accepted",
        reason="provider confirmed the request was not accepted",
        occurred_at=now + timedelta(seconds=3),
    )

    rejected_attempt = restarted_attempts.transition(
        registration.account_space_id,
        prepared_attempt.attempt_id,
        rejected_event,
    )

    assert rejected_attempt.status.value == "failed"
    assert rejected_attempt.attempt_id == unknown_attempt.attempt_id
    assert rejected_attempt.route_id == unknown_attempt.route_id
    assert rejected_attempt.provider_idempotency_key == unknown_attempt.provider_idempotency_key
    assert rejected_attempt.provider_cost_rate_id == first_cost.version_id
    assert (
        restarted_attempts.transition(
            registration.account_space_id,
            prepared_attempt.attempt_id,
            rejected_event,
        )
        == rejected_attempt
    )
    assert SqlAlchemyGenerationAttempts.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        provider_cost_rates=restarted_provider_costs,
    ).for_task(registration.account_space_id, submitted.task_id) == (rejected_attempt,)
    assert restarted_tasks.get(registration.account_space_id, submitted.task_id).status.value == "queued"
    assert restarted_credits.statement(registration.account_space_id).frozen_credits == "0.3000"
    restarted_tasks.transition(
        registration.account_space_id,
        submitted.task_id,
        GenerationStarted(provider_task_id="provider-1", occurred_at=now),
    )
    media_objects = InMemoryMediaObjects(
        {
            "temporary/postgres-task-1/result-1.png",
            "temporary/postgres-task-1/result-2.png",
        }
    )
    storage_allowances = InMemoryStorageAllowances({registration.account_space_id: 1235})
    generated_media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
        id_factory=iter(("postgres-media-1", "postgres-media-2")).__next__,
    )
    media = generated_media.register(
        GeneratedMediaRegistration(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id=submitted.task_id,
            result_reference="postgres-result-1",
            object_key="temporary/postgres-task-1/result-1.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    expiring_media = generated_media.register(
        GeneratedMediaRegistration(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id=submitted.task_id,
            result_reference="postgres-result-2",
            object_key="temporary/postgres-task-1/result-2.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1,
            content_hash="b" * 64,
            created_at=now,
        )
    )
    restarted_media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
    )
    assert restarted_media.get(registration.account_space_id, media.media_id) == media
    outputs = (
        GenerationOutput(
            result_reference="postgres-result-1",
            object_key="temporary/postgres-task-1/result-1.png",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
        ),
        GenerationOutput(
            result_reference="postgres-result-2",
            object_key="temporary/postgres-task-1/result-2.png",
            mime_type="image/png",
            size_bytes=1,
            content_hash="b" * 64,
        ),
    )
    receiver = GenerationOutputReceiver(restarted_tasks, restarted_media)
    succeeded = receiver.receive(
        registration.account_space_id,
        submitted.task_id,
        outputs,
        completed_at=now,
    )
    assert succeeded.status.value == "succeeded"
    assert succeeded.delivered_quantity == 2
    assert (
        receiver.receive(
            registration.account_space_id,
            submitted.task_id,
            outputs,
            completed_at=now,
        )
        == succeeded
    )
    assert restarted_media.list_for_task(registration.account_space_id, submitted.task_id) == (
        media,
        expiring_media,
    )
    retained_media = restarted_media.retain_to_canvas(registration.account_space_id, media.media_id, now)
    assert retained_media.state.value == "persistent"
    assert retained_media.expires_at is None
    personal_assets = SqlAlchemyPersonalAssets.for_database_url(
        database_url,
        generated_media=restarted_media,
        id_factory=lambda: "postgres-asset-1",
    )
    personal_assets.save_generated_media(
        PersonalAssetSave(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            media_id=media.media_id,
            display_name="PostgreSQL 资产",
            idempotency_key="postgres-save-result-1",
            saved_at=now,
        )
    )
    removed_at = now + timedelta(minutes=1)

    personal_assets.remove(registration.account_space_id, "postgres-asset-1", removed_at)

    assert personal_assets.list(registration.account_space_id) == ()
    assert restarted_media.get(registration.account_space_id, media.media_id).state.value == "persistent"
    release = restarted_media.reconcile_canvas_references(
        registration.account_space_id,
        "canvas-1",
        (),
        removed_at,
    )
    assert release.released_media_ids == (media.media_id,)
    assert restarted_media.get(registration.account_space_id, media.media_id).state.value == "released"
    expiration = restarted_media.expire_due(now + timedelta(hours=24))
    assert expiration.expired_media_ids == (expiring_media.media_id,)
    assert restarted_media.get(registration.account_space_id, expiring_media.media_id).state.value == "expired"
    failed = restarted_tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="postgres-task-2",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    failure = GenerationFailed(reason="provider failed", outcome_reference="postgres-failure-1", occurred_at=now)
    failed_result = restarted_tasks.transition(registration.account_space_id, failed.task_id, failure)
    assert failed_result.status.value == "failed"
    assert restarted_tasks.transition(registration.account_space_id, failed.task_id, failure) == failed_result


def test_postgresql_account_session_and_email_verification_security_flows() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    current_time = [datetime(2026, 8, 8, 14, 0, tzinfo=UTC)]
    delivery = _RecordingVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        clock=lambda: current_time[0],
        session_ttl=timedelta(minutes=5),
        verification_delivery=delivery,
        verification_ttl=timedelta(minutes=5),
    )
    accounts.register("postgres-security@example.com", "a-correct-horse-battery-staple")
    verification_token = delivery.messages[-1][1]
    first_session = accounts.login("postgres-security@example.com", "a-correct-horse-battery-staple")
    second_session = accounts.login("postgres-security@example.com", "a-correct-horse-battery-staple")

    restarted = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        clock=lambda: current_time[0],
        session_ttl=timedelta(minutes=5),
        verification_delivery=delivery,
        verification_ttl=timedelta(minutes=5),
    )
    restarted.verify_email(verification_token)
    assert restarted.current_user(first_session.access_token).email_verified is True
    with pytest.raises(InvalidEmailVerification):
        restarted.verify_email(verification_token)

    restarted.logout(first_session.access_token)
    with pytest.raises(InvalidSession):
        restarted.current_user(first_session.access_token)
    assert restarted.current_user(second_session.access_token).email == "postgres-security@example.com"

    current_time[0] += timedelta(minutes=6)
    with pytest.raises(InvalidSession):
        restarted.current_user(second_session.access_token)
    with pytest.raises(InvalidSession):
        restarted.credit_balance(second_session.access_token)


def test_postgresql_auth_rate_limit_is_atomic_across_adapter_instances() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    hash_key = "postgres-auth-rate-limit-test-key-0001"
    subject_value = f"concurrent-{uuid4()}@example.com"
    adapters = (
        SqlAlchemyAuthAbuseProtection.for_database_url(database_url, hash_key=hash_key, clock=lambda: now),
        SqlAlchemyAuthAbuseProtection.for_database_url(database_url, hash_key=hash_key, clock=lambda: now),
    )
    subjects = (RateLimitSubject("email", subject_value, RateLimitPolicy(5, timedelta(minutes=10))),)

    def consume(index: int) -> bool:
        return adapters[index % len(adapters)].consume(AuthAction.LOGIN, subjects).allowed

    with ThreadPoolExecutor(max_workers=10) as executor:
        allowed = list(executor.map(consume, range(20)))

    assert allowed.count(True) == 5
    assert allowed.count(False) == 15
    with create_engine(database_url).connect() as connection:
        stored_hash = connection.execute(
            text(
                "SELECT subject_hash FROM auth_rate_limit_windows "
                "WHERE action = 'login' AND subject_scope = 'email' "
                "ORDER BY last_seen_at DESC LIMIT 1"
            )
        ).scalar_one()
    assert stored_hash != subject_value
    assert len(stored_hash) == 64

    adapters[1].reset(AuthAction.LOGIN, "email", subject_value)
    assert adapters[0].consume(AuthAction.LOGIN, subjects).allowed is True

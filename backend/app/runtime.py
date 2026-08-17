"""Production composition root for the SaaS HTTP application."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.account_generation_limits import SqlAlchemyAccountGenerationLimits
from app.accounts import AccountAccess, InvalidSession, SqlAlchemyAccountAccess
from app.assets import SqlAlchemyPersonalAssets
from app.auth_abuse import (
    AuthAbusePolicies,
    ClientIpResolver,
    RateLimitPolicy,
    SqlAlchemyAuthAbuseProtection,
)
from app.canvases import SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits, SqlAlchemyModelPrices
from app.database_runtime import configure_postgresql_engine, postgres_advisory_lock
from app.email_settings import SqlAlchemyEmailSettings
from app.generation import GenerationDeadlineScheduler, SqlAlchemyGenerationTasks
from app.generation_attempts import GenerationAttemptSubmitter, SqlAlchemyGenerationAttempts
from app.generation_results import GenerationImageDelivery
from app.http import HttpSecuritySettings, create_app
from app.http.security import validate_allowed_hosts
from app.media import (
    MediaContentStore,
    SqlAlchemyGeneratedMedia,
    SqlAlchemyStorageAllowances,
    configured_file_system_media_objects,
)
from app.model_routing import (
    HttpxRouteProbe,
    ProviderSecrets,
    RouteHealthScheduler,
    SqlAlchemyModelRouting,
    configured_file_system_provider_secrets,
)
from app.orders import SqlAlchemyRechargeOrders
from app.payments import SqlAlchemyEpayPayments
from app.platform_content import SqlAlchemyPlatformContentSettings
from app.prompt_assets import SqlAlchemyPromptAssets
from app.provider_costs import SqlAlchemyProviderCostRates, SqlAlchemyProviderCostSummaries
from app.provider_images import OpenAICompatibleImageSubmissions
from app.reference_media import SqlAlchemyReferenceMedia
from app.runninghub_capabilities import SqlAlchemyRunningHubCapabilities
from app.user_llm import SqlAlchemyUserLLMProviders
from app.worker_capacity import SqlAlchemyWorkerCapacitySettings

_LOG = logging.getLogger(__name__)


class ProductionConfigurationError(RuntimeError):
    """A required production setting is missing or unsafe."""


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    """Validated values consumed by the multi-process SaaS composition root."""

    database_url: str
    generated_media_root: Path
    provider_secrets_root: Path
    platform_admin_emails: frozenset[str]
    max_active_generation_tasks: int
    database_pool_size: int
    database_max_overflow: int
    database_pool_timeout_seconds: float
    generation_submission_mode: str
    generation_worker_deployed_limit: int
    auth_rate_limit_hash_key: str
    auth_abuse_policies: AuthAbusePolicies
    trusted_proxy_cidrs: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    enable_hsts: bool

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> ProductionSettings:
        """Load production settings without providing unsafe fallback storage."""
        values = os.environ if environ is None else environ
        database_url = values.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ProductionConfigurationError("DATABASE_URL must be configured")
        try:
            driver_name = make_url(database_url).drivername
        except ArgumentError as exc:
            raise ProductionConfigurationError("DATABASE_URL must be a valid PostgreSQL URL") from exc
        if driver_name != "postgresql+psycopg":
            raise ProductionConfigurationError("DATABASE_URL must use PostgreSQL through the psycopg driver")

        media_root_value = values.get("GENERATED_MEDIA_ROOT", "").strip()
        if not media_root_value:
            raise ProductionConfigurationError("GENERATED_MEDIA_ROOT must be configured")
        generated_media_root = Path(media_root_value)
        if not generated_media_root.is_absolute():
            raise ProductionConfigurationError("GENERATED_MEDIA_ROOT must be an absolute path")

        platform_admin_emails = frozenset(
            email.strip().casefold() for email in values.get("PLATFORM_ADMIN_EMAILS", "").split(",") if email.strip()
        )
        if not platform_admin_emails:
            raise ProductionConfigurationError("PLATFORM_ADMIN_EMAILS must contain at least one account email")

        provider_secrets_root_value = values.get("PROVIDER_SECRETS_ROOT", "").strip()
        if not provider_secrets_root_value:
            raise ProductionConfigurationError("PROVIDER_SECRETS_ROOT must be configured")
        provider_secrets_root = Path(provider_secrets_root_value)
        if not provider_secrets_root.is_absolute():
            raise ProductionConfigurationError("PROVIDER_SECRETS_ROOT must be an absolute path")
        if provider_secrets_root.resolve() == generated_media_root.resolve():
            raise ProductionConfigurationError(
                "PROVIDER_SECRETS_ROOT and GENERATED_MEDIA_ROOT must be different directories"
            )

        configured_limit = values.get("MAX_ACTIVE_GENERATION_TASKS", "20").strip()
        try:
            max_active_generation_tasks = int(configured_limit)
        except ValueError as exc:
            raise ProductionConfigurationError("MAX_ACTIVE_GENERATION_TASKS must be a positive integer") from exc
        if max_active_generation_tasks <= 0:
            raise ProductionConfigurationError("MAX_ACTIVE_GENERATION_TASKS must be a positive integer")

        database_pool_size = _positive_int(values, "DATABASE_POOL_SIZE", 8)
        database_max_overflow = _non_negative_int(values, "DATABASE_MAX_OVERFLOW", 4)
        database_pool_timeout_seconds = _positive_float(values, "DATABASE_POOL_TIMEOUT_SECONDS", 10.0)
        generation_submission_mode = values.get("GENERATION_SUBMISSION_MODE", "queued").strip().casefold()
        if generation_submission_mode not in {"queued", "inline"}:
            raise ProductionConfigurationError("GENERATION_SUBMISSION_MODE must be queued or inline")

        auth_rate_limit_hash_key = values.get("AUTH_RATE_LIMIT_HASH_KEY", "").strip()
        if len(auth_rate_limit_hash_key.encode("utf-8")) < 32:
            raise ProductionConfigurationError("AUTH_RATE_LIMIT_HASH_KEY must contain at least 32 bytes")
        trusted_proxy_cidrs = tuple(
            value.strip() for value in values.get("TRUSTED_PROXY_CIDRS", "").split(",") if value.strip()
        )
        try:
            ClientIpResolver(trusted_proxy_cidrs)
            if any(ip_network(value, strict=False).prefixlen == 0 for value in trusted_proxy_cidrs):
                raise ValueError("all-address networks are unsafe")
        except ValueError as exc:
            raise ProductionConfigurationError(
                "TRUSTED_PROXY_CIDRS must contain specific valid IP networks, not all-address networks"
            ) from exc
        try:
            allowed_hosts = validate_allowed_hosts(values.get("ALLOWED_HOSTS", "").split(","))
        except ValueError as exc:
            raise ProductionConfigurationError("ALLOWED_HOSTS must contain exact public host names") from exc
        auth_abuse_policies = AuthAbusePolicies(
            login_ip=RateLimitPolicy(
                _positive_int(values, "AUTH_LOGIN_IP_LIMIT", 10),
                timedelta(seconds=_positive_int(values, "AUTH_LOGIN_WINDOW_SECONDS", 600)),
            ),
            login_email=RateLimitPolicy(
                _positive_int(values, "AUTH_LOGIN_EMAIL_LIMIT", 5),
                timedelta(seconds=_positive_int(values, "AUTH_LOGIN_WINDOW_SECONDS", 600)),
            ),
            register_ip=RateLimitPolicy(
                _positive_int(values, "AUTH_REGISTER_IP_LIMIT", 5),
                timedelta(seconds=_positive_int(values, "AUTH_REGISTER_WINDOW_SECONDS", 3600)),
            ),
            email_verification_account=RateLimitPolicy(
                _positive_int(values, "AUTH_EMAIL_VERIFICATION_ACCOUNT_LIMIT", 3),
                timedelta(seconds=_positive_int(values, "AUTH_EMAIL_VERIFICATION_WINDOW_SECONDS", 3600)),
            ),
        )

        return cls(
            database_url=database_url,
            generated_media_root=generated_media_root,
            provider_secrets_root=provider_secrets_root,
            platform_admin_emails=platform_admin_emails,
            max_active_generation_tasks=max_active_generation_tasks,
            database_pool_size=database_pool_size,
            database_max_overflow=database_max_overflow,
            database_pool_timeout_seconds=database_pool_timeout_seconds,
            generation_submission_mode=generation_submission_mode,
            generation_worker_deployed_limit=_positive_int(values, "GENERATION_WORKER_DEPLOYED_LIMIT", 4),
            auth_rate_limit_hash_key=auth_rate_limit_hash_key,
            auth_abuse_policies=auth_abuse_policies,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
            allowed_hosts=allowed_hosts,
            enable_hsts=_boolean(values, "ENABLE_HSTS", False),
        )


def _positive_int(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(values.get(name, str(default)).strip())
    except ValueError as exc:
        raise ProductionConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ProductionConfigurationError(f"{name} must be a positive integer")
    return value


def _non_negative_int(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(values.get(name, str(default)).strip())
    except ValueError as exc:
        raise ProductionConfigurationError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ProductionConfigurationError(f"{name} must be a non-negative integer")
    return value


def _positive_float(values: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(values.get(name, str(default)).strip())
    except ValueError as exc:
        raise ProductionConfigurationError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ProductionConfigurationError(f"{name} must be a positive number")
    return value


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    configured = values.get(name, "true" if default else "false").strip().casefold()
    if configured not in {"true", "false"}:
        raise ProductionConfigurationError(f"{name} must be true or false")
    return configured == "true"


def account_admin_authorizer(
    accounts: AccountAccess,
    configured_emails: frozenset[str],
) -> Callable[[str], None]:
    """Authorize an existing Bearer session by its normalized account email."""
    normalized_emails = frozenset(email.casefold() for email in configured_emails)

    def authorize(access_token: str) -> None:
        try:
            current = accounts.current_user(access_token)
        except InvalidSession as exc:
            raise PermissionError("platform administrator access required") from exc
        if current.email.casefold() not in normalized_emails:
            raise PermissionError("platform administrator access required")

    return authorize


def create_production_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    """Compose persistent SaaS adapters from explicit production settings."""
    settings = ProductionSettings.from_environ(environ)
    media_objects = configured_file_system_media_objects(environ)
    provider_secrets = configured_file_system_provider_secrets(environ)
    engine = configure_postgresql_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    app = _compose_application(settings, sessions, media_objects, provider_secrets, engine=engine)
    app.state.database_engine = engine
    _install_database_lifecycle(app, engine)
    return app


def _install_database_lifecycle(app: FastAPI, engine: Engine) -> None:
    @app.get("/readyz", include_in_schema=False)
    def readiness_check() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable") from exc
        return {"status": "ready"}

    async def dispose_database_engine() -> None:
        engine.dispose()

    app.router.add_event_handler("shutdown", dispose_database_engine)


def install_route_health_schedule(
    app: FastAPI,
    scheduler: RouteHealthScheduler,
    *,
    poll_interval: timedelta = timedelta(minutes=1),
    run_guard: Callable[[], AbstractContextManager[bool]] | None = None,
) -> None:
    """Run due route checks under an optional cluster-wide guard."""
    if poll_interval <= timedelta(0):
        raise ValueError("route health poll interval must be positive")

    async def run() -> None:
        while True:
            try:
                if run_guard is None:
                    await asyncio.to_thread(scheduler.run_due)
                else:
                    def guarded_run() -> None:
                        with run_guard() as acquired:
                            if acquired:
                                scheduler.run_due()

                    await asyncio.to_thread(guarded_run)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("automatic route health check failed")
            await asyncio.sleep(poll_interval.total_seconds())

    async def start() -> None:
        app.state.route_health_schedule_task = asyncio.create_task(run())

    async def stop() -> None:
        task: asyncio.Task[None] | None = getattr(app.state, "route_health_schedule_task", None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app.router.add_event_handler("startup", start)
    app.router.add_event_handler("shutdown", stop)
    app.state.route_health_scheduler = scheduler


def install_generation_deadline_schedule(
    app: FastAPI,
    scheduler: GenerationDeadlineScheduler,
    *,
    poll_interval: timedelta = timedelta(seconds=1),
    run_guard: Callable[[], AbstractContextManager[bool]] | None = None,
) -> None:
    """持续执行生成任务截止扫描，可由集群级互斥保护。"""
    if poll_interval <= timedelta(0):
        raise ValueError("generation deadline poll interval must be positive")

    async def run() -> None:
        while True:
            try:
                if run_guard is None:
                    await asyncio.to_thread(scheduler.run_due)
                else:
                    def guarded_run() -> None:
                        with run_guard() as acquired:
                            if acquired:
                                scheduler.run_due()

                    await asyncio.to_thread(guarded_run)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("automatic generation deadline scan failed")
            await asyncio.sleep(poll_interval.total_seconds())

    async def start() -> None:
        app.state.generation_deadline_schedule_task = asyncio.create_task(run())

    async def stop() -> None:
        task: asyncio.Task[None] | None = getattr(app.state, "generation_deadline_schedule_task", None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app.router.add_event_handler("startup", start)
    app.router.add_event_handler("shutdown", stop)
    app.state.generation_deadline_scheduler = scheduler


def _compose_application(
    settings: ProductionSettings,
    sessions: sessionmaker[Session],
    media_objects: MediaContentStore,
    provider_secrets: ProviderSecrets,
    *,
    engine: Engine,
) -> FastAPI:
    email_settings = SqlAlchemyEmailSettings(sessions, provider_secrets)
    platform_content = SqlAlchemyPlatformContentSettings(sessions, media_objects)
    accounts = SqlAlchemyAccountAccess(sessions, verification_delivery=email_settings)
    model_prices = SqlAlchemyModelPrices(sessions)
    credits = SqlAlchemyCredits(sessions, model_prices=model_prices)
    canvases = SqlAlchemyCanvases(sessions)
    worker_capacity = SqlAlchemyWorkerCapacitySettings(
        sessions,
        deployed_worker_limit=settings.generation_worker_deployed_limit,
    )
    generation_tasks = SqlAlchemyGenerationTasks(
        sessions,
        credits=credits,
        canvases=canvases,
        max_active_tasks=settings.max_active_generation_tasks,
        deadline=lambda: timedelta(minutes=worker_capacity.current().task_deadline_minutes),
    )
    storage_allowances = SqlAlchemyStorageAllowances(sessions)
    account_generation_limits = SqlAlchemyAccountGenerationLimits(sessions)
    generated_media = SqlAlchemyGeneratedMedia(
        sessions,
        generation_tasks=generation_tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
    )
    reference_media = SqlAlchemyReferenceMedia(sessions, media_objects=media_objects)
    provider_cost_rates = SqlAlchemyProviderCostRates(sessions)
    model_routing = SqlAlchemyModelRouting(
        sessions,
        provider_secrets,
        probe=HttpxRouteProbe(),
    )
    generation_attempts = SqlAlchemyGenerationAttempts(
        sessions,
        generation_tasks=generation_tasks,
        provider_cost_rates=provider_cost_rates,
    )
    generation_attempt_submissions = GenerationAttemptSubmitter(
        generation_tasks,
        generation_attempts,
        OpenAICompatibleImageSubmissions(model_routing),
        image_delivery=GenerationImageDelivery(generation_tasks, generated_media, media_objects),
        reference_media=reference_media,
        clock=lambda: datetime.now(UTC),
    )
    personal_assets = SqlAlchemyPersonalAssets(sessions, generated_media=generated_media)
    prompt_assets = SqlAlchemyPromptAssets(sessions)
    user_llm_providers = SqlAlchemyUserLLMProviders(sessions, provider_secrets)
    recharge_orders = SqlAlchemyRechargeOrders(
        sessions,
        packages=credits,
        credit_accounting=credits,
    )
    epay_payments = SqlAlchemyEpayPayments(sessions, provider_secrets)
    auth_abuse_protection = SqlAlchemyAuthAbuseProtection(
        sessions,
        hash_key=settings.auth_rate_limit_hash_key,
    )

    app = create_app(
        accounts,
        account_directory=accounts,
        credit_accounting=credits,
        generation_tasks=generation_tasks,
        generation_attempt_submissions=(
            generation_attempt_submissions if settings.generation_submission_mode == "inline" else None
        ),
        generation_submission_deferred=settings.generation_submission_mode == "queued",
        generated_media=generated_media,
        media_content=media_objects,
        reference_media=reference_media,
        storage_allowances=storage_allowances,
        account_generation_limits=account_generation_limits,
        personal_assets=personal_assets,
        prompt_assets=prompt_assets,
        user_llm_providers=user_llm_providers,
        canvases=canvases,
        model_prices=model_prices,
        recharge_packages=credits,
        recharge_orders=recharge_orders,
        recharge_order_chargebacks=recharge_orders,
        payment_methods=epay_payments,
        epay_payments=epay_payments,
        model_routing=model_routing,
        provider_cost_rates=provider_cost_rates,
        provider_cost_summaries=SqlAlchemyProviderCostSummaries(sessions),
        runninghub_capabilities=SqlAlchemyRunningHubCapabilities(sessions),
        worker_capacity=worker_capacity,
        email_settings=email_settings,
        platform_content=platform_content,
        admin_authorizer=account_admin_authorizer(accounts, settings.platform_admin_emails),
        auth_abuse_protection=auth_abuse_protection,
        auth_abuse_policies=settings.auth_abuse_policies,
        client_ip_resolver=ClientIpResolver(settings.trusted_proxy_cidrs),
        http_security=HttpSecuritySettings(
            allowed_hosts=settings.allowed_hosts,
            enable_hsts=settings.enable_hsts,
        ),
    )
    app.state.generation_tasks = generation_tasks
    app.state.generation_attempt_submissions = generation_attempt_submissions
    app.state.worker_capacity = worker_capacity
    app.state.account_generation_limits = account_generation_limits
    install_route_health_schedule(
        app,
        RouteHealthScheduler(model_routing),
        run_guard=lambda: postgres_advisory_lock(engine, "scheduler:route-health"),
    )
    install_generation_deadline_schedule(
        app,
        GenerationDeadlineScheduler(generation_tasks),
        run_guard=lambda: postgres_advisory_lock(engine, "scheduler:generation-deadline"),
    )
    return app

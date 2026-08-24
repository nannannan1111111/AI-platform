from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.generation import GenerationDeadlineScheduler
from app.http import create_app
from app.model_routing import (
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProbeResult,
    ProviderCreation,
    ProviderProtocol,
    ProviderUpdate,
    RouteHealthScheduler,
    RouteHealthStatus,
    RouteProbeTarget,
)
from app.runtime import (
    ProductionConfigurationError,
    ProductionSettings,
    account_admin_authorizer,
    create_production_app,
    install_generation_deadline_schedule,
    install_route_health_schedule,
)


def test_production_settings_require_postgresql_media_root_and_admins(tmp_path: Path) -> None:
    with pytest.raises(ProductionConfigurationError, match="DATABASE_URL"):
        ProductionSettings.from_environ({})

    with pytest.raises(ProductionConfigurationError, match="PostgreSQL"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "sqlite:///ignored.db",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            }
        )

    with pytest.raises(ProductionConfigurationError, match="PLATFORM_ADMIN_EMAILS"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
            }
        )


def test_production_settings_require_a_provider_secret_directory(tmp_path: Path) -> None:
    with pytest.raises(ProductionConfigurationError, match="PROVIDER_SECRETS_ROOT"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            }
        )


def test_production_settings_keep_provider_secrets_separate_from_generated_media(tmp_path: Path) -> None:
    with pytest.raises(ProductionConfigurationError, match="must be different"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PROVIDER_SECRETS_ROOT": str(tmp_path),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            }
        )


def test_production_settings_parse_explicit_deployment_values(tmp_path: Path) -> None:
    provider_secrets_root = tmp_path / "provider-secrets"
    settings = ProductionSettings.from_environ(
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
            "GENERATED_MEDIA_ROOT": str(tmp_path),
            "PROVIDER_SECRETS_ROOT": str(provider_secrets_root),
            "PLATFORM_ADMIN_EMAILS": " Admin@Example.com,ops@example.com ",
            "MAX_ACTIVE_GENERATION_TASKS": "7",
            "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
            "ALLOWED_HOSTS": "studio.example.com",
        }
    )

    assert settings.database_url == "postgresql+psycopg://example.invalid/app"
    assert settings.generated_media_root == tmp_path
    assert settings.provider_secrets_root == provider_secrets_root
    assert settings.platform_admin_emails == frozenset({"admin@example.com", "ops@example.com"})
    assert settings.max_active_generation_tasks == 7
    assert settings.database_pool_size == 8
    assert settings.database_max_overflow == 4
    assert settings.database_pool_timeout_seconds == 10
    assert settings.generation_submission_mode == "queued"
    assert settings.generation_worker_deployed_limit == 10
    assert settings.auth_abuse_policies.login_ip.limit == 10
    assert settings.auth_abuse_policies.login_email.limit == 5
    assert settings.trusted_proxy_cidrs == ()
    assert settings.allowed_hosts == ("studio.example.com", "127.0.0.1", "localhost")
    assert settings.enable_hsts is False
    assert settings.metrics_token is None


def test_production_settings_default_to_five_hundred_active_image_units(tmp_path: Path) -> None:
    settings = ProductionSettings.from_environ(
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
            "GENERATED_MEDIA_ROOT": str(tmp_path),
            "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
            "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
            "ALLOWED_HOSTS": "studio.example.com",
        }
    )

    assert settings.max_active_generation_tasks == 500


def test_production_settings_parse_capacity_controls(tmp_path: Path) -> None:
    settings = ProductionSettings.from_environ(
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
            "GENERATED_MEDIA_ROOT": str(tmp_path),
            "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
            "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            "DATABASE_POOL_SIZE": "12",
            "DATABASE_MAX_OVERFLOW": "3",
            "DATABASE_POOL_TIMEOUT_SECONDS": "4.5",
            "GENERATION_SUBMISSION_MODE": "inline",
            "GENERATION_WORKER_DEPLOYED_LIMIT": "8",
            "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
            "ALLOWED_HOSTS": "studio.example.com",
        }
    )

    assert settings.database_pool_size == 12
    assert settings.database_max_overflow == 3
    assert settings.database_pool_timeout_seconds == 4.5
    assert settings.generation_submission_mode == "inline"
    assert settings.generation_worker_deployed_limit == 8


def test_production_settings_parse_protected_metrics_token(tmp_path: Path) -> None:
    settings = ProductionSettings.from_environ(
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
            "GENERATED_MEDIA_ROOT": str(tmp_path),
            "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
            "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
            "ALLOWED_HOSTS": "studio.example.com",
            "METRICS_TOKEN": "metrics-token-123456",
        }
    )
    assert settings.metrics_token == "metrics-token-123456"
    with pytest.raises(ProductionConfigurationError, match="METRICS_TOKEN"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
                "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
                "ALLOWED_HOSTS": "studio.example.com",
                "METRICS_TOKEN": "short",
            }
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_POOL_SIZE", "0"),
        ("DATABASE_MAX_OVERFLOW", "-1"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "0"),
        ("GENERATION_SUBMISSION_MODE", "sometimes"),
    ],
)
def test_production_settings_reject_invalid_capacity_controls(tmp_path: Path, name: str, value: str) -> None:
    environ = {
        "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
        "GENERATED_MEDIA_ROOT": str(tmp_path),
        "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
        "PLATFORM_ADMIN_EMAILS": "admin@example.com",
        "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
        "ALLOWED_HOSTS": "studio.example.com",
        name: value,
    }
    with pytest.raises(ProductionConfigurationError, match=name):
        ProductionSettings.from_environ(environ)


@pytest.mark.parametrize("configured", ["0", "not-a-number"])
def test_production_settings_reject_invalid_generation_limit(tmp_path: Path, configured: str) -> None:
    with pytest.raises(ProductionConfigurationError, match="MAX_ACTIVE_GENERATION_TASKS"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
                "MAX_ACTIVE_GENERATION_TASKS": configured,
                "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
                "ALLOWED_HOSTS": "studio.example.com",
            }
        )


def test_production_settings_require_a_stable_auth_rate_limit_hash_key(tmp_path: Path) -> None:
    with pytest.raises(ProductionConfigurationError, match="AUTH_RATE_LIMIT_HASH_KEY"):
        ProductionSettings.from_environ(
            {
                "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
                "GENERATED_MEDIA_ROOT": str(tmp_path),
                "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
                "AUTH_RATE_LIMIT_HASH_KEY": "too-short",
            }
        )


def test_production_settings_validate_trusted_proxy_networks(tmp_path: Path) -> None:
    base = {
        "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
        "GENERATED_MEDIA_ROOT": str(tmp_path),
        "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
        "PLATFORM_ADMIN_EMAILS": "admin@example.com",
        "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
        "ALLOWED_HOSTS": "studio.example.com",
    }

    settings = ProductionSettings.from_environ({**base, "TRUSTED_PROXY_CIDRS": "127.0.0.1/32, 10.0.0.0/8"})
    assert settings.trusted_proxy_cidrs == ("127.0.0.1/32", "10.0.0.0/8")
    with pytest.raises(ProductionConfigurationError, match="TRUSTED_PROXY_CIDRS"):
        ProductionSettings.from_environ({**base, "TRUSTED_PROXY_CIDRS": "not-a-network"})
    with pytest.raises(ProductionConfigurationError, match="TRUSTED_PROXY_CIDRS"):
        ProductionSettings.from_environ({**base, "TRUSTED_PROXY_CIDRS": "0.0.0.0/0"})


def test_production_settings_require_exact_hosts_and_parse_hsts(tmp_path: Path) -> None:
    base = {
        "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
        "GENERATED_MEDIA_ROOT": str(tmp_path),
        "PROVIDER_SECRETS_ROOT": str(tmp_path / "provider-secrets"),
        "PLATFORM_ADMIN_EMAILS": "admin@example.com",
        "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
    }

    with pytest.raises(ProductionConfigurationError, match="ALLOWED_HOSTS"):
        ProductionSettings.from_environ(base)
    with pytest.raises(ProductionConfigurationError, match="ALLOWED_HOSTS"):
        ProductionSettings.from_environ({**base, "ALLOWED_HOSTS": "*.example.com"})
    settings = ProductionSettings.from_environ(
        {**base, "ALLOWED_HOSTS": "Studio.Example.com", "ENABLE_HSTS": "true"}
    )
    assert settings.allowed_hosts == ("studio.example.com", "127.0.0.1", "localhost")
    assert settings.enable_hsts is True
    with pytest.raises(ProductionConfigurationError, match="ENABLE_HSTS"):
        ProductionSettings.from_environ({**base, "ALLOWED_HOSTS": "studio.example.com", "ENABLE_HSTS": "yes"})


def test_admin_authorizer_uses_authenticated_account_email_allowlist() -> None:
    accounts = InMemoryAccountAccess()
    admin = accounts.register("admin@example.com", "correct horse battery staple")
    member = accounts.register("member@example.com", "correct horse battery staple")
    admin_token = accounts.login(admin.email, "correct horse battery staple").access_token
    member_token = accounts.login(member.email, "correct horse battery staple").access_token
    authorize = account_admin_authorizer(accounts, frozenset({"ADMIN@example.com"}))

    authorize(admin_token)
    with pytest.raises(PermissionError):
        authorize(member_token)
    with pytest.raises(PermissionError):
        authorize("unknown-session")


def test_saas_application_exposes_container_health_check() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_background_route_health_schedule_runs_a_due_check_after_startup() -> None:
    checked = Event()

    class SignallingProbe:
        def probe(self, target: RouteProbeTarget) -> ProbeResult:
            checked.set()
            return ProbeResult(RouteHealthStatus.HEALTHY, 120)

    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        probe=SignallingProbe(),
        id_factory=iter(("provider-1", "route-1")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-a",
        )
    )
    routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    routing.update_provider(ProviderUpdate(provider.provider_id, enabled=True))
    app = create_app(InMemoryAccountAccess())
    install_route_health_schedule(app, RouteHealthScheduler(routing))

    with TestClient(app):
        assert checked.wait(timeout=1)

    assert routing.route_health("route-1").status is RouteHealthStatus.HEALTHY


def test_background_generation_deadline_schedule_runs_without_a_browser() -> None:
    checked = Event()
    fixed_now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

    class SignallingTasks:
        def expire_due(self, now: datetime) -> tuple[object, ...]:
            assert now == fixed_now
            checked.set()
            return ()

    app = create_app(InMemoryAccountAccess())
    scheduler = GenerationDeadlineScheduler(SignallingTasks(), clock=lambda: fixed_now)  # type: ignore[arg-type]
    install_generation_deadline_schedule(app, scheduler, poll_interval=timedelta(milliseconds=10))

    with TestClient(app):
        assert checked.wait(timeout=1)


def test_production_app_wires_provider_management_and_generation_submission(tmp_path: Path) -> None:
    media_root = tmp_path / "generated-media"
    provider_secrets_root = tmp_path / "provider-secrets"
    media_root.mkdir()
    provider_secrets_root.mkdir()
    app = create_production_app(
        {
            "DATABASE_URL": "postgresql+psycopg://example.invalid/app",
            "GENERATED_MEDIA_ROOT": str(media_root),
            "PROVIDER_SECRETS_ROOT": str(provider_secrets_root),
            "PLATFORM_ADMIN_EMAILS": "admin@example.com",
            "AUTH_RATE_LIMIT_HASH_KEY": "test-auth-rate-limit-hash-key-0001",
            "ALLOWED_HOSTS": "studio.example.com",
        }
    )

    route_paths = {route.path for route in app.routes}

    assert "/api/v1/admin/providers" in route_paths
    assert "/api/v1/admin/image-model-routes" in route_paths
    assert "/api/v1/generation-tasks/{task_id}/retry" in route_paths
    assert "/api/v1/generation-tasks/{task_id}/events" in route_paths
    assert "/api/v1/reference-media" in route_paths
    assert "/api/v1/reference-media/{media_id}/content" in route_paths
    assert "/api/v1/admin/users" in route_paths
    assert "/api/v1/admin/users/by-email" in route_paths
    assert "/api/v1/admin/user-activity" in route_paths
    assert "/api/v1/admin/users/{user_id}/credit-grants" in route_paths
    assert "/api/v1/admin/email-settings" in route_paths
    assert "/api/v1/admin/payment-settings" in route_paths
    assert "/api/v1/payments/epay/notify" in route_paths
    assert "/api/v1/platform-content" in route_paths
    assert "/api/v1/admin/platform-content" in route_paths
    assert "/api/v1/canvases" in route_paths
    assert "/api/v1/canvases/{canvas_id}" in route_paths
    assert "/api/v1/canvases/{canvas_id}/media" in route_paths
    app.state.database_engine.dispose()

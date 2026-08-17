from pathlib import Path


def test_root_dockerfile_builds_and_runs_the_saas_backend() -> None:
    repository_root = Path(__file__).parents[2]
    dockerfile = (repository_root / "Dockerfile").read_text(encoding="utf-8")

    assert "backend/requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "backend/app" in dockerfile
    assert "PYTHONPATH=/app/backend" in dockerfile
    assert "app.runtime:create_production_app" in dockerfile
    assert "/var/lib/infinite-canvas/provider-secrets" in dockerfile
    assert "--factory" in dockerfile
    assert "${WEB_CONCURRENCY:-4}" in dockerfile
    assert "--limit-concurrency ${WEB_MAX_CONNECTIONS:-400}" in dockerfile
    assert "/readyz" in dockerfile
    assert "--forwarded-allow-ips='*'" not in dockerfile
    assert '--forwarded-allow-ips=\\"${TRUSTED_PROXY_CIDRS:-127.0.0.1}\\"' in dockerfile
    assert "creative_studio.bootstrap.runtime:app" not in dockerfile


def test_production_compose_mounts_media_and_runs_migrations_separately() -> None:
    repository_root = Path(__file__).parents[2]
    compose = (repository_root / "deploy" / "compose.production.yml").read_text(encoding="utf-8")

    assert "DATABASE_URL" in compose
    assert "PLATFORM_ADMIN_EMAILS" in compose
    assert "PUBLIC_BASE_URL" not in compose
    assert "SMTP_PASSWORD" not in compose
    assert "GENERATED_MEDIA_HOST_PATH" in compose
    assert "target: /var/lib/infinite-canvas/generated-media" in compose
    assert "alembic" in compose
    assert "service_completed_successfully" in compose
    assert "CREATIVE_STUDIO_RUNTIME_DIR" not in compose
    assert "DATABASE_POOL_SIZE" in compose
    assert "AUTH_RATE_LIMIT_HASH_KEY" in compose
    assert "AUTH_LOGIN_EMAIL_LIMIT" in compose
    assert "TRUSTED_PROXY_CIDRS" in compose
    assert "ALLOWED_HOSTS" in compose
    assert "ENABLE_HSTS" in compose
    assert "GENERATION_SUBMISSION_MODE: queued" in compose


def test_production_compose_runs_a_separately_scalable_generation_worker() -> None:
    repository_root = Path(__file__).parents[2]
    compose = (repository_root / "deploy" / "compose.production.yml").read_text(encoding="utf-8")

    assert "generation-worker:" in compose
    assert '["python", "-m", "app.worker"]' in compose
    assert "GENERATION_WORKER_REPLICAS" in compose
    assert "WORKER_DATABASE_POOL_SIZE" in compose
    assert 'DATABASE_POOL_SIZE: "${WORKER_DATABASE_POOL_SIZE:-2}"' in compose
    assert 'DATABASE_MAX_OVERFLOW: "${WORKER_DATABASE_MAX_OVERFLOW:-1}"' in compose
    assert compose.count("target: /var/lib/infinite-canvas/generated-media") == 2
    worker_definition = compose.split("  generation-worker:", maxsplit=1)[1]
    assert "healthcheck:" in worker_definition
    assert "disable: true" in worker_definition


def test_production_compose_mounts_the_provider_secret_directory_only_for_the_web_process() -> None:
    repository_root = Path(__file__).parents[2]
    compose = (repository_root / "deploy" / "compose.production.yml").read_text(encoding="utf-8")
    environment_example = (repository_root / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "PROVIDER_SECRETS_HOST_PATH" in environment_example
    assert "PROVIDER_SECRETS_ROOT: /var/lib/infinite-canvas/provider-secrets" in compose
    assert 'source: "${PROVIDER_SECRETS_HOST_PATH:' in compose
    assert "target: /var/lib/infinite-canvas/provider-secrets" in compose
    migrate_definition, web_definition = compose.split("  creative-studio:", maxsplit=1)
    assert "PROVIDER_SECRETS_ROOT" not in migrate_definition
    assert "provider-secrets" not in migrate_definition
    assert "PROVIDER_SECRETS_ROOT" in web_definition


def test_repository_only_exposes_the_production_deployment_entrypoint() -> None:
    repository_root = Path(__file__).parents[2]

    assert not (repository_root / "run.bat").exists()
    assert not (repository_root / "stop.bat").exists()
    assert not (repository_root / "deploy" / "compose.local.yml").exists()
    assert not (repository_root / "deploy" / "compose.saas.local.yml").exists()
    assert (repository_root / "deploy" / "compose.production.yml").is_file()
    assert (repository_root / "deploy" / ".env.example").is_file()


def test_production_environment_example_matches_the_supported_defaults() -> None:
    repository_root = Path(__file__).parents[2]
    environment_example = (repository_root / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "CREATIVE_STUDIO_PORT=8000" in environment_example
    assert "MAX_ACTIVE_GENERATION_TASKS=20" in environment_example
    assert "WORKER_DATABASE_POOL_SIZE=2" in environment_example
    assert "WORKER_DATABASE_MAX_OVERFLOW=1" in environment_example
    assert "AUTH_RATE_LIMIT_HASH_KEY=" in environment_example
    assert "AUTH_LOGIN_IP_LIMIT=10" in environment_example
    assert "AUTH_LOGIN_EMAIL_LIMIT=5" in environment_example
    assert "AUTH_REGISTER_IP_LIMIT=5" in environment_example
    assert "AUTH_EMAIL_VERIFICATION_ACCOUNT_LIMIT=3" in environment_example
    assert "TRUSTED_PROXY_CIDRS=" in environment_example
    assert "ALLOWED_HOSTS=studio.example.com" in environment_example
    assert "ENABLE_HSTS=false" in environment_example
    assert "PUBLIC_BASE_URL" not in environment_example
    assert "SMTP_PASSWORD" not in environment_example
    assert "CREATIVE_STUDIO_PORT=2020" not in environment_example

from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.model_routing import (
    FileSystemProviderSecrets,
    InMemoryModelRouting,
    InMemoryProviderSecrets,
    ModelRouteCreation,
    ProviderCreation,
    ProviderUpdate,
    SqlAlchemyModelRouting,
    StoredProviderSecret,
)
from app.model_routing._generation_targets import ProviderGenerationTarget, ProviderGenerationTargetNotFound
from app.model_routing.models import ProviderProtocol


class UnreadableProviderSecrets:
    def store(self, provider_id: str, api_key: str) -> StoredProviderSecret:
        return StoredProviderSecret(secret_ref="sensitive-secret-reference", key_fingerprint="deadbeef")

    def read(self, secret_ref: str) -> str:
        raise KeyError(secret_ref)


def test_frozen_route_resolves_a_disabled_provider_target_without_exposing_its_secret() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="test-source-a-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-upstream",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    target = routing.resolve(route.route_id)

    assert target == ProviderGenerationTarget(
        protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
        base_url="https://source-a.example.com/v1",
        api_key="test-source-a-secret",
        provider_model_name="gpt-image-2-upstream",
    )
    assert "test-source-a-secret" not in repr(target)


def test_missing_provider_generation_target_uses_a_sanitized_error() -> None:
    routing = InMemoryModelRouting(InMemoryProviderSecrets())

    try:
        routing.resolve("missing-route-containing-sensitive-reference")
    except ProviderGenerationTargetNotFound as error:
        assert str(error) == "provider generation target is unavailable"
        assert "sensitive-reference" not in repr(error)
    else:
        raise AssertionError("missing target must be rejected")


def test_unreadable_provider_secret_uses_the_same_sanitized_error() -> None:
    routing = InMemoryModelRouting(
        UnreadableProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="write-only-test-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-upstream",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    try:
        routing.resolve(route.route_id)
    except ProviderGenerationTargetNotFound as error:
        assert str(error) == "provider generation target is unavailable"
        assert "sensitive-secret-reference" not in repr(error)
    else:
        raise AssertionError("unreadable secret must be rejected")


def test_in_memory_target_resolution_uses_rotated_provider_configuration() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://old-source.example.com/v1",
            api_key="old-test-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-upstream",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    routing.update_provider(
        ProviderUpdate(
            provider_id=provider.provider_id,
            base_url="https://current-source.example.com/v1",
            api_key="current-test-secret",
        )
    )

    target = routing.resolve(route.route_id)

    assert target.base_url == "https://current-source.example.com/v1"
    assert target.api_key == "current-test-secret"
    assert "current-test-secret" not in repr(target)


def test_sqlalchemy_target_resolution_survives_restart_and_uses_rotated_provider_configuration(
    tmp_path: Path,
) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'provider-generation-targets.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    secrets = InMemoryProviderSecrets()
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        secrets,
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://old-source.example.com/v1",
            api_key="old-test-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-upstream",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )
    routing.update_provider(
        ProviderUpdate(
            provider_id=provider.provider_id,
            base_url="https://current-source.example.com/v1",
            api_key="current-test-secret",
        )
    )
    restarted = SqlAlchemyModelRouting.for_database_url(database_url, secrets)

    target = restarted.resolve(route.route_id)

    assert target == ProviderGenerationTarget(
        protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
        base_url="https://current-source.example.com/v1",
        api_key="current-test-secret",
        provider_model_name="gpt-image-2-upstream",
    )
    assert "current-test-secret" not in repr(target)


def test_sqlalchemy_target_resolution_restarts_with_file_system_secrets_without_database_plaintext(
    tmp_path: Path,
) -> None:
    backend_root = Path(__file__).parents[1]
    database_path = tmp_path / "file-provider-generation-targets.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    secret_root = tmp_path / "provider-secrets"
    secret_root.mkdir()
    routing = SqlAlchemyModelRouting.for_database_url(
        database_url,
        FileSystemProviderSecrets(secret_root),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    provider = routing.create_provider(
        ProviderCreation(
            code="source-a",
            display_name="来源 A",
            protocol=ProviderProtocol.OPENAI_COMPATIBLE_IMAGES,
            base_url="https://source-a.example.com/v1",
            api_key="file-backed-test-secret",
        )
    )
    route = routing.create_route(
        ModelRouteCreation(
            provider_id=provider.provider_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            provider_model_name="gpt-image-2-upstream",
            compatibility_group="gpt-image-2/4k/v1",
        )
    )

    restarted = SqlAlchemyModelRouting.for_database_url(
        database_url,
        FileSystemProviderSecrets(secret_root),
    )
    target = restarted.resolve(route.route_id)

    assert target.api_key == "file-backed-test-secret"
    assert "file-backed-test-secret" not in repr(target)
    assert b"file-backed-test-secret" not in database_path.read_bytes()

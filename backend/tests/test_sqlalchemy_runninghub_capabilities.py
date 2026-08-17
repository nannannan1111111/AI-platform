from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.runninghub_capabilities import (
    PublicRunningHubCapability,
    PublicRunningHubInputSchema,
    RunningHubCapabilityInput,
    RunningHubCapabilityPublication,
    RunningHubCapabilityUpdate,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubUserPricePublication,
    SqlAlchemyRunningHubCapabilities,
)


def test_runninghub_capabilities_survive_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'runninghub-capabilities.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    updated_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    capabilities = SqlAlchemyRunningHubCapabilities.for_database_url(
        database_url,
        id_factory=lambda: "capability-1",
        clock=iter((created_at, updated_at)).__next__,
    )
    published = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )
    updated = capabilities.update(
        RunningHubCapabilityUpdate(
            capability_id=published.capability_id,
            input_capabilities=(RunningHubInputCapability.TEXT, RunningHubInputCapability.IMAGE),
            available=False,
        )
    )

    restarted = SqlAlchemyRunningHubCapabilities.for_database_url(database_url)

    assert restarted.list_for_administration() == (updated,)
    assert restarted.catalog() == (
        PublicRunningHubCapability(
            capability_id="capability-1",
            name="商品摄影",
            input_capabilities=(
                RunningHubInputCapability.TEXT,
                RunningHubInputCapability.IMAGE,
            ),
            available=False,
        ),
    )


def test_runninghub_input_schema_history_survives_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'runninghub-input-schemas.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    first_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    second_published_at = datetime(2026, 8, 9, 10, 10, tzinfo=UTC)
    capabilities = SqlAlchemyRunningHubCapabilities.for_database_url(
        database_url,
        id_factory=iter(("capability-1", "schema-1", "schema-2")).__next__,
        clock=iter((created_at, first_published_at, second_published_at)).__next__,
    )
    capability = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )
    first = capabilities.publish_input_schema(
        RunningHubInputSchemaPublication(
            capability_id=capability.capability_id,
            inputs=(
                RunningHubCapabilityInput(
                    input_key="prompt",
                    label="提示词",
                    kind=RunningHubInputCapability.TEXT,
                    required=True,
                ),
            ),
        )
    )
    second = capabilities.publish_input_schema(
        RunningHubInputSchemaPublication(
            capability_id=capability.capability_id,
            inputs=(
                RunningHubCapabilityInput(
                    input_key="reference_image",
                    label="参考图",
                    kind=RunningHubInputCapability.IMAGE,
                    required=False,
                ),
            ),
        )
    )

    restarted = SqlAlchemyRunningHubCapabilities.for_database_url(database_url)

    assert restarted.input_schema_versions(capability.capability_id) == (first, second)
    public_capability = restarted.catalog()[0]
    assert public_capability.input_capabilities == (RunningHubInputCapability.IMAGE,)
    assert public_capability.input_schema == PublicRunningHubInputSchema(
        schema_version_id="schema-2",
        version=2,
        inputs=second.inputs,
    )


def test_runninghub_user_price_history_survives_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'runninghub-user-prices.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    first_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    second_published_at = datetime(2026, 8, 9, 10, 10, tzinfo=UTC)
    second_effective_from = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    capabilities = SqlAlchemyRunningHubCapabilities.for_database_url(
        database_url,
        id_factory=iter(("capability-1", "price-1", "price-2")).__next__,
        clock=iter((created_at, first_published_at, second_published_at)).__next__,
    )
    capability = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )

    first = capabilities.publish_user_price(
        RunningHubUserPricePublication(
            capability_id=capability.capability_id,
            credits_per_run="0.1000",
            effective_from=first_published_at,
        )
    )
    second = capabilities.publish_user_price(
        RunningHubUserPricePublication(
            capability_id=capability.capability_id,
            credits_per_run="0.2500",
            effective_from=second_effective_from,
        )
    )

    restarted = SqlAlchemyRunningHubCapabilities.for_database_url(database_url, clock=lambda: second_published_at)

    assert restarted.user_price_versions(capability.capability_id) == (first, second)
    assert restarted.user_price_at(capability.capability_id, second_published_at) == first
    assert restarted.user_price_at(capability.capability_id, second_effective_from) == second
    assert restarted.catalog()[0].credits_per_run == "0.1000"

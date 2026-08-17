from datetime import UTC, datetime

import pytest

from app.runninghub_capabilities import (
    InMemoryRunningHubCapabilities,
    InvalidRunningHubCapability,
    PublicRunningHubCapability,
    PublicRunningHubInputSchema,
    RunningHubCapabilityInput,
    RunningHubCapabilityPublication,
    RunningHubCapabilityUpdate,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubInputSchemaVersion,
    RunningHubUserPricePublication,
    RunningHubUserPriceVersion,
)


def test_administrator_publishes_a_user_safe_runninghub_capability() -> None:
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=lambda: "capability-1",
        clock=lambda: now,
    )

    published = capabilities.publish(
        RunningHubCapabilityPublication(
            name="  商品摄影  ",
            workflow_id="  internal-workflow-42  ",
            input_capabilities=(
                RunningHubInputCapability.IMAGE,
                RunningHubInputCapability.TEXT,
                RunningHubInputCapability.IMAGE,
            ),
            available=True,
        )
    )

    assert published.capability_id == "capability-1"
    assert published.name == "商品摄影"
    assert published.workflow_id == "internal-workflow-42"
    assert capabilities.catalog() == (
        PublicRunningHubCapability(
            capability_id="capability-1",
            name="商品摄影",
            input_capabilities=(
                RunningHubInputCapability.TEXT,
                RunningHubInputCapability.IMAGE,
            ),
            available=True,
        ),
    )


def test_administrator_updates_a_capability_without_changing_its_public_identity() -> None:
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    updated_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    clock = iter((created_at, updated_at)).__next__
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=lambda: "capability-1",
        clock=clock,
    )
    original = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )

    updated = capabilities.update(
        RunningHubCapabilityUpdate(
            capability_id=original.capability_id,
            name="电商商品摄影",
            workflow_id="internal-workflow-84",
            input_capabilities=(RunningHubInputCapability.TEXT, RunningHubInputCapability.IMAGE),
            available=False,
        )
    )

    assert updated.capability_id == original.capability_id
    assert updated.created_at == created_at
    assert updated.updated_at == updated_at
    assert updated.workflow_id == "internal-workflow-84"
    assert capabilities.list_for_administration() == (updated,)
    assert capabilities.catalog() == (
        PublicRunningHubCapability(
            capability_id="capability-1",
            name="电商商品摄影",
            input_capabilities=(
                RunningHubInputCapability.TEXT,
                RunningHubInputCapability.IMAGE,
            ),
            available=False,
        ),
    )


def test_administrator_publishes_immutable_runninghub_input_schema_versions() -> None:
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    first_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    second_published_at = datetime(2026, 8, 9, 10, 10, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
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

    assert first == RunningHubInputSchemaVersion(
        schema_version_id="schema-1",
        capability_id="capability-1",
        version=1,
        inputs=(
            RunningHubCapabilityInput(
                input_key="prompt",
                label="提示词",
                kind=RunningHubInputCapability.TEXT,
                required=True,
            ),
        ),
        published_at=first_published_at,
    )
    assert second.version == 2
    assert capabilities.input_schema_versions(capability.capability_id) == (first, second)
    public_capability = capabilities.catalog()[0]
    assert public_capability.input_capabilities == (RunningHubInputCapability.IMAGE,)
    assert public_capability.input_schema == PublicRunningHubInputSchema(
        schema_version_id="schema-2",
        version=2,
        inputs=second.inputs,
    )


def test_administrator_cannot_override_derived_input_capabilities_after_schema_publication() -> None:
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "schema-1")).__next__,
    )
    capability = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )
    capabilities.publish_input_schema(
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

    with pytest.raises(InvalidRunningHubCapability):
        capabilities.update(
            RunningHubCapabilityUpdate(
                capability_id=capability.capability_id,
                input_capabilities=(RunningHubInputCapability.IMAGE,),
            )
        )


def test_administrator_publishes_immutable_effective_runninghub_user_price_versions() -> None:
    capability_created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    first_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    second_published_at = datetime(2026, 8, 9, 10, 10, tzinfo=UTC)
    second_effective_from = datetime(2026, 8, 9, 11, 0, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "price-1", "price-2")).__next__,
        clock=iter((capability_created_at, first_published_at, second_published_at)).__next__,
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
            credits_per_run="0.1",
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

    assert first == RunningHubUserPriceVersion(
        price_version_id="price-1",
        capability_id="capability-1",
        version=1,
        credits_per_run="0.1000",
        effective_from=first_published_at,
        published_at=first_published_at,
    )
    assert second.version == 2
    assert capabilities.user_price_versions(capability.capability_id) == (first, second)
    assert capabilities.user_price_at(capability.capability_id, second_published_at) == first
    assert capabilities.user_price_at(capability.capability_id, second_effective_from) == second

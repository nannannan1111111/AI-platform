from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.runninghub_capabilities import (
    InMemoryRunningHubCapabilities,
    RunningHubCapabilityInput,
    RunningHubCapabilityPublication,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubUserPricePublication,
)


def test_administrator_publishes_and_lists_runninghub_capabilities() -> None:
    authorized_tokens: list[str] = []
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=lambda: "capability-1",
        clock=lambda: datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            runninghub_capabilities=capabilities,
            admin_authorizer=authorized_tokens.append,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}

    created = client.post(
        "/api/v1/admin/runninghub-capabilities",
        headers=headers,
        json={
            "name": "商品摄影",
            "workflow_id": "internal-workflow-42",
            "input_capabilities": ["image", "text"],
            "available": True,
        },
    )

    assert created.status_code == 201
    assert created.json() == {
        "capability_id": "capability-1",
        "name": "商品摄影",
        "workflow_id": "internal-workflow-42",
        "input_capabilities": ["text", "image"],
        "available": True,
        "created_at": "2026-08-09T10:00:00Z",
        "updated_at": "2026-08-09T10:00:00Z",
    }
    listed = client.get("/api/v1/admin/runninghub-capabilities", headers=headers)

    assert listed.status_code == 200
    assert listed.json() == [created.json()]
    for forbidden in ("provider", "route", "credential", "secret", "cost"):
        assert forbidden not in listed.text.casefold()
    assert authorized_tokens == ["admin-session", "admin-session"]


def test_administrator_updates_a_runninghub_capability_with_patch_semantics() -> None:
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    updated_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=lambda: "capability-1",
        clock=iter((created_at, updated_at)).__next__,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            runninghub_capabilities=capabilities,
            admin_authorizer=lambda _: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    created = client.post(
        "/api/v1/admin/runninghub-capabilities",
        headers=headers,
        json={
            "name": "商品摄影",
            "workflow_id": "internal-workflow-42",
            "input_capabilities": ["text"],
            "available": True,
        },
    )

    updated = client.patch(
        "/api/v1/admin/runninghub-capabilities/capability-1",
        headers=headers,
        json={"name": "电商商品摄影", "available": False},
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json() == {
        **created.json(),
        "name": "电商商品摄影",
        "available": False,
        "updated_at": "2026-08-09T10:05:00Z",
    }


def test_administrator_publishes_and_lists_runninghub_input_schema_versions() -> None:
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    schema_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "schema-1")).__next__,
        clock=iter((created_at, schema_published_at)).__next__,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            runninghub_capabilities=capabilities,
            admin_authorizer=lambda _: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    capability = client.post(
        "/api/v1/admin/runninghub-capabilities",
        headers=headers,
        json={
            "name": "商品摄影",
            "workflow_id": "internal-workflow-42",
            "input_capabilities": ["text"],
            "available": True,
        },
    )
    assert capability.status_code == 201

    published = client.post(
        "/api/v1/admin/runninghub-capabilities/capability-1/input-schema-versions",
        headers=headers,
        json={
            "inputs": [
                {"input_key": "prompt", "label": "提示词", "kind": "text", "required": True},
                {
                    "input_key": "reference_image",
                    "label": "参考图",
                    "kind": "image",
                    "required": False,
                },
            ]
        },
    )

    assert published.status_code == 201
    assert published.json() == {
        "schema_version_id": "schema-1",
        "capability_id": "capability-1",
        "version": 1,
        "inputs": [
            {"input_key": "prompt", "label": "提示词", "kind": "text", "required": True},
            {
                "input_key": "reference_image",
                "label": "参考图",
                "kind": "image",
                "required": False,
            },
        ],
        "published_at": "2026-08-09T10:05:00Z",
    }
    history = client.get(
        "/api/v1/admin/runninghub-capabilities/capability-1/input-schema-versions",
        headers=headers,
    )
    assert history.status_code == 200
    assert history.json() == [published.json()]
    for forbidden in ("workflow_id", "node_id", "field_name", "provider", "route", "credential", "cost"):
        assert forbidden not in history.text.casefold()


def test_administrator_cannot_patch_derived_input_capabilities_after_schema_publication() -> None:
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "schema-1")).__next__,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            runninghub_capabilities=capabilities,
            admin_authorizer=lambda _: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    created = client.post(
        "/api/v1/admin/runninghub-capabilities",
        headers=headers,
        json={
            "name": "商品摄影",
            "workflow_id": "internal-workflow-42",
            "input_capabilities": ["text"],
            "available": True,
        },
    )
    assert created.status_code == 201
    published = client.post(
        "/api/v1/admin/runninghub-capabilities/capability-1/input-schema-versions",
        headers=headers,
        json={"inputs": [{"input_key": "prompt", "label": "提示词", "kind": "text", "required": True}]},
    )
    assert published.status_code == 201

    response = client.patch(
        "/api/v1/admin/runninghub-capabilities/capability-1",
        headers=headers,
        json={"input_capabilities": ["image"]},
    )

    assert response.status_code == 422


def test_administrator_publishes_and_lists_runninghub_user_price_versions() -> None:
    capability_created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    price_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "price-1")).__next__,
        clock=iter((capability_created_at, price_published_at)).__next__,
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            runninghub_capabilities=capabilities,
            admin_authorizer=lambda _: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    capability = client.post(
        "/api/v1/admin/runninghub-capabilities",
        headers=headers,
        json={
            "name": "商品摄影",
            "workflow_id": "internal-workflow-42",
            "input_capabilities": ["text"],
            "available": True,
        },
    )
    assert capability.status_code == 201

    published = client.post(
        "/api/v1/admin/runninghub-capabilities/capability-1/price-versions",
        headers=headers,
        json={"credits_per_run": "0.1", "effective_from": "2026-08-09T10:05:00Z"},
    )

    assert published.status_code == 201
    assert published.json() == {
        "price_version_id": "price-1",
        "capability_id": "capability-1",
        "version": 1,
        "credits_per_run": "0.1000",
        "effective_from": "2026-08-09T10:05:00Z",
        "published_at": "2026-08-09T10:05:00Z",
    }
    history = client.get(
        "/api/v1/admin/runninghub-capabilities/capability-1/price-versions",
        headers=headers,
    )
    assert history.status_code == 200
    assert history.json() == [published.json()]
    for forbidden in ("workflow_id", "provider", "route", "credential", "secret", "cost"):
        assert forbidden not in history.text.casefold()


def test_logged_in_user_reads_only_the_safe_runninghub_capability_catalog() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("user@example.com", "correct-horse-battery-staple")
    session = accounts.login("user@example.com", "correct-horse-battery-staple")
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=lambda: "capability-1",
        clock=lambda: datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
    )
    capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT, RunningHubInputCapability.IMAGE),
            available=False,
        )
    )
    client = TestClient(create_app(accounts, runninghub_capabilities=capabilities))

    response = client.get(
        "/api/v1/runninghub-capabilities",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "capability_id": "capability-1",
                "name": "商品摄影",
                "input_capabilities": ["text", "image"],
                "available": False,
                "input_schema": None,
                "credits_per_run": None,
            }
        ]
    }
    for forbidden in ("workflow", "provider", "route", "credential", "secret", "cost"):
        assert forbidden not in response.text.casefold()
    assert client.get("/api/v1/runninghub-capabilities").status_code == 401


def test_logged_in_user_reads_the_current_safe_runninghub_input_schema() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("user@example.com", "correct-horse-battery-staple")
    session = accounts.login("user@example.com", "correct-horse-battery-staple")
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "schema-1")).__next__,
        clock=iter(
            (
                datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 8, 9, 10, 5, tzinfo=UTC),
            )
        ).__next__,
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
    client = TestClient(create_app(accounts, runninghub_capabilities=capabilities))

    response = client.get(
        "/api/v1/runninghub-capabilities",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "capability_id": "capability-1",
                "name": "商品摄影",
                "input_capabilities": ["text", "image"],
                "available": True,
                "input_schema": {
                    "schema_version_id": "schema-1",
                    "version": 1,
                    "inputs": [
                        {"input_key": "prompt", "label": "提示词", "kind": "text", "required": True},
                        {
                            "input_key": "reference_image",
                            "label": "参考图",
                            "kind": "image",
                            "required": False,
                        },
                    ],
                },
                "credits_per_run": None,
            }
        ]
    }
    for forbidden in (
        "workflow_id",
        "node_id",
        "field_name",
        "provider",
        "route",
        "credential",
        "secret",
        "cost",
    ):
        assert forbidden not in response.text.casefold()


def test_logged_in_user_reads_only_the_current_runninghub_user_price() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("user@example.com", "correct-horse-battery-staple")
    session = accounts.login("user@example.com", "correct-horse-battery-staple")
    capability_created_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    first_published_at = datetime(2026, 8, 9, 10, 5, tzinfo=UTC)
    second_published_at = datetime(2026, 8, 9, 10, 10, tzinfo=UTC)
    catalog_at = datetime(2026, 8, 9, 10, 30, tzinfo=UTC)
    capabilities = InMemoryRunningHubCapabilities(
        id_factory=iter(("capability-1", "price-1", "price-2")).__next__,
        clock=iter((capability_created_at, first_published_at, second_published_at, catalog_at)).__next__,
    )
    capability = capabilities.publish(
        RunningHubCapabilityPublication(
            name="商品摄影",
            workflow_id="internal-workflow-42",
            input_capabilities=(RunningHubInputCapability.TEXT,),
            available=True,
        )
    )
    capabilities.publish_user_price(
        RunningHubUserPricePublication(
            capability_id=capability.capability_id,
            credits_per_run="0.1000",
            effective_from=first_published_at,
        )
    )
    capabilities.publish_user_price(
        RunningHubUserPricePublication(
            capability_id=capability.capability_id,
            credits_per_run="0.2500",
            effective_from=datetime(2026, 8, 9, 11, 0, tzinfo=UTC),
        )
    )
    client = TestClient(create_app(accounts, runninghub_capabilities=capabilities))

    response = client.get(
        "/api/v1/runninghub-capabilities",
        headers={"Authorization": f"Bearer {session.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "capability_id": "capability-1",
                "name": "商品摄影",
                "input_capabilities": ["text"],
                "available": True,
                "input_schema": None,
                "credits_per_run": "0.1000",
            }
        ]
    }
    assert "0.2500" not in response.text
    for forbidden in ("workflow", "provider", "route", "credential", "secret", "cost"):
        assert forbidden not in response.text.casefold()

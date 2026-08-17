from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.model_routing import InMemoryModelRouting, InMemoryProviderSecrets


def test_admin_configures_api_source_without_exposing_its_credential() -> None:
    authorized_tokens: list[str] = []
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=routing,
            admin_authorizer=authorized_tokens.append,
        )
    )

    created = client.post(
        "/api/v1/admin/providers",
        headers={"Authorization": "Bearer admin-session"},
        json={
            "code": "openai-compatible-01",
            "display_name": "图片来源一",
            "protocol": "openai_compatible_images",
            "base_url": "https://images.example.com/v1/",
            "api_key": "test-only-credential",
        },
    )

    assert created.status_code == 201
    assert created.json() == {
        "provider_id": "provider-1",
        "code": "openai-compatible-01",
        "display_name": "图片来源一",
        "protocol": "openai_compatible_images",
        "base_url": "https://images.example.com/v1",
        "image_response_mode": "auto",
        "concurrency_group": "openai-compatible-01",
        "max_concurrency": 20,
        "request_timeout_seconds": 600,
        "credential_configured": True,
        "key_fingerprint": "875336ca",
        "enabled": False,
        "created_at": "2026-08-08T00:00:00Z",
        "updated_at": "2026-08-08T00:00:00Z",
    }
    assert "api_key" not in created.text
    assert "secret_ref" not in created.text

    listed = client.get(
        "/api/v1/admin/providers",
        headers={"Authorization": "Bearer admin-session"},
    )

    assert listed.status_code == 200
    assert listed.json() == [created.json()]
    assert "api_key" not in listed.text
    assert "secret_ref" not in listed.text
    assert authorized_tokens == ["admin-session", "admin-session"]


def test_admin_maps_multiple_api_sources_to_one_logical_model() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "provider-2", "route-1", "route-2")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=routing,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    provider_ids = []
    for code in ("source-a", "source-b"):
        response = client.post(
            "/api/v1/admin/providers",
            headers=headers,
            json={
                "code": code,
                "display_name": code,
                "protocol": "openai_compatible_images",
                "base_url": f"https://{code}.example.com/v1",
                "api_key": f"test-{code}",
            },
        )
        assert response.status_code == 201
        provider_ids.append(response.json()["provider_id"])

    for provider_id in provider_ids:
        response = client.post(
            "/api/v1/admin/image-model-routes",
            headers=headers,
            json={
                "provider_id": provider_id,
                "logical_model": "gpt-image-2",
                "output_spec": "4k",
                "provider_model_name": "gpt-image-2",
                "compatibility_group": "gpt-image-2/4k/v1",
                "priority": 100,
                "max_reference_images": 6,
            },
        )
        assert response.status_code == 201
        assert response.json()["enabled"] is False
        assert response.json()["health_status"] == "unknown"
        assert response.json()["max_reference_images"] == 6

    listed = client.get(
        "/api/v1/admin/image-model-routes?logical_model=gpt-image-2&output_spec=4k",
        headers=headers,
    )

    assert listed.status_code == 200
    assert [route["route_id"] for route in listed.json()] == ["route-1", "route-2"]
    assert {route["provider_id"] for route in listed.json()} == {"provider-1", "provider-2"}
    assert {route["max_reference_images"] for route in listed.json()} == {6}


def test_admin_edits_and_irreversibly_deletes_provider_configuration() -> None:
    routing = InMemoryModelRouting(
        InMemoryProviderSecrets(),
        id_factory=iter(("provider-1", "route-1")).__next__,
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC),
    )
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=routing,
            admin_authorizer=lambda token: None,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}
    provider = client.post(
        "/api/v1/admin/providers",
        headers=headers,
        json={
            "code": "source-a",
            "display_name": "来源 A",
            "protocol": "openai_compatible_images",
            "base_url": "https://source-a.example.com/v1",
            "api_key": "test-source-a",
        },
    ).json()
    route = client.post(
        "/api/v1/admin/image-model-routes",
        headers=headers,
        json={
            "provider_id": provider["provider_id"],
            "logical_model": "gpt-image-2",
            "output_spec": "4k",
            "provider_model_name": "gpt-image-2",
            "compatibility_group": "gpt-image-2/4k/v1",
            "priority": 100,
        },
    ).json()

    edited_provider = client.patch(
        f"/api/v1/admin/providers/{provider['provider_id']}",
        headers=headers,
        json={
            "display_name": "来源 A（新版）",
            "base_url": "https://source-b.example.com/v1",
            "api_key": "rotated-test-key",
        },
    )
    edited_route = client.patch(
        f"/api/v1/admin/image-model-routes/{route['route_id']}",
        headers=headers,
        json={
            "provider_model_name": "gpt-image-2-2026",
            "compatibility_group": "gpt-image-2/4k/v2",
            "priority": 20,
            "max_reference_images": 8,
        },
    )

    assert edited_provider.status_code == 200
    assert edited_provider.json()["display_name"] == "来源 A（新版）"
    assert edited_provider.json()["base_url"] == "https://source-b.example.com/v1"
    assert "api_key" not in edited_provider.text
    assert edited_route.status_code == 200
    assert edited_route.json()["provider_model_name"] == "gpt-image-2-2026"
    assert edited_route.json()["compatibility_group"] == "gpt-image-2/4k/v2"
    assert edited_route.json()["priority"] == 20
    assert edited_route.json()["max_reference_images"] == 8

    blocked = client.delete(f"/api/v1/admin/providers/{provider['provider_id']}", headers=headers)

    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "必须先删除该来源的全部模型路由"}

    deleted_route = client.delete(f"/api/v1/admin/image-model-routes/{route['route_id']}", headers=headers)
    deleted_provider = client.delete(f"/api/v1/admin/providers/{provider['provider_id']}", headers=headers)

    assert deleted_route.status_code == 204
    assert deleted_provider.status_code == 204
    assert client.get("/api/v1/admin/providers", headers=headers).json() == []
    assert client.get("/api/v1/admin/image-model-routes", headers=headers).json() == []


def test_api_source_configuration_requires_platform_admin() -> None:
    def deny(_: str) -> None:
        raise PermissionError

    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            model_routing=InMemoryModelRouting(InMemoryProviderSecrets()),
            admin_authorizer=deny,
        )
    )

    response = client.get(
        "/api/v1/admin/providers",
        headers={"Authorization": "Bearer user-session"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "需要平台管理员权限"}

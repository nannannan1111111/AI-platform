from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.prompt_assets import InMemoryPromptAssets


def test_prompt_assets_are_seeded_per_account_shared_with_canvas_contract_and_deletable() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("prompts-first@example.com", "a-correct-horse-battery-staple")
    accounts.register("prompts-second@example.com", "a-correct-horse-battery-staple")
    first_session = accounts.login("prompts-first@example.com", "a-correct-horse-battery-staple")
    second_session = accounts.login("prompts-second@example.com", "a-correct-horse-battery-staple")
    client = TestClient(create_app(accounts, prompt_assets=InMemoryPromptAssets()))
    first_headers = {"Authorization": f"Bearer {first_session.access_token}"}
    second_headers = {"Authorization": f"Bearer {second_session.access_token}"}

    seeded = client.get("/api/v1/prompt-libraries", headers=first_headers)
    assert seeded.status_code == 200
    system = seeded.json()["library"]["libraries"][0]
    assert system["id"] == "system"
    assert system["readonly"] is False
    assert len(system["items"]) >= 5

    created = client.post(
        "/api/v1/prompt-libraries/items",
        headers=first_headers,
        json={"library_id": "system", "name": "我的构图", "positive": "主体居中，干净背景", "category": "custom"},
    )
    assert created.status_code == 201
    created_id = created.json()["item"]["id"]
    assert any(item["id"] == created_id for item in created.json()["library"]["libraries"][0]["items"])
    assert all(item["id"] != created_id for item in client.get("/api/v1/prompt-libraries", headers=second_headers).json()["library"]["libraries"][0]["items"])

    builtin_id = system["items"][0]["id"]
    deleted = client.delete(f"/api/v1/prompt-libraries/items/{builtin_id}", headers=first_headers)
    assert deleted.status_code == 200
    assert all(item["id"] != builtin_id for item in deleted.json()["library"]["libraries"][0]["items"])
    assert all(item["id"] != builtin_id for item in client.get("/api/v1/prompt-libraries", headers=first_headers).json()["library"]["libraries"][0]["items"])
    assert any(item["id"] == builtin_id for item in client.get("/api/v1/prompt-libraries", headers=second_headers).json()["library"]["libraries"][0]["items"])


def test_prompt_asset_mutations_require_authentication() -> None:
    client = TestClient(create_app(InMemoryAccountAccess(), prompt_assets=InMemoryPromptAssets()))
    assert client.get("/api/v1/prompt-libraries").status_code == 401
    assert client.post("/api/v1/prompt-libraries/items", json={"name": "x", "positive": "y"}).status_code == 401

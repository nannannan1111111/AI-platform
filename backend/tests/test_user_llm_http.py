from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.model_routing import InMemoryProviderSecrets
from app.user_llm import SqlAlchemyUserLLMProviders
from app.user_llm.tables import metadata


def _client(transport: httpx.BaseTransport | None = None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    accounts = InMemoryAccountAccess(clock=lambda: datetime(2026, 8, 12, tzinfo=UTC))
    providers = SqlAlchemyUserLLMProviders(sessionmaker(engine, expire_on_commit=False), InMemoryProviderSecrets(), transport=transport)
    return TestClient(create_app(accounts, user_llm_providers=providers)), accounts


def _register(client: TestClient, email: str) -> dict[str, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": "password-1234"})
    return {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"email": email, "password": "password-1234"}).json()["access_token"]}


def test_user_llm_provider_is_account_scoped_and_never_exposes_key() -> None:
    client, _ = _client()
    alice = _register(client, "alice@example.com")
    bob = _register(client, "bob@example.com")
    created = client.post("/api/v1/llm-providers", headers=alice, json={
        "code": "openai", "display_name": "我的 OpenAI", "base_url": "https://api.example.com/v1",
        "api_key": "secret-key", "models": ["gpt-test"], "enabled": True,
    })
    assert created.status_code == 201
    assert "secret-key" not in created.text
    assert "secret_ref" not in created.text
    assert client.get("/api/v1/llm-providers", headers=bob).json() == []
    assert client.get("/api/v1/llm-providers", headers=alice).json()[0]["has_key"] is True


def test_canvas_llm_uses_current_accounts_provider_and_key() -> None:
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]})

    client, _ = _client(httpx.MockTransport(upstream))
    headers = _register(client, "owner@example.com")
    client.post("/api/v1/llm-providers", headers=headers, json={
        "code": "mine", "display_name": "我的模型", "base_url": "https://llm.example/v1/",
        "api_key": "owner-key", "models": ["model-a"], "enabled": True,
    })
    response = client.post("/api/v1/canvas-llm", headers=headers, json={
        "provider": "mine", "model": "model-a", "message": "你好", "system_prompt": "简洁回答",
    })
    assert response.status_code == 200
    assert response.json() == {"text": "完成"}
    assert seen["authorization"] == "Bearer owner-key"
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert "model-a" in seen["body"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"choices": [{"message": {"content": [{"type": "text", "text": "第一段"}, {"text": "第二段"}]}}]},
            "第一段第二段",
        ),
        ({"output_text": "顶层返回"}, "顶层返回"),
        ({"choices": [{"text": "旧版返回"}]}, "旧版返回"),
    ],
)
def test_canvas_llm_preserves_text_from_compatible_upstream_shapes(
    payload: dict[str, object],
    expected: str,
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client, _ = _client(httpx.MockTransport(upstream))
    headers = _register(client, f"{expected}@example.com")
    client.post("/api/v1/llm-providers", headers=headers, json={
        "code": "mine", "display_name": "我的模型", "base_url": "https://llm.example/v1",
        "api_key": "owner-key", "models": ["model-a"], "enabled": True,
    })
    response = client.post("/api/v1/canvas-llm", headers=headers, json={
        "provider": "mine", "model": "model-a", "message": "你好",
    })
    assert response.status_code == 200
    assert response.json() == {"text": expected}

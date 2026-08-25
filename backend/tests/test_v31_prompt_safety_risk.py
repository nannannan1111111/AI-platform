from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.canvases import InMemoryCanvases
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.generation import InMemoryGenerationTasks
from app.http import create_app
from app.prompt_safety import InMemoryPromptSafety
from app.risk_events import InMemoryRiskEvents


def _context():
    now = datetime(2026, 8, 25, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("admin-v31@example.com", "a-correct-horse-battery-staple")
    user = accounts.register("user-v31@example.com", "another-correct-horse-battery-staple")
    admin_session = accounts.login("admin-v31@example.com", "a-correct-horse-battery-staple")
    user_session = accounts.login("user-v31@example.com", "another-correct-horse-battery-staple")
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={user.account_space_id}, model_prices=prices)
    package = credits.publish("starter", payment_cny="1", credits="10", effective_from=now)
    credits.record_recharge(user.account_space_id, package.version_id, payment_reference="v31-test", occurred_at=now)
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=5)
    safety = InMemoryPromptSafety()
    risks = InMemoryRiskEvents()
    client = TestClient(create_app(accounts, account_directory=accounts, credit_accounting=credits, generation_tasks=tasks, prompt_safety=safety, risk_events=risks, admin_authorizer=lambda token: None if token == admin_session.access_token else (_ for _ in ()).throw(PermissionError()), clock=lambda: now))
    return client, admin_session.access_token, user_session.access_token, user.account_space_id, tasks, safety, risks


def test_keyword_blocks_before_task_or_credit_freeze_and_admin_can_import_txt():
    client, admin_token, user_token, account_space_id, tasks, safety, risks = _context()
    response = client.put("/api/v1/admin/prompt-safety", headers={"Authorization": f"Bearer {admin_token}"}, json={"enabled": True, "prompt_check_enabled": True, "keywords": ["禁止词"]})
    assert response.status_code == 200
    blocked = client.post("/api/v1/generation-tasks", headers={"Authorization": f"Bearer {user_token}"}, json={"task_id": "blocked", "logical_model": "gpt-image-2", "output_spec": "4k", "quantity": 1, "prompt": "包含禁止词", "params": {"aspect_ratio": "1:1"}})
    assert blocked.status_code == 422
    assert tasks.recent_for_account(account_space_id, limit=10) == ()
    assert risks.total(since=None) == 1
    uploaded = client.post("/api/v1/admin/prompt-safety/upload", headers={"Authorization": f"Bearer {admin_token}"}, files={"file": ("words.txt", b"alpha\n\nAlpha\nbeta", "text/plain")})
    assert uploaded.status_code == 200
    assert uploaded.json()["keywords"] == ["alpha", "beta"]


def test_consecutive_failures_create_one_risk_event_and_admin_pagination():
    client, admin_token, _, _, _, _, risks = _context()
    for _ in range(9):
        assert risks.record_generation_outcome(False) is None
    assert risks.record_generation_outcome(False) is not None
    assert risks.record_generation_outcome(False) is None
    response = client.get("/api/v1/admin/risk-events?window=all&page=1&page_size=20", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["total_entries"] == 1

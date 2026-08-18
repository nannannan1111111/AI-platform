import json
from pathlib import Path

import httpx
import pytest

from app.release_contract import ReleaseContractError, validate_release_contract, write_release_snapshot
from scripts.staging_acceptance import AcceptanceInputs, run_acceptance
from scripts.staging_smoke import run_smoke


@pytest.mark.anyio
async def test_staging_smoke_checks_public_contracts_and_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.url.path == "/api/v1/auth/me":
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"user_id": "user-1"})
        return httpx.Response(200, json={}, headers={"X-Request-ID": "safe-request-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_smoke(client, "https://staging.example.com", email="test@example.com", password="not-written")

    assert all(result.ok for result in results)
    assert all("not-written" not in json.dumps(result.__dict__ if hasattr(result, "__dict__") else {}) for result in results)
    assert any(result.request_id == "safe-request-1" for result in results)


@pytest.mark.anyio
async def test_staging_acceptance_requires_an_explicit_state_change_flag() -> None:
    requests: list[httpx.Request] = []

    async with httpx.AsyncClient(transport=httpx.MockTransport(requests.append)) as client:
        results = await run_acceptance(
            client,
            "https://staging.example.com",
            scenarios=("cancel-task", "payment-replay", "cleanup-media"),
            inputs=AcceptanceInputs(
                admin_password="admin-secret",
                epay_notification=b"sign=payment-secret",
                cleanup_media_ids=("media-internal-1",),
            ),
        )

    assert requests == []
    assert {result.detail for result in results} == {"state_change_not_allowed"}
    evidence = json.dumps([result.__dict__ if hasattr(result, "__dict__") else str(result) for result in results])
    assert "admin-secret" not in evidence
    assert "payment-secret" not in evidence
    assert "media-internal-1" not in evidence


@pytest.mark.anyio
async def test_staging_acceptance_checks_idempotency_and_emits_redacted_evidence() -> None:
    cancelled = {"status": "cancelled"}
    balance = {"available_credits": "1.0000", "frozen_credits": "0.0000"}
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_id = "request-" + str(len(deleted) + 1)
        if request.url.path == "/api/v1/auth/login":
            body = request.content.decode()
            token = "admin-token" if "admin@example.com" in body else "user-token"
            return httpx.Response(200, json={"access_token": token}, headers={"X-Request-ID": request_id})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json=cancelled, headers={"X-Request-ID": request_id})
        if request.url.path.endswith("/task-timeout"):
            return httpx.Response(
                200,
                json={
                    "status": "failed",
                    "failure_message": "生成超过管理员设置的任务时限，已按超时结束，冻结额度已退回。",
                    "prompt": "private prompt",
                },
                headers={"X-Request-ID": request_id},
            )
        if request.url.path.endswith("/task-late"):
            return httpx.Response(
                200,
                json={
                    "status": "failed",
                    "failure_message": "生成超过管理员设置的任务时限，已按超时结束，冻结额度已退回。",
                    "prompt": "private prompt",
                },
                headers={"X-Request-ID": request_id},
            )
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json=[], headers={"X-Request-ID": request_id})
        if request.url.path == "/api/v1/payments/epay/notify":
            return httpx.Response(200, text="success", headers={"X-Request-ID": request_id})
        if request.url.path == "/api/v1/credits/balance":
            return httpx.Response(200, json=balance, headers={"X-Request-ID": request_id})
        if request.method == "DELETE" and request.url.path.startswith("/api/v1/media/"):
            deleted.append(request.url.path)
            return httpx.Response(204, headers={"X-Request-ID": request_id})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_acceptance(
            client,
            "https://staging.example.com",
            scenarios=("cancel-task", "provider-timeout", "late-result", "payment-replay", "cleanup-media"),
            inputs=AcceptanceInputs(
                test_email="test@example.com",
                test_password="test-password",
                admin_email="admin@example.com",
                admin_password="admin-password",
                account_space_id="account-space-private",
                cancel_task_id="task-cancel",
                timeout_task_id="task-timeout",
                late_result_task_id="task-late",
                epay_notification=b"out_trade_no=private-order&sign=payment-signature",
                cleanup_media_ids=("media-private-1",),
            ),
            allow_state_change=True,
        )

    assert all(result.outcome == "passed" for result in results)
    assert deleted == ["/api/v1/media/media-private-1"]
    evidence = json.dumps([result.__dict__ if hasattr(result, "__dict__") else str(result) for result in results])
    for secret in ("test-password", "admin-password", "private prompt", "payment-signature", "account-space-private"):
        assert secret not in evidence
    assert "/api/v1/" not in evidence


def test_release_contract_rejects_mutable_images_and_incompatible_rollback() -> None:
    with pytest.raises(ReleaseContractError, match="immutable"):
        validate_release_contract(image="registry/app:latest", migration_head="head")
    with pytest.raises(ReleaseContractError, match="migration heads differ"):
        validate_release_contract(
            image="registry/app@sha256:" + "a" * 64,
            migration_head="new-head",
            previous_image="registry/app@sha256:" + "b" * 64,
            previous_migration_head="old-head",
        )


def test_release_contract_writes_non_sensitive_snapshot(tmp_path: Path) -> None:
    destination = tmp_path / "release-state.json"
    snapshot = write_release_snapshot(
        destination=destination,
        image="registry/app@sha256:" + "a" * 64,
        migration_head="head",
    )
    assert snapshot.image.endswith("a" * 64)
    assert json.loads(destination.read_text(encoding="utf-8"))["migration_head"] == "head"


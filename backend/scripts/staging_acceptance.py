#!/usr/bin/env python3
"""Run guarded staging acceptance scenarios and emit redacted evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from scripts.staging_smoke import run_smoke, safe_request_id

Scenario = Literal["baseline", "cancel-task", "provider-timeout", "late-result", "payment-replay", "cleanup-media"]
Outcome = Literal["passed", "failed"]

SCENARIOS: tuple[Scenario, ...] = (
    "baseline",
    "cancel-task",
    "provider-timeout",
    "late-result",
    "payment-replay",
    "cleanup-media",
)
STATE_CHANGING_SCENARIOS = frozenset({"cancel-task", "payment-replay", "cleanup-media"})
_TIMEOUT_MESSAGE = "生成超过管理员设置的任务时限，已按超时结束，冻结额度已退回。"
_SAFE_BASELINE_NAMES = {
    "/healthz": "baseline-health",
    "/readyz": "baseline-ready",
    "/api/v1/image-models": "baseline-image-model-catalog",
    "/api/v1/payment-methods": "baseline-payment-method-catalog",
    "/api/v1/auth/login": "baseline-login",
    "/api/v1/auth/login/token": "baseline-login-token",
    "/api/v1/auth/me": "baseline-session",
}


@dataclass(frozen=True, slots=True)
class AcceptanceInputs:
    """Environment-provided staging identifiers and credentials."""

    test_email: str | None = None
    test_password: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    account_space_id: str | None = None
    cancel_task_id: str | None = None
    timeout_task_id: str | None = None
    late_result_task_id: str | None = None
    epay_notification: bytes | None = None
    cleanup_media_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    """A deliberately small evidence record that cannot contain response bodies."""

    scenario: str
    mode: Literal["automated", "operator-assisted"]
    outcome: Outcome
    status_code: int
    request_id: str = ""
    detail: str = ""


def _result(
    scenario: str,
    *,
    ok: bool,
    response: httpx.Response | None = None,
    mode: Literal["automated", "operator-assisted"] = "automated",
    detail: str = "",
) -> AcceptanceResult:
    return AcceptanceResult(
        scenario=scenario,
        mode=mode,
        outcome="passed" if ok else "failed",
        status_code=0 if response is None else response.status_code,
        request_id="" if response is None else safe_request_id(response),
        detail=detail,
    )


async def _request(
    client: httpx.AsyncClient,
    base_url: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response | None:
    try:
        return await client.request(method, f"{base_url}{path}", **kwargs)
    except Exception:
        return None


async def _login(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    email: str | None,
    password: str | None,
    role: str,
) -> tuple[str | None, AcceptanceResult]:
    if not email or not password:
        return None, _result(f"{role}-login", ok=False, detail="configuration_missing")
    response = await _request(
        client,
        base_url,
        "POST",
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    token: str | None = None
    if response is not None and response.status_code == 200:
        payload = response.json()
        candidate = payload.get("access_token") if isinstance(payload, dict) else None
        token = candidate if isinstance(candidate, str) and candidate else None
    return token, _result(
        f"{role}-login",
        ok=token is not None,
        response=response,
        detail="" if token is not None else "authentication_failed",
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json_object(response: httpx.Response | None) -> dict[str, object] | None:
    if response is None or response.status_code != 200:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


async def _cancel_task(
    client: httpx.AsyncClient,
    base_url: str,
    inputs: AcceptanceInputs,
    admin_token: str,
    test_token: str,
) -> list[AcceptanceResult]:
    if not inputs.account_space_id or not inputs.cancel_task_id:
        return [_result("cancel-task", ok=False, detail="configuration_missing")]
    path = f"/api/v1/admin/generation-tasks/{inputs.cancel_task_id}/cancel"
    request = {"account_space_id": inputs.account_space_id}
    headers = _bearer(admin_token)
    first = await _request(client, base_url, "POST", path, headers=headers, json=request)
    balance_after_first = await _request(
        client,
        base_url,
        "GET",
        "/api/v1/credits/balance",
        headers=_bearer(test_token),
    )
    replay = await _request(client, base_url, "POST", path, headers=headers, json=request)
    balance_after_replay = await _request(
        client,
        base_url,
        "GET",
        "/api/v1/credits/balance",
        headers=_bearer(test_token),
    )
    first_body = _json_object(first)
    replay_body = _json_object(replay)
    first_ok = first_body is not None and first_body.get("status") == "cancelled"
    replay_ok = replay_body is not None and replay_body.get("status") == "cancelled"
    first_balance = _json_object(balance_after_first)
    replay_balance = _json_object(balance_after_replay)
    stable = first_balance is not None and replay_balance == first_balance
    return [
        _result("cancel-task", ok=first_ok, response=first, detail="" if first_ok else "cancellation_failed"),
        _result(
            "cancel-task-replay",
            ok=replay_ok and replay_body == first_body,
            response=replay,
            detail="" if replay_ok and replay_body == first_body else "idempotency_failed",
        ),
        _result(
            "cancel-task-balance-idempotency",
            ok=stable,
            response=balance_after_replay,
            detail="" if stable else "balance_changed_on_replay",
        ),
    ]


async def _task_outcome(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    token: str,
    task_id: str | None,
    scenario: Literal["provider-timeout", "late-result"],
) -> list[AcceptanceResult]:
    if not task_id:
        return [_result(scenario, ok=False, mode="operator-assisted", detail="configuration_missing")]
    headers = _bearer(token)
    task_response = await _request(client, base_url, "GET", f"/api/v1/generation-tasks/{task_id}", headers=headers)
    media_response = await _request(
        client,
        base_url,
        "GET",
        f"/api/v1/generation-tasks/{task_id}/media",
        headers=headers,
    )
    task = _json_object(task_response)
    media = media_response.json() if media_response is not None and media_response.status_code == 200 else None
    task_ok = task is not None and task.get("status") == "failed" and task.get("failure_message") == _TIMEOUT_MESSAGE
    media_ok = isinstance(media, list) and not media
    detail = "" if task_ok and media_ok else "authoritative_timeout_not_observed"
    return [
        _result(
            scenario,
            ok=task_ok and media_ok,
            response=task_response,
            mode="operator-assisted",
            detail=detail,
        ),
        _result(
            f"{scenario}-media-empty",
            ok=media_ok,
            response=media_response,
            mode="operator-assisted",
            detail="" if media_ok else "unexpected_media",
        ),
    ]


async def _payment_replay(
    client: httpx.AsyncClient,
    base_url: str,
    inputs: AcceptanceInputs,
    test_token: str,
) -> list[AcceptanceResult]:
    if inputs.epay_notification is None:
        return [_result("payment-replay", ok=False, mode="operator-assisted", detail="configuration_missing")]
    headers = _bearer(test_token)
    notification_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    first = await _request(
        client,
        base_url,
        "POST",
        "/api/v1/payments/epay/notify",
        headers=notification_headers,
        content=inputs.epay_notification,
    )
    balance_after_first = await _request(client, base_url, "GET", "/api/v1/credits/balance", headers=headers)
    replay = await _request(
        client,
        base_url,
        "POST",
        "/api/v1/payments/epay/notify",
        headers=notification_headers,
        content=inputs.epay_notification,
    )
    balance_after_replay = await _request(client, base_url, "GET", "/api/v1/credits/balance", headers=headers)
    first_ok = first is not None and first.status_code == 200 and first.text == "success"
    replay_ok = replay is not None and replay.status_code == 200 and replay.text == "success"
    first_balance = _json_object(balance_after_first)
    replay_balance = _json_object(balance_after_replay)
    stable = first_balance is not None and replay_balance == first_balance
    return [
        _result(
            "payment-notification",
            ok=first_ok,
            response=first,
            mode="operator-assisted",
            detail="" if first_ok else "notification_failed",
        ),
        _result(
            "payment-notification-replay",
            ok=replay_ok,
            response=replay,
            mode="operator-assisted",
            detail="" if replay_ok else "notification_replay_failed",
        ),
        _result(
            "payment-balance-idempotency",
            ok=stable,
            response=balance_after_replay,
            mode="operator-assisted",
            detail="" if stable else "balance_changed_on_replay",
        ),
    ]


async def _cleanup_media(
    client: httpx.AsyncClient,
    base_url: str,
    inputs: AcceptanceInputs,
    test_token: str,
) -> list[AcceptanceResult]:
    if not inputs.cleanup_media_ids:
        return [_result("cleanup-media", ok=False, detail="configuration_missing")]
    results: list[AcceptanceResult] = []
    for index, media_id in enumerate(inputs.cleanup_media_ids, start=1):
        response = await _request(
            client,
            base_url,
            "DELETE",
            f"/api/v1/media/{media_id}",
            headers=_bearer(test_token),
        )
        ok = response is not None and response.status_code in (204, 404)
        results.append(
            _result(
                f"cleanup-media-{index}",
                ok=ok,
                response=response,
                detail="" if ok else "cleanup_failed",
            )
        )
    return results


async def run_acceptance(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    scenarios: tuple[Scenario, ...],
    inputs: AcceptanceInputs,
    allow_state_change: bool = False,
) -> tuple[AcceptanceResult, ...]:
    """Execute selected scenarios without returning bodies, tokens, IDs, or paths."""
    normalized_base = base_url.rstrip("/")
    results: list[AcceptanceResult] = []
    blocked = STATE_CHANGING_SCENARIOS.intersection(scenarios) if not allow_state_change else set()
    runnable = tuple(scenario for scenario in scenarios if scenario not in blocked)
    for scenario in scenarios:
        if scenario in blocked:
            results.append(_result(scenario, ok=False, detail="state_change_not_allowed"))

    if "baseline" in runnable:
        smoke = await run_smoke(
            client,
            normalized_base,
            email=inputs.test_email,
            password=inputs.test_password,
        )
        results.extend(
            AcceptanceResult(
                scenario=_SAFE_BASELINE_NAMES.get(probe.path, "baseline-probe"),
                mode="automated",
                outcome="passed" if probe.ok else "failed",
                status_code=probe.status_code,
                request_id=probe.request_id,
                detail=probe.detail.replace(" ", "_") if probe.detail else "",
            )
            for probe in smoke
        )

    user_scenarios = {
        "cancel-task",
        "provider-timeout",
        "late-result",
        "payment-replay",
        "cleanup-media",
    }.intersection(runnable)
    test_token: str | None = None
    if user_scenarios:
        test_token, login_result = await _login(
            client,
            normalized_base,
            email=inputs.test_email,
            password=inputs.test_password,
            role="test-user",
        )
        results.append(login_result)

    admin_token: str | None = None
    if "cancel-task" in runnable:
        admin_token, login_result = await _login(
            client,
            normalized_base,
            email=inputs.admin_email,
            password=inputs.admin_password,
            role="admin",
        )
        results.append(login_result)

    if "cancel-task" in runnable and admin_token is not None and test_token is not None:
        results.extend(await _cancel_task(client, normalized_base, inputs, admin_token, test_token))
    if "provider-timeout" in runnable and test_token is not None:
        results.extend(
            await _task_outcome(
                client,
                normalized_base,
                token=test_token,
                task_id=inputs.timeout_task_id,
                scenario="provider-timeout",
            )
        )
    if "late-result" in runnable and test_token is not None:
        results.extend(
            await _task_outcome(
                client,
                normalized_base,
                token=test_token,
                task_id=inputs.late_result_task_id,
                scenario="late-result",
            )
        )
    if "payment-replay" in runnable and test_token is not None:
        results.extend(await _payment_replay(client, normalized_base, inputs, test_token))
    if "cleanup-media" in runnable and test_token is not None:
        results.extend(await _cleanup_media(client, normalized_base, inputs, test_token))
    return tuple(results)


def _read_notification() -> bytes | None:
    configured = os.getenv("STAGING_EPAY_NOTIFICATION_FILE")
    if not configured:
        return None
    try:
        return Path(configured).read_bytes()
    except OSError:
        return None


def _environment_inputs() -> AcceptanceInputs:
    cleanup_ids = tuple(filter(None, (item.strip() for item in os.getenv("STAGING_CLEANUP_MEDIA_IDS", "").split(","))))
    return AcceptanceInputs(
        test_email=os.getenv("STAGING_TEST_EMAIL"),
        test_password=os.getenv("STAGING_TEST_PASSWORD"),
        admin_email=os.getenv("STAGING_ADMIN_EMAIL"),
        admin_password=os.getenv("STAGING_ADMIN_PASSWORD"),
        account_space_id=os.getenv("STAGING_ACCOUNT_SPACE_ID"),
        cancel_task_id=os.getenv("STAGING_CANCEL_TASK_ID"),
        timeout_task_id=os.getenv("STAGING_TIMEOUT_TASK_ID"),
        late_result_task_id=os.getenv("STAGING_LATE_RESULT_TASK_ID"),
        epay_notification=_read_notification(),
        cleanup_media_ids=cleanup_ids,
    )


def _validate_base_url(value: str) -> str:
    url = httpx.URL(value)
    if url.scheme not in ("http", "https") or not url.host or url.userinfo or url.query or url.fragment:
        raise argparse.ArgumentTypeError("base_url must be an HTTP(S) origin without credentials, query, or fragment")
    if url.path not in ("", "/"):
        raise argparse.ArgumentTypeError("base_url must not contain a path")
    return value.rstrip("/")


async def main() -> int:
    """Parse options, run guarded scenarios, and optionally save JSONL evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", type=_validate_base_url, help="staging origin, for example https://staging.example.com")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS, dest="scenarios")
    parser.add_argument("--allow-state-change", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--evidence", type=Path, help="optional JSONL evidence destination")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    scenarios = tuple(args.scenarios or ("baseline",))
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits, follow_redirects=False) as client:
        results = await run_acceptance(
            client,
            args.base_url,
            scenarios=scenarios,
            inputs=_environment_inputs(),
            allow_state_change=args.allow_state_change,
        )
    lines = [json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")) for result in results]
    output = "\n".join(lines) + ("\n" if lines else "")
    print(output, end="")
    if args.evidence is not None:
        args.evidence.write_text(output, encoding="utf-8")
    return 0 if results and all(result.outcome == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

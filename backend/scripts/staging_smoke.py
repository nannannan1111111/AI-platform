#!/usr/bin/env python3
"""Run a safe, non-destructive pre-release HTTP smoke test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """A status-only probe result that never stores response bodies."""

    path: str
    status_code: int
    expected: tuple[int, ...]
    ok: bool
    detail: str = ""
    request_id: str = ""


def safe_request_id(response: httpx.Response) -> str:
    """Return only a bounded, log-safe correlation identifier."""
    request_id = response.headers.get("x-request-id", "").strip()
    return request_id if _SAFE_REQUEST_ID.fullmatch(request_id) else ""


async def run_smoke(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    email: str | None = None,
    password: str | None = None,
) -> tuple[ProbeResult, ...]:
    """Probe public contracts and, when supplied, one isolated test account."""
    normalized_base = base_url.rstrip("/")
    results: list[ProbeResult] = []

    async def probe(path: str, expected: tuple[int, ...], **request: Any) -> httpx.Response | None:
        try:
            response = await client.request(request.pop("method", "GET"), f"{normalized_base}{path}", **request)
        except Exception as exc:
            results.append(ProbeResult(path, 0, expected, False, type(exc).__name__))
            return None
        ok = response.status_code in expected
        results.append(
            ProbeResult(
                path,
                response.status_code,
                expected,
                ok,
                "" if ok else "unexpected status",
                safe_request_id(response),
            )
        )
        return response

    await probe("/healthz", (200,))
    await probe("/readyz", (200,))
    await probe("/api/v1/image-models", (200,))
    await probe("/api/v1/payment-methods", (200,))
    if (email is None) != (password is None):
        results.append(ProbeResult("/api/v1/auth/login", 0, (200,), False, "email and password must be provided together"))
    elif email is not None and password is not None:
        login = await probe(
            "/api/v1/auth/login",
            (200,),
            method="POST",
            json={"email": email, "password": password},
        )
        if login is not None and login.status_code == 200:
            payload = login.json()
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                results.append(ProbeResult("/api/v1/auth/login/token", 200, (200,), False, "token missing"))
            else:
                await probe("/api/v1/auth/me", (200,), headers={"Authorization": f"Bearer {token}"})
    return tuple(results)


async def main() -> int:
    """Parse options, run probes, and emit status-only JSON lines."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="staging origin, for example https://staging.example.com")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    email = os.getenv("STAGING_TEST_EMAIL")
    password = os.getenv("STAGING_TEST_PASSWORD")
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits, follow_redirects=False) as client:
        results = await run_smoke(client, args.base_url, email=email, password=password)
    for result in results:
        print(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


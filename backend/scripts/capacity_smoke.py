"""Dependency-light capacity probe with machine-readable stop thresholds."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx


class Clock(Protocol):
    """Return a monotonic timestamp."""

    def __call__(self) -> float:
        """Read the current monotonic timestamp."""
        ...


@dataclass(frozen=True, slots=True)
class CapacityResult:
    """Aggregate capacity evidence without URLs, headers, or response bodies."""

    requests: int
    concurrency: int
    failures: int
    failure_rate_percent: float
    elapsed_seconds: float
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def _percentile(ordered: list[float], value: float) -> float:
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * value) - 1))
    return ordered[index] * 1000


async def run_probe(
    client: httpx.AsyncClient,
    url: str,
    *,
    concurrency: int,
    requests: int,
    expected_statuses: tuple[int, ...] = (200,),
    clock: Clock = time.perf_counter,
) -> CapacityResult:
    """Run bounded concurrent GET requests and return aggregate evidence."""
    if concurrency <= 0 or requests <= 0:
        raise ValueError("concurrency and requests must be positive")
    if not expected_statuses:
        raise ValueError("at least one expected status is required")
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures = 0
    started = clock()

    async def request_once() -> None:
        nonlocal failures
        async with semaphore:
            request_started = clock()
            try:
                response = await client.get(url)
                if response.status_code not in expected_statuses:
                    failures += 1
            except Exception:
                failures += 1
            finally:
                latencies.append(clock() - request_started)

    await asyncio.gather(*(request_once() for _ in range(requests)))
    elapsed = max(clock() - started, 1e-9)
    ordered = sorted(latencies)
    return CapacityResult(
        requests=requests,
        concurrency=concurrency,
        failures=failures,
        failure_rate_percent=round(failures * 100 / requests, 4),
        elapsed_seconds=round(elapsed, 4),
        rps=round(requests / elapsed, 1),
        p50_ms=round(_percentile(ordered, 0.50), 1),
        p95_ms=round(_percentile(ordered, 0.95), 1),
        p99_ms=round(_percentile(ordered, 0.99), 1),
    )


def threshold_violations(
    result: CapacityResult,
    *,
    maximum_failure_rate_percent: float,
    maximum_p95_ms: float | None,
    minimum_rps: float | None,
) -> tuple[str, ...]:
    """Return stable names for capacity thresholds that did not pass."""
    violations: list[str] = []
    if result.failure_rate_percent > maximum_failure_rate_percent:
        violations.append("failure_rate_percent")
    if maximum_p95_ms is not None and result.p95_ms > maximum_p95_ms:
        violations.append("p95_ms")
    if minimum_rps is not None and result.rps < minimum_rps:
        violations.append("rps")
    return tuple(violations)


async def main() -> int:
    """Run the configured capacity probe and return a shell-friendly decision."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="target URL, for example https://studio.example.com/readyz")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--expected-status", type=int, action="append", dest="expected_statuses")
    parser.add_argument("--max-failure-rate-percent", type=float, default=0.0)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--min-rps", type=float)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    if args.concurrency <= 0 or args.requests <= 0 or args.timeout <= 0:
        parser.error("concurrency, requests, and timeout must be positive")
    if not math.isfinite(args.max_failure_rate_percent) or not 0 <= args.max_failure_rate_percent <= 100:
        parser.error("max-failure-rate-percent must be between 0 and 100")
    if args.max_p95_ms is not None and (not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0):
        parser.error("max-p95-ms must be positive")
    if args.min_rps is not None and (not math.isfinite(args.min_rps) or args.min_rps <= 0):
        parser.error("min-rps must be positive")
    headers = {}
    token = os.getenv("CAPACITY_BEARER_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(
        timeout=args.timeout,
        limits=limits,
        follow_redirects=False,
        headers=headers,
    ) as client:
        result = await run_probe(
            client,
            args.url,
            concurrency=args.concurrency,
            requests=args.requests,
            expected_statuses=tuple(args.expected_statuses or (200,)),
        )
    violations = threshold_violations(
        result,
        maximum_failure_rate_percent=args.max_failure_rate_percent,
        maximum_p95_ms=args.max_p95_ms,
        minimum_rps=args.min_rps,
    )
    if args.as_json:
        print(
            json.dumps(
                {**asdict(result), "decision": "stop" if violations else "promote", "violations": violations},
                separators=(",", ":"),
            )
        )
    else:
        print(
            f"requests={result.requests} concurrency={result.concurrency} failures={result.failures} "
            f"failure_rate_percent={result.failure_rate_percent:.4f} rps={result.rps:.1f} "
            f"p50_ms={result.p50_ms:.1f} p95_ms={result.p95_ms:.1f} p99_ms={result.p99_ms:.1f} "
            f"decision={'stop' if violations else 'promote'}"
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

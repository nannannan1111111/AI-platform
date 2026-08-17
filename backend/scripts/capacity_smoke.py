"""Small dependency-light concurrency smoke test for a deployed HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import math
import time

import httpx


async def main() -> int:
    """Run the configured concurrency probe and return a shell-friendly status."""
    parser = argparse.ArgumentParser(description="Run concurrent GET requests and report latency/error rate")
    parser.add_argument("url", help="Target URL, for example https://studio.example.com/readyz")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if args.concurrency <= 0 or args.requests <= 0:
        parser.error("concurrency and requests must be positive")

    semaphore = asyncio.Semaphore(args.concurrency)
    latencies: list[float] = []
    failures: list[str] = []
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits, follow_redirects=False) as client:

        async def request_once() -> None:
            async with semaphore:
                request_started = time.perf_counter()
                try:
                    response = await client.get(args.url)
                    if response.status_code >= 400:
                        failures.append(str(response.status_code))
                except Exception as exc:
                    failures.append(type(exc).__name__)
                finally:
                    latencies.append(time.perf_counter() - request_started)

        await asyncio.gather(*(request_once() for _ in range(args.requests)))

    elapsed = time.perf_counter() - started
    ordered = sorted(latencies)

    def percentile(value: float) -> float:
        index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * value) - 1))
        return ordered[index] * 1000

    print(
        f"requests={args.requests} concurrency={args.concurrency} failures={len(failures)} "
        f"rps={args.requests / elapsed:.1f} p50_ms={percentile(0.50):.1f} "
        f"p95_ms={percentile(0.95):.1f} p99_ms={percentile(0.99):.1f}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

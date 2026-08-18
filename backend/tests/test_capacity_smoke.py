import httpx
import pytest

from scripts.capacity_smoke import CapacityResult, run_probe, threshold_violations


@pytest.mark.anyio
async def test_capacity_probe_returns_aggregate_status_without_response_data() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = 200 if calls % 2 else 503
        return httpx.Response(status, text="private-response-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_probe(
            client,
            "https://staging.example.com/readyz",
            concurrency=2,
            requests=4,
        )

    assert result.requests == 4
    assert result.failures == 2
    assert result.failure_rate_percent == 50.0
    assert "private-response-body" not in repr(result)


def test_capacity_thresholds_produce_stable_stop_reasons() -> None:
    result = CapacityResult(
        requests=1000,
        concurrency=100,
        failures=11,
        failure_rate_percent=1.1,
        elapsed_seconds=20.0,
        rps=50.0,
        p50_ms=400.0,
        p95_ms=2501.0,
        p99_ms=3000.0,
    )

    assert threshold_violations(
        result,
        maximum_failure_rate_percent=1.0,
        maximum_p95_ms=2500.0,
        minimum_rps=60.0,
    ) == ("failure_rate_percent", "p95_ms", "rps")

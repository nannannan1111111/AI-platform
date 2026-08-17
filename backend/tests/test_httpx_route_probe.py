import httpx

from app.model_routing import HttpxRouteProbe, RouteHealthStatus, RouteProbeTarget


def test_httpx_route_probe_verifies_model_and_measures_total_latency() -> None:
    ticks = iter((10.0, 10.184))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "gpt-image-2"}]}, request=request)
    )
    probe = HttpxRouteProbe(
        client=httpx.Client(transport=transport),
        resolver=lambda host, port: ("93.184.216.34",),
        monotonic=iter(ticks).__next__,
    )

    result = probe.probe(
        RouteProbeTarget(
            base_url="https://images.example.com/v1",
            api_key="test-only",
            provider_model_name="gpt-image-2",
        )
    )

    assert result.status is RouteHealthStatus.HEALTHY
    assert result.total_latency_ms == 184
    assert result.error_code == ""


def test_httpx_route_probe_rejects_private_addresses_before_sending_request() -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, request=request))
    probe = HttpxRouteProbe(
        client=httpx.Client(transport=transport),
        resolver=lambda host, port: ("127.0.0.1",),
    )

    result = probe.probe(
        RouteProbeTarget(
            base_url="https://internal.example/v1",
            api_key="test-only",
            provider_model_name="gpt-image-2",
        )
    )

    assert result.status is RouteHealthStatus.UNHEALTHY
    assert result.error_code == "unsafe_address"
    assert requests == []

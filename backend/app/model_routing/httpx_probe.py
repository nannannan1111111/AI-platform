"""OpenAI 兼容图片来源的安全 HTTP 健康探测 Adapter。"""

from __future__ import annotations

import socket
from collections.abc import Callable
from ipaddress import ip_address
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from app.model_routing.models import RouteHealthStatus
from app.model_routing.probe import ProbeResult, RouteProbeTarget


class HttpxRouteProbe:
    """验证公共 HTTPS 来源、鉴权、模型存在性并测量总延时。"""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        resolver: Callable[[str, int], tuple[str, ...]] | None = None,
        monotonic: Callable[[], float] | None = None,
        timeout_seconds: float = 10.0,
        degraded_latency_ms: int = 3_000,
    ) -> None:
        """接受可替换网络依赖，使安全规则无需真实外部请求即可验证。"""
        self._client = client or httpx.Client(follow_redirects=False)
        self._resolver = resolver or _resolve_addresses
        self._monotonic = monotonic or perf_counter
        self._timeout_seconds = timeout_seconds
        self._degraded_latency_ms = degraded_latency_ms

    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        """探测模型列表端点，且不在结果或异常中携带响应正文与凭据。"""
        parsed = urlsplit(target.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            return _failed("invalid_https_url")
        try:
            addresses = self._resolver(parsed.hostname, parsed.port or 443)
        except OSError:
            return _failed("dns_failure")
        if not addresses or any(not ip_address(address).is_global for address in addresses):
            return _failed("unsafe_address")

        started_at = self._monotonic()
        try:
            response = self._client.get(
                f"{target.base_url}/models",
                headers={
                    "Authorization": f"Bearer {target.api_key}",
                    "Accept": "application/json",
                },
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            return _failed("timeout", _elapsed_ms(started_at, self._monotonic()))
        except httpx.HTTPError:
            return _failed("network_error", _elapsed_ms(started_at, self._monotonic()))
        elapsed_ms = _elapsed_ms(started_at, self._monotonic())
        if 300 <= response.status_code < 400:
            return _failed("redirect_not_allowed", elapsed_ms)
        if response.status_code in {401, 403}:
            return _failed("authentication_failed", elapsed_ms)
        if response.status_code == 429:
            return _failed("rate_limited", elapsed_ms)
        if response.status_code >= 500:
            return _failed("upstream_unavailable", elapsed_ms)
        if not 200 <= response.status_code < 300:
            return _failed("unexpected_status", elapsed_ms)
        try:
            payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            model_ids = {
                str(model["id"]) for model in models if isinstance(model, dict) and isinstance(model.get("id"), str)
            }
        except (TypeError, ValueError):
            return _failed("invalid_response", elapsed_ms)
        if target.provider_model_name not in model_ids:
            return _failed("model_unavailable", elapsed_ms)
        health = RouteHealthStatus.DEGRADED if elapsed_ms >= self._degraded_latency_ms else RouteHealthStatus.HEALTHY
        return ProbeResult(status=health, total_latency_ms=elapsed_ms)


def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
    """解析探测时实际可见的全部 IPv4/IPv6 地址。"""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(address[4][0]) for address in addresses))


def _elapsed_ms(started_at: float, finished_at: float) -> int:
    return max(round((finished_at - started_at) * 1_000), 0)


def _failed(error_code: str, total_latency_ms: int = 0) -> ProbeResult:
    return ProbeResult(
        status=RouteHealthStatus.UNHEALTHY,
        total_latency_ms=total_latency_ms,
        error_code=error_code,
    )

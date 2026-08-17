"""模型路由健康探测的内部 seam。"""

from dataclasses import dataclass
from typing import Protocol

from app.model_routing.models import RouteHealthStatus


@dataclass(frozen=True, slots=True)
class RouteProbeTarget:
    """仅在探测调用期间存在的来源目标与凭据。"""

    base_url: str
    api_key: str
    provider_model_name: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """不包含上游响应正文的单次探测结果。"""

    status: RouteHealthStatus
    total_latency_ms: int
    error_code: str = ""


class RouteProbe(Protocol):
    """从与生成 Worker 相同的网络位置探测一个来源路由。"""

    def probe(self, target: RouteProbeTarget) -> ProbeResult:
        """检测连接、鉴权、模型存在性与总延时。"""

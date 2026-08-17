"""模型路由 Module 的内部 Provider 执行目标 seam。"""

from dataclasses import dataclass, field
from typing import Protocol

from app.model_routing.models import ImageResponseMode, ProviderProtocol


class ProviderGenerationTargetNotFound(LookupError):
    """已固化路由的 Provider 执行配置不完整或不可读取。"""

    def __init__(self) -> None:
        super().__init__("provider generation target is unavailable")


@dataclass(frozen=True, slots=True)
class ProviderGenerationTarget:
    """只供 Provider Adapter 在单次调用期间使用的敏感执行目标。"""

    protocol: ProviderProtocol
    base_url: str
    api_key: str = field(repr=False)
    provider_model_name: str = ""
    image_response_mode: ImageResponseMode = ImageResponseMode.AUTO
    request_timeout_seconds: int = 600


class ProviderGenerationTargets(Protocol):
    """从已固化模型路由解析当前 Provider 执行配置。"""

    def resolve(self, route_id: str) -> ProviderGenerationTarget:
        """解析一个不重新执行健康或启用准入的敏感目标。"""

"""模型路由 Module 的公开 Interface。"""

from typing import Protocol

from app.model_routing.models import (
    ApiProvider,
    ImageModelRoute,
    ModelAvailability,
    ModelRouteCreation,
    ModelRouteUpdate,
    ProviderCreation,
    ProviderUpdate,
    RouteHealth,
    RouteSelection,
    RoutingPolicy,
    RoutingPolicyUpdate,
)


class ModelRouting(Protocol):
    """集中管理 API 来源及逻辑模型的兼容来源路由。"""

    def create_provider(self, command: ProviderCreation) -> ApiProvider:
        """保存一个默认禁用且凭据只写的 API 来源。"""

    def list_providers(self) -> tuple[ApiProvider, ...]:
        """读取不包含任何凭据或密钥引用的来源目录。"""

    def update_provider(self, command: ProviderUpdate) -> ApiProvider:
        """更新来源元数据、启用状态或轮换只写凭据。"""

    def delete_provider(self, provider_id: str) -> None:
        """不可恢复地退役一个已经没有活动路由的来源并清理凭据。"""

    def create_route(self, command: ModelRouteCreation) -> ImageModelRoute:
        """为逻辑模型规格增加一个默认禁用的兼容来源路由。"""

    def list_routes(self, logical_model: str = "", output_spec: str = "") -> tuple[ImageModelRoute, ...]:
        """按可选逻辑模型与成品规格读取路由。"""

    def reference_image_limit(self, logical_model: str, output_spec: str) -> int:
        """读取逻辑模型规格允许上传的参考图张数。"""

    def update_route(self, command: ModelRouteUpdate) -> ImageModelRoute:
        """调整路由优先级，或在健康检测通过后启用。"""

    def delete_route(self, route_id: str) -> None:
        """不可恢复地退役路由并让引用它的优先策略回到自动模式。"""

    def check_route(self, route_id: str) -> RouteHealth:
        """执行一次安全探测并更新路由的滚动健康快照。"""

    def route_health(self, route_id: str) -> RouteHealth:
        """读取路由最近一次滚动健康快照。"""

    def set_policy(self, command: RoutingPolicyUpdate) -> RoutingPolicy:
        """设置自动选择或管理员指定优先且失败回退的策略。"""

    def routing_policy(self, logical_model: str, output_spec: str) -> RoutingPolicy:
        """读取策略；没有显式配置时返回自动模式。"""

    def availability(self, logical_model: str, output_spec: str) -> ModelAvailability:
        """只读判断逻辑模型规格是否存在可选路由，不选择或占用路由。"""

    def select(self, logical_model: str, output_spec: str) -> RouteSelection:
        """从最近检测可用且已启用的兼容路由中选择来源。"""

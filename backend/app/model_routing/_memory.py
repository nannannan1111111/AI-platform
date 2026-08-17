"""模型路由 Interface 的内存 Adapter。"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil
from threading import RLock
from uuid import uuid4

from app.model_routing._generation_targets import ProviderGenerationTarget, ProviderGenerationTargetNotFound
from app.model_routing._validation import normalized_base_url, required, valid_reference_image_limit
from app.model_routing.models import (
    ApiProvider,
    ApiProviderNotFound,
    ImageModelRoute,
    InvalidModelRoute,
    InvalidProviderConfiguration,
    InvalidRoutingPolicy,
    ModelAvailability,
    ModelAvailabilityStatus,
    ModelRouteConflict,
    ModelRouteCreation,
    ModelRouteNotFound,
    ModelRouteUpdate,
    NoAvailableModelRoute,
    ProviderCodeConflict,
    ProviderCreation,
    ProviderHasRoutes,
    ProviderUpdate,
    RouteHealth,
    RouteHealthNotFound,
    RouteHealthStatus,
    RouteProbeUnavailable,
    RouteSelection,
    RoutingMode,
    RoutingPolicy,
    RoutingPolicyUpdate,
)
from app.model_routing.probe import RouteProbe, RouteProbeTarget
from app.model_routing.secrets import ProviderSecrets


@dataclass(frozen=True, slots=True)
class _ProviderEntry:
    provider: ApiProvider
    secret_ref: str


@dataclass(frozen=True, slots=True)
class _HealthEntry:
    snapshot: RouteHealth
    latency_samples: tuple[int, ...]
    successful_checks: int


class InMemoryModelRouting:
    """在单进程内保存管理员统一配置的来源和路由。"""

    def __init__(
        self,
        secrets: ProviderSecrets,
        *,
        probe: RouteProbe | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secrets = secrets
        self._probe = probe
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._providers: dict[str, _ProviderEntry] = {}
        self._deleted_provider_ids: set[str] = set()
        self._routes: dict[str, ImageModelRoute] = {}
        self._deleted_route_ids: set[str] = set()
        self._health: dict[str, _HealthEntry] = {}
        self._policies: dict[tuple[str, str], RoutingPolicy] = {}
        self._reference_image_limits: dict[tuple[str, str], int] = {}
        self._lock = RLock()

    def create_provider(self, command: ProviderCreation) -> ApiProvider:
        """保存来源元数据，并把明文凭据交给独立密钥 Adapter。"""
        code = required(command.code, "来源代码")
        display_name = required(command.display_name, "来源名称")
        base_url = normalized_base_url(command.base_url)
        with self._lock:
            if any(entry.provider.code == code for entry in self._providers.values()):
                raise ProviderCodeConflict(code)
            provider_id = self._id_factory()
            stored = self._secrets.store(provider_id, command.api_key)
            now = self._clock()
            provider = ApiProvider(
                provider_id=provider_id,
                code=code,
                display_name=display_name,
                protocol=command.protocol,
                base_url=base_url,
                image_response_mode=command.image_response_mode,
                concurrency_group=required(command.concurrency_group or code, "上游并发账户组"),
                max_concurrency=_valid_max_concurrency(command.max_concurrency),
                request_timeout_seconds=_valid_request_timeout(command.request_timeout_seconds),
                credential_configured=True,
                key_fingerprint=stored.key_fingerprint,
                enabled=False,
                created_at=now,
                updated_at=now,
            )
            self._providers[provider_id] = _ProviderEntry(provider=provider, secret_ref=stored.secret_ref)
        return provider

    def resolve(self, route_id: str) -> ProviderGenerationTarget:
        """解析已固化路由当前使用的敏感 Provider 执行配置。"""
        try:
            with self._lock:
                route = self._routes[route_id]
                if route_id in self._deleted_route_ids or route.provider_id in self._deleted_provider_ids:
                    raise KeyError(route_id)
                provider_entry = self._providers[route.provider_id]
                return ProviderGenerationTarget(
                    protocol=provider_entry.provider.protocol,
                    base_url=provider_entry.provider.base_url,
                    api_key=self._secrets.read(provider_entry.secret_ref),
                    provider_model_name=route.provider_model_name,
                    image_response_mode=provider_entry.provider.image_response_mode,
                    request_timeout_seconds=provider_entry.provider.request_timeout_seconds,
                )
        except KeyError as exc:
            raise ProviderGenerationTargetNotFound from exc

    def list_providers(self) -> tuple[ApiProvider, ...]:
        """返回不包含密钥引用的来源公开快照。"""
        with self._lock:
            return tuple(
                entry.provider
                for provider_id, entry in self._providers.items()
                if provider_id not in self._deleted_provider_ids
            )

    def update_provider(self, command: ProviderUpdate) -> ApiProvider:
        """更新来源元数据；新凭据仍只交给密钥 Adapter。"""
        with self._lock:
            try:
                entry = self._providers[command.provider_id]
                if command.provider_id in self._deleted_provider_ids:
                    raise KeyError(command.provider_id)
            except KeyError as exc:
                raise ApiProviderNotFound(command.provider_id) from exc
            provider = entry.provider
            secret_ref = entry.secret_ref
            key_fingerprint = provider.key_fingerprint
            base_url = provider.base_url if command.base_url is None else normalized_base_url(command.base_url)
            connection_changed = command.api_key is not None or base_url != provider.base_url
            if command.api_key is not None:
                stored = self._secrets.store(provider.provider_id, command.api_key)
                secret_ref = stored.secret_ref
                key_fingerprint = stored.key_fingerprint
            now = self._clock()
            updated = replace(
                provider,
                display_name=(
                    provider.display_name
                    if command.display_name is None
                    else required(command.display_name, "来源名称")
                ),
                base_url=base_url,
                key_fingerprint=key_fingerprint,
                enabled=provider.enabled if command.enabled is None else command.enabled,
                image_response_mode=(
                    provider.image_response_mode
                    if command.image_response_mode is None
                    else command.image_response_mode
                ),
                concurrency_group=(
                    provider.concurrency_group
                    if command.concurrency_group is None
                    else required(command.concurrency_group, "上游并发账户组")
                ),
                max_concurrency=(
                    provider.max_concurrency
                    if command.max_concurrency is None
                    else _valid_max_concurrency(command.max_concurrency)
                ),
                request_timeout_seconds=(
                    provider.request_timeout_seconds
                    if command.request_timeout_seconds is None
                    else _valid_request_timeout(command.request_timeout_seconds)
                ),
                updated_at=now,
            )
            self._providers[provider.provider_id] = _ProviderEntry(updated, secret_ref)
            if connection_changed:
                for route_id, route in tuple(self._routes.items()):
                    if route.provider_id != provider.provider_id:
                        continue
                    self._routes[route_id] = replace(
                        route,
                        enabled=False,
                        health_status=RouteHealthStatus.UNKNOWN,
                        updated_at=now,
                    )
                    self._health.pop(route_id, None)
            return updated
    def delete_provider(self, provider_id: str) -> None:
        """退役没有活动路由的来源并幂等清理其凭据。"""
        with self._lock:
            try:
                entry = self._providers[provider_id]
            except KeyError as exc:
                raise ApiProviderNotFound(provider_id) from exc
            if provider_id not in self._deleted_provider_ids and any(
                route.provider_id == provider_id and route_id not in self._deleted_route_ids
                for route_id, route in self._routes.items()
            ):
                raise ProviderHasRoutes("必须先删除该来源的全部模型路由")
            if provider_id not in self._deleted_provider_ids:
                self._providers[provider_id] = _ProviderEntry(
                    replace(entry.provider, enabled=False, updated_at=self._clock()),
                    entry.secret_ref,
                )
                self._deleted_provider_ids.add(provider_id)
            self._secrets.delete(entry.secret_ref)

    def create_route(self, command: ModelRouteCreation) -> ImageModelRoute:
        """增加默认禁用且健康状态未知的来源路由。"""
        logical_model = required(command.logical_model, "逻辑模型")
        output_spec = required(command.output_spec, "成品规格")
        provider_model_name = required(command.provider_model_name, "上游模型名称")
        compatibility_group = required(command.compatibility_group, "兼容组")
        if not 0 <= command.priority <= 10_000:
            raise InvalidModelRoute("路由优先级超出范围")
        reference_image_limit = valid_reference_image_limit(command.max_reference_images)
        with self._lock:
            if command.provider_id not in self._providers or command.provider_id in self._deleted_provider_ids:
                raise ApiProviderNotFound(command.provider_id)
            incompatible = any(
                route.route_id not in self._deleted_route_ids
                and route.logical_model == logical_model
                and route.output_spec == output_spec
                and route.compatibility_group != compatibility_group
                for route in self._routes.values()
            )
            if incompatible:
                raise InvalidModelRoute("同一逻辑模型规格的来源路由必须属于同一兼容组")
            if any(
                route.route_id not in self._deleted_route_ids
                and route.provider_id == command.provider_id
                and route.logical_model == logical_model
                and route.output_spec == output_spec
                and route.provider_model_name == provider_model_name
                for route in self._routes.values()
            ):
                raise ModelRouteConflict(command.provider_id)
            now = self._clock()
            route = ImageModelRoute(
                route_id=self._id_factory(),
                provider_id=command.provider_id,
                logical_model=logical_model,
                output_spec=output_spec,
                provider_model_name=provider_model_name,
                compatibility_group=compatibility_group,
                priority=command.priority,
                enabled=False,
                health_status=RouteHealthStatus.UNKNOWN,
                created_at=now,
                updated_at=now,
            )
            self._routes[route.route_id] = route
            self._reference_image_limits[(logical_model, output_spec)] = reference_image_limit
        return route

    def list_routes(self, logical_model: str = "", output_spec: str = "") -> tuple[ImageModelRoute, ...]:
        """按稳定创建顺序读取路由，可按逻辑模型和规格过滤。"""
        with self._lock:
            return tuple(
                route
                for route_id, route in self._routes.items()
                if route_id not in self._deleted_route_ids
                if (not logical_model or route.logical_model == logical_model)
                and (not output_spec or route.output_spec == output_spec)
            )

    def reference_image_limit(self, logical_model: str, output_spec: str) -> int:
        """Read the model-level reference image limit, defaulting existing models to three."""
        with self._lock:
            return self._reference_image_limits.get((logical_model, output_spec), 3)

    def update_route(self, command: ModelRouteUpdate) -> ImageModelRoute:
        """安全调整路由映射或优先级，并阻止未检测路由启用。"""
        with self._lock:
            try:
                route = self._routes[command.route_id]
                if command.route_id in self._deleted_route_ids:
                    raise KeyError(command.route_id)
            except KeyError as exc:
                raise ModelRouteNotFound(command.route_id) from exc
            if command.priority is not None and not 0 <= command.priority <= 10_000:
                raise InvalidModelRoute("路由优先级超出范围")
            reference_image_limit = (
                None
                if command.max_reference_images is None
                else valid_reference_image_limit(command.max_reference_images)
            )
            provider_model_name = (
                route.provider_model_name
                if command.provider_model_name is None
                else required(command.provider_model_name, "上游模型名称")
            )
            compatibility_group = (
                route.compatibility_group
                if command.compatibility_group is None
                else required(command.compatibility_group, "兼容组")
            )
            mapping_changed = (
                provider_model_name != route.provider_model_name or compatibility_group != route.compatibility_group
            )
            if mapping_changed and (route.enabled or command.enabled):
                raise InvalidModelRoute("修改模型映射前必须停用路由")
            if any(
                other.route_id != route.route_id
                and other.route_id not in self._deleted_route_ids
                and other.logical_model == route.logical_model
                and other.output_spec == route.output_spec
                and other.compatibility_group != compatibility_group
                for other in self._routes.values()
            ):
                raise InvalidModelRoute("同一逻辑模型规格的来源路由必须属于同一兼容组")
            if any(
                other.route_id != route.route_id
                and other.route_id not in self._deleted_route_ids
                and other.provider_id == route.provider_id
                and other.logical_model == route.logical_model
                and other.output_spec == route.output_spec
                and other.provider_model_name == provider_model_name
                for other in self._routes.values()
            ):
                raise ModelRouteConflict(route.provider_id)
            if command.enabled:
                health = self._health.get(command.route_id)
                if health is None or not health.snapshot.available:
                    raise InvalidModelRoute("模型路由必须通过健康检测后才能启用")
            updated = replace(
                route,
                provider_model_name=provider_model_name,
                compatibility_group=compatibility_group,
                priority=route.priority if command.priority is None else command.priority,
                enabled=route.enabled if command.enabled is None else command.enabled,
                health_status=RouteHealthStatus.UNKNOWN if mapping_changed else route.health_status,
                updated_at=self._clock(),
            )
            self._routes[route.route_id] = updated
            if reference_image_limit is not None:
                self._reference_image_limits[(route.logical_model, route.output_spec)] = reference_image_limit
            if mapping_changed:
                self._health.pop(route.route_id, None)
            return updated

    def delete_route(self, route_id: str) -> None:
        """退役路由并清除活动健康与指定优先策略。"""
        with self._lock:
            if route_id in self._deleted_route_ids:
                return
            try:
                route = self._routes[route_id]
            except KeyError as exc:
                raise ModelRouteNotFound(route_id) from exc
            now = self._clock()
            self._routes[route_id] = replace(
                route,
                enabled=False,
                health_status=RouteHealthStatus.UNKNOWN,
                updated_at=now,
            )
            self._deleted_route_ids.add(route_id)
            self._health.pop(route_id, None)
            for key, policy in tuple(self._policies.items()):
                if policy.preferred_route_id == route_id:
                    self._policies[key] = replace(
                        policy,
                        mode=RoutingMode.AUTOMATIC,
                        preferred_route_id="",
                        updated_at=now,
                    )

    def check_route(self, route_id: str) -> RouteHealth:
        """使用只在调用期间读取的凭据探测路由并更新滚动指标。"""
        if self._probe is None:
            raise RouteProbeUnavailable(route_id)
        with self._lock:
            try:
                route = self._routes[route_id]
                if route_id in self._deleted_route_ids or route.provider_id in self._deleted_provider_ids:
                    raise KeyError(route_id)
                provider_entry = self._providers[route.provider_id]
            except KeyError as exc:
                raise ModelRouteNotFound(route_id) from exc
            api_key = self._secrets.read(provider_entry.secret_ref)
            target = RouteProbeTarget(
                base_url=provider_entry.provider.base_url,
                api_key=api_key,
                provider_model_name=route.provider_model_name,
            )
        result = self._probe.probe(target)
        with self._lock:
            previous = self._health.get(route_id)
            samples = (*(() if previous is None else previous.latency_samples), result.total_latency_ms)[-20:]
            successful_checks = (0 if previous is None else previous.successful_checks) + int(
                result.status in {RouteHealthStatus.HEALTHY, RouteHealthStatus.DEGRADED}
            )
            sample_count = (0 if previous is None else previous.snapshot.sample_count) + 1
            previous_ewma = result.total_latency_ms if previous is None else previous.snapshot.ewma_latency_ms
            ewma = round(previous_ewma * 0.7 + result.total_latency_ms * 0.3)
            ordered = sorted(samples)
            p95 = ordered[max(ceil(len(ordered) * 0.95) - 1, 0)]
            available = result.status in {RouteHealthStatus.HEALTHY, RouteHealthStatus.DEGRADED}
            now = self._clock()
            snapshot = RouteHealth(
                route_id=route_id,
                status=result.status,
                available=available,
                total_latency_ms=result.total_latency_ms,
                ewma_latency_ms=ewma,
                p95_latency_ms=p95,
                success_rate=round(successful_checks / sample_count, 4),
                sample_count=sample_count,
                checked_at=now,
                error_code=result.error_code,
            )
            self._health[route_id] = _HealthEntry(snapshot, samples, successful_checks)
            self._routes[route_id] = replace(route, health_status=result.status, updated_at=now)
        return snapshot

    def route_health(self, route_id: str) -> RouteHealth:
        """读取最近一次健康快照。"""
        with self._lock:
            if route_id in self._deleted_route_ids:
                raise RouteHealthNotFound(route_id)
            try:
                return self._health[route_id].snapshot
            except KeyError as exc:
                raise RouteHealthNotFound(route_id) from exc

    def set_policy(self, command: RoutingPolicyUpdate) -> RoutingPolicy:
        """保存自动模式或一个同规格的管理员优先来源。"""
        logical_model = required(command.logical_model, "逻辑模型")
        output_spec = required(command.output_spec, "成品规格")
        preferred_route_id = command.preferred_route_id.strip()
        with self._lock:
            if command.mode is RoutingMode.PREFERRED:
                route = self._routes.get(preferred_route_id)
                if (
                    route is None
                    or preferred_route_id in self._deleted_route_ids
                    or route.logical_model != logical_model
                    or route.output_spec != output_spec
                ):
                    raise InvalidRoutingPolicy("管理员指定路由必须属于相同逻辑模型和成品规格")
            else:
                preferred_route_id = ""
            policy = RoutingPolicy(
                logical_model=logical_model,
                output_spec=output_spec,
                mode=command.mode,
                preferred_route_id=preferred_route_id,
                updated_at=self._clock(),
            )
            self._policies[(logical_model, output_spec)] = policy
            return policy

    def routing_policy(self, logical_model: str, output_spec: str) -> RoutingPolicy:
        """读取显式策略；未配置时使用自动模式。"""
        with self._lock:
            return self._policies.get(
                (logical_model, output_spec),
                RoutingPolicy(logical_model, output_spec, RoutingMode.AUTOMATIC, "", self._clock()),
            )

    def availability(self, logical_model: str, output_spec: str) -> ModelAvailability:
        """只读判断当前是否存在符合选路条件的模型路由。"""
        with self._lock:
            status = (
                ModelAvailabilityStatus.AVAILABLE
                if self._eligible_candidates(logical_model, output_spec)
                else ModelAvailabilityStatus.MAINTENANCE
            )
        return ModelAvailability(logical_model, output_spec, status)

    def select(self, logical_model: str, output_spec: str) -> RouteSelection:
        """优先可用性和成功率，并在相同健康水平下选择更低延时路由。"""
        with self._lock:
            candidates = self._eligible_candidates(logical_model, output_spec)
            if not candidates:
                raise NoAvailableModelRoute(f"{logical_model}/{output_spec}")
            candidates.sort(
                key=lambda candidate: (
                    -candidate[1].success_rate,
                    candidate[1].status is RouteHealthStatus.DEGRADED,
                    candidate[1].ewma_latency_ms,
                    candidate[1].p95_latency_ms,
                    candidate[0].priority,
                    candidate[0].route_id,
                )
            )
            policy = self.routing_policy(logical_model, output_spec)
            selected = candidates[0][0]
            reason = "automatic"
            if policy.mode is RoutingMode.PREFERRED:
                preferred = next(
                    (route for route, _ in candidates if route.route_id == policy.preferred_route_id),
                    None,
                )
                if preferred is not None:
                    selected = preferred
                    reason = "preferred"
                else:
                    reason = "preferred_fallback"
            return RouteSelection(
                logical_model=logical_model,
                output_spec=output_spec,
                route_id=selected.route_id,
                provider_id=selected.provider_id,
                provider_model_name=selected.provider_model_name,
                compatibility_group=selected.compatibility_group,
                selection_reason=reason,
                selected_at=self._clock(),
            )

    def _eligible_candidates(self, logical_model: str, output_spec: str) -> list[tuple[ImageModelRoute, RouteHealth]]:
        candidates: list[tuple[ImageModelRoute, RouteHealth]] = []
        for route in self._routes.values():
            if route.route_id in self._deleted_route_ids or route.provider_id in self._deleted_provider_ids:
                continue
            provider = self._providers[route.provider_id].provider
            health_entry = self._health.get(route.route_id)
            if (
                route.logical_model == logical_model
                and route.output_spec == output_spec
                and route.enabled
                and provider.enabled
                and health_entry is not None
                and health_entry.snapshot.available
            ):
                candidates.append((route, health_entry.snapshot))
        return candidates


def _valid_max_concurrency(value: int) -> int:
    if not 1 <= value <= 1000:
        raise InvalidProviderConfiguration("上游账户并发数必须在 1 到 1000 之间")
    return value


def _valid_request_timeout(value: int) -> int:
    if not 60 <= value <= 1800:
        raise InvalidProviderConfiguration("图片请求超时必须在 60 到 1800 秒之间")
    return value

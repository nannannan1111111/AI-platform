"""模型路由 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.model_routing._generation_targets import ProviderGenerationTarget, ProviderGenerationTargetNotFound
from app.model_routing._validation import normalized_base_url, required, valid_reference_image_limit
from app.model_routing.models import (
    ApiProvider,
    ApiProviderNotFound,
    ImageModelRoute,
    ImageResponseMode,
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
    ProviderProtocol,
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

_metadata = MetaData()
_api_providers = Table(
    "api_providers",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("code", String(64), nullable=False, unique=True),
    Column("display_name", String(128), nullable=False),
    Column("protocol", String(64), nullable=False),
    Column("base_url", String(1024), nullable=False),
    Column("image_response_mode", String(32), nullable=False),
    Column("concurrency_group", String(128), nullable=False),
    Column("max_concurrency", Integer, nullable=False),
    Column("request_timeout_seconds", Integer, nullable=False),
    Column("secret_ref", String(1024), nullable=False),
    Column("key_fingerprint", String(16), nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
)
_image_model_routes = Table(
    "image_model_routes",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("provider_id", String(36), ForeignKey("api_providers.id"), nullable=False),
    Column("logical_model", String(128), nullable=False),
    Column("output_spec", String(128), nullable=False),
    Column("provider_model_name", String(255), nullable=False),
    Column("compatibility_group", String(128), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("health_status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
)
_image_model_settings = Table(
    "image_model_settings",
    _metadata,
    Column("logical_model", String(128), primary_key=True),
    Column("output_spec", String(128), primary_key=True),
    Column("max_reference_images", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index(
    "uq_active_image_model_routes_mapping",
    _image_model_routes.c.provider_id,
    _image_model_routes.c.logical_model,
    _image_model_routes.c.output_spec,
    _image_model_routes.c.provider_model_name,
    unique=True,
    postgresql_where=_image_model_routes.c.deleted_at.is_(None),
    sqlite_where=_image_model_routes.c.deleted_at.is_(None),
)
_route_health_snapshots = Table(
    "route_health_snapshots",
    _metadata,
    Column("route_id", String(36), ForeignKey("image_model_routes.id"), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("total_latency_ms", Integer, nullable=False),
    Column("ewma_latency_ms", Integer, nullable=False),
    Column("p95_latency_ms", Integer, nullable=False),
    Column("successful_checks", Integer, nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("latency_samples", JSON, nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False),
    Column("error_code", String(64), nullable=False),
)
_image_routing_policies = Table(
    "image_routing_policies",
    _metadata,
    Column("logical_model", String(128), primary_key=True),
    Column("output_spec", String(128), primary_key=True),
    Column("mode", String(32), nullable=False),
    Column("preferred_route_id", String(36), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyModelRouting:
    """在关系数据库中持久化管理员统一配置的来源与模型路由。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        secrets: ProviderSecrets,
        *,
        probe: RouteProbe | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets
        self._probe = probe
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        secrets: ProviderSecrets,
        *,
        probe: RouteProbe | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyModelRouting:
        """为已经由 Alembic 初始化的数据库创建 Adapter。"""
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            secrets,
            probe=probe,
            id_factory=id_factory,
            clock=clock,
        )

    def create_provider(self, command: ProviderCreation) -> ApiProvider:
        """把来源元数据持久化，数据库只保存独立密钥 Adapter 的引用。"""
        provider_id = self._id_factory()
        stored = self._secrets.store(provider_id, command.api_key)
        now = self._clock()
        provider = ApiProvider(
            provider_id=provider_id,
            code=required(command.code, "来源代码"),
            display_name=required(command.display_name, "来源名称"),
            protocol=command.protocol,
            base_url=normalized_base_url(command.base_url),
            image_response_mode=command.image_response_mode,
            concurrency_group=required(command.concurrency_group or command.code, "上游并发账户组"),
            max_concurrency=_valid_max_concurrency(command.max_concurrency),
            request_timeout_seconds=_valid_request_timeout(command.request_timeout_seconds),
            credential_configured=True,
            key_fingerprint=stored.key_fingerprint,
            enabled=False,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session_factory.begin() as database:
                database.execute(
                    insert(_api_providers).values(
                        id=provider.provider_id,
                        code=provider.code,
                        display_name=provider.display_name,
                        protocol=provider.protocol.value,
                        base_url=provider.base_url,
                        image_response_mode=provider.image_response_mode.value,
                        concurrency_group=provider.concurrency_group,
                        max_concurrency=provider.max_concurrency,
                        request_timeout_seconds=provider.request_timeout_seconds,
                        secret_ref=stored.secret_ref,
                        key_fingerprint=provider.key_fingerprint,
                        enabled=provider.enabled,
                        created_at=provider.created_at,
                        updated_at=provider.updated_at,
                    )
                )
        except IntegrityError as exc:
            raise ProviderCodeConflict(provider.code) from exc
        return provider

    def resolve(self, route_id: str) -> ProviderGenerationTarget:
        """解析已固化路由当前使用的敏感 Provider 执行配置。"""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(
                        _api_providers.c.protocol,
                        _api_providers.c.base_url,
                        _api_providers.c.secret_ref,
                        _api_providers.c.image_response_mode,
                        _api_providers.c.request_timeout_seconds,
                        _image_model_routes.c.provider_model_name,
                    )
                    .select_from(
                        _image_model_routes.join(
                            _api_providers,
                            _api_providers.c.id == _image_model_routes.c.provider_id,
                        )
                    )
                    .where(_image_model_routes.c.id == route_id)
                    .where(
                        _image_model_routes.c.deleted_at.is_(None),
                        _api_providers.c.deleted_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ProviderGenerationTargetNotFound
        try:
            return ProviderGenerationTarget(
                protocol=ProviderProtocol(str(row["protocol"])),
                base_url=str(row["base_url"]),
                api_key=self._secrets.read(str(row["secret_ref"])),
                provider_model_name=str(row["provider_model_name"]),
                image_response_mode=ImageResponseMode(str(row["image_response_mode"])),
                request_timeout_seconds=int(row["request_timeout_seconds"]),
            )
        except (KeyError, ValueError) as exc:
            raise ProviderGenerationTargetNotFound from exc

    def list_providers(self) -> tuple[ApiProvider, ...]:
        """读取来源公开字段，永不选择密钥引用列。"""
        public_columns = (
            _api_providers.c.id,
            _api_providers.c.code,
            _api_providers.c.display_name,
            _api_providers.c.protocol,
            _api_providers.c.base_url,
            _api_providers.c.image_response_mode,
            _api_providers.c.concurrency_group,
            _api_providers.c.max_concurrency,
            _api_providers.c.request_timeout_seconds,
            _api_providers.c.key_fingerprint,
            _api_providers.c.enabled,
            _api_providers.c.created_at,
            _api_providers.c.updated_at,
        )
        with self._session_factory() as database:
            rows = database.execute(
                select(*public_columns)
                .where(_api_providers.c.deleted_at.is_(None))
                .order_by(_api_providers.c.created_at, _api_providers.c.id)
            ).mappings()
            return tuple(_provider_from_row(row) for row in rows)

    def update_provider(self, command: ProviderUpdate) -> ApiProvider:
        """更新来源元数据，轮换凭据时数据库仍只保存新引用。"""
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_api_providers)
                    .where(
                        _api_providers.c.id == command.provider_id,
                        _api_providers.c.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ApiProviderNotFound(command.provider_id)
            secret_ref = str(row["secret_ref"])
            key_fingerprint = str(row["key_fingerprint"])
            base_url = str(row["base_url"]) if command.base_url is None else normalized_base_url(command.base_url)
            connection_changed = command.api_key is not None or base_url != str(row["base_url"])
            if command.api_key is not None:
                stored = self._secrets.store(command.provider_id, command.api_key)
                secret_ref = stored.secret_ref
                key_fingerprint = stored.key_fingerprint
            now = self._clock()
            values = {
                "display_name": (
                    str(row["display_name"])
                    if command.display_name is None
                    else required(command.display_name, "来源名称")
                ),
                "base_url": base_url,
                "image_response_mode": (
                    str(row["image_response_mode"])
                    if command.image_response_mode is None
                    else command.image_response_mode.value
                ),
                "concurrency_group": (
                    str(row["concurrency_group"])
                    if command.concurrency_group is None
                    else required(command.concurrency_group, "上游并发账户组")
                ),
                "max_concurrency": (
                    int(row["max_concurrency"])
                    if command.max_concurrency is None
                    else _valid_max_concurrency(command.max_concurrency)
                ),
                "request_timeout_seconds": (
                    int(row["request_timeout_seconds"])
                    if command.request_timeout_seconds is None
                    else _valid_request_timeout(command.request_timeout_seconds)
                ),
                "secret_ref": secret_ref,
                "key_fingerprint": key_fingerprint,
                "enabled": bool(row["enabled"]) if command.enabled is None else command.enabled,
                "updated_at": now,
            }
            database.execute(update(_api_providers).where(_api_providers.c.id == command.provider_id).values(**values))
            if connection_changed:
                route_ids = select(_image_model_routes.c.id).where(
                    _image_model_routes.c.provider_id == command.provider_id,
                    _image_model_routes.c.deleted_at.is_(None),
                )
                database.execute(
                    update(_image_model_routes)
                    .where(
                        _image_model_routes.c.provider_id == command.provider_id,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                    .values(enabled=False, health_status=RouteHealthStatus.UNKNOWN.value, updated_at=now)
                )
                database.execute(
                    delete(_route_health_snapshots).where(_route_health_snapshots.c.route_id.in_(route_ids))
                )
            public_row = {**row, **values}
        return _provider_from_row(public_row)

    def delete_provider(self, provider_id: str) -> None:
        """退役没有活动路由的来源并幂等清理其凭据。"""
        with self._session_factory.begin() as database:
            row = (
                database.execute(select(_api_providers).where(_api_providers.c.id == provider_id).with_for_update())
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ApiProviderNotFound(provider_id)
            if row["deleted_at"] is None:
                active_route = database.scalar(
                    select(_image_model_routes.c.id).where(
                        _image_model_routes.c.provider_id == provider_id,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                )
                if active_route is not None:
                    raise ProviderHasRoutes("必须先删除该来源的全部模型路由")
                now = self._clock()
                database.execute(
                    update(_api_providers)
                    .where(_api_providers.c.id == provider_id)
                    .values(enabled=False, updated_at=now, deleted_at=now)
                )
            secret_ref = str(row["secret_ref"])
        self._secrets.delete(secret_ref)

    def create_route(self, command: ModelRouteCreation) -> ImageModelRoute:
        """持久化默认禁用且健康未知的兼容来源路由。"""
        if not 0 <= command.priority <= 10_000:
            raise InvalidModelRoute("路由优先级超出范围")
        reference_image_limit = valid_reference_image_limit(command.max_reference_images)
        now = self._clock()
        route = ImageModelRoute(
            route_id=self._id_factory(),
            provider_id=command.provider_id,
            logical_model=required(command.logical_model, "逻辑模型"),
            output_spec=required(command.output_spec, "成品规格"),
            provider_model_name=required(command.provider_model_name, "上游模型名称"),
            compatibility_group=required(command.compatibility_group, "兼容组"),
            priority=command.priority,
            enabled=False,
            health_status=RouteHealthStatus.UNKNOWN,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session_factory.begin() as database:
                provider_exists = database.scalar(
                    select(_api_providers.c.id).where(
                        _api_providers.c.id == command.provider_id,
                        _api_providers.c.deleted_at.is_(None),
                    )
                )
                if provider_exists is None:
                    raise ApiProviderNotFound(command.provider_id)
                incompatible = database.scalar(
                    select(_image_model_routes.c.id).where(
                        _image_model_routes.c.logical_model == route.logical_model,
                        _image_model_routes.c.output_spec == route.output_spec,
                        _image_model_routes.c.compatibility_group != route.compatibility_group,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                )
                if incompatible is not None:
                    raise InvalidModelRoute("同一逻辑模型规格的来源路由必须属于同一兼容组")
                database.execute(
                    insert(_image_model_routes).values(
                        id=route.route_id,
                        provider_id=route.provider_id,
                        logical_model=route.logical_model,
                        output_spec=route.output_spec,
                        provider_model_name=route.provider_model_name,
                        compatibility_group=route.compatibility_group,
                        priority=route.priority,
                        enabled=route.enabled,
                        health_status=route.health_status.value,
                        created_at=route.created_at,
                        updated_at=route.updated_at,
                    )
                )
                _store_reference_image_limit(
                    database,
                    route.logical_model,
                    route.output_spec,
                    reference_image_limit,
                    at=now,
                )
        except IntegrityError as exc:
            raise ModelRouteConflict(command.provider_id) from exc
        return route

    def list_routes(self, logical_model: str = "", output_spec: str = "") -> tuple[ImageModelRoute, ...]:
        """按创建顺序读取模型路由，可按逻辑模型和规格过滤。"""
        query = select(_image_model_routes)
        if logical_model:
            query = query.where(_image_model_routes.c.logical_model == logical_model)
        if output_spec:
            query = query.where(_image_model_routes.c.output_spec == output_spec)
        query = query.where(_image_model_routes.c.deleted_at.is_(None))
        query = query.order_by(_image_model_routes.c.created_at, _image_model_routes.c.id)
        with self._session_factory() as database:
            rows = database.execute(query).mappings()
            return tuple(_route_from_row(row) for row in rows)

    def reference_image_limit(self, logical_model: str, output_spec: str) -> int:
        """Read the model-level reference image limit, defaulting existing models to three."""
        with self._session_factory() as database:
            value = database.scalar(
                select(_image_model_settings.c.max_reference_images).where(
                    _image_model_settings.c.logical_model == logical_model,
                    _image_model_settings.c.output_spec == output_spec,
                )
            )
        return 3 if value is None else int(value)

    def update_route(self, command: ModelRouteUpdate) -> ImageModelRoute:
        """安全调整路由映射或优先级，并只允许最近检测可用的路由启用。"""
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_image_model_routes)
                    .where(
                        _image_model_routes.c.id == command.route_id,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ModelRouteNotFound(command.route_id)
            if command.priority is not None and not 0 <= command.priority <= 10_000:
                raise InvalidModelRoute("路由优先级超出范围")
            reference_image_limit = (
                None
                if command.max_reference_images is None
                else valid_reference_image_limit(command.max_reference_images)
            )
            provider_model_name = (
                str(row["provider_model_name"])
                if command.provider_model_name is None
                else required(command.provider_model_name, "上游模型名称")
            )
            compatibility_group = (
                str(row["compatibility_group"])
                if command.compatibility_group is None
                else required(command.compatibility_group, "兼容组")
            )
            mapping_changed = provider_model_name != str(row["provider_model_name"]) or compatibility_group != str(
                row["compatibility_group"]
            )
            if mapping_changed and (bool(row["enabled"]) or command.enabled):
                raise InvalidModelRoute("修改模型映射前必须停用路由")
            incompatible = database.scalar(
                select(_image_model_routes.c.id).where(
                    _image_model_routes.c.id != command.route_id,
                    _image_model_routes.c.logical_model == row["logical_model"],
                    _image_model_routes.c.output_spec == row["output_spec"],
                    _image_model_routes.c.compatibility_group != compatibility_group,
                    _image_model_routes.c.deleted_at.is_(None),
                )
            )
            if incompatible is not None:
                raise InvalidModelRoute("同一逻辑模型规格的来源路由必须属于同一兼容组")
            conflict = database.scalar(
                select(_image_model_routes.c.id).where(
                    _image_model_routes.c.id != command.route_id,
                    _image_model_routes.c.provider_id == row["provider_id"],
                    _image_model_routes.c.logical_model == row["logical_model"],
                    _image_model_routes.c.output_spec == row["output_spec"],
                    _image_model_routes.c.provider_model_name == provider_model_name,
                    _image_model_routes.c.deleted_at.is_(None),
                )
            )
            if conflict is not None:
                raise ModelRouteConflict(str(row["provider_id"]))
            if command.enabled:
                health_row = (
                    database.execute(
                        select(_route_health_snapshots).where(_route_health_snapshots.c.route_id == command.route_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if health_row is None:
                    raise InvalidModelRoute("模型路由必须通过健康检测后才能启用")
                if RouteHealthStatus(str(health_row["status"])) not in {
                    RouteHealthStatus.HEALTHY,
                    RouteHealthStatus.DEGRADED,
                }:
                    raise InvalidModelRoute("模型路由必须通过健康检测后才能启用")
            updated_at = self._clock()
            values = {
                "provider_model_name": provider_model_name,
                "compatibility_group": compatibility_group,
                "priority": int(row["priority"]) if command.priority is None else command.priority,
                "enabled": bool(row["enabled"]) if command.enabled is None else command.enabled,
                "health_status": (RouteHealthStatus.UNKNOWN.value if mapping_changed else str(row["health_status"])),
                "updated_at": updated_at,
            }
            database.execute(
                update(_image_model_routes).where(_image_model_routes.c.id == command.route_id).values(**values)
            )
            if reference_image_limit is not None:
                _store_reference_image_limit(
                    database,
                    str(row["logical_model"]),
                    str(row["output_spec"]),
                    reference_image_limit,
                    at=updated_at,
                )
            if mapping_changed:
                database.execute(
                    delete(_route_health_snapshots).where(_route_health_snapshots.c.route_id == command.route_id)
                )
            updated_row = {**row, **values}
        return _route_from_row(updated_row)

    def delete_route(self, route_id: str) -> None:
        """持久化路由退役墓碑并把指定优先策略恢复为自动模式。"""
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_image_model_routes).where(_image_model_routes.c.id == route_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ModelRouteNotFound(route_id)
            if row["deleted_at"] is not None:
                return
            now = self._clock()
            database.execute(
                update(_image_model_routes)
                .where(_image_model_routes.c.id == route_id)
                .values(
                    enabled=False,
                    health_status=RouteHealthStatus.UNKNOWN.value,
                    updated_at=now,
                    deleted_at=now,
                )
            )
            database.execute(delete(_route_health_snapshots).where(_route_health_snapshots.c.route_id == route_id))
            database.execute(
                update(_image_routing_policies)
                .where(_image_routing_policies.c.preferred_route_id == route_id)
                .values(mode=RoutingMode.AUTOMATIC.value, preferred_route_id="", updated_at=now)
            )

    def check_route(self, route_id: str) -> RouteHealth:
        """探测来源路由并原子持久化最近二十次检测的滚动指标。"""
        if self._probe is None:
            raise RouteProbeUnavailable(route_id)
        with self._session_factory() as database:
            target_row = (
                database.execute(
                    select(
                        _image_model_routes.c.provider_model_name,
                        _api_providers.c.base_url,
                        _api_providers.c.secret_ref,
                    )
                    .select_from(
                        _image_model_routes.join(
                            _api_providers,
                            _image_model_routes.c.provider_id == _api_providers.c.id,
                        )
                    )
                    .where(
                        _image_model_routes.c.id == route_id,
                        _image_model_routes.c.deleted_at.is_(None),
                        _api_providers.c.deleted_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if target_row is None:
            raise ModelRouteNotFound(route_id)
        result = self._probe.probe(
            RouteProbeTarget(
                base_url=str(target_row["base_url"]),
                api_key=self._secrets.read(str(target_row["secret_ref"])),
                provider_model_name=str(target_row["provider_model_name"]),
            )
        )
        now = self._clock()
        with self._session_factory.begin() as database:
            previous = (
                database.execute(
                    select(_route_health_snapshots)
                    .where(_route_health_snapshots.c.route_id == route_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            previous_samples = () if previous is None else tuple(int(value) for value in previous["latency_samples"])
            samples = (*previous_samples, result.total_latency_ms)[-20:]
            successful_checks = (0 if previous is None else int(previous["successful_checks"])) + int(
                result.status in {RouteHealthStatus.HEALTHY, RouteHealthStatus.DEGRADED}
            )
            sample_count = (0 if previous is None else int(previous["sample_count"])) + 1
            previous_ewma = result.total_latency_ms if previous is None else int(previous["ewma_latency_ms"])
            ewma = round(previous_ewma * 0.7 + result.total_latency_ms * 0.3)
            ordered = sorted(samples)
            p95 = ordered[max(ceil(len(ordered) * 0.95) - 1, 0)]
            values = {
                "route_id": route_id,
                "status": result.status.value,
                "total_latency_ms": result.total_latency_ms,
                "ewma_latency_ms": ewma,
                "p95_latency_ms": p95,
                "successful_checks": successful_checks,
                "sample_count": sample_count,
                "latency_samples": list(samples),
                "checked_at": now,
                "error_code": result.error_code,
            }
            if previous is None:
                database.execute(insert(_route_health_snapshots).values(**values))
            else:
                database.execute(
                    update(_route_health_snapshots)
                    .where(_route_health_snapshots.c.route_id == route_id)
                    .values(**values)
                )
            database.execute(
                update(_image_model_routes)
                .where(_image_model_routes.c.id == route_id)
                .values(health_status=result.status.value, updated_at=now)
            )
        return RouteHealth(
            route_id=route_id,
            status=result.status,
            available=result.status in {RouteHealthStatus.HEALTHY, RouteHealthStatus.DEGRADED},
            total_latency_ms=result.total_latency_ms,
            ewma_latency_ms=ewma,
            p95_latency_ms=p95,
            success_rate=round(successful_checks / sample_count, 4),
            sample_count=sample_count,
            checked_at=now,
            error_code=result.error_code,
        )

    def route_health(self, route_id: str) -> RouteHealth:
        """读取已持久化的路由滚动健康快照。"""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_route_health_snapshots)
                    .join(_image_model_routes, _route_health_snapshots.c.route_id == _image_model_routes.c.id)
                    .where(
                        _route_health_snapshots.c.route_id == route_id,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise RouteHealthNotFound(route_id)
        return _health_from_row(row)

    def set_policy(self, command: RoutingPolicyUpdate) -> RoutingPolicy:
        """持久化自动模式或一个同规格的管理员优先路由。"""
        logical_model = required(command.logical_model, "逻辑模型")
        output_spec = required(command.output_spec, "成品规格")
        preferred_route_id = command.preferred_route_id.strip()
        now = self._clock()
        with self._session_factory.begin() as database:
            if command.mode is RoutingMode.PREFERRED:
                preferred = database.execute(
                    select(_image_model_routes.c.logical_model, _image_model_routes.c.output_spec).where(
                        _image_model_routes.c.id == preferred_route_id,
                        _image_model_routes.c.deleted_at.is_(None),
                    )
                ).one_or_none()
                if preferred is None or tuple(preferred) != (logical_model, output_spec):
                    raise InvalidRoutingPolicy("管理员指定路由必须属于相同逻辑模型和成品规格")
            else:
                preferred_route_id = ""
            existing = database.scalar(
                select(_image_routing_policies.c.logical_model).where(
                    _image_routing_policies.c.logical_model == logical_model,
                    _image_routing_policies.c.output_spec == output_spec,
                )
            )
            values = {
                "logical_model": logical_model,
                "output_spec": output_spec,
                "mode": command.mode.value,
                "preferred_route_id": preferred_route_id,
                "updated_at": now,
            }
            if existing is None:
                database.execute(insert(_image_routing_policies).values(**values))
            else:
                database.execute(
                    update(_image_routing_policies)
                    .where(
                        _image_routing_policies.c.logical_model == logical_model,
                        _image_routing_policies.c.output_spec == output_spec,
                    )
                    .values(**values)
                )
        return RoutingPolicy(logical_model, output_spec, command.mode, preferred_route_id, now)

    def routing_policy(self, logical_model: str, output_spec: str) -> RoutingPolicy:
        """读取显式策略；不存在时返回自动模式。"""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_image_routing_policies).where(
                        _image_routing_policies.c.logical_model == logical_model,
                        _image_routing_policies.c.output_spec == output_spec,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return RoutingPolicy(logical_model, output_spec, RoutingMode.AUTOMATIC, "", self._clock())
        return _policy_from_row(row)

    def availability(self, logical_model: str, output_spec: str) -> ModelAvailability:
        """只读判断当前是否存在符合选路条件的持久化模型路由。"""
        status = (
            ModelAvailabilityStatus.AVAILABLE
            if self._eligible_candidates(logical_model, output_spec)
            else ModelAvailabilityStatus.MAINTENANCE
        )
        return ModelAvailability(logical_model, output_spec, status)

    def select(self, logical_model: str, output_spec: str) -> RouteSelection:
        """从持久化健康快照中选择成功率优先、延时较低的可用路由。"""
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
        with self._session_factory() as database:
            rows = database.execute(
                select(
                    _image_model_routes,
                    _route_health_snapshots,
                    _api_providers.c.enabled.label("provider_enabled"),
                )
                .select_from(
                    _image_model_routes.join(
                        _api_providers,
                        _image_model_routes.c.provider_id == _api_providers.c.id,
                    ).join(
                        _route_health_snapshots,
                        _image_model_routes.c.id == _route_health_snapshots.c.route_id,
                    )
                )
                .where(
                    _image_model_routes.c.logical_model == logical_model,
                    _image_model_routes.c.output_spec == output_spec,
                    _image_model_routes.c.enabled.is_(True),
                    _api_providers.c.enabled.is_(True),
                    _image_model_routes.c.deleted_at.is_(None),
                    _api_providers.c.deleted_at.is_(None),
                )
            ).mappings()
            candidates: list[tuple[ImageModelRoute, RouteHealth]] = []
            for row in rows:
                route = _route_from_row(row)
                health = _health_from_row(row)
                if health.available:
                    candidates.append((route, health))
        return candidates


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("数据库模型路由日期时间类型无效")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _store_reference_image_limit(
    database: Session,
    logical_model: str,
    output_spec: str,
    value: int,
    *,
    at: datetime,
) -> None:
    existing = database.scalar(
        select(_image_model_settings.c.logical_model).where(
            _image_model_settings.c.logical_model == logical_model,
            _image_model_settings.c.output_spec == output_spec,
        )
    )
    if existing is None:
        database.execute(
            insert(_image_model_settings).values(
                logical_model=logical_model,
                output_spec=output_spec,
                max_reference_images=value,
                created_at=at,
                updated_at=at,
            )
        )
        return
    database.execute(
        update(_image_model_settings)
        .where(
            _image_model_settings.c.logical_model == logical_model,
            _image_model_settings.c.output_spec == output_spec,
        )
        .values(max_reference_images=value, updated_at=at)
    )


def _provider_from_row(row: RowMapping | Mapping[str, Any]) -> ApiProvider:
    return ApiProvider(
        provider_id=str(row["id"]),
        code=str(row["code"]),
        display_name=str(row["display_name"]),
        protocol=ProviderProtocol(str(row["protocol"])),
        base_url=str(row["base_url"]),
        image_response_mode=ImageResponseMode(str(row["image_response_mode"])),
        concurrency_group=str(row["concurrency_group"]),
        max_concurrency=int(row["max_concurrency"]),
        request_timeout_seconds=int(row["request_timeout_seconds"]),
        credential_configured=True,
        key_fingerprint=str(row["key_fingerprint"]),
        enabled=bool(row["enabled"]),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )


def _route_from_row(row: RowMapping | Mapping[str, Any]) -> ImageModelRoute:
    return ImageModelRoute(
        route_id=str(row["id"]),
        provider_id=str(row["provider_id"]),
        logical_model=str(row["logical_model"]),
        output_spec=str(row["output_spec"]),
        provider_model_name=str(row["provider_model_name"]),
        compatibility_group=str(row["compatibility_group"]),
        priority=int(row["priority"]),
        enabled=bool(row["enabled"]),
        health_status=RouteHealthStatus(str(row["health_status"])),
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
    )


def _health_from_row(row: RowMapping | Mapping[str, Any]) -> RouteHealth:
    status = RouteHealthStatus(str(row["status"]))
    successful_checks = int(row["successful_checks"])
    sample_count = int(row["sample_count"])
    return RouteHealth(
        route_id=str(row["route_id"]),
        status=status,
        available=status in {RouteHealthStatus.HEALTHY, RouteHealthStatus.DEGRADED},
        total_latency_ms=int(row["total_latency_ms"]),
        ewma_latency_ms=int(row["ewma_latency_ms"]),
        p95_latency_ms=int(row["p95_latency_ms"]),
        success_rate=round(successful_checks / sample_count, 4),
        sample_count=sample_count,
        checked_at=_aware(row["checked_at"]),
        error_code=str(row["error_code"]),
    )


def _policy_from_row(row: RowMapping | Mapping[str, Any]) -> RoutingPolicy:
    return RoutingPolicy(
        logical_model=str(row["logical_model"]),
        output_spec=str(row["output_spec"]),
        mode=RoutingMode(str(row["mode"])),
        preferred_route_id=str(row["preferred_route_id"]),
        updated_at=_aware(row["updated_at"]),
    )


def _valid_max_concurrency(value: int) -> int:
    if not 1 <= value <= 1000:
        raise InvalidProviderConfiguration("上游账户并发数必须在 1 到 1000 之间")
    return value


def _valid_request_timeout(value: int) -> int:
    if not 60 <= value <= 1800:
        raise InvalidProviderConfiguration("图片请求超时必须在 60 到 1800 秒之间")
    return value

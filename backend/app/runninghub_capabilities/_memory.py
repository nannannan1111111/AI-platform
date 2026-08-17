"""RunningHub 能力目录 Interface 的内存 Adapter。"""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from app.credits._amounts import credit_units, format_credits
from app.credits._validation import validated_effective_time
from app.runninghub_capabilities._validation import availability, input_capabilities, required, schema_inputs
from app.runninghub_capabilities.models import (
    InvalidRunningHubCapability,
    PublicRunningHubCapability,
    PublicRunningHubInputSchema,
    RunningHubCapability,
    RunningHubCapabilityNotFound,
    RunningHubCapabilityPublication,
    RunningHubCapabilityUpdate,
    RunningHubInputCapability,
    RunningHubInputSchemaPublication,
    RunningHubInputSchemaVersion,
    RunningHubUserPriceConflict,
    RunningHubUserPriceNotFound,
    RunningHubUserPricePublication,
    RunningHubUserPriceVersion,
)


class InMemoryRunningHubCapabilities:
    """在单进程内保存管理员发布的 RunningHub 能力。"""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capabilities: list[RunningHubCapability] = []
        self._input_schema_versions: dict[str, list[RunningHubInputSchemaVersion]] = {}
        self._user_price_versions: dict[str, list[RunningHubUserPriceVersion]] = {}
        self._lock = Lock()

    def publish(self, command: RunningHubCapabilityPublication) -> RunningHubCapability:
        """发布一个稳定公开身份与内部工作流绑定分离的能力。"""
        published_at = self._clock()
        capability = RunningHubCapability(
            capability_id=required(self._id_factory(), "能力标识", maximum=36),
            name=required(command.name, "能力名称", maximum=120),
            workflow_id=required(command.workflow_id, "内部工作流标识", maximum=255),
            input_capabilities=input_capabilities(command.input_capabilities),
            available=availability(command.available),
            created_at=published_at,
            updated_at=published_at,
        )
        with self._lock:
            self._capabilities.append(capability)
        return capability

    def list_for_administration(self) -> tuple[RunningHubCapability, ...]:
        """按发布时间返回包含内部工作流绑定的管理员目录。"""
        with self._lock:
            return tuple(self._capabilities)

    def update(self, command: RunningHubCapabilityUpdate) -> RunningHubCapability:
        """原地替换能力快照，同时保持稳定公开身份和创建时间。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        updated_at = self._clock()
        with self._lock:
            for index, current in enumerate(self._capabilities):
                if current.capability_id != capability_id:
                    continue
                if command.input_capabilities is not None and self._input_schema_versions.get(capability_id):
                    raise InvalidRunningHubCapability("输入能力已经由当前 schema 派生")
                updated = RunningHubCapability(
                    capability_id=current.capability_id,
                    name=(current.name if command.name is None else required(command.name, "能力名称", maximum=120)),
                    workflow_id=(
                        current.workflow_id
                        if command.workflow_id is None
                        else required(command.workflow_id, "内部工作流标识", maximum=255)
                    ),
                    input_capabilities=(
                        current.input_capabilities
                        if command.input_capabilities is None
                        else input_capabilities(command.input_capabilities)
                    ),
                    available=(current.available if command.available is None else availability(command.available)),
                    created_at=current.created_at,
                    updated_at=updated_at,
                )
                self._capabilities[index] = updated
                return updated
        raise RunningHubCapabilityNotFound(capability_id)

    def publish_input_schema(self, command: RunningHubInputSchemaPublication) -> RunningHubInputSchemaVersion:
        """追加 schema 版本并同步当前粗粒度输入能力。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        inputs = schema_inputs(command.inputs)
        with self._lock:
            capability_index = next(
                (
                    index
                    for index, capability in enumerate(self._capabilities)
                    if capability.capability_id == capability_id
                ),
                None,
            )
            if capability_index is None:
                raise RunningHubCapabilityNotFound(capability_id)
            current = self._capabilities[capability_index]
            published_at = self._clock()
            versions = self._input_schema_versions.setdefault(capability_id, [])
            version = RunningHubInputSchemaVersion(
                schema_version_id=required(self._id_factory(), "schema 版本标识", maximum=36),
                capability_id=capability_id,
                version=len(versions) + 1,
                inputs=inputs,
                published_at=published_at,
            )
            versions.append(version)
            kinds = {item.kind for item in inputs}
            self._capabilities[capability_index] = RunningHubCapability(
                capability_id=current.capability_id,
                name=current.name,
                workflow_id=current.workflow_id,
                input_capabilities=tuple(kind for kind in RunningHubInputCapability if kind in kinds),
                available=current.available,
                created_at=current.created_at,
                updated_at=published_at,
            )
            return version

    def input_schema_versions(self, capability_id: str) -> tuple[RunningHubInputSchemaVersion, ...]:
        """按版本号返回指定能力的 schema 历史。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._lock:
            if not any(capability.capability_id == capability_id for capability in self._capabilities):
                raise RunningHubCapabilityNotFound(capability_id)
            return tuple(self._input_schema_versions.get(capability_id, ()))

    def publish_user_price(self, command: RunningHubUserPricePublication) -> RunningHubUserPriceVersion:
        """追加一个按时间生效的用户价格版本。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        published_at = self._clock()
        effective_from = validated_effective_time(command.effective_from, published_at)
        credits_per_run = format_credits(credit_units(command.credits_per_run))
        with self._lock:
            if not any(capability.capability_id == capability_id for capability in self._capabilities):
                raise RunningHubCapabilityNotFound(capability_id)
            versions = self._user_price_versions.setdefault(capability_id, [])
            if any(version.effective_from == effective_from for version in versions):
                raise RunningHubUserPriceConflict(capability_id)
            version = RunningHubUserPriceVersion(
                price_version_id=required(self._id_factory(), "价格版本标识", maximum=36),
                capability_id=capability_id,
                version=len(versions) + 1,
                credits_per_run=credits_per_run,
                effective_from=effective_from,
                published_at=published_at,
            )
            versions.append(version)
            return version

    def user_price_versions(self, capability_id: str) -> tuple[RunningHubUserPriceVersion, ...]:
        """按版本号返回指定能力的用户价格历史。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._lock:
            if not any(capability.capability_id == capability_id for capability in self._capabilities):
                raise RunningHubCapabilityNotFound(capability_id)
            return tuple(self._user_price_versions.get(capability_id, ()))

    def user_price_at(self, capability_id: str, at: datetime) -> RunningHubUserPriceVersion:
        """读取指定时刻最新生效的用户价格。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._lock:
            candidates = [
                version for version in self._user_price_versions.get(capability_id, ()) if version.effective_from <= at
            ]
        if not candidates:
            raise RunningHubUserPriceNotFound(capability_id)
        return max(candidates, key=lambda version: version.effective_from)

    def catalog(self) -> tuple[PublicRunningHubCapability, ...]:
        """投影用户可见字段，同时保留停用项。"""
        with self._lock:
            capabilities = tuple(self._capabilities)
            current_schemas = {
                capability_id: versions[-1]
                for capability_id, versions in self._input_schema_versions.items()
                if versions
            }
            price_versions = tuple(version for versions in self._user_price_versions.values() for version in versions)
        current_prices: dict[str, RunningHubUserPriceVersion] = {}
        if price_versions:
            at = self._clock()
            for version in price_versions:
                if version.effective_from > at:
                    continue
                selected = current_prices.get(version.capability_id)
                if selected is None or selected.effective_from < version.effective_from:
                    current_prices[version.capability_id] = version
        return tuple(
            PublicRunningHubCapability(
                capability_id=capability.capability_id,
                name=capability.name,
                input_capabilities=capability.input_capabilities,
                available=capability.available,
                input_schema=(
                    _public_input_schema(current_schemas[capability.capability_id])
                    if capability.capability_id in current_schemas
                    else None
                ),
                credits_per_run=(
                    current_prices[capability.capability_id].credits_per_run
                    if capability.capability_id in current_prices
                    else None
                ),
            )
            for capability in capabilities
        )


def _public_input_schema(version: RunningHubInputSchemaVersion) -> PublicRunningHubInputSchema:
    return PublicRunningHubInputSchema(
        schema_version_id=version.schema_version_id,
        version=version.version,
        inputs=version.inputs,
    )

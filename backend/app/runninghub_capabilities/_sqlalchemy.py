"""RunningHub 能力目录 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.credits._amounts import credit_units, format_credits
from app.credits._validation import validated_effective_time
from app.runninghub_capabilities._validation import availability, input_capabilities, required, schema_inputs
from app.runninghub_capabilities.models import (
    InvalidRunningHubCapability,
    PublicRunningHubCapability,
    PublicRunningHubInputSchema,
    RunningHubCapability,
    RunningHubCapabilityInput,
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

_metadata = MetaData()
_runninghub_capabilities = Table(
    "runninghub_capabilities",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("workflow_id", String(255), nullable=False),
    Column("input_capabilities", JSON, nullable=False),
    Column("available", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
_runninghub_input_schema_versions = Table(
    "runninghub_input_schema_versions",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("capability_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
)
_runninghub_user_price_versions = Table(
    "runninghub_user_price_versions",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("capability_id", String(36), nullable=False),
    Column("version", Integer, nullable=False),
    Column("credit_units", BigInteger, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyRunningHubCapabilities:
    """使用 SQL 事务持久化管理员发布的 RunningHub 能力。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyRunningHubCapabilities:
        """为已经由 Alembic 初始化的数据库创建 Adapter。"""
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False), id_factory=id_factory, clock=clock)

    def publish(self, command: RunningHubCapabilityPublication) -> RunningHubCapability:
        """新增一个公开身份与内部工作流绑定分离的能力。"""
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
        with self._session_factory.begin() as database:
            database.execute(
                insert(_runninghub_capabilities).values(
                    id=capability.capability_id,
                    name=capability.name,
                    workflow_id=capability.workflow_id,
                    input_capabilities=[value.value for value in capability.input_capabilities],
                    available=capability.available,
                    created_at=capability.created_at,
                    updated_at=capability.updated_at,
                )
            )
        return capability

    def list_for_administration(self) -> tuple[RunningHubCapability, ...]:
        """按发布时间返回包含内部工作流绑定的管理员目录。"""
        with self._session_factory() as database:
            rows = (
                database.execute(
                    select(_runninghub_capabilities).order_by(
                        _runninghub_capabilities.c.created_at,
                        _runninghub_capabilities.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_capability_from_row(row) for row in rows)

    def update(self, command: RunningHubCapabilityUpdate) -> RunningHubCapability:
        """锁定并替换能力快照，同时保持稳定公开身份和创建时间。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        updated_at = self._clock()
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_runninghub_capabilities)
                    .where(_runninghub_capabilities.c.id == capability_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RunningHubCapabilityNotFound(capability_id)
            current = _capability_from_row(row)
            if command.input_capabilities is not None:
                schema_exists = database.scalar(
                    select(_runninghub_input_schema_versions.c.id)
                    .where(_runninghub_input_schema_versions.c.capability_id == capability_id)
                    .limit(1)
                )
                if schema_exists is not None:
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
            database.execute(
                update(_runninghub_capabilities)
                .where(_runninghub_capabilities.c.id == capability_id)
                .values(
                    name=updated.name,
                    workflow_id=updated.workflow_id,
                    input_capabilities=[value.value for value in updated.input_capabilities],
                    available=updated.available,
                    updated_at=updated.updated_at,
                )
            )
        return updated

    def publish_input_schema(self, command: RunningHubInputSchemaPublication) -> RunningHubInputSchemaVersion:
        """在能力锁内追加 schema 版本并同步当前粗粒度输入能力。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        inputs = schema_inputs(command.inputs)
        published_at = self._clock()
        with self._session_factory.begin() as database:
            capability = database.execute(
                select(_runninghub_capabilities.c.id)
                .where(_runninghub_capabilities.c.id == capability_id)
                .with_for_update()
            ).one_or_none()
            if capability is None:
                raise RunningHubCapabilityNotFound(capability_id)
            current_version = database.scalar(
                select(func.max(_runninghub_input_schema_versions.c.version)).where(
                    _runninghub_input_schema_versions.c.capability_id == capability_id
                )
            )
            version = RunningHubInputSchemaVersion(
                schema_version_id=required(self._id_factory(), "schema 版本标识", maximum=36),
                capability_id=capability_id,
                version=1 if current_version is None else int(current_version) + 1,
                inputs=inputs,
                published_at=published_at,
            )
            database.execute(
                insert(_runninghub_input_schema_versions).values(
                    id=version.schema_version_id,
                    capability_id=version.capability_id,
                    version=version.version,
                    inputs=_inputs_to_json(version.inputs),
                    published_at=version.published_at,
                )
            )
            kinds = {item.kind for item in inputs}
            database.execute(
                update(_runninghub_capabilities)
                .where(_runninghub_capabilities.c.id == capability_id)
                .values(
                    input_capabilities=[kind.value for kind in RunningHubInputCapability if kind in kinds],
                    updated_at=published_at,
                )
            )
        return version

    def input_schema_versions(self, capability_id: str) -> tuple[RunningHubInputSchemaVersion, ...]:
        """按版本号返回指定能力的不可改写 schema 历史。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._session_factory() as database:
            capability = database.execute(
                select(_runninghub_capabilities.c.id).where(_runninghub_capabilities.c.id == capability_id)
            ).one_or_none()
            if capability is None:
                raise RunningHubCapabilityNotFound(capability_id)
            rows = (
                database.execute(
                    select(_runninghub_input_schema_versions)
                    .where(_runninghub_input_schema_versions.c.capability_id == capability_id)
                    .order_by(_runninghub_input_schema_versions.c.version)
                )
                .mappings()
                .all()
            )
        return tuple(_schema_from_row(row) for row in rows)

    def publish_user_price(self, command: RunningHubUserPricePublication) -> RunningHubUserPriceVersion:
        """在能力锁内追加一个按时间生效的用户价格版本。"""
        capability_id = required(command.capability_id, "能力标识", maximum=36)
        published_at = self._clock()
        effective_from = validated_effective_time(command.effective_from, published_at)
        credits_per_run = format_credits(credit_units(command.credits_per_run))
        try:
            with self._session_factory.begin() as database:
                capability = database.execute(
                    select(_runninghub_capabilities.c.id)
                    .where(_runninghub_capabilities.c.id == capability_id)
                    .with_for_update()
                ).one_or_none()
                if capability is None:
                    raise RunningHubCapabilityNotFound(capability_id)
                current_version = database.scalar(
                    select(func.max(_runninghub_user_price_versions.c.version)).where(
                        _runninghub_user_price_versions.c.capability_id == capability_id
                    )
                )
                version = RunningHubUserPriceVersion(
                    price_version_id=required(self._id_factory(), "价格版本标识", maximum=36),
                    capability_id=capability_id,
                    version=1 if current_version is None else int(current_version) + 1,
                    credits_per_run=credits_per_run,
                    effective_from=effective_from,
                    published_at=published_at,
                )
                database.execute(
                    insert(_runninghub_user_price_versions).values(
                        id=version.price_version_id,
                        capability_id=version.capability_id,
                        version=version.version,
                        credit_units=credit_units(version.credits_per_run),
                        effective_from=version.effective_from,
                        published_at=version.published_at,
                    )
                )
        except IntegrityError as exc:
            raise RunningHubUserPriceConflict(capability_id) from exc
        return version

    def user_price_versions(self, capability_id: str) -> tuple[RunningHubUserPriceVersion, ...]:
        """按版本号返回指定能力的用户价格历史。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._session_factory() as database:
            capability = database.execute(
                select(_runninghub_capabilities.c.id).where(_runninghub_capabilities.c.id == capability_id)
            ).one_or_none()
            if capability is None:
                raise RunningHubCapabilityNotFound(capability_id)
            rows = (
                database.execute(
                    select(_runninghub_user_price_versions)
                    .where(_runninghub_user_price_versions.c.capability_id == capability_id)
                    .order_by(_runninghub_user_price_versions.c.version)
                )
                .mappings()
                .all()
            )
        return tuple(_user_price_from_row(row) for row in rows)

    def user_price_at(self, capability_id: str, at: datetime) -> RunningHubUserPriceVersion:
        """读取指定时刻最新生效的用户价格。"""
        capability_id = required(capability_id, "能力标识", maximum=36)
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_runninghub_user_price_versions)
                    .where(
                        _runninghub_user_price_versions.c.capability_id == capability_id,
                        _runninghub_user_price_versions.c.effective_from <= at,
                    )
                    .order_by(_runninghub_user_price_versions.c.effective_from.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise RunningHubUserPriceNotFound(capability_id)
        return _user_price_from_row(row)

    def catalog(self) -> tuple[PublicRunningHubCapability, ...]:
        """返回不包含内部工作流绑定的用户目录。"""
        capabilities = self.list_for_administration()
        with self._session_factory() as database:
            schema_rows = (
                database.execute(
                    select(_runninghub_input_schema_versions).order_by(
                        _runninghub_input_schema_versions.c.capability_id,
                        _runninghub_input_schema_versions.c.version,
                    )
                )
                .mappings()
                .all()
            )
            price_rows = (
                database.execute(
                    select(_runninghub_user_price_versions).order_by(
                        _runninghub_user_price_versions.c.capability_id,
                        _runninghub_user_price_versions.c.effective_from,
                    )
                )
                .mappings()
                .all()
            )
        current_schemas: dict[str, RunningHubInputSchemaVersion] = {}
        for row in schema_rows:
            schema = _schema_from_row(row)
            current_schemas[schema.capability_id] = schema
        current_prices: dict[str, RunningHubUserPriceVersion] = {}
        if price_rows:
            at = self._clock()
            for row in price_rows:
                price = _user_price_from_row(row)
                if price.effective_from <= at:
                    current_prices[price.capability_id] = price
        return tuple(
            _public_capability(
                capability,
                current_schemas.get(capability.capability_id),
                current_prices.get(capability.capability_id),
            )
            for capability in capabilities
        )


def _capability_from_row(row: RowMapping) -> RunningHubCapability:
    created_at = _datetime(row["created_at"])
    updated_at = _datetime(row["updated_at"])
    raw_inputs = row["input_capabilities"]
    if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, str):
        raise RuntimeError("数据库 RunningHub 输入能力类型无效")
    try:
        inputs = input_capabilities(tuple(RunningHubInputCapability(str(value)) for value in raw_inputs))
    except ValueError as exc:
        raise RuntimeError("数据库 RunningHub 输入能力值无效") from exc
    return RunningHubCapability(
        capability_id=str(row["id"]),
        name=str(row["name"]),
        workflow_id=str(row["workflow_id"]),
        input_capabilities=inputs,
        available=bool(row["available"]),
        created_at=created_at,
        updated_at=updated_at,
    )


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("数据库 RunningHub 能力日期时间类型无效")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _schema_from_row(row: RowMapping) -> RunningHubInputSchemaVersion:
    raw_inputs = row["inputs"]
    if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, str):
        raise RuntimeError("数据库 RunningHub 输入 schema 类型无效")
    parsed: list[RunningHubCapabilityInput] = []
    try:
        for raw_input in raw_inputs:
            if not isinstance(raw_input, Mapping):
                raise RuntimeError("数据库 RunningHub 输入 schema 项无效")
            parsed.append(
                RunningHubCapabilityInput(
                    input_key=str(raw_input["input_key"]),
                    label=str(raw_input["label"]),
                    kind=RunningHubInputCapability(str(raw_input["kind"])),
                    required=raw_input["required"],
                )
            )
        inputs = schema_inputs(tuple(parsed))
    except (KeyError, ValueError) as exc:
        raise RuntimeError("数据库 RunningHub 输入 schema 值无效") from exc
    return RunningHubInputSchemaVersion(
        schema_version_id=str(row["id"]),
        capability_id=str(row["capability_id"]),
        version=int(row["version"]),
        inputs=inputs,
        published_at=_datetime(row["published_at"]),
    )


def _inputs_to_json(inputs: tuple[RunningHubCapabilityInput, ...]) -> list[dict[str, object]]:
    return [
        {
            "input_key": item.input_key,
            "label": item.label,
            "kind": item.kind.value,
            "required": item.required,
        }
        for item in inputs
    ]


def _user_price_from_row(row: RowMapping) -> RunningHubUserPriceVersion:
    return RunningHubUserPriceVersion(
        price_version_id=str(row["id"]),
        capability_id=str(row["capability_id"]),
        version=int(row["version"]),
        credits_per_run=format_credits(int(row["credit_units"])),
        effective_from=_datetime(row["effective_from"]),
        published_at=_datetime(row["published_at"]),
    )


def _public_capability(
    capability: RunningHubCapability,
    schema: RunningHubInputSchemaVersion | None = None,
    user_price: RunningHubUserPriceVersion | None = None,
) -> PublicRunningHubCapability:
    return PublicRunningHubCapability(
        capability_id=capability.capability_id,
        name=capability.name,
        input_capabilities=capability.input_capabilities,
        available=capability.available,
        input_schema=(
            PublicRunningHubInputSchema(
                schema_version_id=schema.schema_version_id,
                version=schema.version,
                inputs=schema.inputs,
            )
            if schema is not None
            else None
        ),
        credits_per_run=user_price.credits_per_run if user_price is not None else None,
    )

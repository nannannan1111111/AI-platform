"""模型价格 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.credits._amounts import credit_units, format_credits
from app.credits._validation import validated_effective_time
from app.credits.models import (
    InvalidModelReferenceLimit,
    ModelPriceConflict,
    ModelPriceVersion,
    UnknownModelPriceVersion,
)

_metadata = MetaData()
_model_price_versions = Table(
    "model_price_versions",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("logical_model", String(128), nullable=False),
    Column("output_spec", String(128), nullable=False),
    Column("credit_units", BigInteger, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    Column("max_reference_images", BigInteger, nullable=False),
)


def _validated_reference_limit(value: int) -> int:
    if isinstance(value, bool) or not 0 <= value <= 16:
        raise InvalidModelReferenceLimit(value)
    return value


class SqlAlchemyModelPrices:
    """使用 SQL 事务持久化模型价格版本。"""

    def __init__(self, session_factory: sessionmaker[Session], *, clock: Callable[[], datetime] | None = None) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> SqlAlchemyModelPrices:
        """为已经由 Alembic 初始化的数据库创建 Adapter。"""
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False), clock=clock)

    def publish(
        self,
        logical_model: str,
        output_spec: str,
        *,
        credits_per_result: str,
        effective_from: datetime,
        max_reference_images: int = 3,
    ) -> ModelPriceVersion:
        """新增模型价格版本，不改写已有版本。"""
        published_at = self._clock()
        version = ModelPriceVersion(
            version_id=str(uuid4()),
            logical_model=logical_model,
            output_spec=output_spec,
            credits_per_result=format_credits(credit_units(credits_per_result)),
            effective_from=validated_effective_time(effective_from, published_at),
            published_at=published_at,
            max_reference_images=_validated_reference_limit(max_reference_images),
        )
        try:
            with self._session_factory.begin() as database:
                database.execute(
                    insert(_model_price_versions).values(
                        id=version.version_id,
                        logical_model=version.logical_model,
                        output_spec=version.output_spec,
                        credit_units=credit_units(version.credits_per_result),
                        effective_from=version.effective_from,
                        published_at=version.published_at,
                        max_reference_images=version.max_reference_images,
                    )
                )
        except IntegrityError as exc:
            raise ModelPriceConflict(logical_model, output_spec) from exc
        return version

    def effective_at(self, logical_model: str, output_spec: str, at: datetime) -> ModelPriceVersion:
        """读取指定时间最新生效的模型价格。"""
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_model_price_versions)
                    .where(
                        _model_price_versions.c.logical_model == logical_model,
                        _model_price_versions.c.output_spec == output_spec,
                        _model_price_versions.c.effective_from <= at,
                        _model_price_versions.c.deleted_at.is_(None),
                    )
                    .order_by(_model_price_versions.c.effective_from.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UnknownModelPriceVersion(f"{logical_model}/{output_spec}")
        return _model_price_from_row(row)

    def catalog_at(self, at: datetime) -> tuple[ModelPriceVersion, ...]:
        """读取指定时刻每个模型规格的最新生效价格。"""
        with self._session_factory() as database:
            rows = database.execute(
                select(_model_price_versions)
                .where(_model_price_versions.c.effective_from <= at)
                .where(_model_price_versions.c.deleted_at.is_(None))
                .order_by(
                    _model_price_versions.c.logical_model,
                    _model_price_versions.c.output_spec,
                    _model_price_versions.c.effective_from,
                )
            ).mappings()
            current: dict[tuple[str, str], ModelPriceVersion] = {}
            for row in rows:
                version = _model_price_from_row(row)
                current[(version.logical_model, version.output_spec)] = version
        return tuple(current[key] for key in sorted(current))

    def get_version(self, version_id: str) -> ModelPriceVersion:
        """读取任意历史模型价格版本。"""
        with self._session_factory() as database:
            row = (
                database.execute(select(_model_price_versions).where(_model_price_versions.c.id == version_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UnknownModelPriceVersion(version_id)
        return _model_price_from_row(row)

    def delete(self, version_id: str, deleted_at: datetime) -> None:
        """退役一个逻辑模型规格的全部价格版本，历史任务仍可按 ID 读取。"""
        with self._session_factory.begin() as database:
            row = database.execute(
                select(_model_price_versions.c.logical_model, _model_price_versions.c.output_spec).where(
                    _model_price_versions.c.id == version_id,
                    _model_price_versions.c.deleted_at.is_(None),
                )
            ).one_or_none()
            if row is None:
                raise UnknownModelPriceVersion(version_id)
            database.execute(
                update(_model_price_versions).where(
                    _model_price_versions.c.logical_model == row.logical_model,
                    _model_price_versions.c.output_spec == row.output_spec,
                    _model_price_versions.c.deleted_at.is_(None),
                ).values(deleted_at=deleted_at)
            )


def _model_price_from_row(row: RowMapping) -> ModelPriceVersion:
    effective_from = row["effective_from"]
    published_at = row["published_at"]
    if not isinstance(effective_from, datetime) or not isinstance(published_at, datetime):
        raise RuntimeError("数据库模型价格日期时间类型无效")
    return ModelPriceVersion(
        version_id=str(row["id"]),
        logical_model=str(row["logical_model"]),
        output_spec=str(row["output_spec"]),
        credits_per_result=format_credits(int(row["credit_units"])),
        effective_from=effective_from.replace(tzinfo=UTC) if effective_from.tzinfo is None else effective_from,
        published_at=published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at,
        max_reference_images=int(row["max_reference_images"]),
    )

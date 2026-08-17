"""模型价格 Interface 的内存 Adapter。"""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from app.credits._amounts import credit_units, format_credits
from app.credits._validation import validated_effective_time
from app.credits.models import (
    InvalidModelReferenceLimit,
    ModelPriceConflict,
    ModelPriceVersion,
    UnknownModelPriceVersion,
)


def _validated_reference_limit(value: int) -> int:
    if isinstance(value, bool) or not 0 <= value <= 16:
        raise InvalidModelReferenceLimit(value)
    return value


class InMemoryModelPrices:
    """在单进程内保存不可改写的模型价格版本。"""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._versions_by_id: dict[str, ModelPriceVersion] = {}
        self._deleted_version_ids: set[str] = set()
        self._lock = Lock()
        now = self._clock()
        version_id = str(uuid4())
        self._versions_by_id[version_id] = ModelPriceVersion(
            version_id=version_id,
            logical_model="gpt-image-2",
            output_spec="4k",
            credits_per_result="0.1500",
            effective_from=now,
            published_at=now,
            max_reference_images=3,
        )

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
        with self._lock:
            if any(
                existing.logical_model == logical_model
                and existing.output_spec == output_spec
                and existing.effective_from == effective_from
                for existing in self._versions_by_id.values()
            ):
                raise ModelPriceConflict(logical_model, output_spec)
            self._versions_by_id[version.version_id] = version
        return version

    def effective_at(self, logical_model: str, output_spec: str, at: datetime) -> ModelPriceVersion:
        """读取指定时间最新生效的模型价格。"""
        with self._lock:
            candidates = [
                version
                for version in self._versions_by_id.values()
                if version.logical_model == logical_model
                and version.output_spec == output_spec
                and version.effective_from <= at
                and version.version_id not in self._deleted_version_ids
            ]
        if not candidates:
            raise UnknownModelPriceVersion(f"{logical_model}/{output_spec}")
        return max(candidates, key=lambda version: version.effective_from)

    def catalog_at(self, at: datetime) -> tuple[ModelPriceVersion, ...]:
        """读取指定时刻每个模型规格的最新生效价格。"""
        with self._lock:
            current: dict[tuple[str, str], ModelPriceVersion] = {}
            for version in self._versions_by_id.values():
                if version.effective_from > at or version.version_id in self._deleted_version_ids:
                    continue
                key = (version.logical_model, version.output_spec)
                selected = current.get(key)
                if selected is None or selected.effective_from < version.effective_from:
                    current[key] = version
        return tuple(current[key] for key in sorted(current))

    def get_version(self, version_id: str) -> ModelPriceVersion:
        """读取任意历史模型价格版本。"""
        with self._lock:
            try:
                return self._versions_by_id[version_id]
            except KeyError as exc:
                raise UnknownModelPriceVersion(version_id) from exc

    def delete(self, version_id: str, deleted_at: datetime) -> None:
        """退役同一逻辑模型规格的全部活动价格版本。"""
        del deleted_at
        with self._lock:
            try:
                selected = self._versions_by_id[version_id]
            except KeyError as exc:
                raise UnknownModelPriceVersion(version_id) from exc
            if version_id in self._deleted_version_ids:
                raise UnknownModelPriceVersion(version_id)
            self._deleted_version_ids.update(
                version.version_id for version in self._versions_by_id.values()
                if version.logical_model == selected.logical_model and version.output_spec == selected.output_spec
            )

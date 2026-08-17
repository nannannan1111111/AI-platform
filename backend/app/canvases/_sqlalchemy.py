"""SQLAlchemy Adapter for the Canvases Interface."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, sessionmaker

from app.canvases._validation import validated_document, validated_kind, validated_title
from app.canvases.models import (
    Canvas,
    CanvasCreation,
    CanvasDeletion,
    CanvasKind,
    CanvasNotFound,
    CanvasSave,
    CanvasVersionConflict,
)

_metadata = MetaData()
_canvases = Table(
    "canvases",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("title", String(80), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("document", JSON, nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=True),
)


class SqlAlchemyCanvases:
    """Persist account-owned canvas documents with optimistic versions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._id_factory = id_factory or (lambda: str(uuid4()))

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> SqlAlchemyCanvases:
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False), id_factory=id_factory)

    def create(self, creation: CanvasCreation) -> Canvas:
        canvas = Canvas(
            canvas_id=self._id_factory(),
            user_id=creation.user_id,
            account_space_id=creation.account_space_id,
            title=validated_title(creation.title),
            kind=validated_kind(creation.kind),
            document={
                "nodes": [],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
            version=1,
            created_at=creation.created_at,
            updated_at=creation.created_at,
        )
        with self._session_factory.begin() as database:
            database.execute(insert(_canvases).values(**_canvas_values(canvas)))
        return canvas

    def list(self, account_space_id: str) -> tuple[Canvas, ...]:
        with self._session_factory() as database:
            rows = (
                database.execute(
                    select(_canvases)
                    .where(
                        _canvases.c.account_space_id == account_space_id,
                        _canvases.c.deleted_at.is_(None),
                    )
                    .order_by(_canvases.c.created_at, _canvases.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(_canvas_from_row(row) for row in rows)

    def get(self, account_space_id: str, canvas_id: str) -> Canvas:
        with self._session_factory() as database:
            row = _canvas_row(database, account_space_id, canvas_id)
        if row is None:
            raise CanvasNotFound(canvas_id)
        return _canvas_from_row(row)

    def save(self, command: CanvasSave) -> Canvas:
        with self._session_factory.begin() as database:
            row = _canvas_row(database, command.account_space_id, command.canvas_id, for_update=True)
            if row is None:
                raise CanvasNotFound(command.canvas_id)
            canvas = _canvas_from_row(row)
            if canvas.version != command.expected_version:
                raise CanvasVersionConflict(command.canvas_id)
            saved = Canvas(
                canvas_id=canvas.canvas_id,
                user_id=canvas.user_id,
                account_space_id=canvas.account_space_id,
                title=canvas.title if command.title is None else validated_title(command.title),
                kind=canvas.kind,
                document=validated_document(command.document),
                version=canvas.version + 1,
                created_at=canvas.created_at,
                updated_at=command.saved_at,
            )
            database.execute(
                update(_canvases)
                .where(
                    _canvases.c.id == canvas.canvas_id,
                    _canvases.c.account_space_id == canvas.account_space_id,
                    _canvases.c.version == canvas.version,
                )
                .values(**_canvas_values(saved))
            )
            persisted_row = _canvas_row(database, command.account_space_id, command.canvas_id)
            if persisted_row is None or int(persisted_row["version"]) != saved.version:
                raise CanvasVersionConflict(command.canvas_id)
            return saved

    def delete(self, command: CanvasDeletion) -> None:
        """清空画布内容并保留只用于历史外键完整性的删除墓碑。"""
        with self._session_factory.begin() as database:
            row = _canvas_row(database, command.account_space_id, command.canvas_id, for_update=True)
            if row is None:
                raise CanvasNotFound(command.canvas_id)
            database.execute(
                update(_canvases)
                .where(
                    _canvases.c.id == command.canvas_id,
                    _canvases.c.account_space_id == command.account_space_id,
                    _canvases.c.deleted_at.is_(None),
                )
                .values(
                    title="已删除画布",
                    document={
                        "nodes": [],
                        "connections": [],
                        "viewport": {"x": 0, "y": 0, "scale": 1},
                    },
                    version=int(row["version"]) + 1,
                    updated_at=command.deleted_at,
                    deleted_at=command.deleted_at,
                )
            )


def _canvas_row(database: Session, account_space_id: str, canvas_id: str, *, for_update: bool = False) -> Any:
    query = select(_canvases).where(
        _canvases.c.id == canvas_id,
        _canvases.c.account_space_id == account_space_id,
        _canvases.c.deleted_at.is_(None),
    )
    if for_update:
        query = query.with_for_update()
    return database.execute(query).mappings().one_or_none()


def _canvas_values(canvas: Canvas) -> dict[str, Any]:
    return {
        "id": canvas.canvas_id,
        "user_id": canvas.user_id,
        "account_space_id": canvas.account_space_id,
        "title": canvas.title,
        "kind": canvas.kind.value,
        "document": deepcopy(canvas.document),
        "version": canvas.version,
        "created_at": canvas.created_at,
        "updated_at": canvas.updated_at,
        "deleted_at": None,
    }


def _canvas_from_row(row: Any) -> Canvas:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return Canvas(
        canvas_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        title=str(row["title"]),
        kind=CanvasKind(str(row["kind"])),
        document=deepcopy(dict(row["document"])),
        version=int(row["version"]),
        created_at=aware(row["created_at"]),
        updated_at=aware(row["updated_at"]),
    )

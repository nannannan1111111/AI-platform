"""Canvases Interface 的内存 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from app.canvases._validation import validated_document, validated_kind, validated_title
from app.canvases.models import (
    Canvas,
    CanvasCreation,
    CanvasDeletion,
    CanvasNotFound,
    CanvasSave,
    CanvasVersionConflict,
)


class InMemoryCanvases:
    """在单进程内保存账户空间归属的画布。"""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._canvases_by_id: dict[str, Canvas] = {}
        self._lock = RLock()

    def create(self, creation: CanvasCreation) -> Canvas:
        """创建一个带空节点图的持久画布。"""
        title = validated_title(creation.title)
        kind = validated_kind(creation.kind)
        canvas = Canvas(
            canvas_id=self._id_factory(),
            user_id=creation.user_id,
            account_space_id=creation.account_space_id,
            title=title,
            kind=kind,
            document={
                "nodes": [],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
            version=1,
            created_at=creation.created_at,
            updated_at=creation.created_at,
        )
        with self._lock:
            self._canvases_by_id[canvas.canvas_id] = canvas
        return _snapshot(canvas)

    def get(self, account_space_id: str, canvas_id: str) -> Canvas:
        """读取账户空间拥有的画布。"""
        with self._lock:
            canvas = self._canvases_by_id.get(canvas_id)
        if canvas is None or canvas.account_space_id != account_space_id:
            raise CanvasNotFound(canvas_id)
        return _snapshot(canvas)

    def list(self, account_space_id: str) -> tuple[Canvas, ...]:
        """按创建顺序读取账户空间拥有的画布。"""
        with self._lock:
            return tuple(
                _snapshot(canvas)
                for canvas in sorted(
                    (canvas for canvas in self._canvases_by_id.values() if canvas.account_space_id == account_space_id),
                    key=lambda canvas: (canvas.created_at, canvas.canvas_id),
                )
            )

    def save(self, command: CanvasSave) -> Canvas:
        """预期版本匹配时保存完整画布文档。"""
        with self._lock:
            canvas = self._canvases_by_id.get(command.canvas_id)
            if canvas is None or canvas.account_space_id != command.account_space_id:
                raise CanvasNotFound(command.canvas_id)
            if canvas.version != command.expected_version:
                raise CanvasVersionConflict(command.canvas_id)
            document = validated_document(command.document)
            title = canvas.title if command.title is None else validated_title(command.title)
            saved = replace(
                canvas,
                title=title,
                document=document,
                version=canvas.version + 1,
                updated_at=command.saved_at,
            )
            self._canvases_by_id[canvas.canvas_id] = saved
            return _snapshot(saved)

    def delete(self, command: CanvasDeletion) -> None:
        """不可恢复地删除账户空间拥有的画布。"""
        with self._lock:
            canvas = self._canvases_by_id.get(command.canvas_id)
            if canvas is None or canvas.account_space_id != command.account_space_id:
                raise CanvasNotFound(command.canvas_id)
            del self._canvases_by_id[command.canvas_id]


def _snapshot(canvas: Canvas) -> Canvas:
    return Canvas(
        canvas_id=canvas.canvas_id,
        user_id=canvas.user_id,
        account_space_id=canvas.account_space_id,
        title=canvas.title,
        kind=canvas.kind,
        document=deepcopy(canvas.document),
        version=canvas.version,
        created_at=canvas.created_at,
        updated_at=canvas.updated_at,
    )

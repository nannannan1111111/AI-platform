"""SaaS 画布 Module 的公开 Interface。"""

from typing import Protocol

from app.canvases.models import Canvas, CanvasCreation, CanvasDeletion, CanvasSave


class Canvases(Protocol):
    """管理个人账户空间拥有的版本化画布。"""

    def create(self, creation: CanvasCreation) -> Canvas:
        """创建版本为 1 的空画布。"""

    def list(self, account_space_id: str) -> tuple[Canvas, ...]:
        """按创建时间和画布标识返回账户空间拥有的画布。"""

    def get(self, account_space_id: str, canvas_id: str) -> Canvas:
        """读取账户空间拥有的画布；其他账户按不存在处理。"""

    def save(self, command: CanvasSave) -> Canvas:
        """预期版本匹配时保存文档并递增版本。"""

    def delete(self, command: CanvasDeletion) -> None:
        """不可恢复地删除账户空间拥有的画布。"""

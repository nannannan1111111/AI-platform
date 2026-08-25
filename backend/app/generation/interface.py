"""SaaS 生成任务 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.generation.models import (
    GenerationActivitySummary,
    GenerationSubmission,
    GenerationTask,
    GenerationTransition,
)


class GenerationTasks(Protocol):
    """管理独立于浏览器连接的归属生成任务。"""

    def submit(self, submission: GenerationSubmission) -> GenerationTask:
        """创建任务并冻结预计额度；相同提交可安全重放。"""

    def transition(self, account_space_id: str, task_id: str, event: GenerationTransition) -> GenerationTask:
        """推进任务生命周期并结算或释放冻结额度。"""

    def expire_due(self, now: datetime) -> tuple[GenerationTask, ...]:
        """按当前管理员截止时间幂等失败活动任务并释放冻结额度。"""

    def get(self, account_space_id: str, task_id: str) -> GenerationTask:
        """读取账户空间拥有的任务；其他空间按不存在处理。"""

    def active_across_accounts(self) -> tuple[GenerationTask, ...]:
        """按创建顺序读取全站仍在排队或运行的任务，供平台管理使用。"""

    def admin_recent(self, *, since: datetime | None, offset: int, limit: int) -> tuple[GenerationTask, ...]:
        """按创建时间倒序读取管理员任务监管列表。"""

    def admin_total(self, *, since: datetime | None) -> int:
        """返回管理员任务监管列表总数。"""

    def active_for_canvas(self, account_space_id: str, canvas_id: str) -> tuple[GenerationTask, ...]:
        """按创建顺序读取指定画布的活动任务。"""

    def recent_for_canvas(
        self,
        account_space_id: str,
        canvas_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        """按创建时间从新到旧读取指定画布最近任务，包含终态。"""

    def recent_for_account(
        self,
        account_space_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        """按创建时间从新到旧读取账户空间最近任务，包含终态。"""

    def clear_history(self, account_space_id: str, *, cleared_at: datetime) -> int:
        """从用户历史视图隐藏账户空间内尚未隐藏的终态任务。"""

    def activity_summary(
        self,
        account_space_id: str,
        *,
        since: datetime | None,
    ) -> GenerationActivitySummary:
        """Summarize tasks submitted since the optional inclusive boundary."""

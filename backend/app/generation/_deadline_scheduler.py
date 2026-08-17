"""由服务端时钟驱动生成任务截止扫描。"""

from collections.abc import Callable
from datetime import UTC, datetime

from app.generation.interface import GenerationTasks
from app.generation.models import GenerationTask


class GenerationDeadlineScheduler:
    """隐藏时钟读取并执行一次管理员配置的截止扫描。"""

    def __init__(
        self,
        generation_tasks: GenerationTasks,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._generation_tasks = generation_tasks
        self._clock = clock

    def run_due(self) -> tuple[GenerationTask, ...]:
        """按服务端当前时间失败所有达到截止点的活动任务。"""
        return self._generation_tasks.expire_due(self._clock())

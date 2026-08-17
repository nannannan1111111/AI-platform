"""基于已登记媒体完成生成任务。"""

from datetime import datetime

from app.generation import (
    GenerationFailed,
    GenerationSucceeded,
    GenerationTask,
    GenerationTasks,
    GenerationTaskStatus,
)
from app.generation_results.models import InvalidGenerationResult
from app.media import GeneratedMedia, GeneratedMediaKind, GeneratedMediaState


class GenerationResultFinalizer:
    """把安全媒体登记投影为一次幂等任务交付。"""

    def __init__(self, generation_tasks: GenerationTasks, generated_media: GeneratedMedia) -> None:
        """装配任务与已登记媒体 Interface。"""
        self._generation_tasks = generation_tasks
        self._generated_media = generated_media

    def finalize(self, account_space_id: str, task_id: str, *, occurred_at: datetime) -> GenerationTask:
        """按任务仍可用的已登记图片数量成功或失败收口。"""
        task = self._generation_tasks.get(account_space_id, task_id)
        if task.status not in {
            GenerationTaskStatus.RUNNING,
            GenerationTaskStatus.SUCCEEDED,
            GenerationTaskStatus.FAILED,
        }:
            raise InvalidGenerationResult("只有运行中的图片任务可以交付结果")
        deliverable = tuple(
            media
            for media in self._generated_media.list_for_task(account_space_id, task_id)
            if media.state in {GeneratedMediaState.TEMPORARY, GeneratedMediaState.PERSISTENT}
        )
        if any(media.kind is not GeneratedMediaKind.IMAGE for media in deliverable):
            raise InvalidGenerationResult("图片任务包含非图片交付结果")
        delivered_quantity = len(deliverable)
        if delivered_quantity > task.quantity:
            raise InvalidGenerationResult("交付结果数量超过任务请求数量")
        outcome_reference = _outcome_reference(account_space_id, task_id, delivered_quantity)
        event: GenerationSucceeded | GenerationFailed
        if delivered_quantity:
            event = GenerationSucceeded(
                delivered_quantity=delivered_quantity,
                outcome_reference=outcome_reference,
                occurred_at=occurred_at,
            )
        else:
            event = GenerationFailed(
                reason="no generated media was delivered",
                outcome_reference=outcome_reference,
                occurred_at=occurred_at,
            )
        return self._generation_tasks.transition(account_space_id, task_id, event)


def _outcome_reference(account_space_id: str, task_id: str, delivered_quantity: int) -> str:
    return f"generation-result:{account_space_id}:{task_id}:{delivered_quantity}"

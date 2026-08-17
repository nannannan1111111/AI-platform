"""安全接收已经写入平台存储的规范化生成输出。"""

from collections.abc import Iterable
from datetime import datetime

from app.generation import GenerationTask, GenerationTasks
from app.generation_results.finalizer import GenerationResultFinalizer
from app.generation_results.models import (
    GenerationOutput,
    InvalidGenerationOutputBatch,
)
from app.media import GeneratedMedia, GeneratedMediaRegistration


class GenerationOutputReceiver:
    """统一登记一批图片输出，并在全部登记后收口任务。"""

    def __init__(self, generation_tasks: GenerationTasks, generated_media: GeneratedMedia) -> None:
        """装配任务与媒体 Interface。"""
        self._generation_tasks = generation_tasks
        self._generated_media = generated_media
        self._finalizer = GenerationResultFinalizer(generation_tasks, generated_media)

    def receive(
        self,
        account_space_id: str,
        task_id: str,
        outputs: Iterable[GenerationOutput],
        *,
        completed_at: datetime,
    ) -> GenerationTask:
        """整体校验并幂等登记输出；全部成功后执行一次交付。"""
        self.register(account_space_id, task_id, outputs, completed_at=completed_at)
        return self._finalizer.finalize(account_space_id, task_id, occurred_at=completed_at)

    def register(
        self,
        account_space_id: str,
        task_id: str,
        outputs: Iterable[GenerationOutput],
        *,
        completed_at: datetime,
    ) -> None:
        """Register an incremental batch without completing the still-running task."""
        task = self._generation_tasks.get(account_space_id, task_id)
        batch = tuple(outputs)
        references = tuple(output.result_reference for output in batch)
        if len(set(references)) != len(references):
            raise InvalidGenerationOutputBatch("生成输出批次包含重复结果引用")
        registered_references = {
            media.result_reference for media in self._generated_media.list_for_task(account_space_id, task_id)
        }
        if len(registered_references | set(references)) > task.quantity:
            raise InvalidGenerationOutputBatch("生成输出数量超过任务请求数量")
        for output in batch:
            self._generated_media.register(
                GeneratedMediaRegistration(
                    user_id=task.user_id,
                    account_space_id=task.account_space_id,
                    canvas_id=task.canvas_id,
                    task_id=task.task_id,
                    result_reference=output.result_reference,
                    object_key=output.object_key,
                    kind="image",
                    mime_type=output.mime_type,
                    size_bytes=output.size_bytes,
                    content_hash=output.content_hash,
                    created_at=completed_at,
                )
            )

    def finalize(self, account_space_id: str, task_id: str, *, completed_at: datetime) -> GenerationTask:
        """Complete a task after all incrementally registered outputs have arrived."""
        return self._finalizer.finalize(account_space_id, task_id, occurred_at=completed_at)

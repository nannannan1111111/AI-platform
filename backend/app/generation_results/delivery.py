"""把规范化图片字节安全写入平台存储并完成交付。"""

from collections.abc import Iterable
from datetime import datetime

from app.generation import GenerationTask, GenerationTasks, GenerationTaskStatus
from app.generation.deadlines import is_generation_timeout
from app.generation_results.models import (
    GenerationImageContent,
    GenerationOutput,
    InvalidGenerationOutputBatch,
)
from app.generation_results.receiver import GenerationOutputReceiver
from app.media import GeneratedMedia, MediaContentStore

_SUPPORTED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def _has_image_signature(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    if mime_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


class GenerationImageDelivery:
    """隐藏落盘、元数据登记和最终结算的一次图片交付。"""

    def __init__(
        self,
        generation_tasks: GenerationTasks,
        generated_media: GeneratedMedia,
        media_content: MediaContentStore,
    ) -> None:
        """装配任务、媒体元数据与内容存储 Interface。"""
        self._generation_tasks = generation_tasks
        self._generated_media = generated_media
        self._media_content = media_content
        self._output_receiver = GenerationOutputReceiver(generation_tasks, generated_media)

    def receive(
        self,
        account_space_id: str,
        task_id: str,
        images: Iterable[GenerationImageContent],
        *,
        completed_at: datetime,
    ) -> GenerationTask:
        """整体校验图片字节，幂等落盘后登记并完成任务。"""
        outputs = self._store(account_space_id, task_id, images, completed_at=completed_at)
        return self._output_receiver.receive(
            account_space_id,
            task_id,
            outputs,
            completed_at=completed_at,
        )

    def receive_partial(
        self,
        account_space_id: str,
        task_id: str,
        images: Iterable[GenerationImageContent],
        *,
        completed_at: datetime,
    ) -> None:
        """Persist images as they arrive while leaving the task running."""
        outputs = self._store(account_space_id, task_id, images, completed_at=completed_at)
        self._output_receiver.register(
            account_space_id,
            task_id,
            outputs,
            completed_at=completed_at,
        )

    def finalize(self, account_space_id: str, task_id: str, *, completed_at: datetime) -> GenerationTask:
        """Settle a task after its streaming response has ended."""
        return self._output_receiver.finalize(account_space_id, task_id, completed_at=completed_at)

    def _store(
        self,
        account_space_id: str,
        task_id: str,
        images: Iterable[GenerationImageContent],
        *,
        completed_at: datetime,
    ) -> tuple[GenerationOutput, ...]:
        self._generation_tasks.expire_due(completed_at)
        task = self._generation_tasks.get(account_space_id, task_id)
        if task.status is GenerationTaskStatus.CANCELLED:
            raise InvalidGenerationOutputBatch("任务已取消，迟到的图片结果已作废且不会写入平台")
        if is_generation_timeout(task):
            raise InvalidGenerationOutputBatch("图片结果超过管理员设置的任务截止时间，已作废且不会写入平台")
        batch = tuple(images)
        references = tuple(image.result_reference.strip() for image in batch)
        if any(not reference for reference in references) or len(set(references)) != len(references):
            raise InvalidGenerationOutputBatch("图片结果引用为空或重复")
        registered_references = {
            item.result_reference for item in self._generated_media.list_for_task(account_space_id, task_id)
        }
        if len(registered_references | set(references)) > task.quantity:
            raise InvalidGenerationOutputBatch("图片结果数量超过任务请求数量")
        if any(
            (mime_type := image.mime_type.strip().casefold()) not in _SUPPORTED_IMAGE_TYPES
            or not _has_image_signature(mime_type, image.content)
            for image in batch
        ):
            raise InvalidGenerationOutputBatch("图片内容或 MIME 类型无效")
        outputs: list[GenerationOutput] = []
        for image, result_reference in zip(batch, references, strict=True):
            mime_type = image.mime_type.strip().casefold()
            stored = self._media_content.put_temporary(
                account_space_id=task.account_space_id,
                task_id=task.task_id,
                result_reference=result_reference,
                content=image.content,
                mime_type=mime_type,
            )
            outputs.append(
                GenerationOutput(
                    result_reference=result_reference,
                    object_key=stored.object_key,
                    mime_type=mime_type,
                    size_bytes=stored.size_bytes,
                    content_hash=stored.content_hash,
                )
            )
        return tuple(outputs)

"""临时生成媒体 Module 的公开 Interface。"""

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from app.media.models import (
    CanvasMediaUpload,
    GeneratedMediaRecord,
    GeneratedMediaRegistration,
    MediaExpirationReport,
    MediaReferenceReconciliation,
    StorageAllowance,
)


class GeneratedMedia(Protocol):
    """登记并读取账户空间归属的临时生成媒体。"""

    def register(self, registration: GeneratedMediaRegistration) -> GeneratedMediaRecord:
        """登记运行中任务的结果；相同结果引用可安全重放。"""

    def upload_to_canvas(self, upload: CanvasMediaUpload) -> GeneratedMediaRecord:
        """校验图片、核算额度并作为持久画布媒体保存。"""

    def get(self, account_space_id: str, media_id: str) -> GeneratedMediaRecord:
        """读取账户空间拥有的媒体；其他空间按不存在处理。"""

    def list_for_task(self, account_space_id: str, task_id: str) -> tuple[GeneratedMediaRecord, ...]:
        """按创建时间和媒体标识读取任务登记的媒体。"""

    def storage_allowance(self, account_space_id: str) -> StorageAllowance:
        """返回按账户内内容去重核算的存储额度快照。"""

    def expire_due(self, now: datetime) -> MediaExpirationReport:
        """删除已到期临时对象并保留状态为已过期的元数据。"""

    def delete(self, account_space_id: str, media_id: str, deleted_at: datetime) -> GeneratedMediaRecord:
        """永久删除未被引用的工作区临时结果并保留最小墓碑。"""

    def retain_to_canvas(
        self,
        account_space_id: str,
        media_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        """在存储额度内把临时媒体幂等保留到原所属画布。"""

    def retain_to_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        retained_at: datetime,
    ) -> GeneratedMediaRecord:
        """在存储额度内把媒体幂等保留到个人资产。"""

    def release_from_personal_asset(
        self,
        account_space_id: str,
        media_id: str,
        asset_id: str,
        released_at: datetime,
    ) -> GeneratedMediaRecord:
        """移除个人资产引用，并在最后一条引用消失时释放媒体。"""

    def reconcile_canvas_references(
        self,
        account_space_id: str,
        canvas_id: str,
        retained_media_ids: Iterable[str],
        occurred_at: datetime,
    ) -> MediaReferenceReconciliation:
        """释放画布文档不再保留的持久媒体引用。"""

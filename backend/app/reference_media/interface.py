"""临时参考媒体 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.reference_media.models import ReferenceMediaContent, ReferenceMediaRecord, ReferenceMediaUpload


class ReferenceMedia(Protocol):
    """保存并读取账户隔离、24 小时失效的图片生成参考媒体。"""

    def upload(self, upload: ReferenceMediaUpload) -> ReferenceMediaRecord:
        """校验并保存一项临时参考图片。"""

    def read(self, account_space_id: str, media_id: str, *, at: datetime) -> ReferenceMediaContent:
        """读取仍可用且属于账户空间的参考图片。"""

    def list_recent(self, account_space_id: str, *, at: datetime) -> tuple[ReferenceMediaRecord, ...]:
        """按创建时间列出账户仍可用的 24 小时参考图片。"""

    def delete(self, account_space_id: str, media_id: str) -> ReferenceMediaRecord:
        """立即删除账户拥有的参考图片字节并保留删除状态。"""

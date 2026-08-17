"""个人资产库 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.assets.models import PersonalAsset, PersonalAssetRename, PersonalAssetSave


class PersonalAssets(Protocol):
    """保存并读取个人账户空间拥有的媒体资产。"""

    def save_generated_media(self, command: PersonalAssetSave) -> PersonalAsset:
        """把生成媒体幂等保存为个人资产。"""

    def list(self, account_space_id: str) -> tuple[PersonalAsset, ...]:
        """按保存时间和资产标识读取账户空间的资产。"""

    def rename(self, command: PersonalAssetRename) -> PersonalAsset:
        """修改账户空间中可见个人资产的显示名称。"""

    def remove(self, account_space_id: str, asset_id: str, removed_at: datetime) -> None:
        """不可恢复地移除账户空间中的个人资产。"""

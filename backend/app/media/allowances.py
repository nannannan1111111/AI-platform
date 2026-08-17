"""个人账户空间持久媒体存储额度 Interface 与内存 Adapter。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

MAX_STORAGE_ALLOWANCE_BYTES = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class StorageAllowancePolicy:
    """统一作用于个人账户空间的存储额度配置。"""

    limit_bytes: int


@dataclass(frozen=True, slots=True)
class AccountStorageAllowancePolicy:
    """单独作用于一个个人账户空间的存储额度配置。"""

    account_space_id: str
    limit_bytes: int


class StorageAllowances(Protocol):
    """提供由套餐或运营配置决定的账户存储额度。"""

    def limit_bytes(self, account_space_id: str) -> int:
        """返回账户空间可占用的持久媒体字节数。"""

    def global_limit_bytes(self) -> int:
        """返回没有单独配置时使用的统一额度。"""

    def set_global_limit(self, limit_bytes: int) -> StorageAllowancePolicy:
        """替换所有现有及未来个人账户空间使用的统一额度。"""

    def set_account_limit(self, account_space_id: str, limit_bytes: int) -> AccountStorageAllowancePolicy:
        """为一个账户空间设置优先于统一额度的单独额度。"""


class InMemoryStorageAllowances:
    """以内存映射提供测试和单进程装配所需的存储额度。"""

    def __init__(self, limits_by_account_space: Mapping[str, int]) -> None:
        """校验并保存每个账户空间的非负字节额度。"""
        if any(limit < 0 or limit > MAX_STORAGE_ALLOWANCE_BYTES for limit in limits_by_account_space.values()):
            raise ValueError("存储额度必须在数据库支持范围内")
        self._limits = dict(limits_by_account_space)
        self._account_limits: dict[str, int] = {}
        self._global_limit: int | None = None

    def limit_bytes(self, account_space_id: str) -> int:
        """未配置的账户空间安全地返回零额度。"""
        if account_space_id in self._account_limits:
            return self._account_limits[account_space_id]
        if self._global_limit is not None:
            return self._global_limit
        return self._limits.get(account_space_id, 0)

    def global_limit_bytes(self) -> int:
        """返回当前内存装配中的统一额度。"""
        return self._global_limit or 0

    def set_global_limit(self, limit_bytes: int) -> StorageAllowancePolicy:
        """用统一额度替换测试装配中的账户级初始值。"""
        if limit_bytes < 0 or limit_bytes > MAX_STORAGE_ALLOWANCE_BYTES:
            raise ValueError("存储额度必须在数据库支持范围内")
        self._global_limit = limit_bytes
        self._limits.clear()
        return StorageAllowancePolicy(limit_bytes=limit_bytes)

    def set_account_limit(self, account_space_id: str, limit_bytes: int) -> AccountStorageAllowancePolicy:
        """只替换指定账户空间的额度覆盖值。"""
        if limit_bytes < 0 or limit_bytes > MAX_STORAGE_ALLOWANCE_BYTES:
            raise ValueError("存储额度必须在数据库支持范围内")
        self._account_limits[account_space_id] = limit_bytes
        return AccountStorageAllowancePolicy(account_space_id=account_space_id, limit_bytes=limit_bytes)

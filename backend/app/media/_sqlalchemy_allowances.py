"""StorageAllowances Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.media.allowances import (
    MAX_STORAGE_ALLOWANCE_BYTES,
    AccountStorageAllowancePolicy,
    StorageAllowancePolicy,
)

_POLICY_KEY = "global"
_metadata = MetaData()
_storage_allowance_policies = Table(
    "storage_allowance_policies",
    _metadata,
    Column("policy_key", String(32), primary_key=True),
    Column("limit_bytes", BigInteger, nullable=False),
    CheckConstraint("limit_bytes >= 0", name="ck_storage_allowance_policies_limit_nonnegative"),
)
_account_storage_allowances = Table(
    "account_storage_allowances",
    _metadata,
    Column("account_space_id", String(36), primary_key=True),
    Column("limit_bytes", BigInteger, nullable=False),
    CheckConstraint("limit_bytes >= 0", name="ck_account_storage_allowances_limit_nonnegative"),
)


class SqlAlchemyStorageAllowances:
    """在关系数据库中持久化统一存储额度配置。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def for_database_url(cls, database_url: str) -> SqlAlchemyStorageAllowances:
        """使用数据库 URL 创建独立会话 Adapter。"""
        engine = create_engine(database_url)
        return cls(sessionmaker(engine, expire_on_commit=False))

    def limit_bytes(self, account_space_id: str) -> int:
        """优先返回账户单独额度，否则返回统一额度。"""
        with self._session_factory() as database:
            account_value = database.scalar(
                select(_account_storage_allowances.c.limit_bytes).where(
                    _account_storage_allowances.c.account_space_id == account_space_id
                )
            )
            if account_value is not None:
                return int(account_value)
            value = database.scalar(
                select(_storage_allowance_policies.c.limit_bytes).where(
                    _storage_allowance_policies.c.policy_key == _POLICY_KEY
                )
            )
        return int(value or 0)

    def global_limit_bytes(self) -> int:
        """返回没有账户覆盖时使用的统一额度。"""
        with self._session_factory() as database:
            value = database.scalar(
                select(_storage_allowance_policies.c.limit_bytes).where(
                    _storage_allowance_policies.c.policy_key == _POLICY_KEY
                )
            )
        return int(value or 0)

    def set_global_limit(self, limit_bytes: int) -> StorageAllowancePolicy:
        """原子替换统一额度，并在缺少种子记录时安全补建。"""
        if limit_bytes < 0 or limit_bytes > MAX_STORAGE_ALLOWANCE_BYTES:
            raise ValueError("存储额度必须在数据库支持范围内")
        with self._session_factory.begin() as database:
            existing = database.scalar(
                select(_storage_allowance_policies.c.policy_key)
                .where(_storage_allowance_policies.c.policy_key == _POLICY_KEY)
                .with_for_update()
            )
            if existing is None:
                database.execute(
                    insert(_storage_allowance_policies).values(policy_key=_POLICY_KEY, limit_bytes=limit_bytes)
                )
            else:
                database.execute(
                    update(_storage_allowance_policies)
                    .where(_storage_allowance_policies.c.policy_key == _POLICY_KEY)
                    .values(limit_bytes=limit_bytes)
                )
        return StorageAllowancePolicy(limit_bytes=limit_bytes)

    def set_account_limit(self, account_space_id: str, limit_bytes: int) -> AccountStorageAllowancePolicy:
        """原子新增或替换指定账户空间的额度覆盖值。"""
        if limit_bytes < 0 or limit_bytes > MAX_STORAGE_ALLOWANCE_BYTES:
            raise ValueError("存储额度必须在数据库支持范围内")
        with self._session_factory.begin() as database:
            insert_statement = (
                postgresql_insert(_account_storage_allowances)
                if database.get_bind().dialect.name == "postgresql"
                else sqlite_insert(_account_storage_allowances)
            )
            database.execute(
                insert_statement.values(
                    account_space_id=account_space_id,
                    limit_bytes=limit_bytes,
                ).on_conflict_do_update(
                    index_elements=[_account_storage_allowances.c.account_space_id],
                    set_={"limit_bytes": limit_bytes},
                )
            )
        return AccountStorageAllowancePolicy(account_space_id=account_space_id, limit_bytes=limit_bytes)

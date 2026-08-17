"""充值包与额度账务 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.credits._amounts import cny_units, credit_units, format_cny, format_credits, signed_credit_units
from app.credits._pricing_sqlalchemy import SqlAlchemyModelPrices
from app.credits._validation import (
    validated_audit_reason,
    validated_audit_reference,
    validated_effective_time,
    validated_reversal_reason,
)
from app.credits.interface import ModelPrices
from app.credits.models import (
    CreditFreeze,
    CreditFreezeAlreadyFinalized,
    CreditPosting,
    CreditStatement,
    CreditStatementPage,
    InsufficientCredits,
    PackageVersionConflict,
    PostingAlreadyReversed,
    RechargePackageVersion,
    ReferenceConflict,
    UnknownAccountSpace,
    UnknownCreditFreeze,
    UnknownCreditPosting,
    UnknownRechargePackageVersion,
)

_metadata = MetaData()
_credit_accounts = Table(
    "credit_accounts",
    _metadata,
    Column("account_space_id", String(36), primary_key=True),
    Column("available_credit_units", BigInteger, nullable=False),
    Column("frozen_credit_units", BigInteger, nullable=False),
)
_package_versions = Table(
    "recharge_package_versions",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("package_code", String(64), nullable=False),
    Column("payment_cny_units", BigInteger, nullable=False),
    Column("credit_units", BigInteger, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
)
_credit_postings = Table(
    "credit_postings",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("account_space_id", String(36), nullable=False),
    Column("sequence_number", BigInteger, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("available_delta_units", BigInteger, nullable=False),
    Column("available_units_after", BigInteger, nullable=False),
    Column("package_version_id", String(36), nullable=True),
    Column("reference", String(255), nullable=False),
    Column("reverses_posting_id", String(36), nullable=True),
    Column("reason", String(255), nullable=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("frozen_delta_units", BigInteger, nullable=False, default=0),
    Column("frozen_units_after", BigInteger, nullable=False, default=0),
    Column("model_price_version_id", String(36), nullable=True),
    Column("generation_reference", String(255), nullable=True),
)
_credit_freezes = Table(
    "credit_freezes",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("account_space_id", String(36), nullable=False),
    Column("task_reference", String(255), nullable=False),
    Column("model_price_version_id", String(36), nullable=False),
    Column("logical_model", String(128), nullable=False),
    Column("output_spec", String(128), nullable=False),
    Column("quantity", BigInteger, nullable=False),
    Column("unit_price_units", BigInteger, nullable=False),
    Column("frozen_units", BigInteger, nullable=False),
    Column("available_units_after", BigInteger, nullable=False),
    Column("frozen_units_after", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)


class SqlAlchemyCredits:
    """使用 SQL 事务持久化充值包版本和额度账务记录。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
        model_prices: ModelPrices | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._model_prices = model_prices or SqlAlchemyModelPrices(session_factory, clock=self._clock)

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        clock: Callable[[], datetime] | None = None,
        model_prices: ModelPrices | None = None,
    ) -> SqlAlchemyCredits:
        """为已经由 Alembic 初始化的数据库创建 Adapter。"""
        engine = create_engine(database_url)
        session_factory = sessionmaker(engine, expire_on_commit=False)
        return cls(session_factory, clock=clock, model_prices=model_prices)

    def publish(
        self,
        package_code: str,
        *,
        payment_cny: str,
        credits: str,
        effective_from: datetime,
    ) -> RechargePackageVersion:
        """在单独事务中插入不可改写的充值包版本。"""
        payment_units = cny_units(payment_cny)
        amount_units = credit_units(credits)
        published_at = self._clock()
        version = RechargePackageVersion(
            version_id=str(uuid4()),
            package_code=package_code,
            payment_cny=format_cny(payment_units),
            credits=format_credits(amount_units),
            effective_from=validated_effective_time(effective_from, published_at),
            published_at=published_at,
        )
        try:
            with self._session_factory.begin() as database:
                database.execute(
                    insert(_package_versions).values(
                        id=version.version_id,
                        package_code=version.package_code,
                        payment_cny_units=payment_units,
                        credit_units=amount_units,
                        effective_from=version.effective_from,
                        published_at=version.published_at,
                    )
                )
        except IntegrityError as exc:
            raise PackageVersionConflict(package_code) from exc
        return version

    def sellable_at(self, at: datetime) -> tuple[RechargePackageVersion, ...]:
        """选择每个充值包在指定时间最新生效的版本。"""
        with self._session_factory() as database:
            rows = database.execute(
                select(_package_versions)
                .where(_package_versions.c.effective_from <= at)
                .order_by(_package_versions.c.package_code, _package_versions.c.effective_from)
            ).mappings()
            current_by_code = {str(row["package_code"]): _package_from_row(row) for row in rows}
        return tuple(current_by_code[code] for code in sorted(current_by_code))

    def get_version(self, version_id: str) -> RechargePackageVersion:
        """按永久标识读取任意历史版本。"""
        with self._session_factory() as database:
            row = (
                database.execute(select(_package_versions).where(_package_versions.c.id == version_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UnknownRechargePackageVersion(version_id)
        return _package_from_row(row)

    def record_recharge(
        self,
        account_space_id: str,
        package_version_id: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """原子增加余额并记录由充值包快照确定的到账。"""
        payment_reference = validated_audit_reference(payment_reference)
        try:
            with self._session_factory.begin() as database:
                existing = _posting_by_reference(database, payment_reference)
                if existing is not None:
                    _require_matching_recharge(existing, account_space_id, package_version_id, payment_reference)
                    return existing
                package_row = (
                    database.execute(select(_package_versions).where(_package_versions.c.id == package_version_id))
                    .mappings()
                    .one_or_none()
                )
                if package_row is None:
                    raise UnknownRechargePackageVersion(package_version_id)
                account_row = (
                    database.execute(
                        select(_credit_accounts)
                        .where(_credit_accounts.c.account_space_id == account_space_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if account_row is None:
                    raise UnknownAccountSpace(account_space_id)
                delta_units = int(package_row["credit_units"])
                available_units = int(account_row["available_credit_units"]) + delta_units
                posting = CreditPosting(
                    posting_id=str(uuid4()),
                    account_space_id=account_space_id,
                    kind="recharge",
                    delta_available_credits=format_credits(delta_units),
                    available_credits_after=format_credits(available_units),
                    package_version_id=package_version_id,
                    reference=payment_reference,
                    reverses_posting_id=None,
                    reason=None,
                    occurred_at=occurred_at,
                    frozen_credits_after=format_credits(int(account_row["frozen_credit_units"])),
                )
                database.execute(
                    update(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == account_space_id)
                    .values(available_credit_units=available_units)
                )
                _insert_posting(database, posting, _next_sequence_number(database, account_space_id))
                return posting
        except IntegrityError:
            with self._session_factory() as database:
                existing = _posting_by_reference(database, payment_reference)
            if existing is None:
                raise
            _require_matching_recharge(existing, account_space_id, package_version_id, payment_reference)
            return existing

    def record_admin_grant(
        self,
        account_space_id: str,
        credits: str,
        *,
        grant_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """原子增加管理员指定额度并保存不可改写的审计原因。"""
        grant_reference = validated_audit_reference(grant_reference)
        reason = validated_audit_reason(reason)
        delta_units = credit_units(credits)
        try:
            with self._session_factory.begin() as database:
                existing = _posting_by_reference(database, grant_reference)
                if existing is not None:
                    _require_matching_admin_grant(
                        existing,
                        account_space_id,
                        delta_units,
                        reason,
                        grant_reference,
                    )
                    return existing
                account_row = (
                    database.execute(
                        select(_credit_accounts)
                        .where(_credit_accounts.c.account_space_id == account_space_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if account_row is None:
                    raise UnknownAccountSpace(account_space_id)
                available_units = int(account_row["available_credit_units"]) + delta_units
                posting = CreditPosting(
                    posting_id=str(uuid4()),
                    account_space_id=account_space_id,
                    kind="admin_grant",
                    delta_available_credits=format_credits(delta_units),
                    available_credits_after=format_credits(available_units),
                    package_version_id=None,
                    reference=grant_reference,
                    reverses_posting_id=None,
                    reason=reason,
                    occurred_at=occurred_at,
                    frozen_credits_after=format_credits(int(account_row["frozen_credit_units"])),
                )
                database.execute(
                    update(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == account_space_id)
                    .values(available_credit_units=available_units)
                )
                _insert_posting(database, posting, _next_sequence_number(database, account_space_id))
                return posting
        except IntegrityError:
            with self._session_factory() as database:
                existing = _posting_by_reference(database, grant_reference)
            if existing is None:
                raise
            _require_matching_admin_grant(existing, account_space_id, delta_units, reason, grant_reference)
            return existing

    def record_direct_recharge(
        self,
        account_space_id: str,
        credits: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """原子增加普通充值订单已固化的额度。"""
        payment_reference = validated_audit_reference(payment_reference)
        delta_units = credit_units(credits)
        try:
            with self._session_factory.begin() as database:
                existing = _posting_by_reference(database, payment_reference)
                if existing is not None:
                    _require_matching_direct_recharge(
                        existing, account_space_id, delta_units, payment_reference
                    )
                    return existing
                account_row = (
                    database.execute(
                        select(_credit_accounts)
                        .where(_credit_accounts.c.account_space_id == account_space_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if account_row is None:
                    raise UnknownAccountSpace(account_space_id)
                available_units = int(account_row["available_credit_units"]) + delta_units
                posting = CreditPosting(
                    posting_id=str(uuid4()),
                    account_space_id=account_space_id,
                    kind="recharge",
                    delta_available_credits=format_credits(delta_units),
                    available_credits_after=format_credits(available_units),
                    package_version_id=None,
                    reference=payment_reference,
                    reverses_posting_id=None,
                    reason=None,
                    occurred_at=occurred_at,
                    frozen_credits_after=format_credits(int(account_row["frozen_credit_units"])),
                )
                database.execute(
                    update(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == account_space_id)
                    .values(available_credit_units=available_units)
                )
                _insert_posting(database, posting, _next_sequence_number(database, account_space_id))
                return posting
        except IntegrityError:
            with self._session_factory() as database:
                existing = _posting_by_reference(database, payment_reference)
            if existing is None:
                raise
            _require_matching_direct_recharge(existing, account_space_id, delta_units, payment_reference)
            return existing

    def freeze(
        self,
        account_space_id: str,
        logical_model: str,
        output_spec: str,
        *,
        quantity: int,
        task_reference: str,
        occurred_at: datetime,
    ) -> CreditFreeze:
        """原子冻结按任务提交时模型价格计算的额度。"""
        task_reference = validated_audit_reference(task_reference)
        if quantity <= 0:
            raise ValueError("生成数量必须为正整数")
        price = self._model_prices.effective_at(logical_model, output_spec, occurred_at)
        unit_price_units = credit_units(price.credits_per_result)
        frozen_delta_units = unit_price_units * quantity
        with self._session_factory.begin() as database:
            existing_row = (
                database.execute(select(_credit_freezes).where(_credit_freezes.c.task_reference == task_reference))
                .mappings()
                .one_or_none()
            )
            if existing_row is not None:
                existing = _freeze_from_row(existing_row)
                if (
                    existing.account_space_id != account_space_id
                    or existing.model_price_version_id != price.version_id
                    or existing.quantity != quantity
                ):
                    raise ReferenceConflict(task_reference)
                return existing
            account_row = (
                database.execute(
                    select(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == account_space_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(account_space_id)
            available_units = int(account_row["available_credit_units"])
            frozen_units_before = int(account_row["frozen_credit_units"])
            if available_units < frozen_delta_units:
                raise InsufficientCredits(account_space_id)
            available_units -= frozen_delta_units
            frozen_units = frozen_units_before + frozen_delta_units
            freeze = CreditFreeze(
                freeze_id=str(uuid4()),
                account_space_id=account_space_id,
                task_reference=task_reference,
                model_price_version_id=price.version_id,
                logical_model=logical_model,
                output_spec=output_spec,
                quantity=quantity,
                unit_price=price.credits_per_result,
                frozen_credits=format_credits(frozen_delta_units),
                available_credits_after=format_credits(available_units),
                frozen_credits_after=format_credits(frozen_units),
                occurred_at=occurred_at,
            )
            database.execute(
                insert(_credit_freezes).values(
                    id=freeze.freeze_id,
                    account_space_id=account_space_id,
                    task_reference=task_reference,
                    model_price_version_id=price.version_id,
                    logical_model=logical_model,
                    output_spec=output_spec,
                    quantity=quantity,
                    unit_price_units=unit_price_units,
                    frozen_units=frozen_delta_units,
                    available_units_after=available_units,
                    frozen_units_after=frozen_units,
                    status="active",
                    occurred_at=occurred_at,
                )
            )
            posting = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=account_space_id,
                kind="freeze",
                delta_available_credits=format_credits(-frozen_delta_units),
                available_credits_after=format_credits(available_units),
                package_version_id=None,
                reference=task_reference,
                reverses_posting_id=None,
                reason=None,
                occurred_at=occurred_at,
                delta_frozen_credits=format_credits(frozen_delta_units),
                frozen_credits_after=format_credits(frozen_units),
                model_price_version_id=price.version_id,
                generation_reference=freeze.freeze_id,
            )
            database.execute(
                update(_credit_accounts)
                .where(_credit_accounts.c.account_space_id == account_space_id)
                .values(
                    available_credit_units=available_units,
                    frozen_credit_units=frozen_units,
                )
            )
            _insert_posting(database, posting, _next_sequence_number(database, account_space_id))
            return freeze

    def settle(
        self,
        freeze_id: str,
        *,
        delivered_quantity: int,
        settlement_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """结算成功数量并释放冻结余量。"""
        settlement_reference = validated_audit_reference(settlement_reference)
        with self._session_factory.begin() as database:
            existing = _posting_by_reference(database, settlement_reference)
            if existing is not None:
                if existing.kind != "settlement" or existing.generation_reference != freeze_id:
                    raise ReferenceConflict(settlement_reference)
                return existing
            freeze_row = (
                database.execute(select(_credit_freezes).where(_credit_freezes.c.id == freeze_id).with_for_update())
                .mappings()
                .one_or_none()
            )
            if freeze_row is None:
                raise UnknownCreditFreeze(freeze_id)
            if freeze_row["status"] != "active":
                raise CreditFreezeAlreadyFinalized(freeze_id)
            quantity = int(freeze_row["quantity"])
            if delivered_quantity < 0 or delivered_quantity > quantity:
                raise ValueError("实际成功数量超出额度冻结范围")
            account_row = (
                database.execute(
                    select(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == freeze_row["account_space_id"])
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(str(freeze_row["account_space_id"]))
            total_units = int(freeze_row["frozen_units"])
            consumed_units = int(freeze_row["unit_price_units"]) * delivered_quantity
            released_units = total_units - consumed_units
            available_units = int(account_row["available_credit_units"]) + released_units
            frozen_units = int(account_row["frozen_credit_units"]) - total_units
            settlement = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=str(freeze_row["account_space_id"]),
                kind="settlement",
                delta_available_credits=format_credits(released_units),
                available_credits_after=format_credits(available_units),
                package_version_id=None,
                reference=settlement_reference,
                reverses_posting_id=None,
                reason=None,
                occurred_at=occurred_at,
                delta_frozen_credits=format_credits(-total_units),
                frozen_credits_after=format_credits(frozen_units),
                model_price_version_id=str(freeze_row["model_price_version_id"]),
                generation_reference=freeze_id,
            )
            database.execute(update(_credit_freezes).where(_credit_freezes.c.id == freeze_id).values(status="settled"))
            database.execute(
                update(_credit_accounts)
                .where(_credit_accounts.c.account_space_id == freeze_row["account_space_id"])
                .values(available_credit_units=available_units, frozen_credit_units=frozen_units)
            )
            _insert_posting(database, settlement, _next_sequence_number(database, str(freeze_row["account_space_id"])))
            return settlement

    def release(
        self,
        freeze_id: str,
        *,
        release_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """失败或取消时原子释放全部冻结额度。"""
        release_reference = validated_audit_reference(release_reference)
        reason = validated_reversal_reason(reason)
        with self._session_factory.begin() as database:
            existing = _posting_by_reference(database, release_reference)
            if existing is not None:
                if (
                    existing.kind != "release"
                    or existing.generation_reference != freeze_id
                    or existing.reason != reason
                ):
                    raise ReferenceConflict(release_reference)
                return existing
            freeze_row = (
                database.execute(select(_credit_freezes).where(_credit_freezes.c.id == freeze_id).with_for_update())
                .mappings()
                .one_or_none()
            )
            if freeze_row is None:
                raise UnknownCreditFreeze(freeze_id)
            if freeze_row["status"] != "active":
                raise CreditFreezeAlreadyFinalized(freeze_id)
            account_row = (
                database.execute(
                    select(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == freeze_row["account_space_id"])
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(str(freeze_row["account_space_id"]))
            total_units = int(freeze_row["frozen_units"])
            available_units = int(account_row["available_credit_units"]) + total_units
            frozen_units = int(account_row["frozen_credit_units"]) - total_units
            release = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=str(freeze_row["account_space_id"]),
                kind="release",
                delta_available_credits=format_credits(total_units),
                available_credits_after=format_credits(available_units),
                package_version_id=None,
                reference=release_reference,
                reverses_posting_id=None,
                reason=reason,
                occurred_at=occurred_at,
                delta_frozen_credits=format_credits(-total_units),
                frozen_credits_after=format_credits(frozen_units),
                model_price_version_id=str(freeze_row["model_price_version_id"]),
                generation_reference=freeze_id,
            )
            database.execute(update(_credit_freezes).where(_credit_freezes.c.id == freeze_id).values(status="released"))
            database.execute(
                update(_credit_accounts)
                .where(_credit_accounts.c.account_space_id == freeze_row["account_space_id"])
                .values(available_credit_units=available_units, frozen_credit_units=frozen_units)
            )
            _insert_posting(database, release, _next_sequence_number(database, str(freeze_row["account_space_id"])))
            return release

    def reverse(
        self,
        posting_id: str,
        *,
        reversal_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """原子追加一次反向账务记录。"""
        reversal_reference = validated_audit_reference(reversal_reference)
        reason = validated_reversal_reason(reason)
        with self._session_factory.begin() as database:
            existing = _posting_by_reference(database, reversal_reference)
            if existing is not None:
                _require_matching_reversal(existing, posting_id, reason, reversal_reference)
                return existing
            original_row = (
                database.execute(select(_credit_postings).where(_credit_postings.c.id == posting_id))
                .mappings()
                .one_or_none()
            )
            if original_row is None:
                raise UnknownCreditPosting(posting_id)
            original = _posting_from_row(original_row)
            account_row = (
                database.execute(
                    select(_credit_accounts)
                    .where(_credit_accounts.c.account_space_id == original.account_space_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(original.account_space_id)
            existing = _posting_by_reference(database, reversal_reference)
            if existing is not None:
                _require_matching_reversal(existing, posting_id, reason, reversal_reference)
                return existing
            prior_reversal = database.scalar(
                select(_credit_postings.c.id).where(_credit_postings.c.reverses_posting_id == posting_id)
            )
            if prior_reversal is not None:
                raise PostingAlreadyReversed(posting_id)
            delta_units = -int(original_row["available_delta_units"])
            available_units = int(account_row["available_credit_units"]) + delta_units
            reversal = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=original.account_space_id,
                kind="reversal",
                delta_available_credits=format_credits(delta_units),
                available_credits_after=format_credits(available_units),
                package_version_id=original.package_version_id,
                reference=reversal_reference,
                reverses_posting_id=original.posting_id,
                reason=reason,
                occurred_at=occurred_at,
                frozen_credits_after=format_credits(int(account_row["frozen_credit_units"])),
            )
            database.execute(
                update(_credit_accounts)
                .where(_credit_accounts.c.account_space_id == original.account_space_id)
                .values(available_credit_units=available_units)
            )
            _insert_posting(database, reversal, _next_sequence_number(database, original.account_space_id))
            return reversal

    def statement(self, account_space_id: str) -> CreditStatement:
        """读取余额快照和顺序稳定的完整账务记录。"""
        with self._session_factory() as database:
            account_row = (
                database.execute(
                    select(_credit_accounts).where(_credit_accounts.c.account_space_id == account_space_id)
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(account_space_id)
            rows = database.execute(
                select(_credit_postings)
                .where(_credit_postings.c.account_space_id == account_space_id)
                .order_by(_credit_postings.c.sequence_number)
            ).mappings()
            entries = tuple(_posting_from_row(row) for row in rows)
        return CreditStatement(
            available_credits=format_credits(int(account_row["available_credit_units"])),
            frozen_credits=format_credits(int(account_row["frozen_credit_units"])),
            entries=entries,
        )

    def statement_page(self, account_space_id: str, *, page: int, page_size: int) -> CreditStatementPage:
        """读取最新优先的一页账务记录，并保持余额为当前快照。"""
        if page < 1 or page_size < 1:
            raise ValueError("页码和每页数量必须为正整数")
        with self._session_factory() as database:
            account_row = (
                database.execute(
                    select(_credit_accounts).where(_credit_accounts.c.account_space_id == account_space_id)
                )
                .mappings()
                .one_or_none()
            )
            if account_row is None:
                raise UnknownAccountSpace(account_space_id)
            total_entries = int(
                database.scalar(
                    select(func.count()).select_from(_credit_postings).where(
                        _credit_postings.c.account_space_id == account_space_id
                    )
                )
                or 0
            )
            total_pages = max(1, (total_entries + page_size - 1) // page_size)
            current_page = min(page, total_pages)
            rows = database.execute(
                select(_credit_postings)
                .where(_credit_postings.c.account_space_id == account_space_id)
                .order_by(_credit_postings.c.sequence_number.desc())
                .offset((current_page - 1) * page_size)
                .limit(page_size)
            ).mappings()
            entries = tuple(_posting_from_row(row) for row in rows)
        return CreditStatementPage(
            available_credits=format_credits(int(account_row["available_credit_units"])),
            frozen_credits=format_credits(int(account_row["frozen_credit_units"])),
            entries=entries,
            page=current_page,
            page_size=page_size,
            total_entries=total_entries,
            total_pages=total_pages,
        )


def _next_sequence_number(database: Session, account_space_id: str) -> int:
    current = database.scalar(
        select(func.coalesce(func.max(_credit_postings.c.sequence_number), 0)).where(
            _credit_postings.c.account_space_id == account_space_id
        )
    )
    if current is None:
        return 1
    return int(current) + 1


def _insert_posting(database: Session, posting: CreditPosting, sequence_number: int) -> None:
    database.execute(
        insert(_credit_postings).values(
            id=posting.posting_id,
            account_space_id=posting.account_space_id,
            sequence_number=sequence_number,
            kind=posting.kind,
            available_delta_units=signed_credit_units(posting.delta_available_credits),
            available_units_after=signed_credit_units(posting.available_credits_after),
            package_version_id=posting.package_version_id,
            reference=posting.reference,
            reverses_posting_id=posting.reverses_posting_id,
            reason=posting.reason,
            occurred_at=posting.occurred_at,
            frozen_delta_units=signed_credit_units(posting.delta_frozen_credits),
            frozen_units_after=signed_credit_units(posting.frozen_credits_after),
            model_price_version_id=posting.model_price_version_id,
            generation_reference=posting.generation_reference,
        )
    )


def _posting_by_reference(database: Session, reference: str) -> CreditPosting | None:
    row = (
        database.execute(select(_credit_postings).where(_credit_postings.c.reference == reference))
        .mappings()
        .one_or_none()
    )
    return None if row is None else _posting_from_row(row)


def _require_matching_recharge(
    existing: CreditPosting,
    account_space_id: str,
    package_version_id: str,
    reference: str,
) -> None:
    if (
        existing.kind != "recharge"
        or existing.account_space_id != account_space_id
        or existing.package_version_id != package_version_id
    ):
        raise ReferenceConflict(reference)


def _require_matching_admin_grant(
    existing: CreditPosting,
    account_space_id: str,
    delta_units: int,
    reason: str,
    reference: str,
) -> None:
    if (
        existing.kind != "admin_grant"
        or existing.account_space_id != account_space_id
        or signed_credit_units(existing.delta_available_credits) != delta_units
        or existing.reason != reason
    ):
        raise ReferenceConflict(reference)


def _require_matching_direct_recharge(
    existing: CreditPosting,
    account_space_id: str,
    delta_units: int,
    reference: str,
) -> None:
    if (
        existing.kind != "recharge"
        or existing.account_space_id != account_space_id
        or existing.package_version_id is not None
        or signed_credit_units(existing.delta_available_credits) != delta_units
    ):
        raise ReferenceConflict(reference)


def _require_matching_reversal(existing: CreditPosting, posting_id: str, reason: str, reference: str) -> None:
    if existing.kind != "reversal" or existing.reverses_posting_id != posting_id or existing.reason != reason:
        raise ReferenceConflict(reference)


def _package_from_row(row: RowMapping) -> RechargePackageVersion:
    return RechargePackageVersion(
        version_id=str(row["id"]),
        package_code=str(row["package_code"]),
        payment_cny=format_cny(int(row["payment_cny_units"])),
        credits=format_credits(int(row["credit_units"])),
        effective_from=_aware_datetime(row["effective_from"]),
        published_at=_aware_datetime(row["published_at"]),
    )


def _posting_from_row(row: RowMapping) -> CreditPosting:
    raw_kind = str(row["kind"])
    if raw_kind not in {"recharge", "admin_grant", "reversal", "freeze", "settlement", "release"}:
        raise RuntimeError(f"未知额度账务类型: {raw_kind}")
    kind = cast(
        Literal["recharge", "admin_grant", "reversal", "freeze", "settlement", "release"],
        raw_kind,
    )
    frozen_delta_units = row.get("frozen_delta_units", 0)
    frozen_units_after = row.get("frozen_units_after", 0)
    return CreditPosting(
        posting_id=str(row["id"]),
        account_space_id=str(row["account_space_id"]),
        kind=kind,
        delta_available_credits=format_credits(int(row["available_delta_units"])),
        available_credits_after=format_credits(int(row["available_units_after"])),
        package_version_id=None if row["package_version_id"] is None else str(row["package_version_id"]),
        reference=str(row["reference"]),
        reverses_posting_id=None if row["reverses_posting_id"] is None else str(row["reverses_posting_id"]),
        reason=None if row["reason"] is None else str(row["reason"]),
        occurred_at=_aware_datetime(row["occurred_at"]),
        delta_frozen_credits=format_credits(int(frozen_delta_units)),
        frozen_credits_after=format_credits(int(frozen_units_after)),
        model_price_version_id=(
            None if row.get("model_price_version_id") is None else str(row["model_price_version_id"])
        ),
        generation_reference=(None if row.get("generation_reference") is None else str(row["generation_reference"])),
    )


def _freeze_from_row(row: RowMapping) -> CreditFreeze:
    return CreditFreeze(
        freeze_id=str(row["id"]),
        account_space_id=str(row["account_space_id"]),
        task_reference=str(row["task_reference"]),
        model_price_version_id=str(row["model_price_version_id"]),
        logical_model=str(row["logical_model"]),
        output_spec=str(row["output_spec"]),
        quantity=int(row["quantity"]),
        unit_price=format_credits(int(row["unit_price_units"])),
        frozen_credits=format_credits(int(row["frozen_units"])),
        available_credits_after=format_credits(int(row["available_units_after"])),
        frozen_credits_after=format_credits(int(row["frozen_units_after"])),
        occurred_at=_aware_datetime(row["occurred_at"]),
    )


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("数据库日期时间类型无效")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value

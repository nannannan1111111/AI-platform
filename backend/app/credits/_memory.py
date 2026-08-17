"""充值包与额度账务 Interface 的内存 Adapter。"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from app.credits._amounts import cny_units, credit_units, format_cny, format_credits, signed_credit_units
from app.credits._pricing_memory import InMemoryModelPrices
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


class InMemoryCredits:
    """在单进程内保存充值包版本。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        account_space_ids: Iterable[str] = (),
        model_prices: ModelPrices | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._model_prices = model_prices or InMemoryModelPrices(clock=self._clock)
        self._versions_by_id: dict[str, RechargePackageVersion] = {}
        self._available_units = dict.fromkeys(account_space_ids, 0)
        self._frozen_units = dict.fromkeys(account_space_ids, 0)
        self._postings_by_account: dict[str, list[CreditPosting]] = {
            account_space_id: [] for account_space_id in self._available_units
        }
        self._postings_by_reference: dict[str, CreditPosting] = {}
        self._postings_by_id: dict[str, CreditPosting] = {}
        self._reversals_by_original_id: dict[str, CreditPosting] = {}
        self._freezes_by_id: dict[str, CreditFreeze] = {}
        self._freezes_by_task: dict[str, CreditFreeze] = {}
        self._finalized_freezes: dict[str, CreditPosting] = {}
        self._lock = Lock()

    def publish(
        self,
        package_code: str,
        *,
        payment_cny: str,
        credits: str,
        effective_from: datetime,
    ) -> RechargePackageVersion:
        """新增充值包版本，不改写已有版本。"""
        published_at = self._clock()
        version = RechargePackageVersion(
            version_id=str(uuid4()),
            package_code=package_code,
            payment_cny=format_cny(cny_units(payment_cny)),
            credits=format_credits(credit_units(credits)),
            effective_from=validated_effective_time(effective_from, published_at),
            published_at=published_at,
        )
        with self._lock:
            if any(
                existing.package_code == package_code and existing.effective_from == effective_from
                for existing in self._versions_by_id.values()
            ):
                raise PackageVersionConflict(package_code)
            self._versions_by_id[version.version_id] = version
        return version

    def sellable_at(self, at: datetime) -> tuple[RechargePackageVersion, ...]:
        """选择每个充值包在指定时间最新生效的版本。"""
        current_by_code: dict[str, RechargePackageVersion] = {}
        with self._lock:
            versions = tuple(self._versions_by_id.values())
        for version in versions:
            if version.effective_from > at:
                continue
            current = current_by_code.get(version.package_code)
            if current is None or current.effective_from < version.effective_from:
                current_by_code[version.package_code] = version
        return tuple(current_by_code[code] for code in sorted(current_by_code))

    def get_version(self, version_id: str) -> RechargePackageVersion:
        """读取任意历史版本。"""
        try:
            with self._lock:
                return self._versions_by_id[version_id]
        except KeyError as exc:
            raise UnknownRechargePackageVersion(version_id) from exc

    def record_recharge(
        self,
        account_space_id: str,
        package_version_id: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """按充值包快照增加可用额度并追加账务记录。"""
        payment_reference = validated_audit_reference(payment_reference)
        with self._lock:
            existing = self._postings_by_reference.get(payment_reference)
            if existing is not None:
                if (
                    existing.kind != "recharge"
                    or existing.account_space_id != account_space_id
                    or existing.package_version_id != package_version_id
                ):
                    raise ReferenceConflict(payment_reference)
                return existing
            if account_space_id not in self._available_units:
                raise UnknownAccountSpace(account_space_id)
            try:
                package = self._versions_by_id[package_version_id]
            except KeyError as exc:
                raise UnknownRechargePackageVersion(package_version_id) from exc
            delta_units = credit_units(package.credits)
            available_units = self._available_units[account_space_id] + delta_units
            posting = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=account_space_id,
                kind="recharge",
                delta_available_credits=format_credits(delta_units),
                available_credits_after=format_credits(available_units),
                package_version_id=package.version_id,
                reference=payment_reference,
                reverses_posting_id=None,
                reason=None,
                occurred_at=occurred_at,
                frozen_credits_after=format_credits(self._frozen_units[account_space_id]),
            )
            self._available_units[account_space_id] = available_units
            self._postings_by_account[account_space_id].append(posting)
            self._postings_by_reference[payment_reference] = posting
            self._postings_by_id[posting.posting_id] = posting
            return posting

    def record_admin_grant(
        self,
        account_space_id: str,
        credits: str,
        *,
        grant_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """原子增加管理员指定额度并追加可审计账务记录。"""
        grant_reference = validated_audit_reference(grant_reference)
        reason = validated_audit_reason(reason)
        delta_units = credit_units(credits)
        with self._lock:
            existing = self._postings_by_reference.get(grant_reference)
            if existing is not None:
                if (
                    existing.kind != "admin_grant"
                    or existing.account_space_id != account_space_id
                    or signed_credit_units(existing.delta_available_credits) != delta_units
                    or existing.reason != reason
                ):
                    raise ReferenceConflict(grant_reference)
                return existing
            if account_space_id not in self._available_units:
                raise UnknownAccountSpace(account_space_id)
            available_units = self._available_units[account_space_id] + delta_units
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
                frozen_credits_after=format_credits(self._frozen_units[account_space_id]),
            )
            self._available_units[account_space_id] = available_units
            self._postings_by_account[account_space_id].append(posting)
            self._postings_by_reference[grant_reference] = posting
            self._postings_by_id[posting.posting_id] = posting
            return posting

    def record_direct_recharge(
        self,
        account_space_id: str,
        credits: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """按普通充值订单固化额度增加余额。"""
        payment_reference = validated_audit_reference(payment_reference)
        delta_units = credit_units(credits)
        with self._lock:
            existing = self._postings_by_reference.get(payment_reference)
            if existing is not None:
                if (
                    existing.kind != "recharge"
                    or existing.account_space_id != account_space_id
                    or existing.package_version_id is not None
                    or signed_credit_units(existing.delta_available_credits) != delta_units
                ):
                    raise ReferenceConflict(payment_reference)
                return existing
            if account_space_id not in self._available_units:
                raise UnknownAccountSpace(account_space_id)
            available_units = self._available_units[account_space_id] + delta_units
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
                frozen_credits_after=format_credits(self._frozen_units[account_space_id]),
            )
            self._available_units[account_space_id] = available_units
            self._postings_by_account[account_space_id].append(posting)
            self._postings_by_reference[payment_reference] = posting
            self._postings_by_id[posting.posting_id] = posting
            return posting

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
        with self._lock:
            existing = self._freezes_by_task.get(task_reference)
            if existing is not None:
                if (
                    existing.account_space_id != account_space_id
                    or existing.model_price_version_id != price.version_id
                    or existing.quantity != quantity
                ):
                    raise ReferenceConflict(task_reference)
                return existing
            if account_space_id not in self._available_units:
                raise UnknownAccountSpace(account_space_id)
            if self._available_units[account_space_id] < frozen_delta_units:
                raise InsufficientCredits(account_space_id)
            available_units = self._available_units[account_space_id] - frozen_delta_units
            frozen_units = self._frozen_units[account_space_id] + frozen_delta_units
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
                generation_reference=task_reference,
            )
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
            self._available_units[account_space_id] = available_units
            self._frozen_units[account_space_id] = frozen_units
            self._freezes_by_id[freeze.freeze_id] = freeze
            self._freezes_by_task[task_reference] = freeze
            self._postings_by_account[account_space_id].append(posting)
            self._postings_by_reference[task_reference] = posting
            self._postings_by_id[posting.posting_id] = posting
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
        with self._lock:
            existing = self._postings_by_reference.get(settlement_reference)
            if existing is not None:
                if existing.kind != "settlement" or existing.generation_reference != freeze_id:
                    raise ReferenceConflict(settlement_reference)
                return existing
            try:
                freeze = self._freezes_by_id[freeze_id]
            except KeyError as exc:
                raise UnknownCreditFreeze(freeze_id) from exc
            if freeze_id in self._finalized_freezes:
                raise CreditFreezeAlreadyFinalized(freeze_id)
            if delivered_quantity < 0 or delivered_quantity > freeze.quantity:
                raise ValueError("实际成功数量超出额度冻结范围")
            total_units = credit_units(freeze.frozen_credits)
            consumed_units = credit_units(freeze.unit_price) * delivered_quantity
            released_units = total_units - consumed_units
            available_units = self._available_units[freeze.account_space_id] + released_units
            frozen_units = self._frozen_units[freeze.account_space_id] - total_units
            settlement = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=freeze.account_space_id,
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
                model_price_version_id=freeze.model_price_version_id,
                generation_reference=freeze_id,
            )
            self._available_units[freeze.account_space_id] = available_units
            self._frozen_units[freeze.account_space_id] = frozen_units
            self._finalized_freezes[freeze_id] = settlement
            self._postings_by_account[freeze.account_space_id].append(settlement)
            self._postings_by_reference[settlement_reference] = settlement
            self._postings_by_id[settlement.posting_id] = settlement
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
        with self._lock:
            existing = self._postings_by_reference.get(release_reference)
            if existing is not None:
                if (
                    existing.kind != "release"
                    or existing.generation_reference != freeze_id
                    or existing.reason != reason
                ):
                    raise ReferenceConflict(release_reference)
                return existing
            try:
                freeze = self._freezes_by_id[freeze_id]
            except KeyError as exc:
                raise UnknownCreditFreeze(freeze_id) from exc
            if freeze_id in self._finalized_freezes:
                raise CreditFreezeAlreadyFinalized(freeze_id)
            total_units = credit_units(freeze.frozen_credits)
            available_units = self._available_units[freeze.account_space_id] + total_units
            frozen_units = self._frozen_units[freeze.account_space_id] - total_units
            release = CreditPosting(
                posting_id=str(uuid4()),
                account_space_id=freeze.account_space_id,
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
                model_price_version_id=freeze.model_price_version_id,
                generation_reference=freeze_id,
            )
            self._available_units[freeze.account_space_id] = available_units
            self._frozen_units[freeze.account_space_id] = frozen_units
            self._finalized_freezes[freeze_id] = release
            self._postings_by_account[freeze.account_space_id].append(release)
            self._postings_by_reference[release_reference] = release
            self._postings_by_id[release.posting_id] = release
            return release

    def reverse(
        self,
        posting_id: str,
        *,
        reversal_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """追加一条与原充值金额相反的账务记录。"""
        reversal_reference = validated_audit_reference(reversal_reference)
        reason = validated_reversal_reason(reason)
        with self._lock:
            existing = self._postings_by_reference.get(reversal_reference)
            if existing is not None:
                if (
                    existing.kind != "reversal"
                    or existing.reverses_posting_id != posting_id
                    or existing.reason != reason
                ):
                    raise ReferenceConflict(reversal_reference)
                return existing
            try:
                original = self._postings_by_id[posting_id]
            except KeyError as exc:
                raise UnknownCreditPosting(posting_id) from exc
            if posting_id in self._reversals_by_original_id:
                raise PostingAlreadyReversed(posting_id)
            delta_units = -signed_credit_units(original.delta_available_credits)
            available_units = self._available_units[original.account_space_id] + delta_units
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
                frozen_credits_after=format_credits(self._frozen_units[original.account_space_id]),
            )
            self._available_units[original.account_space_id] = available_units
            self._postings_by_account[original.account_space_id].append(reversal)
            self._postings_by_reference[reversal_reference] = reversal
            self._postings_by_id[reversal.posting_id] = reversal
            self._reversals_by_original_id[original.posting_id] = reversal
            return reversal

    def statement(self, account_space_id: str) -> CreditStatement:
        """读取余额及按记账顺序排列的账务记录。"""
        with self._lock:
            try:
                available_units = self._available_units[account_space_id]
            except KeyError as exc:
                raise UnknownAccountSpace(account_space_id) from exc
            entries = tuple(self._postings_by_account[account_space_id])
        return CreditStatement(
            available_credits=format_credits(available_units),
            frozen_credits=format_credits(self._frozen_units[account_space_id]),
            entries=entries,
        )

    def statement_page(self, account_space_id: str, *, page: int, page_size: int) -> CreditStatementPage:
        """读取最新优先的一页账务记录。"""
        if page < 1 or page_size < 1:
            raise ValueError("页码和每页数量必须为正整数")
        with self._lock:
            try:
                available_units = self._available_units[account_space_id]
            except KeyError as exc:
                raise UnknownAccountSpace(account_space_id) from exc
            all_entries = self._postings_by_account[account_space_id]
            total_entries = len(all_entries)
            total_pages = max(1, (total_entries + page_size - 1) // page_size)
            current_page = min(page, total_pages)
            offset = (current_page - 1) * page_size
            entries = tuple(reversed(all_entries))[offset : offset + page_size]
            frozen_units = self._frozen_units[account_space_id]
        return CreditStatementPage(
            available_credits=format_credits(available_units),
            frozen_credits=format_credits(frozen_units),
            entries=entries,
            page=current_page,
            page_size=page_size,
            total_entries=total_entries,
            total_pages=total_pages,
        )

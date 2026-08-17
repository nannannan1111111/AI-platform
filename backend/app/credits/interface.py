"""充值包与额度账务 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.credits.models import (
    CreditFreeze,
    CreditPosting,
    CreditStatement,
    CreditStatementPage,
    ModelPriceVersion,
    RechargePackageVersion,
)


class ModelPrices(Protocol):
    """发布和读取不可改写的模型价格版本。"""

    def publish(
        self,
        logical_model: str,
        output_spec: str,
        *,
        credits_per_result: str,
        effective_from: datetime,
        max_reference_images: int = 3,
    ) -> ModelPriceVersion:
        """发布一个未来或立即生效的新价格版本。"""

    def effective_at(self, logical_model: str, output_spec: str, at: datetime) -> ModelPriceVersion:
        """返回任务提交时该模型规格的生效价格。"""

    def catalog_at(self, at: datetime) -> tuple[ModelPriceVersion, ...]:
        """返回指定时刻每个模型规格的当前生效价格版本。"""

    def get_version(self, version_id: str) -> ModelPriceVersion:
        """按永久版本标识读取历史价格。"""

    def delete(self, version_id: str, deleted_at: datetime) -> None:
        """从当前目录退役该逻辑模型规格的全部价格，同时保留历史引用。"""


class RechargePackages(Protocol):
    """发布和读取不可改写的充值包版本。"""

    def publish(
        self,
        package_code: str,
        *,
        payment_cny: str,
        credits: str,
        effective_from: datetime,
    ) -> RechargePackageVersion:
        """发布一个未来或立即生效的新版本；重复生效时间会失败。"""

    def sellable_at(self, at: datetime) -> tuple[RechargePackageVersion, ...]:
        """按充值包代码排序，返回指定时间每个充值包的最新生效版本。"""

    def get_version(self, version_id: str) -> RechargePackageVersion:
        """按永久版本标识读取历史充值包。"""


class CreditAccounting(Protocol):
    """记录充值、冲销并读取额度账务记录。"""

    def record_recharge(
        self,
        account_space_id: str,
        package_version_id: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """按充值包版本记录一次到账；相同支付引用和参数可安全重放。"""

    def record_direct_recharge(
        self,
        account_space_id: str,
        credits: str,
        *,
        payment_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """按普通充值订单固化额度记录到账；不依赖充值包版本。"""

    def record_admin_grant(
        self,
        account_space_id: str,
        credits: str,
        *,
        grant_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """记录管理员人工增加额度；相同引用和参数可安全重放。"""

    def reverse(
        self,
        posting_id: str,
        *,
        reversal_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """为已有记录追加一次反向记录；每条记录最多有一条直接反向记录。"""

    def statement(self, account_space_id: str) -> CreditStatement:
        """返回当前额度与按记账顺序排列且不可改写的完整账务记录。"""

    def statement_page(self, account_space_id: str, *, page: int, page_size: int) -> CreditStatementPage:
        """返回当前额度与一页从新到旧排列的账务记录。"""


class GenerationCredits(Protocol):
    """冻结、结算和释放生成任务额度。"""

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
        """按提交时间的模型价格冻结生成任务预计额度。"""

    def settle(
        self,
        freeze_id: str,
        *,
        delivered_quantity: int,
        settlement_reference: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """按实际成功数量结算并释放未使用冻结额度。"""

    def release(
        self,
        freeze_id: str,
        *,
        release_reference: str,
        reason: str,
        occurred_at: datetime,
    ) -> CreditPosting:
        """失败或取消时释放全部冻结额度。"""

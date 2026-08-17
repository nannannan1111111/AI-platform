"""RunningHub 能力目录 Module 的公开 Interface。"""

from datetime import datetime
from typing import Protocol

from app.runninghub_capabilities.models import (
    PublicRunningHubCapability,
    RunningHubCapability,
    RunningHubCapabilityPublication,
    RunningHubCapabilityUpdate,
    RunningHubInputSchemaPublication,
    RunningHubInputSchemaVersion,
    RunningHubUserPricePublication,
    RunningHubUserPriceVersion,
)


class RunningHubCapabilities(Protocol):
    """发布管理员能力并提供去敏用户目录。"""

    def publish(self, command: RunningHubCapabilityPublication) -> RunningHubCapability:
        """发布一个带内部工作流绑定的能力。"""

    def list_for_administration(self) -> tuple[RunningHubCapability, ...]:
        """返回包含内部工作流绑定的管理员目录。"""

    def update(self, command: RunningHubCapabilityUpdate) -> RunningHubCapability:
        """修改能力字段或可用状态，但不改变公开身份。"""

    def publish_input_schema(self, command: RunningHubInputSchemaPublication) -> RunningHubInputSchemaVersion:
        """发布一个立即成为当前版本的不可改写输入 schema。"""

    def input_schema_versions(self, capability_id: str) -> tuple[RunningHubInputSchemaVersion, ...]:
        """按版本号返回指定能力的输入 schema 历史。"""

    def publish_user_price(self, command: RunningHubUserPricePublication) -> RunningHubUserPriceVersion:
        """发布一个未来或立即生效的不可改写用户价格版本。"""

    def user_price_versions(self, capability_id: str) -> tuple[RunningHubUserPriceVersion, ...]:
        """按版本号返回指定能力的用户价格历史。"""

    def user_price_at(self, capability_id: str, at: datetime) -> RunningHubUserPriceVersion:
        """返回指定时刻该能力的生效用户价格。"""

    def catalog(self) -> tuple[PublicRunningHubCapability, ...]:
        """返回包含停用项且不泄露内部绑定的用户目录。"""

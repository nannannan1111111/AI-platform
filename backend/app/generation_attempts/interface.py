"""生成尝试 Module 的公开 Interface。"""

from typing import Protocol

from app.generation_attempts.models import (
    GenerationAttempt,
    GenerationAttemptPreparation,
    GenerationAttemptTransition,
)


class GenerationAttempts(Protocol):
    """在任何上游提交前持久化稳定的生成尝试身份。"""

    def prepare(self, preparation: GenerationAttemptPreparation) -> GenerationAttempt:
        """幂等预备当前尝试；最新尝试明确失败时预备下一次尝试。"""

    def for_task(self, account_space_id: str, task_id: str) -> tuple[GenerationAttempt, ...]:
        """按序读取账户空间拥有的生成任务尝试。"""

    def transition(
        self,
        account_space_id: str,
        attempt_id: str,
        event: GenerationAttemptTransition,
    ) -> GenerationAttempt:
        """幂等记录生成尝试的 Provider 提交阶段事实。"""


class GenerationAttemptSubmissions(Protocol):
    """使用已持久化任务身份提交或安全重入当前生成尝试。"""

    def submit(self, account_space_id: str, task_id: str) -> GenerationAttempt:
        """提交当前生成尝试，并返回不暴露给用户的内部尝试记录。"""


class GenerationAttemptReconciliations(Protocol):
    """核实状态未知的原生成尝试，不创建新的上游生成。"""

    def reconcile(self, account_space_id: str, task_id: str) -> GenerationAttempt:
        """核实当前生成尝试，并返回不暴露给用户的内部尝试记录。"""

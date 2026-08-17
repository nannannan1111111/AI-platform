"""PaymentMethods Interface 的内存 Adapter。"""

from collections.abc import Iterable

from app.payments.models import PaymentMethod


class InMemoryPaymentMethods:
    """以内存配置提供支付途径目录。"""

    def __init__(self, methods: Iterable[PaymentMethod] = ()) -> None:
        self._methods = tuple(methods)

    def available(self) -> tuple[PaymentMethod, ...]:
        """返回部署时配置的支付途径显示信息。"""
        return self._methods

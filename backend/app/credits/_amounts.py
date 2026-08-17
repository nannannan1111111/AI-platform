"""人民币与额度的整数精度转换。"""

from decimal import Decimal, InvalidOperation

from app.credits.models import InvalidAmount

CNY_SCALE = 100
CREDIT_SCALE = 10_000


def cny_units(value: str) -> int:
    """把人民币字符串转换为分。"""
    return _positive_units(value, CNY_SCALE)


def credit_units(value: str) -> int:
    """把额度字符串转换为万分之一额度。"""
    return _positive_units(value, CREDIT_SCALE)


def signed_credit_units(value: str) -> int:
    """把可正、可负或为零的额度字符串转换为整数单位。"""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidAmount(value) from exc
    scaled = amount * CREDIT_SCALE
    if not amount.is_finite() or scaled != scaled.to_integral_value():
        raise InvalidAmount(value)
    return int(scaled)


def format_cny(units: int) -> str:
    """把分格式化为两位小数人民币。"""
    return f"{Decimal(units) / CNY_SCALE:.2f}"


def format_credits(units: int) -> str:
    """把整数单位格式化为四位小数额度。"""
    return f"{Decimal(units) / CREDIT_SCALE:.4f}"


def _positive_units(value: str, scale: int) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidAmount(value) from exc
    scaled = amount * scale
    if not amount.is_finite() or amount <= 0 or scaled != scaled.to_integral_value():
        raise InvalidAmount(value)
    return int(scaled)

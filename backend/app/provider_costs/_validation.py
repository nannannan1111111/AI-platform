"""Provider 成本版本的内部校验。"""

import re
from datetime import datetime

from app.provider_costs.models import InvalidProviderCostRate

_CURRENCY = re.compile(r"[A-Za-z]{3}").fullmatch


def required(value: str, field: str, *, maximum: int) -> str:
    """返回去除首尾空白后的必填短文本。"""
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise InvalidProviderCostRate(f"{field}无效")
    return normalized


def currency(value: str) -> str:
    """返回规范化的大写三字母 Provider 计费币种。"""
    normalized = value.strip()
    if _CURRENCY(normalized) is None:
        raise InvalidProviderCostRate("Provider 计费币种无效")
    return normalized.upper()


def cost_micros(value: int) -> int:
    """返回非负的每张微单位成本。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidProviderCostRate("Provider 单张成本无效")
    return value


def cost_cents(value: int) -> int:
    """返回可精确换算为微单位的非负整数分成本。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidProviderCostRate("Provider 单张成本无效")
    return value


def effective_time(value: datetime, published_at: datetime) -> datetime:
    """只允许带时区且不早于发布时间的生效时间。"""
    if value.tzinfo is None or value.utcoffset() is None or value < published_at:
        raise InvalidProviderCostRate("Provider 成本生效时间无效")
    return value

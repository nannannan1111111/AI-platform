"""充值包与额度账务公开输入校验。"""

from datetime import datetime

from app.credits.models import InvalidAuditReference, InvalidEffectiveTime, InvalidReversalReason


def validated_effective_time(effective_from: datetime, published_at: datetime) -> datetime:
    """要求发布时间和生效时间带时区，且禁止回溯生效。"""
    if published_at.tzinfo is None or effective_from.tzinfo is None or effective_from < published_at:
        raise InvalidEffectiveTime
    return effective_from


def validated_audit_reference(reference: str) -> str:
    """要求账务外部引用非空且可完整保存。"""
    if not reference.strip() or len(reference) > 255:
        raise InvalidAuditReference(reference)
    return reference


def validated_reversal_reason(reason: str) -> str:
    """要求冲销原因非空且可完整保存。"""
    if not reason.strip() or len(reason) > 255:
        raise InvalidReversalReason(reason)
    return reason


def validated_audit_reason(reason: str) -> str:
    """要求管理员账务原因非空且可完整保存。"""
    if not reason.strip() or len(reason) > 255:
        raise InvalidReversalReason(reason)
    return reason.strip()

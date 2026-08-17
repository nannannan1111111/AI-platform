"""账户输入规范化与校验。"""

from email_validator import EmailNotValidError, validate_email

from app.accounts.models import InvalidEmail, WeakPassword


def registration_email(value: str) -> str:
    """返回可用于唯一身份的规范化邮箱。"""
    try:
        normalized = validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise InvalidEmail from exc
    return normalized.casefold()


def registration_password(value: str) -> str:
    """要求注册密码至少包含十二个字符。"""
    if len(value) < 12:
        raise WeakPassword
    return value

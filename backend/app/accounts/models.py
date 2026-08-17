"""账户公开结果模型。"""

from dataclasses import dataclass
from datetime import datetime


class EmailAlreadyRegistered(ValueError):
    """邮箱已经对应一个用户。"""


class InvalidCredentials(ValueError):
    """邮箱或密码无法通过认证。"""


class InvalidEmail(ValueError):
    """邮箱不能作为有效登录身份。"""


class WeakPassword(ValueError):
    """密码未达到注册安全要求。"""


class InvalidSession(ValueError):
    """访问令牌没有对应的有效会话。"""


class InvalidEmailVerification(ValueError):
    """邮箱验证令牌未知、已过期或已经使用。"""


class EmailVerificationUnavailable(RuntimeError):
    """当前部署没有可用的邮箱验证投递途径。"""


@dataclass(frozen=True, slots=True)
class Registration:
    """一次成功注册产生的用户与个人账户空间。"""

    user_id: str
    account_space_id: str
    email: str
    available_credits: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """一次邮箱密码登录创建的访问会话。"""

    user_id: str
    account_space_id: str
    email: str
    access_token: str


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """当前访问会话对应的用户。"""

    user_id: str
    account_space_id: str
    email: str
    email_verified: bool


@dataclass(frozen=True, slots=True)
class CreditBalance:
    """个人账户空间的用户可见额度余额。"""

    available_credits: str
    frozen_credits: str


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    """管理员可见的注册用户身份与个人账户空间投影。"""

    user_id: str
    account_space_id: str
    email: str
    email_verified: bool
    registered_at: datetime

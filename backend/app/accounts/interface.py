"""账户 Module 的公开 Interface。"""

from typing import Protocol

from app.accounts.models import AuthenticatedSession, CreditBalance, CurrentUser, RegisteredUser, Registration


class AccountAccess(Protocol):
    """提供注册、登录和当前个人账户查询。"""

    def register(self, email: str, password: str) -> Registration:
        """创建邮箱身份及一对一的个人账户空间。"""

    def login(self, email: str, password: str) -> AuthenticatedSession:
        """校验邮箱密码并创建访问会话。"""

    def current_user(self, access_token: str) -> CurrentUser:
        """返回访问令牌对应的用户。"""

    def logout(self, access_token: str) -> None:
        """撤销当前访问令牌。"""

    def verify_email(self, token: str) -> None:
        """使用一次性令牌确认用户邮箱。"""

    def request_email_verification(self, access_token: str) -> None:
        """为当前未验证用户重新签发并投递一次性验证令牌。"""

    def change_password(self, access_token: str, current_password: str, new_password: str) -> None:
        """校验当前密码、更新密码并撤销该用户的全部登录会话。"""

    def request_password_reset(self, email: str) -> None:
        """若邮箱存在则替换并投递短期一次性密码重置令牌。"""

    def reset_password(self, token: str, new_password: str) -> None:
        """消费一次性令牌、更新密码并撤销该用户的全部登录会话。"""

    def credit_balance(self, access_token: str) -> CreditBalance:
        """返回访问令牌对应个人账户空间的额度。"""


class EmailVerificationDelivery(Protocol):
    """把原始账户邮件令牌交给外部邮件系统的 Interface。"""

    def send_verification(self, email: str, token: str) -> None:
        """向规范化邮箱发送一次验证令牌。"""

    def send_password_reset(self, email: str, token: str) -> None:
        """向规范化邮箱发送一次密码重置令牌。"""


class AccountDirectory(Protocol):
    """提供管理员账户目录读取，不暴露认证秘密。"""

    def list_registered_users(self) -> tuple[RegisteredUser, ...]:
        """按邮箱返回所有注册用户及其个人账户空间。"""

    def registered_user(self, user_id: str) -> RegisteredUser:
        """按用户标识返回注册用户。"""

    def registered_user_by_email(self, email: str) -> RegisteredUser:
        """按规范化邮箱返回注册用户。"""

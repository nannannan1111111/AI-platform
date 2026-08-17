"""通过标准 SMTP 投递账户邮箱验证链接。"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import quote


class EmailDeliveryFailed(RuntimeError):
    """账户邮件没有被 SMTP 服务接受。"""


@dataclass(frozen=True, slots=True)
class SmtpEmailVerificationDelivery:
    """使用 SMTP、STARTTLS 或隐式 TLS 发送验证链接。"""

    host: str
    port: int
    sender: str
    public_base_url: str
    username: str = ""
    password: str = ""
    security: str = "starttls"
    timeout_seconds: float = 10.0

    def send_verification(self, email: str, token: str) -> None:
        """发送不在日志或 API 响应中出现的一次性验证链接。"""
        verification_url = f"{self.public_base_url.rstrip('/')}/verify-email#token={quote(token, safe='')}"
        message = EmailMessage()
        message["Subject"] = "验证您的乐云工坊邮箱"
        message["From"] = self.sender
        message["To"] = email
        message.set_content(
            "欢迎使用乐云工坊。\n\n"
            f"请在 24 小时内打开以下链接完成邮箱验证：\n{verification_url}\n\n"
            "如果这不是您的操作，请忽略这封邮件。"
        )
        message.add_alternative(
            "<p>欢迎使用乐云工坊。</p>"
            f'<p><a href="{verification_url}">验证邮箱地址</a></p>'
            "<p>此链接将在 24 小时后失效。如果这不是您的操作，请忽略这封邮件。</p>",
            subtype="html",
        )

        self._send(email, message)

    def send_password_reset(self, email: str, token: str) -> None:
        """发送只含 URL fragment、短期有效且不可重放的密码重置链接。"""
        reset_url = f"{self.public_base_url.rstrip('/')}/reset-password#token={quote(token, safe='')}"
        message = EmailMessage()
        message["Subject"] = "重置您的乐云工坊密码"
        message["From"] = self.sender
        message["To"] = email
        message.set_content(
            "我们收到了您的乐云工坊密码重置请求。\n\n"
            f"请在 30 分钟内打开以下链接设置新密码：\n{reset_url}\n\n"
            "链接只能使用一次。成功重置后，所有设备上的登录会话都会失效。\n"
            "如果这不是您的操作，请忽略这封邮件。"
        )
        message.add_alternative(
            "<p>我们收到了您的乐云工坊密码重置请求。</p>"
            f'<p><a href="{reset_url}">设置新密码</a></p>'
            "<p>此链接将在 30 分钟后失效且只能使用一次。成功重置后，所有设备上的登录会话都会失效。</p>"
            "<p>如果这不是您的操作，请忽略这封邮件。</p>",
            subtype="html",
        )

        self._send(email, message)

    def _send(self, email: str, message: EmailMessage) -> None:
        """通过所选 SMTP 安全模式发送一封已构造的账户邮件。"""
        try:
            if self.security == "ssl":
                with smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    timeout=self.timeout_seconds,
                    context=ssl.create_default_context(),
                ) as smtp:
                    self._authenticate_and_send(smtp, email, message)
                return
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as smtp:
                smtp.ehlo()
                if self.security == "starttls":
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                self._authenticate_and_send(smtp, email, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryFailed("account email delivery failed") from exc

    def _authenticate_and_send(self, smtp: smtplib.SMTP, recipient: str, message: EmailMessage) -> None:
        if self.username:
            smtp.login(self.username, self.password)
        smtp.send_message(message, from_addr=self.sender, to_addrs=[recipient])

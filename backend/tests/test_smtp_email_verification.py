from email.message import EmailMessage

from app.accounts import SmtpEmailVerificationDelivery


class _RecordingSmtp:
    messages: list[EmailMessage] = []

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        assert (host, port, timeout) == ("smtp.example.com", 587, 4.0)

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def ehlo(self) -> None:
        pass

    def starttls(self, *, context: object) -> None:
        assert context is not None

    def login(self, username: str, password: str) -> None:
        assert (username, password) == ("mailer", "secret")

    def send_message(self, message: EmailMessage, *, from_addr: str, to_addrs: list[str]) -> None:
        assert from_addr == "noreply@example.com"
        assert to_addrs == ["artist@example.com"]
        self.messages.append(message)


def test_smtp_delivery_sends_a_public_single_use_verification_link(monkeypatch) -> None:
    _RecordingSmtp.messages = []
    monkeypatch.setattr("app.accounts.smtp.smtplib.SMTP", _RecordingSmtp)
    delivery = SmtpEmailVerificationDelivery(
        host="smtp.example.com",
        port=587,
        sender="noreply@example.com",
        public_base_url="https://studio.example.com",
        username="mailer",
        password="secret",
        timeout_seconds=4.0,
    )

    delivery.send_verification("artist@example.com", "token with / unsafe?")

    message = _RecordingSmtp.messages[0]
    assert message["To"] == "artist@example.com"
    assert message["Subject"] == "验证您的乐云工坊邮箱"
    plain_body = message.get_body(preferencelist=("plain",))
    assert plain_body is not None
    assert "https://studio.example.com/verify-email#token=token%20with%20%2F%20unsafe%3F" in plain_body.get_content()

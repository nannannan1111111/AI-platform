from datetime import UTC, datetime, timedelta

from fastapi import Request
from fastapi.testclient import TestClient

from app.accounts import EmailDeliveryFailed, InMemoryAccountAccess
from app.auth_abuse import (
    AuthAbusePolicies,
    AuthAction,
    ClientIpResolver,
    InMemoryAuthAbuseProtection,
    RateLimitBackendUnavailable,
    RateLimitPolicy,
    RateLimitSubject,
)
from app.http import create_app


class _RecordingVerificationDelivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))


class _FailingVerificationDelivery:
    def send_verification(self, email: str, token: str) -> None:
        raise EmailDeliveryFailed("SMTP unavailable")


class _UnavailableAuthAbuseProtection:
    def consume(self, action: AuthAction, subjects: tuple[RateLimitSubject, ...]) -> object:
        raise RateLimitBackendUnavailable

    def reset(self, action: AuthAction, scope: str, subject_value: str) -> None:
        raise RateLimitBackendUnavailable


def _policies(*, login_ip: int = 10, login_email: int = 5) -> AuthAbusePolicies:
    return AuthAbusePolicies(
        login_ip=RateLimitPolicy(login_ip, timedelta(minutes=10)),
        login_email=RateLimitPolicy(login_email, timedelta(minutes=10)),
        register_ip=RateLimitPolicy(5, timedelta(hours=1)),
        email_verification_account=RateLimitPolicy(3, timedelta(hours=1)),
    )


def _request(client: str, forwarded_for: str | None = None) -> Request:
    headers = [] if forwarded_for is None else [(b"x-forwarded-for", forwarded_for.encode())]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (client, 12345),
            "server": ("example.com", 443),
        }
    )


def test_fixed_window_blocks_after_limit_and_reopens_at_boundary() -> None:
    current = [datetime(2026, 8, 17, 12, 0, 5, tzinfo=UTC)]
    protection = InMemoryAuthAbuseProtection(clock=lambda: current[0])
    subject = (RateLimitSubject("email", "artist@example.com", RateLimitPolicy(2, timedelta(seconds=10))),)

    assert protection.consume(AuthAction.LOGIN, subject).allowed is True
    assert protection.consume(AuthAction.LOGIN, subject).allowed is True
    denied = protection.consume(AuthAction.LOGIN, subject)

    assert denied.allowed is False
    assert denied.retry_after_seconds == 5
    assert denied.blocked_scopes == ("email",)
    current[0] += timedelta(seconds=5)
    assert protection.consume(AuthAction.LOGIN, subject).allowed is True


def test_reset_clears_a_login_subject_before_the_window_expires() -> None:
    protection = InMemoryAuthAbuseProtection()
    subject = (RateLimitSubject("email", "artist@example.com", RateLimitPolicy(1, timedelta(minutes=10))),)
    assert protection.consume(AuthAction.LOGIN, subject).allowed is True
    assert protection.consume(AuthAction.LOGIN, subject).allowed is False

    protection.reset(AuthAction.LOGIN, "email", "artist@example.com")

    assert protection.consume(AuthAction.LOGIN, subject).allowed is True


def test_client_ip_ignores_spoofed_forwarding_from_untrusted_peer() -> None:
    resolver = ClientIpResolver(("10.0.0.0/8",))

    assert resolver(_request("203.0.113.8", "198.51.100.9")) == "203.0.113.8"
    assert resolver(_request("10.0.0.2", "198.51.100.9, 10.0.0.3")) == "198.51.100.9"
    assert resolver(_request("10.0.0.2", "not-an-ip")) == "10.0.0.2"


def test_client_ip_canonicalizes_ipv6_through_a_trusted_proxy() -> None:
    resolver = ClientIpResolver(("2001:db8:1::/48",))

    assert resolver(_request("2001:db8:1::2", "2001:db8:2:0:0:0:0:9")) == "2001:db8:2::9"


def test_login_returns_429_with_retry_after_after_email_threshold() -> None:
    accounts = InMemoryAccountAccess()
    protection = InMemoryAuthAbuseProtection(clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC))
    client = TestClient(create_app(accounts, auth_abuse_protection=protection, auth_abuse_policies=_policies()))
    credentials = {"email": "missing@example.com", "password": "wrong-password-value"}

    assert [client.post("/api/v1/auth/login", json=credentials).status_code for _ in range(5)] == [401] * 5
    denied = client.post("/api/v1/auth/login", json=credentials)

    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "600"
    assert denied.json() == {"detail": "请求过于频繁，请稍后重试"}


def test_successful_login_clears_prior_email_failures() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    protection = InMemoryAuthAbuseProtection(clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC))
    client = TestClient(
        create_app(
            accounts,
            auth_abuse_protection=protection,
            auth_abuse_policies=_policies(login_ip=20, login_email=3),
        )
    )
    wrong = {"email": "artist@example.com", "password": "wrong-password-value"}
    correct = {"email": "artist@example.com", "password": "a-correct-horse-battery-staple"}

    assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert client.post("/api/v1/auth/login", json=correct).status_code == 200
    assert [client.post("/api/v1/auth/login", json=wrong).status_code for _ in range(3)] == [401] * 3
    assert client.post("/api/v1/auth/login", json=wrong).status_code == 429


def test_registration_is_ip_limited_and_does_not_reveal_existing_email() -> None:
    accounts = InMemoryAccountAccess()
    protection = InMemoryAuthAbuseProtection(clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC))
    client = TestClient(create_app(accounts, auth_abuse_protection=protection, auth_abuse_policies=_policies()))
    password = "a-correct-horse-battery-staple"

    accepted = client.post(
        "/api/v1/auth/register",
        json={"email": "first@example.com", "password": password},
    )
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": "first@example.com", "password": password},
    )
    for index in range(3):
        response = client.post(
            "/api/v1/auth/register",
            json={"email": f"artist-{index}@example.com", "password": password},
        )
        assert response.status_code == 202

    denied = client.post(
        "/api/v1/auth/register",
        json={"email": "blocked@example.com", "password": password},
    )

    assert accepted.status_code == duplicate.status_code == 202
    assert accepted.json() == duplicate.json()
    delivery_failure = TestClient(
        create_app(InMemoryAccountAccess(verification_delivery=_FailingVerificationDelivery()))
    ).post(
        "/api/v1/auth/register",
        json={"email": "delivery-failure@example.com", "password": password},
    )
    assert delivery_failure.status_code == accepted.status_code
    assert delivery_failure.json() == accepted.json()
    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "3600"


def test_email_verification_resend_is_limited_per_account() -> None:
    delivery = _RecordingVerificationDelivery()
    accounts = InMemoryAccountAccess(verification_delivery=delivery)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    client = TestClient(
        create_app(
            accounts,
            auth_abuse_protection=InMemoryAuthAbuseProtection(clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC)),
            auth_abuse_policies=_policies(),
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}

    assert [client.post("/api/v1/auth/email-verification", headers=headers).status_code for _ in range(3)] == [204] * 3
    denied = client.post("/api/v1/auth/email-verification", headers=headers)

    assert denied.status_code == 429
    assert denied.headers["Retry-After"] == "3600"
    assert len(delivery.messages) == 4


def test_authentication_fails_closed_when_shared_limiter_is_unavailable() -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            auth_abuse_protection=_UnavailableAuthAbuseProtection(),  # type: ignore[arg-type]
        )
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "wrong-password-value"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证保护服务暂时不可用，请稍后重试"}

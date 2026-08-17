from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app


class _RecordingEmailVerificationDelivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))


def test_register_login_current_user_and_balance_flow() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    registration_response = client.post(
        "/api/v1/auth/register",
        json={"email": "Artist@Example.COM", "password": "a-correct-horse-battery-staple"},
    )
    assert registration_response.status_code == 201
    registration = registration_response.json()
    assert registration["email"] == "artist@example.com"

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    current_user_response = client.get("/api/v1/auth/me", headers=headers)
    assert current_user_response.status_code == 200
    assert current_user_response.json() == {
        "user_id": registration["user_id"],
        "account_space_id": registration["account_space_id"],
        "email": "artist@example.com",
        "email_verified": False,
    }

    balance_response = client.get("/api/v1/credits/balance", headers=headers)
    assert balance_response.status_code == 200
    assert balance_response.json() == {
        "available_credits": "0.0000",
        "frozen_credits": "0.0000",
    }


def test_registration_reports_invalid_email_without_server_error() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    response = client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "a-correct-horse-battery-staple"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "邮箱格式无效"}


def test_logout_invalidates_the_bearer_token() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    client.post(
        "/api/v1/auth/register",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    logout_response = client.post("/api/v1/auth/logout", headers=headers)

    assert logout_response.status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_email_verification_token_is_delivered_out_of_band_and_updates_current_user() -> None:
    delivery = _RecordingEmailVerificationDelivery()
    client = TestClient(create_app(InMemoryAccountAccess(verification_delivery=delivery)))
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    )
    token = delivery.messages[0][1]

    assert "verification_token" not in registration.json()
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 204

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).json()["email_verified"] is True


def test_authenticated_user_can_resend_verification_email() -> None:
    delivery = _RecordingEmailVerificationDelivery()
    client = TestClient(create_app(InMemoryAccountAccess(verification_delivery=delivery)))
    client.post(
        "/api/v1/auth/register",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    ).json()

    response = client.post(
        "/api/v1/auth/email-verification",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )

    assert response.status_code == 204
    assert len(delivery.messages) == 2


def test_authenticated_user_changes_password_and_is_required_to_log_in_again() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    client.post(
        "/api/v1/auth/register",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-correct-horse-battery-staple"},
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "a-correct-horse-battery-staple", "new_password": "a-new-secure-password-value"},
    )

    assert response.status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"email": "artist@example.com", "password": "a-new-secure-password-value"},
    ).status_code == 200


def test_web_ui_exposes_email_verification_and_password_change_paths() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    verification_page = client.get("/verify-email")
    script = client.get("/web-assets/app.js")

    assert verification_page.status_code == 200
    assert "window.location.hash.slice(1)" in script.text
    assert "/api/v1/auth/email-verification" in script.text
    assert "/api/v1/auth/change-password" in script.text
    assert "/admin/email-settings" in script.text
    assert "/api/v1/admin/email-settings" in script.text
    assert "乐云工坊" in verification_page.text
    assert "乐云工坊" in script.text
    assert "豌豆工坊" not in script.text

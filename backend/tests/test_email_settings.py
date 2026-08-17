import pytest
from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess, InvalidCredentials, SmtpEmailVerificationDelivery
from app.email_settings import (
    EmailSettingsUpdate,
    InMemoryEmailSettings,
    InvalidEmailSettings,
    SqlAlchemyEmailSettings,
)
from app.http import create_app
from app.model_routing import InMemoryProviderSecrets


def _command(*, password: str = "smtp-secret") -> EmailSettingsUpdate:
    return EmailSettingsUpdate(
        public_base_url="https://studio.example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_sender="noreply@example.com",
        smtp_username="mailer",
        smtp_password=password,
        smtp_security="starttls",
        smtp_timeout_seconds=8,
    )


def test_unconfigured_email_settings_allow_bootstrap_registration_without_fake_delivery() -> None:
    settings = InMemoryEmailSettings()
    accounts = InMemoryAccountAccess(verification_delivery=settings)

    registration = accounts.register("admin@example.com", "a-correct-horse-battery-staple")

    assert registration.email == "admin@example.com"
    assert accounts.login("admin@example.com", "a-correct-horse-battery-staple").access_token


def test_sql_email_settings_store_password_outside_the_database_and_never_project_it(tmp_path) -> None:
    secrets = InMemoryProviderSecrets()
    settings = SqlAlchemyEmailSettings.for_database_url(
        f"sqlite+pysqlite:///{(tmp_path / 'email-settings.db').as_posix()}",
        secrets,
        initialize_schema=True,
    )

    snapshot = settings.update(_command())

    assert snapshot.configured is True
    assert snapshot.password_configured is True
    assert "smtp-secret" not in repr(snapshot)


def test_sql_email_settings_keep_existing_password_when_the_update_password_is_blank(tmp_path, monkeypatch) -> None:
    secrets = InMemoryProviderSecrets()
    settings = SqlAlchemyEmailSettings.for_database_url(
        f"sqlite+pysqlite:///{(tmp_path / 'email-settings-rotation.db').as_posix()}",
        secrets,
        initialize_schema=True,
    )
    settings.update(_command())
    sent: list[tuple[str, str, str]] = []

    def record(self: SmtpEmailVerificationDelivery, email: str, token: str) -> None:
        sent.append((self.password, email, token))

    monkeypatch.setattr(SmtpEmailVerificationDelivery, "send_verification", record)
    settings.update(_command(password=""))
    settings.send_verification("artist@example.com", "verification-token")

    assert sent == [("smtp-secret", "artist@example.com", "verification-token")]


def test_sql_email_settings_rotate_password_and_delete_the_previous_secret(tmp_path, monkeypatch) -> None:
    secrets = InMemoryProviderSecrets()
    deleted: list[str] = []
    original_delete = secrets.delete
    monkeypatch.setattr(secrets, "delete", lambda secret_ref: (deleted.append(secret_ref), original_delete(secret_ref))[1])
    settings = SqlAlchemyEmailSettings.for_database_url(
        f"sqlite+pysqlite:///{(tmp_path / 'email-settings-password-rotation.db').as_posix()}",
        secrets,
        initialize_schema=True,
    )
    settings.update(_command(password="first-secret"))

    settings.update(_command(password="second-secret"))

    assert len(deleted) == 1


def test_email_settings_reject_an_insecure_public_url() -> None:
    settings = InMemoryEmailSettings()

    with pytest.raises(InvalidEmailSettings, match="HTTPS"):
        settings.update(
            EmailSettingsUpdate(
                public_base_url="http://studio.example.com/path",
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_sender="noreply@example.com",
            )
        )


def test_admin_email_settings_api_requires_admin_and_never_returns_password() -> None:
    settings = InMemoryEmailSettings()
    authorized: list[str] = []
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            email_settings=settings,
            admin_authorizer=authorized.append,
        )
    )
    headers = {"Authorization": "Bearer admin-session"}

    response = client.put(
        "/api/v1/admin/email-settings",
        headers=headers,
        json={
            "public_base_url": "https://studio.example.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_sender": "noreply@example.com",
            "smtp_username": "mailer",
            "smtp_password": "smtp-secret",
            "smtp_security": "starttls",
            "smtp_timeout_seconds": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["password_configured"] is True
    assert "smtp-secret" not in response.text
    assert "smtp_password" not in response.text
    assert authorized == ["admin-session"]


def test_registration_uses_email_delivery_after_the_admin_configures_it(monkeypatch) -> None:
    settings = InMemoryEmailSettings()
    accounts = InMemoryAccountAccess(verification_delivery=settings)
    accounts.register("admin@example.com", "a-correct-horse-battery-staple")
    settings.update(_command())
    sent: list[str] = []
    monkeypatch.setattr(
        SmtpEmailVerificationDelivery,
        "send_verification",
        lambda self, email, token: sent.append(email),
    )

    accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    assert sent == ["artist@example.com"]
    with pytest.raises(InvalidCredentials):
        accounts.login("missing@example.com", "a-correct-horse-battery-staple")

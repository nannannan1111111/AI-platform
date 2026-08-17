from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import (
    InvalidCredentials,
    InvalidEmailVerification,
    InvalidPasswordReset,
    InvalidSession,
    SqlAlchemyAccountAccess,
)


class _RecordingEmailVerificationDelivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.password_resets: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))

    def send_password_reset(self, email: str, token: str) -> None:
        self.password_resets.append((email, token))


class _FailingEmailVerificationDelivery:
    def send_verification(self, email: str, token: str) -> None:
        raise RuntimeError("SMTP unavailable")

    def send_password_reset(self, email: str, token: str) -> None:
        raise RuntimeError("SMTP unavailable")


def test_sqlalchemy_account_access_survives_adapter_restart(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'accounts.db').as_posix()}"
    first_process = SqlAlchemyAccountAccess.for_database_url(database_url, initialize_schema=True)
    registration = first_process.register("artist@example.com", "a-correct-horse-battery-staple")

    restarted_process = SqlAlchemyAccountAccess.for_database_url(database_url)
    session = restarted_process.login("artist@example.com", "a-correct-horse-battery-staple")

    assert session.user_id == registration.user_id
    assert restarted_process.current_user(session.access_token).account_space_id == registration.account_space_id
    assert restarted_process.credit_balance(session.access_token).available_credits == "0.0000"


def test_sqlalchemy_account_directory_lists_registered_users_after_restart(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'account-directory.db').as_posix()}"
    now = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    first_process = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        clock=lambda: now,
    )
    registration = first_process.register("artist@example.com", "a-correct-horse-battery-staple")

    users = SqlAlchemyAccountAccess.for_database_url(database_url).list_registered_users()

    assert len(users) == 1
    assert users[0].user_id == registration.user_id
    assert users[0].account_space_id == registration.account_space_id
    assert users[0].email == "artist@example.com"
    assert users[0].email_verified is False
    assert users[0].registered_at == now


def test_sqlalchemy_access_token_expires_after_the_configured_session_lifetime(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'expiring-sessions.db').as_posix()}"
    current_time = [datetime(2026, 8, 8, 8, 0, tzinfo=UTC)]
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        clock=lambda: current_time[0],
        session_ttl=timedelta(minutes=30),
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidSession):
        accounts.current_user(session.access_token)


def test_sqlalchemy_expired_access_token_cannot_read_credit_balance(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'expired-balance.db').as_posix()}"
    current_time = [datetime(2026, 8, 8, 8, 0, tzinfo=UTC)]
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        clock=lambda: current_time[0],
        session_ttl=timedelta(minutes=30),
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidSession):
        accounts.credit_balance(session.access_token)


def test_sqlalchemy_logout_revokes_only_the_presented_access_token(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'logout.db').as_posix()}"
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, initialize_schema=True)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    revoked_session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    active_session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    accounts.logout(revoked_session.access_token)
    accounts.logout(revoked_session.access_token)

    with pytest.raises(InvalidSession):
        accounts.current_user(revoked_session.access_token)
    assert accounts.current_user(active_session.access_token).email == "artist@example.com"


def test_sqlalchemy_email_verification_survives_adapter_restart(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'verified-email.db').as_posix()}"
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        verification_delivery=delivery,
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    assert accounts.current_user(session.access_token).email_verified is False
    accounts.verify_email(delivery.messages[0][1])

    restarted = SqlAlchemyAccountAccess.for_database_url(database_url)
    assert restarted.current_user(session.access_token).email_verified is True


def test_sqlalchemy_email_verification_token_expires(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'expired-email-token.db').as_posix()}"
    current_time = [datetime(2026, 8, 8, 8, 0, tzinfo=UTC)]
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        clock=lambda: current_time[0],
        verification_delivery=delivery,
        verification_ttl=timedelta(minutes=30),
    )
    accounts.register("expired@example.com", "a-correct-horse-battery-staple")
    expired_token = delivery.messages[-1][1]
    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(expired_token)


def test_sqlalchemy_email_verification_token_is_single_use(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'single-use-email-token.db').as_posix()}"
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        verification_delivery=delivery,
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    single_use_token = delivery.messages[0][1]
    accounts.verify_email(single_use_token)

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(single_use_token)


def test_alembic_schema_supports_the_account_access_interface(tmp_path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, verification_delivery=delivery)
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    accounts.verify_email(delivery.messages[0][1])
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    assert registration.available_credits == "0.0000"
    assert accounts.current_user(session.access_token).user_id == registration.user_id
    assert accounts.current_user(session.access_token).email_verified is True


def test_sqlalchemy_change_password_revokes_all_sessions(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'change-password.db').as_posix()}"
    accounts = SqlAlchemyAccountAccess.for_database_url(database_url, initialize_schema=True)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    first = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    second = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    accounts.change_password(first.access_token, "a-correct-horse-battery-staple", "a-new-correct-horse-password")

    with pytest.raises(InvalidSession):
        accounts.current_user(first.access_token)
    with pytest.raises(InvalidSession):
        accounts.current_user(second.access_token)
    with pytest.raises(InvalidCredentials):
        accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    assert accounts.login("artist@example.com", "a-new-correct-horse-password").access_token


def test_sqlalchemy_password_reset_survives_restart_replaces_old_token_and_revokes_sessions(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'password-reset.db').as_posix()}"
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        verification_delivery=delivery,
    )
    old_password = "a-correct-horse-battery-staple"
    new_password = "a-new-correct-horse-battery-staple"
    accounts.register("artist@example.com", old_password)
    first = accounts.login("artist@example.com", old_password)
    second = accounts.login("artist@example.com", old_password)
    accounts.request_password_reset("artist@example.com")
    replaced_token = delivery.password_resets[-1][1]
    accounts.request_password_reset("artist@example.com")
    active_token = delivery.password_resets[-1][1]

    restarted = SqlAlchemyAccountAccess.for_database_url(database_url)
    with pytest.raises(InvalidPasswordReset):
        restarted.reset_password(replaced_token, new_password)
    restarted.reset_password(active_token, new_password)

    for session in (first, second):
        with pytest.raises(InvalidSession):
            restarted.current_user(session.access_token)
    with pytest.raises(InvalidCredentials):
        restarted.login("artist@example.com", old_password)
    assert restarted.login("artist@example.com", new_password).access_token
    with pytest.raises(InvalidPasswordReset):
        restarted.reset_password(active_token, "another-correct-horse-password")


def test_sqlalchemy_password_reset_token_expires(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'expired-password-reset.db').as_posix()}"
    current_time = [datetime(2026, 8, 17, 8, 0, tzinfo=UTC)]
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        clock=lambda: current_time[0],
        verification_delivery=delivery,
        password_reset_ttl=timedelta(minutes=30),
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    accounts.request_password_reset("artist@example.com")
    token = delivery.password_resets[-1][1]
    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidPasswordReset):
        accounts.reset_password(token, "a-new-correct-horse-battery-staple")


def test_sqlalchemy_resend_verification_replaces_previous_token(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'resend-verification.db').as_posix()}"
    delivery = _RecordingEmailVerificationDelivery()
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        verification_delivery=delivery,
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    previous_token = delivery.messages[-1][1]

    accounts.request_email_verification(session.access_token)

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(previous_token)
    accounts.verify_email(delivery.messages[-1][1])


def test_sqlalchemy_registration_rolls_back_when_email_delivery_fails(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'failed-email.db').as_posix()}"
    accounts = SqlAlchemyAccountAccess.for_database_url(
        database_url,
        initialize_schema=True,
        verification_delivery=_FailingEmailVerificationDelivery(),
    )

    with pytest.raises(RuntimeError, match="SMTP unavailable"):
        accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    with pytest.raises(InvalidCredentials):
        accounts.login("artist@example.com", "a-correct-horse-battery-staple")

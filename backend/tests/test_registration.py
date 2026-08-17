from datetime import UTC, datetime, timedelta

import pytest

from app.accounts import (
    EmailAlreadyRegistered,
    InMemoryAccountAccess,
    InvalidCredentials,
    InvalidEmail,
    InvalidEmailVerification,
    InvalidSession,
    WeakPassword,
)


class _RecordingEmailVerificationDelivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append((email, token))


class _FailingEmailVerificationDelivery:
    def send_verification(self, email: str, token: str) -> None:
        raise RuntimeError("SMTP unavailable")


def test_registration_creates_personal_account_with_zero_balance() -> None:
    accounts = InMemoryAccountAccess()

    registration = accounts.register("  Artist@Example.COM ", "a-correct-horse-battery-staple")

    assert registration.email == "artist@example.com"
    assert registration.user_id
    assert registration.account_space_id
    assert registration.user_id != registration.account_space_id
    assert registration.available_credits == "0.0000"


def test_registration_rejects_an_existing_email() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    with pytest.raises(EmailAlreadyRegistered):
        accounts.register(" ARTIST@example.com ", "another-correct-horse-battery-staple")


def test_registration_rejects_an_invalid_email() -> None:
    accounts = InMemoryAccountAccess()

    with pytest.raises(InvalidEmail):
        accounts.register("not-an-email", "a-correct-horse-battery-staple")


def test_registration_rejects_a_password_shorter_than_twelve_characters() -> None:
    accounts = InMemoryAccountAccess()

    with pytest.raises(WeakPassword):
        accounts.register("artist@example.com", "short-pass")


def test_registered_user_can_log_in_with_email_and_password() -> None:
    accounts = InMemoryAccountAccess()
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    session = accounts.login(" ARTIST@EXAMPLE.COM ", "a-correct-horse-battery-staple")

    assert session.user_id == registration.user_id
    assert session.account_space_id == registration.account_space_id
    assert session.email == "artist@example.com"
    assert session.access_token


def test_login_rejects_an_incorrect_password() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    with pytest.raises(InvalidCredentials):
        accounts.login("artist@example.com", "the-wrong-password")


def test_login_does_not_reveal_that_an_email_is_unknown() -> None:
    accounts = InMemoryAccountAccess()

    with pytest.raises(InvalidCredentials):
        accounts.login("missing@example.com", "the-wrong-password")


def test_access_token_resolves_the_current_user() -> None:
    accounts = InMemoryAccountAccess()
    registration = accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    current_user = accounts.current_user(session.access_token)

    assert current_user.user_id == registration.user_id
    assert current_user.account_space_id == registration.account_space_id
    assert current_user.email == "artist@example.com"


def test_current_user_can_query_zero_credit_balance() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    balance = accounts.credit_balance(session.access_token)

    assert balance.available_credits == "0.0000"
    assert balance.frozen_credits == "0.0000"


def test_current_user_rejects_an_unknown_access_token() -> None:
    accounts = InMemoryAccountAccess()

    with pytest.raises(InvalidSession):
        accounts.current_user("unknown-token")


def test_access_token_expires_after_the_configured_session_lifetime() -> None:
    current_time = [datetime(2026, 8, 8, 8, 0, tzinfo=UTC)]
    accounts = InMemoryAccountAccess(
        clock=lambda: current_time[0],
        session_ttl=timedelta(minutes=30),
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidSession):
        accounts.current_user(session.access_token)


def test_logout_revokes_the_current_access_token() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    accounts.logout(session.access_token)

    with pytest.raises(InvalidSession):
        accounts.current_user(session.access_token)


def test_registration_sends_a_token_that_verifies_the_email() -> None:
    delivery = _RecordingEmailVerificationDelivery()
    accounts = InMemoryAccountAccess(verification_delivery=delivery)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    email, token = delivery.messages[0]

    assert email == "artist@example.com"
    assert accounts.current_user(session.access_token).email_verified is False

    accounts.verify_email(token)

    assert accounts.current_user(session.access_token).email_verified is True


def test_email_verification_token_expires() -> None:
    current_time = [datetime(2026, 8, 8, 8, 0, tzinfo=UTC)]
    delivery = _RecordingEmailVerificationDelivery()
    accounts = InMemoryAccountAccess(
        clock=lambda: current_time[0],
        verification_delivery=delivery,
        verification_ttl=timedelta(minutes=30),
    )
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    token = delivery.messages[0][1]

    current_time[0] += timedelta(minutes=31)

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(token)


def test_email_verification_token_can_only_be_used_once() -> None:
    delivery = _RecordingEmailVerificationDelivery()
    accounts = InMemoryAccountAccess(verification_delivery=delivery)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    token = delivery.messages[0][1]
    accounts.verify_email(token)

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(token)


def test_resending_email_verification_replaces_the_previous_token() -> None:
    delivery = _RecordingEmailVerificationDelivery()
    accounts = InMemoryAccountAccess(verification_delivery=delivery)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    previous_token = delivery.messages[-1][1]

    accounts.request_email_verification(session.access_token)
    current_token = delivery.messages[-1][1]

    with pytest.raises(InvalidEmailVerification):
        accounts.verify_email(previous_token)
    accounts.verify_email(current_token)


def test_change_password_revokes_every_session_and_replaces_the_login_secret() -> None:
    accounts = InMemoryAccountAccess()
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


def test_change_password_rejects_an_incorrect_current_password_without_revoking_session() -> None:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")

    with pytest.raises(InvalidCredentials):
        accounts.change_password(session.access_token, "wrong-current-password", "a-new-correct-horse-password")

    assert accounts.current_user(session.access_token).email == "artist@example.com"


def test_registration_rolls_back_when_verification_email_cannot_be_delivered() -> None:
    accounts = InMemoryAccountAccess(verification_delivery=_FailingEmailVerificationDelivery())

    with pytest.raises(RuntimeError, match="SMTP unavailable"):
        accounts.register("artist@example.com", "a-correct-horse-battery-staple")

    with pytest.raises(InvalidCredentials):
        accounts.login("artist@example.com", "a-correct-horse-battery-staple")

from app.database_runtime import lock_account_generation_submissions


class _Dialect:
    name = "sqlite"


class _Bind:
    dialect = _Dialect()


class _Database:
    def __init__(self) -> None:
        self.executed = False

    def get_bind(self) -> _Bind:
        return _Bind()

    def execute(self, *_args: object, **_kwargs: object) -> None:
        self.executed = True


def test_account_submission_lock_keeps_sqlite_tests_portable() -> None:
    database = _Database()

    lock_account_generation_submissions(database, "account-1")  # type: ignore[arg-type]

    assert database.executed is False

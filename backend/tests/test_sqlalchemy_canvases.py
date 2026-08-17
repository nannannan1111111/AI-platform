from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.canvases import (
    CanvasCreation,
    CanvasDeletion,
    CanvasNotFound,
    CanvasSave,
    CanvasVersionConflict,
    SqlAlchemyCanvases,
)


def test_sqlalchemy_canvas_save_survives_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'canvases.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url)
    canvas = canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="持久画布",
            kind="smart",
            created_at=now,
        )
    )
    document = {
        "nodes": [{"id": "node-1", "type": "image", "src": "object://result-1"}],
        "connections": [],
        "viewport": {"x": 1, "y": 2, "scale": 1.25},
    }
    saved = canvases.save(
        CanvasSave(
            account_space_id=registration.account_space_id,
            canvas_id=canvas.canvas_id,
            expected_version=1,
            title="重启后仍存在",
            document=document,
            saved_at=now + timedelta(minutes=1),
        )
    )

    restarted = SqlAlchemyCanvases.for_database_url(database_url)

    assert restarted.get(registration.account_space_id, canvas.canvas_id) == saved
    assert restarted.list(registration.account_space_id) == (saved,)
    with pytest.raises(CanvasVersionConflict):
        restarted.save(
            CanvasSave(
                account_space_id=registration.account_space_id,
                canvas_id=canvas.canvas_id,
                expected_version=1,
                document=document,
                saved_at=now + timedelta(minutes=2),
            )
        )


def test_sqlalchemy_canvas_deletion_remains_irreversible_after_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'deleted-canvases.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "delete@example.com", "a-correct-horse-battery-staple"
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-delete-1")
    canvas = canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="不会恢复的画布",
            kind="classic",
            created_at=now,
        )
    )

    canvases.delete(
        CanvasDeletion(
            account_space_id=registration.account_space_id,
            canvas_id=canvas.canvas_id,
            deleted_at=now + timedelta(minutes=1),
        )
    )
    restarted = SqlAlchemyCanvases.for_database_url(database_url)

    assert restarted.list(registration.account_space_id) == ()
    with pytest.raises(CanvasNotFound):
        restarted.get(registration.account_space_id, canvas.canvas_id)

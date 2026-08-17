import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.media import FileSystemMediaObjects
from app.reference_media import (
    ReferenceMediaNotFound,
    ReferenceMediaOrigin,
    ReferenceMediaUpload,
    SqlAlchemyReferenceMedia,
)

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_sqlalchemy_reference_image_survives_restart_with_account_isolation(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'reference-media.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "reference-owner@example.com", "a-correct-horse-battery-staple"
    )
    objects = FileSystemMediaObjects(tmp_path / "media")
    now = datetime(2026, 8, 10, 11, 0, tzinfo=UTC)
    reference_ids = iter(("reference-1", "canvas-reference"))
    media = SqlAlchemyReferenceMedia.for_database_url(
        database_url,
        media_objects=objects,
        id_factory=lambda: next(reference_ids),
    )

    uploaded = media.upload(
        ReferenceMediaUpload(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            original_name="portrait.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
        )
    )
    restarted = SqlAlchemyReferenceMedia.for_database_url(database_url, media_objects=objects)
    canvas_reference = media.upload(
        ReferenceMediaUpload(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            original_name="canvas-source.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now + timedelta(seconds=1),
            origin=ReferenceMediaOrigin.CANVAS,
        )
    )

    assert (
        restarted.read(
            registration.account_space_id,
            uploaded.media_id,
            at=now + timedelta(minutes=1),
        ).content
        == _PNG_BYTES
    )
    with pytest.raises(ReferenceMediaNotFound):
        restarted.read("another-account-space", uploaded.media_id, at=now)

    assert restarted.list_recent(registration.account_space_id, at=now) == (uploaded,)
    assert restarted.read(registration.account_space_id, canvas_reference.media_id, at=now).content == _PNG_BYTES
    assert restarted.delete(registration.account_space_id, uploaded.media_id).state.value == "deleted"
    assert restarted.list_recent(registration.account_space_id, at=now) == ()
    with pytest.raises(ReferenceMediaNotFound):
        restarted.read(registration.account_space_id, uploaded.media_id, at=now)

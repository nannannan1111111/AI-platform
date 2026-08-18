import json
from pathlib import Path

import httpx
import pytest

from app.release_contract import ReleaseContractError, validate_release_contract, write_release_snapshot
from scripts.staging_smoke import run_smoke


@pytest.mark.anyio
async def test_staging_smoke_checks_public_contracts_and_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.url.path == "/api/v1/auth/me":
            assert request.headers["authorization"] == "Bearer test-token"
            return httpx.Response(200, json={"user_id": "user-1"})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await run_smoke(client, "https://staging.example.com", email="test@example.com", password="not-written")

    assert all(result.ok for result in results)
    assert all("not-written" not in json.dumps(result.__dict__ if hasattr(result, "__dict__") else {}) for result in results)


def test_release_contract_rejects_mutable_images_and_incompatible_rollback() -> None:
    with pytest.raises(ReleaseContractError, match="immutable"):
        validate_release_contract(image="registry/app:latest", migration_head="head")
    with pytest.raises(ReleaseContractError, match="migration heads differ"):
        validate_release_contract(
            image="registry/app@sha256:" + "a" * 64,
            migration_head="new-head",
            previous_image="registry/app@sha256:" + "b" * 64,
            previous_migration_head="old-head",
        )


def test_release_contract_writes_non_sensitive_snapshot(tmp_path: Path) -> None:
    destination = tmp_path / "release-state.json"
    snapshot = write_release_snapshot(
        destination=destination,
        image="registry/app@sha256:" + "a" * 64,
        migration_head="head",
    )
    assert snapshot.image.endswith("a" * 64)
    assert json.loads(destination.read_text(encoding="utf-8"))["migration_head"] == "head"


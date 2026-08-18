import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.observability import (
    JsonLogFormatter,
    MetricsRegistry,
    collect_media_storage_metrics,
    redact_text,
    valid_request_id,
)


def test_request_id_is_echoed_and_invalid_values_are_replaced() -> None:
    client = TestClient(create_app(InMemoryAccountAccess(), metrics_token="metrics-token-123456"))

    response = client.get("/healthz", headers={"x-request-id": "trace-42"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-42"

    replaced = client.get("/healthz", headers={"x-request-id": "bad\r\nvalue"})
    assert replaced.status_code == 200
    assert replaced.headers["x-request-id"] != "bad\r\nvalue"
    assert valid_request_id(replaced.headers["x-request-id"]) is not None


def test_metrics_endpoint_requires_explicit_scraper_token() -> None:
    client = TestClient(create_app(InMemoryAccountAccess(), metrics_token="metrics-token-123456"))
    assert client.get("/metrics").status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-token-123456"})
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")


def test_metrics_endpoint_is_hidden_when_not_configured() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    assert client.get("/metrics").status_code == 404


def test_media_storage_metrics_report_capacity_without_exposing_path(tmp_path: Path) -> None:
    metrics = MetricsRegistry()
    collect_media_storage_metrics(tmp_path, metrics=metrics)

    rendered = metrics.render()
    assert "media_storage_probe_success 1" in rendered
    assert "media_storage_total_bytes" in rendered
    assert "media_storage_used_bytes" in rendered
    assert "media_storage_available_bytes" in rendered
    assert str(tmp_path) not in rendered


def test_authenticated_metrics_scrape_refreshes_media_storage_capacity(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            InMemoryAccountAccess(),
            metrics_token="metrics-token-123456",
            media_storage_root=tmp_path,
        )
    )

    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-token-123456"})

    assert response.status_code == 200
    assert "media_storage_probe_success 1" in response.text
    assert "media_storage_total_bytes" in response.text


def test_media_storage_metrics_fail_closed_when_root_is_unavailable(tmp_path: Path) -> None:
    metrics = MetricsRegistry()
    collect_media_storage_metrics(tmp_path / "missing", metrics=metrics)

    rendered = metrics.render()
    assert "media_storage_probe_success 0" in rendered
    assert "media_storage_total_bytes 0" in rendered


def test_sensitive_log_values_are_redacted() -> None:
    value = redact_text(
        "Authorization: Bearer secret-token database_url=postgresql://user:password@example/db api_key=abc123"
    )
    assert "secret-token" not in value
    assert "postgresql://" not in value
    assert "abc123" not in value

    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed token=%s", ("secret-token",), None
    )
    payload = json.loads(JsonLogFormatter().format(record))
    assert "secret-token" not in json.dumps(payload)

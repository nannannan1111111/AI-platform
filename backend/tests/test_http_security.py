from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import HttpSecuritySettings, create_app
from app.http.security import install_http_security


class _ExecutableMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_event_attributes: list[str] = []
        self.inline_script_count = 0
        self.javascript_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.casefold() == "script" and "src" not in attributes:
            self.inline_script_count += 1
        for name, value in attrs:
            if name.casefold().startswith("on"):
                self.inline_event_attributes.append(name)
            if value and value.strip().casefold().startswith("javascript:"):
                self.javascript_urls.append(value)


def test_security_headers_and_cache_policy_cover_routes_and_static_mounts() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    api = client.get("/healthz")
    html = client.get("/login")
    static = client.get("/web-assets/app.js")

    assert api.headers["cache-control"] == "no-store"
    assert html.headers["cache-control"] == "no-store"
    assert static.headers["cache-control"] == "public, max-age=0, must-revalidate"
    for response in (api, html, static):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        csp = response.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "script-src-attr 'none'" in csp
        script_policy = csp.split("script-src 'self'", maxsplit=1)[1].split(";", maxsplit=1)[0]
        assert "unsafe-inline" not in script_policy
        assert "unsafe-eval" not in csp


def test_fingerprinted_static_assets_receive_immutable_cache_policy() -> None:
    app = FastAPI()
    install_http_security(app, HttpSecuritySettings())

    @app.get("/static/app.12345678.js")
    def asset() -> str:
        return "ok"

    response = TestClient(app).get("/static/app.12345678.js")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_trusted_host_rejects_unlisted_hosts_and_keeps_security_headers() -> None:
    app = create_app(
        InMemoryAccountAccess(),
        http_security=HttpSecuritySettings(allowed_hosts=("studio.example.com", "127.0.0.1")),
    )
    client = TestClient(app)

    rejected = client.get("/healthz")
    accepted = client.get("/healthz", headers={"Host": "studio.example.com"})

    assert rejected.status_code == 400
    assert rejected.headers["x-content-type-options"] == "nosniff"
    assert accepted.status_code == 200


def test_hsts_requires_both_explicit_enablement_and_an_https_request() -> None:
    enabled = create_app(InMemoryAccountAccess(), http_security=HttpSecuritySettings(enable_hsts=True))
    disabled = create_app(InMemoryAccountAccess(), http_security=HttpSecuritySettings(enable_hsts=False))

    assert "strict-transport-security" not in TestClient(enabled).get(
        "/healthz", headers={"X-Forwarded-Proto": "https"}
    ).headers
    assert TestClient(enabled, base_url="https://testserver").get("/healthz").headers[
        "strict-transport-security"
    ] == "max-age=31536000; includeSubDomains"
    assert "strict-transport-security" not in TestClient(disabled, base_url="https://testserver").get(
        "/healthz"
    ).headers


def test_canvas_shells_have_no_inline_script_event_or_javascript_url() -> None:
    static_root = Path(__file__).parents[1] / "app" / "webui" / "static"
    for name in ("canvas.html", "smart-canvas.html"):
        source = (static_root / name).read_text(encoding="utf-8")
        parser = _ExecutableMarkupParser()
        parser.feed(source)
        assert parser.inline_script_count == 0
        assert parser.inline_event_attributes == []
        assert parser.javascript_urls == []
        assert "/static/js/csp-event-bridge.js" in source


def test_malicious_canvas_node_fields_are_not_interpolated_into_header_markup() -> None:
    repository_root = Path(__file__).parents[2]
    source = (repository_root / "static" / "js" / "canvas.js").read_text(encoding="utf-8")
    render_header = source[source.index("function renderNode(node)") : source.index("const body =", source.index("function renderNode(node)"))]

    assert "titleElement.textContent = String(displayTitle)" in render_header
    assert "deleteButton.addEventListener('click'" in render_header
    assert "deleteNodeFromButton(node.id, event)" in render_header
    assert "innerHTML" not in render_header
    assert "onclick=" not in render_header

    provider_options = source[source.index("function chatApiProviders") : source.index("function sanitizeImageNodeProviderModel")]
    assert "escapeHtml(provider.name || provider.id)" in provider_options
    assert "escapeHtml(provider.id)" in provider_options
    assert "<textarea" in source and "${escapeHtml(node.text || '')}</textarea>" in source


def test_media_display_urls_reject_script_and_document_data_protocols() -> None:
    repository_root = Path(__file__).parents[2]
    source = (repository_root / "static" / "js" / "media.js").read_text(encoding="utf-8")

    assert "function safeBrowserMediaUrl" in source
    assert "data:(?:image|video|audio)" in source
    assert "return safeBrowserMediaUrl(displayUrl)" in source
    assert "javascript:" not in source

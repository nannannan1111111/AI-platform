"""HTTP response and browser security boundaries for the SaaS application."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_FINGERPRINTED_ASSET = re.compile(r"(?:^|[-.])[0-9a-f]{8,}(?:\.|$)", re.IGNORECASE)

_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "script-src-attr 'none'",
        # The canvas still positions and resizes elements through style attributes. This
        # exception does not permit script execution and is tracked separately from the
        # enforced script boundary.
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "font-src 'self' data:",
        "connect-src 'self'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
    )
)


@dataclass(frozen=True, slots=True)
class HttpSecuritySettings:
    """Validated application-layer HTTP security settings."""

    allowed_hosts: tuple[str, ...] = ()
    enable_hsts: bool = False


class SecurityHeadersMiddleware:
    """Apply one security and cache policy to routes and mounted static apps."""

    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        """Store the wrapped ASGI app and explicit transport policy."""
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add headers to HTTP response starts without buffering bodies."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = (
                    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                    "magnetometer=(), microphone=(), payment=(), usb=()"
                )
                headers["X-Frame-Options"] = "DENY"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
                if "cache-control" not in headers:
                    headers["Cache-Control"] = _cache_policy(
                        str(scope.get("path", "")), headers.get("content-type")
                    )
                if self.enable_hsts and scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                elif "strict-transport-security" in headers:
                    del headers["strict-transport-security"]
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def install_http_security(app: Starlette, settings: HttpSecuritySettings) -> None:
    """Install Host validation inside the response-header boundary."""
    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.enable_hsts)


def validate_allowed_hosts(hosts: Sequence[str]) -> tuple[str, ...]:
    """Reject wildcard, URL-shaped, and otherwise ambiguous production Host values."""
    normalized: list[str] = []
    for raw_host in hosts:
        host = raw_host.strip().casefold()
        if not host:
            continue
        if "*" in host or "://" in host or "/" in host or any(character.isspace() for character in host):
            raise ValueError("allowed hosts must be exact host names or IP literals")
        if host not in normalized:
            normalized.append(host)
    if not normalized:
        raise ValueError("at least one allowed host is required")
    for loopback in ("127.0.0.1", "localhost"):
        if loopback not in normalized:
            normalized.append(loopback)
    return tuple(normalized)


def _cache_policy(path: str, content_type: str | None) -> str:
    if path.startswith("/api/") or path in {"/healthz", "/readyz"}:
        return "no-store"
    if content_type and content_type.partition(";")[0].strip().casefold() == "text/html":
        return "no-store"
    if path.startswith(("/web-assets/", "/static/")) and _FINGERPRINTED_ASSET.search(path.rsplit("/", 1)[-1]):
        return "public, max-age=31536000, immutable"
    return "public, max-age=0, must-revalidate"

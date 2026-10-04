"""Browser-origin security helpers for the API boundary."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply a conservative browser-security baseline to every API response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # Swagger uses inline assets; API JSON can use a substantially tighter policy.
        if request.url.path not in {"/docs", "/redoc", "/openapi.json"}:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
        production = os.getenv("STOCKPILOT_ENV", "").strip().lower() in {"production", "release"}
        if request.url.scheme.lower() == "https" and production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def normalize_http_origin(value: str) -> str:
    """Return a canonical HTTP(S) origin, rejecting paths and wildcards."""

    raw = str(value or "").strip()
    if not raw or "*" in raw:
        raise ValueError("CORS origins must be explicit HTTP(S) origins.")
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CORS origins must contain only an HTTP(S) scheme and authority.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CORS origin port is invalid.") from exc
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def parse_cors_origins(value: str, *, allow_credentials: bool = True) -> list[str]:
    origins: list[str] = []
    for item in str(value or "").split(","):
        if not item.strip():
            continue
        origin = normalize_http_origin(item)
        if allow_credentials and origin == "*":  # Defensive if normalization changes.
            raise ValueError("Credentialed CORS cannot use a wildcard origin.")
        if origin not in origins:
            origins.append(origin)
    if not origins:
        raise ValueError("At least one explicit CORS origin is required.")
    return origins


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Require an approved Origin when a browser session cookie authorizes writes."""

    def __init__(self, app, *, cookie_name: str, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self.cookie_name = cookie_name
        self.allowed_origins = frozenset(allowed_origins)

    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("Authorization", "")
        bearer_request = authorization.lower().startswith("bearer ") and bool(
            authorization[7:].strip()
        )
        cookie_authenticated = self.cookie_name in request.cookies and not bearer_request
        if request.method.upper() in STATE_CHANGING_METHODS and cookie_authenticated:
            try:
                origin = normalize_http_origin(request.headers.get("Origin", ""))
            except ValueError:
                origin = ""
            if origin not in self.allowed_origins:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Request origin is not allowed."},
                )
        return await call_next(request)

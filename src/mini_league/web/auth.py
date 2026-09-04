"""Organizer sign-in (design doc section 3, milestone 7).

One shared password for the whole league. Anyone may read the leaderboard and
player pages; only a signed-in organizer may change anything.

The rule is fail-safe: with no password configured, the organizer screens are
closed rather than open. An app that quietly accepts writes because someone
forgot an environment variable is the failure worth designing against.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

SESSION_KEY = "organizer"
PUBLIC_PREFIXES = (
    "/static",
    "/login",
    "/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
)


@dataclass(frozen=True)
class AuthConfig:
    """How this instance authenticates its organizer."""

    password: str | None = None
    secret_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.password)


def needs_organizer(method: str, path: str) -> bool:
    """Whether this request may change something.

    Reads stay open so the leaderboard can be shared. Everything under /admin,
    everything under /api/admin, and every write to the API is organizer-only.
    """
    if any(path == prefix or path.startswith(prefix + "/") for prefix in PUBLIC_PREFIXES):
        return False
    if path == "/admin" or path.startswith("/admin/"):
        return True
    if path == "/api/admin" or path.startswith("/api/admin/"):
        return True
    if path.startswith("/api/") and method.upper() not in ("GET", "HEAD", "OPTIONS"):
        return True
    return False


def is_signed_in(request: Request) -> bool:
    try:
        return bool(request.session.get(SESSION_KEY))
    except AssertionError:  # no session middleware installed
        return False


def check_password(config: AuthConfig, attempt: str) -> bool:
    """Constant-time comparison, so a wrong guess reveals nothing by timing."""
    if not config.configured:
        return False
    return secrets.compare_digest(attempt or "", config.password or "")


def safe_next(destination: str | None, fallback: str = "/admin") -> str:
    """Only ever redirect back into this app.

    A leading slash is not enough on its own: "//evil.example" is a
    protocol-relative URL and a browser would leave the site for it. The same
    goes for a backslash, which some browsers normalise to a slash.
    """
    if not destination or not destination.startswith("/"):
        return fallback
    if destination.startswith("//") or destination.startswith("/\\"):
        return fallback
    if "\\" in destination[:2]:
        return fallback
    return destination


def sign_in(request: Request) -> None:
    request.session[SESSION_KEY] = True


def sign_out(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


SETUP_MESSAGE = (
    "No organizer password is set, so the organizer screens are closed. "
    "Set MINI_LEAGUE_PASSWORD and restart to open them."
)


def setup_required(request: Request) -> Response:
    """No password configured: refuse rather than let anyone in."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": SETUP_MESSAGE}, status_code=503)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Setup needed</title>"
        "<body style=\"font:16px/1.5 system-ui, sans-serif; margin:0; padding:24px;"
        " max-width:34rem\">"
        "<h1 style='font-size:1.3rem'>Organizer screens are closed</h1>"
        f"<p>{SETUP_MESSAGE}</p>"
        "<pre style=\"background:#f2f3f5; padding:12px; border-radius:8px;"
        ' overflow-x:auto">MINI_LEAGUE_PASSWORD=your-password \\\n'
        "  uv run uvicorn mini_league.web.app:app --factory</pre>"
        "<p>The leaderboard and player pages are open as usual. "
        "<a href='/'>Back to the leaderboard</a>.</p>"
        "</body>",
        status_code=503,
    )


def refusal(request: Request) -> Response:
    """What to send someone who is not signed in.

    The API answers with a status a client can act on; a browser is sent to the
    sign-in page and returned to where it was going afterwards.
    """
    path = request.url.path
    if path.startswith("/api/"):
        return JSONResponse({"detail": "organizer sign-in required"}, status_code=401)
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={target}", status_code=303)

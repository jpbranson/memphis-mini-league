"""FastAPI application factory."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ..db import make_engine, make_session_factory
from ..settings import get_settings
from . import api, auth, pages
from .deps import STATIC_DIR


def create_app(
    database_url: str | None = None,
    *,
    create_tables: bool = False,
    organizer_password: str | None = None,
    secret_key: str | None = None,
) -> FastAPI:
    """Build the app.

    `database_url` overrides the configured one, which the tests use. Schema is
    normally created by `alembic upgrade head`; `create_tables` is a convenience
    for throwaway databases in tests.
    """
    settings = get_settings()
    url = database_url or settings.database_url
    engine = make_engine(url)
    if create_tables:
        from ..models import Base

        Base.metadata.create_all(engine)

    app = FastAPI(
        title="Frisbee Mini League",
        description=(
            "Public leaderboard and player pages; organizer screens behind a "
            "shared password."
        ),
        version="1.0.0",
    )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.settings = settings
    app.state.auth = auth.AuthConfig(
        password=organizer_password or settings.organizer_password,
        # A random key when none is configured: sessions then end at restart,
        # which is inconvenient but never guessable.
        secret_key=secret_key or settings.secret_key or secrets.token_urlsafe(32),
    )

    @app.middleware("http")
    async def organizer_gate(request: Request, call_next):
        """One gate for every route, so a new admin page cannot forget to lock."""
        config: auth.AuthConfig = request.app.state.auth
        request.state.auth_configured = config.configured
        request.state.is_organizer = auth.is_signed_in(request)

        if auth.needs_organizer(request.method, request.url.path):
            if not config.configured:
                return auth.setup_required(request)
            if not request.state.is_organizer:
                return auth.refusal(request)
        return await call_next(request)

    # Added last so it wraps the gate: Starlette runs the most recently added
    # middleware outermost, and the gate needs request.session to already exist.
    app.add_middleware(SessionMiddleware, secret_key=app.state.auth.secret_key)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(api.router)
    app.include_router(pages.router)

    @app.get("/health", include_in_schema=False)
    def health(request: Request) -> dict[str, str]:
        """Liveness plus a look at the database.

        Reporting the migration revision makes a deploy verifiable from outside:
        if migrations failed to apply, this says so rather than the app looking
        healthy while serving against an old schema.
        """
        from sqlalchemy import text

        factory = request.app.state.session_factory
        try:
            with factory() as db:
                db.execute(text("SELECT 1")).scalar()
                try:
                    revision = db.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar()
                except Exception:
                    # A reachable database that Alembic did not build: the test
                    # suite does this deliberately, so it is not a failure.
                    revision = "unmanaged"
        except Exception:
            return {"status": "degraded", "database": "unreachable"}
        return {"status": "ok", "database": "ok", "schema": revision or "unmigrated"}

    return app


app = create_app  # `uvicorn mini_league.web.app:app --factory`

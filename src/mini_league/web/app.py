"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from ..db import make_engine, make_session_factory
from ..settings import get_settings
from . import api, pages


def create_app(database_url: str | None = None, *, create_tables: bool = False) -> FastAPI:
    """Build the app.

    `database_url` overrides the configured one, which the tests use. Schema is
    normally created by `alembic upgrade head`; `create_tables` is a convenience
    for throwaway databases in tests.
    """
    url = database_url or get_settings().database_url
    engine = make_engine(url)
    if create_tables:
        from ..models import Base

        Base.metadata.create_all(engine)

    app = FastAPI(
        title="Frisbee Mini League",
        description="Organizer flow: sessions, check-in, results. Auth arrives in milestone 7.",
        version="0.2.0",
    )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.include_router(api.router)
    app.include_router(pages.router)
    return app


app = create_app  # `uvicorn mini_league.web.app:app --factory`

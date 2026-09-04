"""Shared request dependencies and template setup."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db(request: Request) -> Iterator[Session]:
    """One SQLAlchemy session per request, from the factory on app.state."""
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"

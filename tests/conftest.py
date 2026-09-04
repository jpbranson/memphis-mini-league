from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mini_league.db import make_engine, make_session_factory
from mini_league.models import Base, LeagueSession, Player, Season


@pytest.fixture
def engine():
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine) -> Session:
    factory = make_session_factory(engine)
    with factory() as session:
        yield session


@pytest.fixture
def season(db: Session) -> Season:
    s = Season(name="Fall 2026", start_date=date(2026, 9, 1))
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def league_session(db: Session, season: Season) -> LeagueSession:
    ls = LeagueSession(season_id=season.id, date=date(2026, 9, 5))
    db.add(ls)
    db.commit()
    return ls


@pytest.fixture
def make_players(db: Session):
    def _make(n: int, prefix: str = "P") -> list[Player]:
        players = [Player(name=f"{prefix}{i}") for i in range(n)]
        db.add_all(players)
        db.commit()
        return players

    return _make


# --- web fixtures ---------------------------------------------------------------


ORGANIZER_PASSWORD = "test-password"


def build_client(tmp_path, *, password: str | None = ORGANIZER_PASSWORD, sign_in=True):
    from fastapi.testclient import TestClient

    from mini_league.web import create_app

    url = f"sqlite:///{(tmp_path / 'web.db').as_posix()}"
    app = create_app(
        url, create_tables=True, organizer_password=password, secret_key="test-secret"
    )
    client = TestClient(app)
    if sign_in and password:
        response = client.post(
            "/login", data={"password": password, "next": "/admin"}, follow_redirects=False
        )
        assert response.status_code == 303, response.text
    return app, client


@pytest.fixture
def client(tmp_path):
    """TestClient over a throwaway database, already signed in as the organizer."""
    app, test_client = build_client(tmp_path)
    with test_client:
        yield test_client
    app.state.engine.dispose()


@pytest.fixture
def visitor(tmp_path):
    """A client that has not signed in: sees only the public pages."""
    app, test_client = build_client(tmp_path, sign_in=False)
    with test_client:
        yield test_client
    app.state.engine.dispose()


@pytest.fixture
def unconfigured(tmp_path):
    """An instance with no organizer password set at all."""
    app, test_client = build_client(tmp_path, password=None, sign_in=False)
    with test_client:
        yield test_client
    app.state.engine.dispose()


@pytest.fixture
def api_season(client):
    """A season exists, so sessions can infer one from their date."""
    response = client.post(
        "/api/seasons", json={"name": "Fall 2026", "start_date": "2026-09-01"}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def api_session(client, api_season) -> int:
    response = client.post("/api/sessions", json={"date": "2026-09-05"})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def page_session(client, api_season) -> int:
    """A session created through the organizer page, for page-level tests."""
    response = client.post(
        "/admin/session/new", data={"date": "2026-09-05"}, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


@pytest.fixture
def make_api_players(client):
    def _make(*names: str) -> list[int]:
        ids = []
        for name in names:
            response = client.post("/api/players", json={"name": name, "force": True})
            assert response.status_code == 201, response.text
            ids.append(response.json()["id"])
        return ids

    return _make

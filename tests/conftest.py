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

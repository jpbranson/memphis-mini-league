"""Schema constraints that the models must enforce."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import insert, inspect
from sqlalchemy.exc import IntegrityError

from mini_league.models import (
    Base,
    Game,
    GameTeam,
    GameTeamPlayer,
    LeagueSession,
    Player,
    RatingHistory,
    Season,
    SessionPlayer,
)

EXPECTED_TABLES = {
    "players",
    "seasons",
    "sessions",
    "session_players",
    "games",
    "game_teams",
    "game_team_players",
    "rating_history",
    "player_season_ratings",
    "audit_log",
}


def test_all_design_doc_tables_exist(engine):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_player_name_unique_among_active_players(db):
    db.add(Player(name="Justin"))
    db.commit()
    db.add(Player(name="Justin"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # An inactive (retired or merged) player may share a name with an active one.
    db.add(Player(name="Justin", active=False))
    db.commit()
    assert db.query(Player).filter_by(name="Justin").count() == 2


def test_merged_into_is_self_reference(db, make_players):
    dup, keep = make_players(2)
    dup.merged_into = keep.id
    dup.active = False
    db.commit()
    db.refresh(dup)
    assert dup.merged_into_player is keep


def test_season_name_unique(db):
    db.add(Season(name="Fall 2026", start_date=date(2026, 9, 1)))
    db.commit()
    db.add(Season(name="Fall 2026", start_date=date(2027, 9, 1)))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_enforced(db):
    db.add(LeagueSession(season_id=999, date=date(2026, 9, 5)))
    with pytest.raises(IntegrityError):
        db.commit()


def test_game_team_index_unique_per_game(db, league_session):
    game = Game(session_id=league_session.id, round_number=1, players_on_field=2)
    game.teams.append(GameTeam(team_index=0, rank=1))
    game.teams.append(GameTeam(team_index=0, rank=2))
    db.add(game)
    with pytest.raises(IntegrityError):
        db.commit()


def test_player_cannot_be_on_same_team_twice(db, league_session, make_players):
    (p,) = make_players(1)
    game = Game(session_id=league_session.id, round_number=1, players_on_field=1)
    team = GameTeam(team_index=0, rank=1)
    team.players.append(GameTeamPlayer(player_id=p.id))
    game.teams.append(team)
    db.add(game)
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(
            insert(GameTeamPlayer.__table__).values(game_team_id=team.id, player_id=p.id)
        )


def test_rating_history_unique_per_player_game(db, league_session, season, make_players):
    (p,) = make_players(1)
    game = Game(session_id=league_session.id, round_number=1, players_on_field=1)
    db.add(game)
    db.commit()
    row = dict(
        player_id=p.id,
        game_id=game.id,
        season_id=season.id,
        mu_before=25,
        sigma_before=8,
        mu_after=26,
        sigma_after=7,
    )
    db.add(RatingHistory(**row))
    db.commit()
    db.add(RatingHistory(**row))
    with pytest.raises(IntegrityError):
        db.commit()


def test_session_players_composite_key(db, league_session, make_players):
    (p,) = make_players(1)
    db.add(SessionPlayer(session_id=league_session.id, player_id=p.id))
    db.commit()
    db.add(SessionPlayer(session_id=league_session.id, player_id=p.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.refresh(league_session)
    assert [sp.player_id for sp in league_session.players] == [p.id]
    assert league_session.players[0].checked_in_at is not None
    assert league_session.players[0].checked_out_at is None


def test_timestamps_default(db, make_players):
    (p,) = make_players(1)
    assert p.created_at is not None
    assert p.active is True
    assert p.merged_into is None

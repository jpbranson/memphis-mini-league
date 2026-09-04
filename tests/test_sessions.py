"""Session creation, season inference, and check-in/check-out."""

from __future__ import annotations

from datetime import date

import pytest

from mini_league.models import Season
from mini_league.players import create_player
from mini_league.seasons import (
    NoSeasonError,
    create_season,
    current_season,
    season_for_date,
)
from mini_league.sessions import (
    check_in,
    check_out,
    checked_in_players,
    create_session,
    get_session,
    list_sessions,
    session_roster,
)


def test_season_inferred_from_session_date(db, season):
    session = create_session(db, date(2026, 9, 12))
    assert session.season_id == season.id


def test_session_without_a_season_is_refused(db):
    with pytest.raises(NoSeasonError, match="no season covers"):
        create_session(db, date(2026, 9, 12))


def test_season_for_date_respects_end_date(db):
    fall = create_season(db, "Fall 2026", date(2026, 9, 1))
    fall.end_date = date(2026, 12, 31)
    db.commit()
    winter = create_season(db, "Winter 2027", date(2027, 1, 1), close_current=False)

    assert season_for_date(db, date(2026, 10, 1)).id == fall.id
    assert season_for_date(db, date(2027, 2, 1)).id == winter.id
    with pytest.raises(NoSeasonError):
        season_for_date(db, date(2026, 8, 1))


def test_create_season_closes_the_open_one(db, season):
    assert season.end_date is None
    winter = create_season(db, "Winter 2027", date(2027, 1, 1))
    db.refresh(season)
    assert season.end_date == date(2026, 12, 31)
    assert current_season(db).id == winter.id


def test_create_season_rejects_duplicate_name_and_bad_dates(db, season):
    with pytest.raises(ValueError, match="already exists"):
        create_season(db, "Fall 2026", date(2027, 1, 1))
    with pytest.raises(ValueError, match="on or after"):
        create_season(db, "Too Early", date(2026, 8, 1))
    with pytest.raises(ValueError, match="name is required"):
        create_season(db, "  ", date(2027, 1, 1))


def test_current_season_is_the_open_one(db):
    assert current_season(db) is None
    db.add(Season(name="Fall 2026", start_date=date(2026, 9, 1), end_date=date(2026, 12, 31)))
    db.commit()
    assert current_season(db) is None
    open_season = create_season(db, "Winter 2027", date(2027, 1, 1))
    assert current_season(db).id == open_season.id


def test_check_in_and_check_out(db, league_session):
    ada = create_player(db, "Ada")
    ben = create_player(db, "Ben")
    check_in(db, league_session.id, ada.id)
    check_in(db, league_session.id, ben.id)
    assert [p.name for p in checked_in_players(db, league_session.id)] == ["Ada", "Ben"]

    check_out(db, league_session.id, ben.id)
    assert [p.name for p in checked_in_players(db, league_session.id)] == ["Ada"]
    # The roster still remembers everyone who was ever here.
    assert len(session_roster(db, league_session.id)) == 2


def test_check_in_is_idempotent(db, league_session):
    ada = create_player(db, "Ada")
    check_in(db, league_session.id, ada.id)
    check_in(db, league_session.id, ada.id)
    assert len(session_roster(db, league_session.id)) == 1


def test_re_check_in_clears_the_check_out(db, league_session):
    ada = create_player(db, "Ada")
    check_in(db, league_session.id, ada.id)
    check_out(db, league_session.id, ada.id)
    entry = check_in(db, league_session.id, ada.id)
    assert entry.checked_out_at is None
    assert [p.name for p in checked_in_players(db, league_session.id)] == ["Ada"]


def test_check_out_someone_never_checked_in(db, league_session):
    ada = create_player(db, "Ada")
    with pytest.raises(LookupError, match="not checked in"):
        check_out(db, league_session.id, ada.id)


def test_check_in_unknown_session_or_player(db, league_session):
    ada = create_player(db, "Ada")
    with pytest.raises(LookupError, match="session 999"):
        check_in(db, 999, ada.id)
    with pytest.raises(LookupError, match="player 999"):
        check_in(db, league_session.id, 999)


def test_check_in_refuses_merged_player(db, league_session):
    dup = create_player(db, "Justin")
    keep = create_player(db, "Priya")
    dup.merged_into = keep.id
    db.commit()
    with pytest.raises(ValueError, match="merged"):
        check_in(db, league_session.id, dup.id)


def test_roster_is_sorted_by_name(db, league_session):
    for name in ["Zoe", "Ada", "Mia"]:
        check_in(db, league_session.id, create_player(db, name).id)
    assert [p.name for p in checked_in_players(db, league_session.id)] == ["Ada", "Mia", "Zoe"]


def test_list_sessions_newest_first(db, season):
    create_session(db, date(2026, 9, 5))
    create_session(db, date(2026, 9, 12))
    assert [s.date for s in list_sessions(db)] == [date(2026, 9, 12), date(2026, 9, 5)]
    assert list_sessions(db, season_id=999) == []


def test_get_session_unknown(db):
    with pytest.raises(LookupError, match="session 404"):
        get_session(db, 404)


def test_notes_are_optional_and_trimmed_to_none(db, season):
    assert create_session(db, date(2026, 9, 5)).notes is None
    assert create_session(db, date(2026, 9, 6), notes="Windy").notes == "Windy"


def test_rounds_played_counts_each_players_games(db, league_session, make_players):
    from mini_league.games import TeamInput, delete_game, record_game
    from mini_league.sessions import rounds_played

    a, b, c = make_players(3)
    assert rounds_played(db, league_session.id) == {}

    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    game = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([c.id], 2)])
    assert rounds_played(db, league_session.id) == {a.id: 2, b.id: 1, c.id: 1}

    delete_game(db, game.id)
    assert rounds_played(db, league_session.id) == {a.id: 1, b.id: 1}


def test_move_session_to_another_season(db, season, league_session, make_players):
    from datetime import date as date_type

    from mini_league.games import TeamInput, record_game
    from mini_league.models import PlayerSeasonRating, Season
    from mini_league.sessions import move_session_to_season

    a, b = make_players(2)
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    assert db.get(PlayerSeasonRating, (a.id, season.id)).games_played == 1

    winter = Season(name="Winter 2027", start_date=date_type(2027, 1, 1))
    db.add(winter)
    db.commit()

    move_session_to_season(db, league_session.id, winter.id)
    # Both seasons were replayed: the old one empties, the new one gains the game.
    assert db.get(PlayerSeasonRating, (a.id, season.id)) is None
    assert db.get(PlayerSeasonRating, (a.id, winter.id)).games_played == 1


def test_move_session_writes_an_audit_entry(db, season, league_session):
    from datetime import date as date_type

    from sqlalchemy import select

    from mini_league.models import AuditLog, Season
    from mini_league.sessions import move_session_to_season

    winter = Season(name="Winter 2027", start_date=date_type(2027, 1, 1))
    db.add(winter)
    db.commit()
    move_session_to_season(db, league_session.id, winter.id)

    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "move_session_season")
    ).one()
    assert entry.payload == {
        "session_id": league_session.id,
        "before": season.id,
        "after": winter.id,
    }


def test_move_session_validation(db, season, league_session):
    from mini_league.sessions import move_session_to_season

    with pytest.raises(LookupError, match="season 999"):
        move_session_to_season(db, league_session.id, 999)
    with pytest.raises(LookupError, match="session 999"):
        move_session_to_season(db, 999, season.id)
    # Moving to the season it is already in is a no-op, not an error.
    assert move_session_to_season(db, league_session.id, season.id).season_id == season.id

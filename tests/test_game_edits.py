"""Editing, deleting, and restoring games, and the audit trail they leave."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mini_league.games import (
    TeamInput,
    delete_game,
    edit_game,
    record_game,
    restore_game,
    session_games,
)
from mini_league.models import AuditLog, GameTeam, PlayerSeasonRating


def snapshot(db, player_id, season_id) -> PlayerSeasonRating:
    return db.get(PlayerSeasonRating, (player_id, season_id))


def audit_actions(db) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_edit_flips_the_winner_and_replays(db, season, league_session, make_players):
    a, b = make_players(2)
    game = record_game(
        db, league_session.id, [TeamInput([a.id], 1, 5), TeamInput([b.id], 2, 3)]
    )
    assert snapshot(db, a.id, season.id).wins == 1
    winner_mu = snapshot(db, a.id, season.id).mu

    edit_game(
        db,
        game.id,
        teams=[TeamInput([a.id], 2, 3), TeamInput([b.id], 1, 5)],
    )

    assert snapshot(db, a.id, season.id).wins == 0
    assert snapshot(db, a.id, season.id).losses == 1
    assert snapshot(db, b.id, season.id).wins == 1
    assert snapshot(db, a.id, season.id).mu < winner_mu
    # Mirror image: the two players swapped places exactly.
    assert snapshot(db, a.id, season.id).mu == pytest.approx(25 - (winner_mu - 25))


def test_edit_can_change_rosters(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    game = record_game(
        db, league_session.id, [TeamInput([a.id, b.id], 1), TeamInput([c.id, d.id], 2)]
    )
    edit_game(db, game.id, teams=[TeamInput([a.id, c.id], 1), TeamInput([b.id, d.id], 2)])

    db.refresh(game)
    teams = sorted(game.teams, key=lambda t: t.team_index)
    assert [sorted(t.player_ids) for t in teams] == [
        sorted([a.id, c.id]),
        sorted([b.id, d.id]),
    ]
    assert snapshot(db, c.id, season.id).wins == 1
    assert snapshot(db, b.id, season.id).losses == 1
    # No orphaned team rows left behind.
    assert db.query(GameTeam).filter_by(game_id=game.id).count() == 2


def test_edit_can_drop_a_player_from_the_game(db, season, league_session, make_players):
    a, b, c = make_players(3)
    game = record_game(
        db, league_session.id, [TeamInput([a.id, b.id], 1), TeamInput([c.id], 2)]
    )
    assert snapshot(db, b.id, season.id) is not None

    edit_game(db, game.id, teams=[TeamInput([a.id], 1), TeamInput([c.id], 2)])
    # b played no games at all now, so they hold no rating in this season.
    assert snapshot(db, b.id, season.id) is None


def test_edit_updates_players_on_field(db, league_session, make_players):
    players = make_players(10)
    game = record_game(
        db,
        league_session.id,
        [TeamInput([p.id for p in players[:5]], 1), TeamInput([p.id for p in players[5:]], 2)],
    )
    assert game.players_on_field == 5
    edit_game(db, game.id, players_on_field=4)  # four a side, one sub each
    db.refresh(game)
    assert game.players_on_field == 4


def test_on_field_cannot_exceed_the_smaller_roster(db, league_session, make_players):
    """A team of three cannot put four on the pitch, whatever the other side has."""
    players = make_players(7)
    three = [p.id for p in players[:3]]
    four = [p.id for p in players[3:]]

    with pytest.raises(ValueError, match="the size of the smaller roster"):
        record_game(
            db,
            league_session.id,
            [TeamInput(three, 1), TeamInput(four, 2)],
            players_on_field=4,
        )

    game = record_game(db, league_session.id, [TeamInput(three, 1), TeamInput(four, 2)])
    assert game.players_on_field == 3
    with pytest.raises(ValueError, match="the size of the smaller roster"):
        edit_game(db, game.id, players_on_field=4)


def test_edit_metadata_only_keeps_teams(db, league_session, make_players):
    a, b = make_players(2)
    game = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    edit_game(db, game.id, round_number=7)
    db.refresh(game)
    assert game.round_number == 7
    assert [sorted(t.player_ids) for t in game.teams] == [[a.id], [b.id]]


def test_edit_writes_an_audit_entry_with_before_and_after(db, league_session, make_players):
    a, b = make_players(2)
    game = record_game(
        db, league_session.id, [TeamInput([a.id], 1, 5), TeamInput([b.id], 2, 2)]
    )
    edit_game(db, game.id, teams=[TeamInput([a.id], 2, 2), TeamInput([b.id], 1, 5)])

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "edit_game")).one()
    before_ranks = [t["rank"] for t in entry.payload["before"]["teams"]]
    after_ranks = [t["rank"] for t in entry.payload["after"]["teams"]]
    assert before_ranks == [1, 2]
    assert after_ranks == [2, 1]
    assert entry.payload["before"]["game_id"] == game.id


def test_invalid_edit_is_rejected(db, league_session, make_players):
    a, b = make_players(2)
    game = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    with pytest.raises(ValueError, match="more than one team"):
        edit_game(db, game.id, teams=[TeamInput([a.id], 1), TeamInput([a.id], 2)])
    with pytest.raises(ValueError, match="ties"):
        edit_game(db, game.id, teams=[TeamInput([a.id], 1), TeamInput([b.id], 1)])
    with pytest.raises(LookupError, match="game 999"):
        edit_game(db, 999, teams=[TeamInput([a.id], 1), TeamInput([b.id], 2)])


def test_delete_then_restore_round_trips(db, season, league_session, make_players):
    a, b = make_players(2)
    first = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    two_game_mu = snapshot(db, a.id, season.id).mu

    delete_game(db, first.id)
    assert snapshot(db, a.id, season.id).games_played == 1
    assert [g.id for g in session_games(db, league_session.id)] != [first.id]

    restore_game(db, first.id)
    assert snapshot(db, a.id, season.id).games_played == 2
    assert snapshot(db, a.id, season.id).mu == pytest.approx(two_game_mu)
    assert audit_actions(db) == ["delete_game", "restore_game"]


def test_delete_audit_entry_can_rebuild_the_game(db, league_session, make_players):
    a, b, c, d = make_players(4)
    game = record_game(
        db,
        league_session.id,
        [TeamInput([a.id, b.id], 1, 5), TeamInput([c.id, d.id], 2, 1)],
    )
    delete_game(db, game.id)

    entry = db.scalars(select(AuditLog).where(AuditLog.action == "delete_game")).one()
    before = entry.payload["before"]
    assert before["game_id"] == game.id
    assert before["players_on_field"] == 2
    assert before["round_number"] == 1
    assert [t["player_ids"] for t in before["teams"]] == [[a.id, b.id], [c.id, d.id]]
    assert [t["score"] for t in before["teams"]] == [5, 1]


def test_double_delete_and_restoring_a_live_game_are_refused(db, league_session, make_players):
    a, b = make_players(2)
    game = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    with pytest.raises(ValueError, match="not deleted"):
        restore_game(db, game.id)
    delete_game(db, game.id)
    with pytest.raises(ValueError, match="already deleted"):
        delete_game(db, game.id)


def test_session_games_hides_deleted_by_default(db, league_session, make_players):
    a, b = make_players(2)
    game = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    delete_game(db, game.id)
    assert session_games(db, league_session.id) == []
    assert [g.id for g in session_games(db, league_session.id, include_deleted=True)] == [game.id]


def test_deleted_round_number_is_reused(db, league_session, make_players):
    a, b = make_players(2)
    first = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    assert first.round_number == 1
    delete_game(db, first.id)
    replacement = record_game(
        db, league_session.id, [TeamInput([a.id], 2), TeamInput([b.id], 1)]
    )
    assert replacement.round_number == 1


def test_score_must_agree_with_the_winner(db, league_session, make_players):
    a, b = make_players(2)
    with pytest.raises(ValueError, match="is down as winning but scored"):
        record_game(db, league_session.id, [TeamInput([a.id], 1, 2), TeamInput([b.id], 2, 5)])
    with pytest.raises(ValueError, match="one of them has to change"):
        record_game(db, league_session.id, [TeamInput([a.id], 1, 5), TeamInput([b.id], 2, 5)])
    with pytest.raises(ValueError, match="negative"):
        record_game(db, league_session.id, [TeamInput([a.id], 1, -1), TeamInput([b.id], 2, 5)])


def test_one_sided_score_is_allowed(db, league_session, make_players):
    """Organizers sometimes record only the winning score."""
    a, b = make_players(2)
    game = record_game(
        db, league_session.id, [TeamInput([a.id], 1, 5), TeamInput([b.id], 2, None)]
    )
    assert [t.score for t in sorted(game.teams, key=lambda t: t.team_index)] == [5, None]

"""record_game + recompute_ratings: derived tables are a pure function of the games."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select
from trueskill import Rating

from mini_league.games import TeamInput, record_game
from mini_league.models import (
    Game,
    LeagueSession,
    PlayerSeasonRating,
    RatingHistory,
    Season,
    utcnow,
)
from mini_league.ratings import make_env, rate_game
from mini_league.recompute import recompute_all_ratings, recompute_ratings
from mini_league.settings import DEFAULT_RATING_CONFIG, RatingConfig

INITIAL_MU = DEFAULT_RATING_CONFIG.mu
INITIAL_SIGMA = DEFAULT_RATING_CONFIG.sigma


def history_for(db, player_id, season_id):
    return db.scalars(
        select(RatingHistory)
        .where(RatingHistory.player_id == player_id, RatingHistory.season_id == season_id)
        .order_by(RatingHistory.id)
    ).all()


def snapshot(db, player_id, season_id) -> PlayerSeasonRating | None:
    return db.get(PlayerSeasonRating, (player_id, season_id))


def ids(players):
    return [p.id for p in players]


def test_record_game_writes_history_and_snapshot(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    game = record_game(
        db,
        league_session.id,
        [TeamInput(ids([a, b]), rank=1, score=5), TeamInput(ids([c, d]), rank=2, score=3)],
    )

    assert game.id is not None
    assert game.round_number == 1
    assert game.players_on_field == 2
    assert [t.rank for t in game.teams] == [1, 2]
    assert [t.score for t in game.teams] == [5, 3]

    rows = db.scalars(select(RatingHistory)).all()
    assert len(rows) == 4
    assert all(r.game_id == game.id and r.season_id == season.id for r in rows)
    assert all((r.mu_before, r.sigma_before) == (INITIAL_MU, INITIAL_SIGMA) for r in rows)

    # Matches the pure ratings module exactly.
    env = make_env()
    expected = rate_game(env, [[Rating(), Rating()], [Rating(), Rating()]], ranks=[1, 2], players_on_field=2)
    for player, exp in zip([a, b, c, d], expected[0] + expected[1]):
        (row,) = history_for(db, player.id, season.id)
        assert row.mu_after == pytest.approx(exp.mu)
        assert row.sigma_after == pytest.approx(exp.sigma)
        snap = snapshot(db, player.id, season.id)
        assert snap.mu == pytest.approx(exp.mu)
        assert snap.sigma == pytest.approx(exp.sigma)
        assert snap.games_played == 1

    assert (snapshot(db, a.id, season.id).wins, snapshot(db, a.id, season.id).losses) == (1, 0)
    assert (snapshot(db, c.id, season.id).wins, snapshot(db, c.id, season.id).losses) == (0, 1)


def test_ratings_chain_across_games(db, season, league_session, make_players):
    a, b = make_players(2)
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])

    first, second = history_for(db, a.id, season.id)
    assert second.mu_before == pytest.approx(first.mu_after)
    assert second.sigma_before == pytest.approx(first.sigma_after)
    assert second.mu_after > first.mu_after

    snap = snapshot(db, a.id, season.id)
    assert (snap.games_played, snap.wins, snap.losses) == (2, 2, 0)
    assert snap.mu == pytest.approx(second.mu_after)

    env = make_env()
    r1 = rate_game(env, [[Rating()], [Rating()]], [1, 2], 1)
    r2 = rate_game(env, [[r1[0][0]], [r1[1][0]]], [1, 2], 1)
    assert snap.mu == pytest.approx(r2[0][0].mu)


def test_round_number_auto_increments(db, league_session, make_players):
    a, b = make_players(2)
    g1 = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    g2 = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    g3 = record_game(
        db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)], round_number=2
    )
    assert (g1.round_number, g2.round_number, g3.round_number) == (1, 2, 2)


def test_replay_is_ordered_by_played_at_not_insertion(db, season, league_session, make_players):
    a, b = make_players(2)
    later = datetime(2026, 9, 5, 10, 0)
    earlier = datetime(2026, 9, 5, 9, 0)
    late_game = record_game(
        db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)], played_at=later
    )
    early_game = record_game(
        db, league_session.id, [TeamInput([b.id], 1), TeamInput([a.id], 2)], played_at=earlier
    )

    rows = history_for(db, a.id, season.id)
    assert [r.game_id for r in rows] == [early_game.id, late_game.id]
    assert rows[0].mu_before == INITIAL_MU
    assert rows[1].mu_before == pytest.approx(rows[0].mu_after)


def test_soft_deleted_game_is_excluded(db, season, league_session, make_players):
    a, b = make_players(2)
    g1 = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    g2 = record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    assert snapshot(db, a.id, season.id).games_played == 2

    g1.deleted_at = utcnow()
    db.commit()
    recompute_ratings(db, season.id)

    rows = history_for(db, a.id, season.id)
    assert [r.game_id for r in rows] == [g2.id]
    assert rows[0].mu_before == INITIAL_MU
    assert snapshot(db, a.id, season.id).games_played == 1
    assert db.get(Game, g1.id) is not None  # still there for undo/audit


def test_recompute_is_idempotent(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    for _ in range(3):
        record_game(db, league_session.id, [TeamInput(ids([a, b]), 1), TeamInput(ids([c, d]), 2)])
    record_game(db, league_session.id, [TeamInput(ids([a, c]), 2), TeamInput(ids([b, d]), 1)])

    def state():
        hist = [
            (r.player_id, r.game_id, r.mu_before, r.sigma_before, r.mu_after, r.sigma_after)
            for r in db.scalars(select(RatingHistory).order_by(RatingHistory.player_id, RatingHistory.game_id))
        ]
        snaps = [
            (s.player_id, s.mu, s.sigma, s.games_played, s.wins, s.losses)
            for s in db.scalars(select(PlayerSeasonRating).order_by(PlayerSeasonRating.player_id))
        ]
        return hist, snaps

    before = state()
    recompute_ratings(db, season.id)
    recompute_ratings(db, season.id)
    assert state() == before
    assert len(before[0]) == 16 and len(before[1]) == 4


def test_seasons_are_independent(db, season, league_session, make_players):
    a, b = make_players(2)
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    season1_snapshot = (snapshot(db, a.id, season.id).mu, snapshot(db, a.id, season.id).sigma)

    season.end_date = date(2026, 12, 31)
    winter = Season(name="Winter 2027", start_date=date(2027, 1, 1))
    db.add(winter)
    db.commit()
    winter_session = LeagueSession(season_id=winter.id, date=date(2027, 1, 4))
    db.add(winter_session)
    db.commit()

    record_game(db, winter_session.id, [TeamInput([b.id], 1), TeamInput([a.id], 2)])

    # Winter starts fresh for everyone.
    (row,) = history_for(db, a.id, winter.id)
    assert (row.mu_before, row.sigma_before) == (INITIAL_MU, INITIAL_SIGMA)
    assert row.mu_after < INITIAL_MU
    # Fall is untouched by the winter game.
    assert (snapshot(db, a.id, season.id).mu, snapshot(db, a.id, season.id).sigma) == season1_snapshot
    assert snapshot(db, a.id, season.id).games_played == 1
    assert snapshot(db, a.id, winter.id).games_played == 1


def test_recompute_all_ratings_rebuilds_every_season(db, season, league_session, make_players):
    a, b = make_players(2)
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    winter = Season(name="Winter 2027", start_date=date(2027, 1, 1))
    db.add(winter)
    db.commit()
    ws = LeagueSession(season_id=winter.id, date=date(2027, 1, 4))
    db.add(ws)
    db.commit()
    record_game(db, ws.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])

    # Corrupt the derived tables, then rebuild.
    db.query(PlayerSeasonRating).delete()
    db.query(RatingHistory).delete()
    db.commit()
    assert db.query(RatingHistory).count() == 0

    recompute_all_ratings(db)
    assert db.query(RatingHistory).count() == 4
    assert {s.season_id for s in db.query(PlayerSeasonRating)} == {season.id, winter.id}


def test_partial_play_weights_used_in_replay(db, season, league_session, make_players):
    players = make_players(7)
    three, four = players[:3], players[3:]
    game = record_game(
        db,
        league_session.id,
        [TeamInput(ids(three), 1), TeamInput(ids(four), 2)],
    )
    assert game.players_on_field == 3  # defaults to the smaller roster

    env = make_env()
    expected = rate_game(
        env, [[Rating()] * 3, [Rating()] * 4], ranks=[1, 2], players_on_field=3
    )
    (full_row,) = history_for(db, three[0].id, season.id)
    (sub_row,) = history_for(db, four[0].id, season.id)
    assert full_row.mu_after == pytest.approx(expected[0][0].mu)
    assert sub_row.mu_after == pytest.approx(expected[1][0].mu)
    assert (INITIAL_MU - sub_row.mu_after) < (full_row.mu_after - INITIAL_MU)
    # Every roster member still gets the W/L on their record.
    assert snapshot(db, four[3].id, season.id).losses == 1


def test_three_team_game_only_rank_one_is_a_win(db, season, league_session, make_players):
    a, b, c = make_players(3)
    record_game(
        db,
        league_session.id,
        [TeamInput([a.id], 2), TeamInput([b.id], 1), TeamInput([c.id], 3)],
    )
    assert (snapshot(db, b.id, season.id).wins, snapshot(db, b.id, season.id).losses) == (1, 0)
    assert (snapshot(db, a.id, season.id).wins, snapshot(db, a.id, season.id).losses) == (0, 1)
    assert (snapshot(db, c.id, season.id).wins, snapshot(db, c.id, season.id).losses) == (0, 1)
    assert snapshot(db, b.id, season.id).mu > snapshot(db, a.id, season.id).mu > snapshot(db, c.id, season.id).mu


def test_changing_parameters_changes_replayed_ratings(db, season, league_session, make_players):
    a, b = make_players(2)
    record_game(db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)])
    default_mu = snapshot(db, a.id, season.id).mu

    recompute_ratings(db, season.id, RatingConfig(beta=25 / 3))
    assert snapshot(db, a.id, season.id).mu != pytest.approx(default_mu)
    assert snapshot(db, a.id, season.id).mu > INITIAL_MU


def test_recompute_unknown_season(db):
    with pytest.raises(ValueError, match="season 42"):
        recompute_ratings(db, 42)


@pytest.mark.parametrize(
    "build_teams, message",
    [
        (lambda p: [TeamInput([p[0].id], 1)], "two teams"),
        (lambda p: [TeamInput([], 1), TeamInput([p[1].id], 2)], "no players"),
        (lambda p: [TeamInput([p[0].id, p[0].id], 1), TeamInput([p[1].id], 2)], "twice"),
        (lambda p: [TeamInput([p[0].id], 1), TeamInput([p[0].id], 2)], "more than one team"),
        (lambda p: [TeamInput([p[0].id], 1), TeamInput([p[1].id], 1)], "ties"),
        (lambda p: [TeamInput([p[0].id], 2), TeamInput([p[1].id], 3)], "rank 1"),
        (lambda p: [TeamInput([p[0].id], 0), TeamInput([p[1].id], 1)], ">= 1"),
        (lambda p: [TeamInput([p[0].id], 1), TeamInput([9999], 2)], "unknown player"),
    ],
)
def test_record_game_validation(db, league_session, make_players, build_teams, message):
    players = make_players(2)
    with pytest.raises(ValueError, match=message):
        record_game(db, league_session.id, build_teams(players))
    assert db.query(Game).count() == 0


def test_record_game_rejects_merged_player(db, league_session, make_players):
    dup, keep, other = make_players(3)
    dup.merged_into = keep.id
    dup.active = False
    db.commit()
    with pytest.raises(ValueError, match="merged"):
        record_game(db, league_session.id, [TeamInput([dup.id], 1), TeamInput([other.id], 2)])


def test_record_game_players_on_field_bounds(db, league_session, make_players):
    a, b = make_players(2)
    with pytest.raises(ValueError, match="players_on_field"):
        record_game(
            db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)], players_on_field=2
        )
    with pytest.raises(ValueError, match="players_on_field"):
        record_game(
            db, league_session.id, [TeamInput([a.id], 1), TeamInput([b.id], 2)], players_on_field=0
        )


def test_record_game_unknown_session(db, make_players):
    a, b = make_players(2)
    with pytest.raises(ValueError, match="session 123"):
        record_game(db, 123, [TeamInput([a.id], 1), TeamInput([b.id], 2)])

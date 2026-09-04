"""Leaderboard and player-page read models (milestone 3)."""

from __future__ import annotations

from datetime import date
from statistics import median

import pytest

from mini_league.games import TeamInput, delete_game, record_game
from mini_league.leaderboard import (
    all_time_record,
    current_ratings,
    leaderboard,
    player_games,
    player_seasons,
    rating_history,
    starting_rating,
    team_ratings_before_each_game,
    typical_sigma,
    typical_team_size,
)
from mini_league.models import LeagueSession, Season
from mini_league.ratings import display_rating


def play(db, session_id, winners, losers, score=None):
    return record_game(
        db,
        session_id,
        [
            TeamInput([p.id for p in winners], 1, score[0] if score else None),
            TeamInput([p.id for p in losers], 2, score[1] if score else None),
        ],
    )


def test_empty_season_has_an_empty_leaderboard(db, season):
    assert leaderboard(db, season.id) == []


def test_ranking_is_by_displayed_rating(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    play(db, league_session.id, [a, b], [c, d])
    play(db, league_session.id, [a, b], [c, d])

    rows = leaderboard(db, season.id)
    assert [r.rank for r in rows] == [1, 2, 3, 4]
    assert {rows[0].player.name, rows[1].player.name} == {a.name, b.name}
    assert rows[0].rating >= rows[1].rating >= rows[2].rating >= rows[3].rating
    assert rows[0].wins == 2 and rows[0].losses == 0
    assert rows[0].record == "2-0"


def test_uncertain_player_does_not_top_the_table(db, season, league_session, make_players):
    """A single win must not outrank a steady record (design doc section 4.3)."""
    a, b, c, d = make_players(4)
    for _ in range(4):
        play(db, league_session.id, [a], [b])
    play(db, league_session.id, [c], [d])  # c has exactly one win

    rows = {r.player.name: r for r in leaderboard(db, season.id)}
    assert rows[a.name].rank < rows[c.name].rank
    assert rows[c.name].games_played == 1


def test_min_games_filter_and_hidden_count(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    for _ in range(3):
        play(db, league_session.id, [a], [b])
    play(db, league_session.id, [c], [d])

    assert len(leaderboard(db, season.id, min_games=0)) == 4
    filtered = leaderboard(db, season.id, min_games=3)
    assert {r.player.name for r in filtered} == {a.name, b.name}
    assert [r.rank for r in filtered] == [1, 2]


def test_inactive_and_merged_players_are_hidden_by_default(
    db, season, league_session, make_players
):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])
    b.active = False
    db.commit()

    assert [r.player.name for r in leaderboard(db, season.id)] == [a.name]
    assert len(leaderboard(db, season.id, include_inactive=True)) == 2


def test_current_ratings_fall_back_to_the_starting_rating(
    db, season, league_session, make_players
):
    a, b, c = make_players(3)
    play(db, league_session.id, [a], [b])

    ratings = current_ratings(db, season.id, [a.id, b.id, c.id])
    assert ratings[a.id].mu > 25
    assert ratings[b.id].mu < 25
    assert ratings[c.id].mu == starting_rating().mu
    assert display_rating(ratings[c.id]) == 0


def test_player_seasons_and_all_time_record(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])

    winter = Season(name="Winter 2027", start_date=date(2027, 1, 1))
    db.add(winter)
    db.commit()
    ws = LeagueSession(season_id=winter.id, date=date(2027, 1, 4))
    db.add(ws)
    db.commit()
    play(db, ws.id, [b], [a])

    summaries = player_seasons(db, a.id)
    assert [s.season.name for s in summaries] == ["Winter 2027", "Fall 2026"]
    assert summaries[0].wins == 0 and summaries[1].wins == 1

    totals = all_time_record(db, a.id)
    assert totals == {"wins": 1, "losses": 1, "games_played": 2, "seasons_played": 2}


def test_rating_history_is_chronological(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])
    play(db, league_session.id, [a], [b])

    history = rating_history(db, a.id, season.id)
    assert len(history) == 2
    assert history[0].mu_before == pytest.approx(25.0)
    assert history[1].mu_before == pytest.approx(history[0].mu_after)
    assert history[1].mu_after > history[0].mu_after


def test_player_games_lists_teammates_opponents_and_scores(
    db, season, league_session, make_players
):
    a, b, c, d = make_players(4)
    play(db, league_session.id, [a, b], [c, d], score=(5, 3))

    (appearance,) = player_games(db, a.id, season.id)
    assert appearance.won is True
    assert appearance.score_for == 5
    assert appearance.score_against == 3
    assert [p.name for p in appearance.teammates] == [b.name]
    assert sorted(p.name for p in appearance.opponents) == sorted([c.name, d.name])
    assert appearance.mu_before == pytest.approx(25.0)
    assert appearance.mu_after > 25
    assert appearance.round_number == 1


def test_player_games_excludes_deleted_games(db, season, league_session, make_players):
    from mini_league.games import delete_game

    a, b = make_players(2)
    game = play(db, league_session.id, [a], [b])
    assert len(player_games(db, a.id, season.id)) == 1
    delete_game(db, game.id)
    assert player_games(db, a.id, season.id) == []


def test_player_games_newest_first(db, season, league_session, make_players):
    a, b = make_players(2)
    first = play(db, league_session.id, [a], [b])
    second = play(db, league_session.id, [b], [a])
    assert [ap.game.id for ap in player_games(db, a.id, season.id)] == [
        second.id,
        first.id,
    ]


def test_seasons_are_reported_separately(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])
    winter = Season(name="Winter 2027", start_date=date(2027, 1, 1))
    db.add(winter)
    db.commit()
    ws = LeagueSession(season_id=winter.id, date=date(2027, 1, 4))
    db.add(ws)
    db.commit()
    play(db, ws.id, [a], [b])

    assert len(rating_history(db, a.id, season.id)) == 1
    assert len(rating_history(db, a.id, winter.id)) == 1
    assert leaderboard(db, winter.id)[0].games_played == 1


# --- describing the season's shape, for the rating explainer ---------------------


def test_typical_team_size_is_the_size_most_games_were_played_at(
    db, season, league_session, make_players
):
    a, b, c, d, e, f = make_players(6)
    play(db, league_session.id, [a, b, c], [d, e, f])
    play(db, league_session.id, [a, b, c], [d, e, f])
    play(db, league_session.id, [a, b], [c, d])

    assert typical_team_size(db, season.id) == 3


def test_typical_team_size_ignores_deleted_games(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    doomed = play(db, league_session.id, [a, b], [c, d])
    play(db, league_session.id, [a], [b])
    delete_game(db, doomed.id)

    assert typical_team_size(db, season.id) == 1


def test_typical_team_size_falls_back_when_nothing_has_been_played(db, season):
    assert typical_team_size(db, season.id, default=4) == 4


def test_typical_sigma_is_the_median_of_the_season(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])

    sigmas = sorted(r.sigma for r in leaderboard(db, season.id, min_games=0))
    assert typical_sigma(db, season.id) == pytest.approx(median(sigmas))
    # Two games in, the league is already surer than it was of a newcomer.
    assert typical_sigma(db, season.id) < starting_rating().sigma


def test_typical_sigma_of_an_empty_season_is_the_starting_uncertainty(db, season):
    assert typical_sigma(db, season.id) == pytest.approx(starting_rating().sigma)


def test_team_ratings_before_each_game_sum_the_pre_game_standings(
    db, season, league_session, make_players
):
    a, b, c, d = make_players(4)
    first = play(db, league_session.id, [a, b], [c, d])
    second = play(db, league_session.id, [a, b], [c, d])

    totals = team_ratings_before_each_game(db, league_session.id)
    start = display_rating(starting_rating())

    # Round one: nobody had played, so both sides are two starting ratings.
    assert totals[first.id] == {0: 2 * start, 1: 2 * start}
    # Round two sees round one's result: the winners are worth more, losers less.
    assert totals[second.id][0] > totals[first.id][0]
    assert totals[second.id][1] < totals[first.id][1]


def test_team_ratings_before_each_game_are_not_the_current_ones(
    db, season, league_session, make_players
):
    a, b, c, d = make_players(4)
    first = play(db, league_session.id, [a, b], [c, d])
    play(db, league_session.id, [a, b], [c, d])

    totals = team_ratings_before_each_game(db, league_session.id)
    now = current_ratings(db, season.id, [a.id, b.id])
    assert totals[first.id][0] != sum(display_rating(r) for r in now.values())


def test_team_ratings_skip_a_deleted_game(db, season, league_session, make_players):
    a, b, c, d = make_players(4)
    doomed = play(db, league_session.id, [a, b], [c, d])
    delete_game(db, doomed.id)

    assert team_ratings_before_each_game(db, league_session.id) == {}

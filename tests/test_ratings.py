"""Unit tests for the TrueSkill module: known inputs -> known outputs."""

from __future__ import annotations

from statistics import NormalDist

import pytest
from trueskill import Rating

from mini_league.ratings import (
    conservative_rating,
    display_rating,
    make_env,
    match_quality,
    new_rating,
    partial_play_weights,
    rate_game,
    win_probabilities,
    win_probability,
    win_probability_for_gap,
)
from mini_league.settings import RatingConfig

APPROX = 1e-3


@pytest.fixture
def env():
    return make_env()


def test_defaults_match_design_doc(env):
    assert env.mu == 25
    assert env.sigma == pytest.approx(25 / 3)
    assert env.beta == pytest.approx(25 / 6)
    assert env.tau == pytest.approx(25 / 300)
    assert env.draw_probability == 0
    r = new_rating(env)
    assert (r.mu, r.sigma) == (25, pytest.approx(25 / 3))


def test_custom_config_is_respected():
    cfg = RatingConfig(mu=1000, sigma=300, beta=150, tau=5, draw_probability=0.1)
    env = make_env(cfg)
    assert (env.mu, env.sigma, env.beta, env.tau) == (1000, 300, 150, 5)
    assert env.draw_probability == 0.1
    assert new_rating(env).mu == 1000


def test_1v1_known_values_library_defaults():
    """Canonical result from the trueskill docs (their default draw_probability=0.10)."""
    env = make_env(RatingConfig(draw_probability=0.10))
    (winner,), (loser,) = rate_game(env, [[Rating()], [Rating()]], ranks=[1, 2])
    assert winner.mu == pytest.approx(29.396, abs=APPROX)
    assert winner.sigma == pytest.approx(7.171, abs=APPROX)
    assert loser.mu == pytest.approx(20.604, abs=APPROX)
    assert loser.sigma == pytest.approx(7.171, abs=APPROX)


def test_1v1_known_values_no_draws(env):
    """With draw_probability=0 (design doc), a 1v1 between new players is analytic.

    c^2 = 2*beta^2 + 2*(sigma^2 + tau^2); v = pdf(0)/cdf(0); w = v*(v+0)
    mu_after = mu +/- (sigma^2 + tau^2)/c * v
    sigma_after^2 = (sigma^2 + tau^2) * (1 - (sigma^2 + tau^2)/c^2 * w)
    """
    sigma_sq = env.sigma**2 + env.tau**2
    c = (2 * env.beta**2 + 2 * sigma_sq) ** 0.5
    v = NormalDist().pdf(0) / NormalDist().cdf(0)
    w = v * v
    expected_mu_delta = sigma_sq / c * v
    expected_sigma = (sigma_sq * (1 - sigma_sq / c**2 * w)) ** 0.5

    (winner,), (loser,) = rate_game(env, [[Rating()], [Rating()]], ranks=[1, 2])
    assert winner.mu == pytest.approx(25 + expected_mu_delta, abs=1e-6)
    assert loser.mu == pytest.approx(25 - expected_mu_delta, abs=1e-6)
    assert winner.sigma == pytest.approx(expected_sigma, abs=1e-6)
    assert loser.sigma == pytest.approx(expected_sigma, abs=1e-6)
    assert winner.mu == pytest.approx(29.205, abs=APPROX)


def test_rank_order_not_team_order(env):
    """rank=1 marks the winner regardless of list position."""
    (a,), (b,) = rate_game(env, [[Rating()], [Rating()]], ranks=[2, 1])
    assert b.mu > 25 > a.mu


def test_team_game_moves_all_players(env):
    teams = [[Rating(), Rating(), Rating()], [Rating(), Rating(), Rating()]]
    winners, losers = rate_game(env, teams, ranks=[1, 2])
    assert all(r.mu > 25 for r in winners)
    assert all(r.mu < 25 for r in losers)
    assert all(r.sigma < 25 / 3 for r in winners + losers)
    # Symmetric new players: equal and opposite updates.
    assert winners[0].mu - 25 == pytest.approx(25 - losers[0].mu, abs=1e-9)


def test_upset_moves_more_than_expected_result(env):
    strong, weak = Rating(35, 3), Rating(20, 3)
    (s1,), (w1,) = rate_game(env, [[strong], [weak]], ranks=[1, 2])  # expected
    (s2,), (w2,) = rate_game(env, [[strong], [weak]], ranks=[2, 1])  # upset
    assert abs(s2.mu - strong.mu) > abs(s1.mu - strong.mu)
    assert abs(w2.mu - weak.mu) > abs(w1.mu - weak.mu)


def test_established_player_moves_less_than_new_player(env):
    veteran = Rating(25, 1.0)
    rookie = Rating(25, 25 / 3)
    (v,), (r,) = rate_game(env, [[veteran], [rookie]], ranks=[1, 2])
    assert abs(v.mu - 25) < abs(r.mu - 25)


def test_three_team_round_robin_ranks(env):
    teams = [[Rating()], [Rating()], [Rating()]]
    first, second, third = rate_game(env, teams, ranks=[1, 2, 3])
    assert first[0].mu > second[0].mu > third[0].mu


def test_ties_rejected_when_draw_probability_zero(env):
    with pytest.raises(ValueError, match="ties"):
        rate_game(env, [[Rating()], [Rating()]], ranks=[1, 1])


def test_ties_allowed_when_draw_probability_positive():
    env = make_env(RatingConfig(draw_probability=0.1))
    (a,), (b,) = rate_game(env, [[Rating()], [Rating()]], ranks=[1, 1])
    assert a.mu == pytest.approx(b.mu)
    assert a.sigma < 25 / 3


@pytest.mark.parametrize(
    "teams, ranks, message",
    [
        ([[Rating()]], [1], "two teams"),
        ([[Rating()], []], [1, 2], "at least one player"),
        ([[Rating()], [Rating()]], [1], "one rank per team"),
        ([[Rating()], [Rating()]], [0, 1], ">= 1"),
        ([[Rating()], [Rating()]], [1.0, 2], ">= 1"),
    ],
)
def test_rate_game_validation(env, teams, ranks, message):
    with pytest.raises(ValueError, match=message):
        rate_game(env, teams, ranks)


# --- partial play (section 5.3) -------------------------------------------------


def test_partial_play_weights_for_sub_team():
    assert partial_play_weights([3, 4], players_on_field=3) == [[1.0] * 3, [0.75] * 4]
    assert partial_play_weights([4, 4], players_on_field=4) == [[1.0] * 4, [1.0] * 4]
    # A team smaller than players_on_field is never treated as more than full strength.
    assert partial_play_weights([2, 3], players_on_field=3) == [[1.0] * 2, [1.0] * 3]


def test_partial_play_weights_validation():
    with pytest.raises(ValueError):
        partial_play_weights([3, 4], players_on_field=0)
    with pytest.raises(ValueError):
        partial_play_weights([3, 0], players_on_field=3)


def test_sub_team_updates_shrink_proportionally(env):
    three = [Rating() for _ in range(3)]
    four = [Rating() for _ in range(4)]
    new_three, new_four = rate_game(env, [three, four], ranks=[1, 2], players_on_field=3)
    delta_full = new_three[0].mu - 25
    delta_sub = 25 - new_four[0].mu
    assert delta_full > 0 and delta_sub > 0
    assert delta_sub < delta_full
    # Unweighted, the same game would move the four-team's players more (they are the
    # "stronger" side on paper), so the weights clearly changed the outcome.
    _, unweighted_four = rate_game(env, [three, four], ranks=[1, 2])
    assert (25 - unweighted_four[0].mu) > delta_sub


def test_weighted_game_matches_library_weights_directly(env):
    three = [Rating() for _ in range(3)]
    four = [Rating() for _ in range(4)]
    ours = rate_game(env, [three, four], ranks=[2, 1], players_on_field=3)
    theirs = env.rate([three, four], ranks=[2, 1], weights=[[1.0] * 3, [0.75] * 4])
    for our_team, their_team in zip(ours, theirs):
        for a, b in zip(our_team, their_team):
            assert (a.mu, a.sigma) == (pytest.approx(b.mu), pytest.approx(b.sigma))


# --- win probability -------------------------------------------------------------


def test_equal_teams_are_fifty_fifty(env):
    assert win_probability(env, [Rating()], [Rating()]) == pytest.approx(0.5)
    assert win_probability(env, [Rating()] * 3, [Rating()] * 3) == pytest.approx(0.5)


def test_win_probability_known_value(env):
    a, b = Rating(30, 25 / 3), Rating(25, 25 / 3)
    expected = NormalDist().cdf(5 / (2 * (a.sigma**2 + env.beta**2)) ** 0.5)
    assert win_probability(env, [a], [b]) == pytest.approx(expected)
    assert expected > 0.6


def test_win_probability_is_complementary(env):
    a = [Rating(32, 4), Rating(28, 5)]
    b = [Rating(24, 3), Rating(30, 8)]
    assert win_probability(env, a, b) + win_probability(env, b, a) == pytest.approx(1.0)
    pa, pb = win_probabilities(env, [a, b])
    assert pa == pytest.approx(win_probability(env, a, b))
    assert pa + pb == pytest.approx(1.0)


def test_uneven_rosters_are_even_with_weights(env):
    three = [Rating()] * 3
    four = [Rating()] * 4
    p_unweighted, _ = win_probabilities(env, [three, four])
    p_weighted, _ = win_probabilities(env, [three, four], players_on_field=3)
    assert p_unweighted < 0.5  # on paper the 4-roster looks stronger
    assert p_weighted == pytest.approx(0.5)  # weights make the sub team comparable


def test_win_probability_validation(env):
    with pytest.raises(ValueError):
        win_probability(env, [], [Rating()])
    with pytest.raises(ValueError):
        win_probability(env, [Rating()], [Rating()], weights_a=[1.0, 1.0])
    with pytest.raises(ValueError):
        win_probabilities(env, [[Rating()], [Rating()], [Rating()]])


def test_match_quality_prefers_even_matches(env):
    even = match_quality(env, [[Rating(25, 2)], [Rating(25, 2)]])
    lopsided = match_quality(env, [[Rating(40, 2)], [Rating(10, 2)]])
    assert 0 < lopsided < even <= 1


# --- displayed rating (section 4.3) ---------------------------------------------


def test_conservative_and_display_rating():
    new = Rating()
    assert conservative_rating(new) == pytest.approx(0.0)
    assert display_rating(new) == 0
    established = Rating(30, 2)
    assert conservative_rating(established) == pytest.approx(24.0)
    assert display_rating(established) == 960

    cfg = RatingConfig(display_scale=40, display_offset=1000)
    assert display_rating(established, cfg) == 1960
    assert display_rating(new, cfg) == 1000


def test_uncertain_player_is_penalized_on_display():
    hot_rookie = Rating(29.4, 7.17)  # one win from new
    steady = Rating(27, 2)
    assert hot_rookie.mu > steady.mu
    assert display_rating(hot_rookie) < display_rating(steady)


# --- turning a gap on the board back into odds -----------------------------------


def test_no_gap_is_a_coin_flip():
    assert win_probability_for_gap(0, sigma=3.0) == pytest.approx(0.5)


def test_a_bigger_gap_wins_more_often():
    odds = [win_probability_for_gap(gap, sigma=3.0) for gap in (0, 100, 200, 400)]
    assert odds == sorted(odds)
    assert all(0.5 <= p < 1.0 for p in odds)


def test_a_gap_matters_less_the_more_players_share_the_pitch():
    """The point of the table: your edge dilutes across team-mates."""
    solo = win_probability_for_gap(200, sigma=3.0, team_size=1)
    threes = win_probability_for_gap(200, sigma=3.0, team_size=3)
    fives = win_probability_for_gap(200, sigma=3.0, team_size=5)
    assert solo > threes > fives > 0.5


def test_it_does_not_matter_who_on_the_team_carries_the_gap(env):
    """Only the team totals enter the model, which is what the page claims."""
    sigma, gap = 3.0, 300.0
    spread = gap / 40 / 3
    evenly_better = [Rating(25 + spread, sigma)] * 3
    level = [Rating(25, sigma)] * 3

    assert win_probability(env, evenly_better, level) == pytest.approx(
        win_probability_for_gap(gap, sigma=sigma, team_size=3)
    )


def test_gap_odds_follow_the_display_scale():
    """Halving the scale doubles the points a given edge is worth."""
    cfg = RatingConfig(display_scale=20)
    assert win_probability_for_gap(100, sigma=3.0, config=cfg) == pytest.approx(
        win_probability_for_gap(200, sigma=3.0)
    )


def test_gap_odds_reject_an_empty_team():
    with pytest.raises(ValueError, match="at least one player"):
        win_probability_for_gap(100, sigma=3.0, team_size=0)

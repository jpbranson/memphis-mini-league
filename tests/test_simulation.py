"""The validation simulator (design doc section 10, milestone 6).

The simulator is only worth anything if it would notice a broken rating system,
so these tests check it against cases whose answers are known in advance.
"""

from __future__ import annotations

from statistics import fmean

import pytest

from mini_league.settings import RatingConfig, TeamGenConfig
from mini_league.simulation import (
    SimulationConfig,
    pearson,
    rank_with_ties,
    simulate_league,
    spearman,
)

FAST = TeamGenConfig(sample_size=60)


def quick(**overrides) -> SimulationConfig:
    base = {
        "player_count": 12,
        "sessions": 12,
        "attendance": (4, 8),
        "team_gen": FAST,
        "seed": 1,
    }
    return SimulationConfig(**{**base, **overrides})


def average(metric, seeds=range(4), **overrides) -> float:
    values = []
    for seed in seeds:
        result = simulate_league(quick(seed=seed, **overrides))
        values.append(metric(result))
    return fmean(values)


# --- statistics ------------------------------------------------------------------


def test_ranks_share_positions_across_ties():
    assert rank_with_ties([10, 20, 30]) == [1, 2, 3]
    assert rank_with_ties([5, 5, 9]) == [1.5, 1.5, 3]
    assert rank_with_ties([7, 7, 7]) == [2, 2, 2]


def test_pearson_known_values():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)
    assert pearson([1, 2, 3], [5, 5, 5]) == 0.0
    with pytest.raises(ValueError):
        pearson([1, 2], [1])


def test_spearman_ignores_scale_and_catches_order():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    # Monotonic but not linear: rank correlation should still be perfect.
    assert spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1], [1]) == 0.0


# --- the simulation runs sensibly ------------------------------------------------


def test_a_run_produces_games_and_players():
    result = simulate_league(quick())
    assert result.games_played > 0
    assert len(result.players) == 12
    assert result.rated, "somebody must have played"
    for player in result.rated:
        assert player.games == player.wins + player.losses
        assert player.mean_on_field > 0


def test_the_same_seed_gives_the_same_league():
    first = simulate_league(quick(seed=7))
    second = simulate_league(quick(seed=7))
    assert [p.mu for p in first.players] == [p.mu for p in second.players]
    assert first.games_played == second.games_played


def test_different_seeds_give_different_leagues():
    first = simulate_league(quick(seed=1))
    second = simulate_league(quick(seed=2))
    assert [p.mu for p in first.players] != [p.mu for p in second.players]


def test_ratings_track_true_skill_better_than_chance():
    assert average(lambda r: r.final_spearman) > 0.3


def test_convergence_is_recorded_and_improves():
    result = simulate_league(quick(sessions=30))
    assert len(result.convergence) > 5
    games = [g for g, _ in result.convergence]
    assert games == sorted(games), "games per player only goes up"
    early = fmean([rho for _, rho in result.convergence[:3]])
    late = fmean([rho for _, rho in result.convergence[-3:]])
    assert late > early


# --- it would notice a rating system that was wrong ------------------------------


def test_a_quieter_world_is_easier_to_rate():
    """If results were nearly deterministic, the order should come out cleaner."""
    noisy = average(lambda r: r.final_spearman, performance_sigma=6.0)
    quiet = average(lambda r: r.final_spearman, performance_sigma=1.0)
    assert quiet > noisy


def test_wider_skill_gaps_are_easier_to_rank():
    narrow = average(lambda r: r.final_spearman, skill_spread=1.5)
    wide = average(lambda r: r.final_spearman, skill_spread=10.0)
    assert wide > narrow


def test_more_games_beat_fewer():
    few = average(lambda r: r.final_spearman, sessions=6)
    many = average(lambda r: r.final_spearman, sessions=30)
    assert many > few


def test_one_on_one_needs_fewer_games_than_five_a_side():
    """Team games spread one result across ten players, so each learns less."""
    singles = average(
        lambda r: r.final_spearman / max(r.mean_games_per_player, 1),
        team_size=1,
        max_on_field=1,
        attendance=(4, 6),
        sessions=20,
    )
    fives = average(
        lambda r: r.final_spearman / max(r.mean_games_per_player, 1),
        team_size=5,
        max_on_field=5,
        attendance=(10, 12),
        sessions=20,
    )
    assert singles > fives


# --- the questions the design doc asks -------------------------------------------


def test_team_size_does_not_bias_ratings():
    for size in (2, 4):
        bias = average(
            lambda r: r.team_size_bias,
            team_size=size,
            max_on_field=size,
            attendance=(size * 2, size * 2 + 2),
            sessions=20,
        )
        assert abs(bias) < 0.25, f"{size}v{size} skewed ratings by size"


def test_uneven_teams_do_not_favour_the_bigger_roster():
    rates = []
    for seed in range(6):
        result = simulate_league(quick(seed=seed, sessions=25, attendance=(5, 9)))
        if result.bigger_roster_win_rate is not None:
            rates.append(result.bigger_roster_win_rate)
    assert rates, "the setup should have produced uneven games"
    assert 0.4 < fmean(rates) < 0.6


def test_partial_play_is_what_keeps_substitutes_fairly_rated():
    """The control: with the same world but no partial play, subs are mis-rated."""
    seeds = range(8)
    with_weights = fmean(
        simulate_league(quick(seed=s, sessions=25, attendance=(5, 9))).substitute_bias
        for s in seeds
    )
    without = fmean(
        simulate_league(
            quick(seed=s, sessions=25, attendance=(5, 9), use_partial_play=False)
        ).substitute_bias
        for s in seeds
    )
    assert abs(with_weights) < abs(without) / 2
    assert without < -0.15, "expected substitutes to be under-rated without it"


def test_partial_play_also_lowers_the_overall_error():
    seeds = range(6)
    with_weights = fmean(
        simulate_league(
            quick(seed=s, sessions=25, attendance=(5, 9))
        ).mean_absolute_error
        for s in seeds
    )
    without = fmean(
        simulate_league(
            quick(seed=s, sessions=25, attendance=(5, 9), use_partial_play=False)
        ).mean_absolute_error
        for s in seeds
    )
    assert with_weights < without


def test_predictions_are_roughly_calibrated():
    result = simulate_league(quick(sessions=40))
    assert result.calibration, "predictions should have been bucketed"
    assert result.calibration_error < 0.2
    for predicted, actual, _ in result.calibration:
        assert 0.0 <= predicted <= 1.0
        assert 0.0 <= actual <= 1.0


# --- configuration is honoured ---------------------------------------------------


def test_team_size_limits_who_plays():
    result = simulate_league(quick(team_size=2, max_on_field=2, attendance=(6, 8)))
    assert all(p.mean_on_field <= 2 for p in result.rated)


def test_the_on_field_cap_is_respected():
    result = simulate_league(quick(max_on_field=3, attendance=(8, 8)))
    assert all(p.mean_on_field <= 3 for p in result.rated)
    assert any(p.substitute_games > 0 for p in result.rated), "expected substitutes"


def test_rating_config_reaches_the_simulation():
    result = simulate_league(quick(rating=RatingConfig(mu=1000, sigma=200, beta=100)))
    assert all(500 < p.mu < 1500 for p in result.rated)


def test_attendance_range_is_validated():
    with pytest.raises(ValueError, match="attendance range"):
        simulate_league(quick(player_count=4, attendance=(9, 12)))


def test_spearman_at_reports_a_fixed_point_in_the_run():
    result = simulate_league(quick(sessions=25))
    assert result.spearman_at(0) is not None
    assert result.spearman_at(10_000) is None

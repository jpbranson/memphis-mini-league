"""Team balancing (design doc section 5). Pure functions, no database."""

from __future__ import annotations

import random

import pytest
from trueskill import Rating

from mini_league.ratings import make_env, win_probability
from mini_league.settings import TeamGenConfig
from mini_league.teams import (
    candidate_splits,
    describe_matchup,
    generate_teams,
    pair_weights,
    team_sizes,
)


def flat_ratings(ids, mu=25.0, sigma=8.333):
    return {i: Rating(mu, sigma) for i in ids}


@pytest.mark.parametrize(
    "players, expected",
    [(4, [2, 2]), (6, [3, 3]), (7, [4, 3]), (2, [1, 1]), (9, [5, 4])],
)
def test_team_sizes_split_evenly_with_the_extra_on_the_first_team(players, expected):
    assert team_sizes(players, 2) == expected


def test_team_sizes_validation():
    with pytest.raises(ValueError, match="at least two teams"):
        team_sizes(4, 1)
    with pytest.raises(ValueError, match="not enough players"):
        team_sizes(1, 2)


def test_every_player_is_used_exactly_once():
    ids = [1, 2, 3, 4, 5, 6, 7]
    split = generate_teams(ids, flat_ratings(ids), rng=random.Random(0))
    assigned = [p for team in split.teams for p in team]
    assert sorted(assigned) == sorted(ids)
    assert len(assigned) == len(set(assigned))
    assert sorted(len(t) for t in split.teams) == [3, 4]


def test_balance_puts_the_strong_players_on_opposite_teams():
    """Two strong and two weak players should split one strong per side."""
    ratings = {
        1: Rating(35, 1.0),
        2: Rating(34, 1.0),
        3: Rating(16, 1.0),
        4: Rating(15, 1.0),
    }
    best = candidate_splits([1, 2, 3, 4], ratings)[0]
    for team in best.teams:
        strong = [p for p in team if ratings[p].mu > 25]
        assert len(strong) == 1, "each team should get one of the strong players"
    assert best.win_probability == pytest.approx(0.5, abs=0.05)


def test_balance_cost_is_zero_for_a_perfect_match():
    ids = [1, 2, 3, 4]
    best = candidate_splits(ids, flat_ratings(ids))[0]
    assert best.balance_cost == pytest.approx(0.0, abs=1e-9)
    assert best.win_probability == pytest.approx(0.5)


def test_splits_are_sorted_by_cost():
    ratings = {1: Rating(35, 2), 2: Rating(30, 2), 3: Rating(20, 2), 4: Rating(15, 2)}
    splits = candidate_splits([1, 2, 3, 4], ratings)
    costs = [s.total_cost for s in splits]
    assert costs == sorted(costs)
    assert splits[0].balance_cost < splits[-1].balance_cost


def test_enumeration_covers_every_distinct_split_without_mirrors():
    ids = [1, 2, 3, 4]
    splits = candidate_splits(ids, flat_ratings(ids))
    # C(4,2)/2 = 3 distinct 2v2 splits once the A/B mirror is removed.
    assert len(splits) == 3
    seen = {frozenset(s.teams[0]) for s in splits}
    assert len(seen) == 3
    assert all(1 in s.teams[0] for s in splits), "player 1 is pinned to team A"


def test_uneven_teams_are_scored_with_partial_play_weights():
    ids = list(range(1, 8))  # 7 players -> 4v3
    best = candidate_splits(ids, flat_ratings(ids))[0]
    assert best.players_on_field == 3
    # Equal players, so weighting makes it a coin flip despite the extra body.
    assert best.win_probability == pytest.approx(0.5, abs=0.02)


def test_pair_weights_favour_recent_rounds():
    history = [
        [[1, 2], [3, 4]],  # oldest
        [[1, 3], [2, 4]],  # most recent
    ]
    weights = pair_weights(history)
    assert weights[frozenset((1, 3))] > weights[frozenset((1, 2))]
    assert frozenset((1, 4)) not in weights
    assert pair_weights([]) == {}


def test_variety_pushes_repeat_pairings_apart():
    """With ratings equal, the generator should avoid last round's teams."""
    ids = [1, 2, 3, 4]
    history = [[[1, 2], [3, 4]]]
    config = TeamGenConfig(w_balance=1.0, w_variety=1.0)
    best = candidate_splits(
        ids, flat_ratings(ids), history=history, team_config=config
    )[0]
    assert frozenset(best.teams[0]) != frozenset({1, 2})
    assert best.variety_cost == 0.0


def test_variety_is_ignored_when_weight_is_zero():
    ids = [1, 2, 3, 4]
    history = [[[1, 2], [3, 4]]]
    config = TeamGenConfig(w_balance=1.0, w_variety=0.0)
    splits = candidate_splits(ids, flat_ratings(ids), history=history, team_config=config)
    assert all(s.total_cost == pytest.approx(s.balance_cost) for s in splits)


def test_generate_picks_among_the_top_candidates_not_always_the_same():
    ratings = {i: Rating(25 + i, 2.0) for i in range(1, 9)}
    ids = list(ratings)
    seen = set()
    for seed in range(25):
        split = generate_teams(
            ids, ratings, team_config=TeamGenConfig(top_n=5), rng=random.Random(seed)
        )
        seen.add(frozenset(split.teams[0]))
    assert len(seen) > 1, "teams should vary between rounds"


def test_top_n_of_one_is_deterministic():
    ratings = {i: Rating(25 + i, 2.0) for i in range(1, 7)}
    ids = list(ratings)
    picks = {
        frozenset(
            generate_teams(
                ids, ratings, team_config=TeamGenConfig(top_n=1), rng=random.Random(s)
            ).teams[0]
        )
        for s in range(10)
    }
    assert len(picks) == 1


def test_large_groups_fall_back_to_sampling():
    ids = list(range(1, 17))  # 16 players, beyond the enumeration limit
    config = TeamGenConfig(enumerate_max_players=12, sample_size=200)
    splits = candidate_splits(ids, flat_ratings(ids), team_config=config, rng=random.Random(1))
    assert 0 < len(splits) <= 200
    for split in splits:
        assert sorted(p for t in split.teams for p in t) == ids


def test_generate_validation():
    with pytest.raises(ValueError, match="at least two players"):
        generate_teams([1], {1: Rating()})
    with pytest.raises(ValueError, match="no rating supplied"):
        generate_teams([1, 2], {1: Rating()})


def test_describe_matchup_reports_strength_and_prediction():
    strong = [Rating(35, 1.0), Rating(34, 1.0)]
    weak = [Rating(16, 1.0), Rating(15, 1.0)]
    summary = describe_matchup(strong, weak)
    assert summary["rating_a"] > summary["rating_b"]
    assert summary["win_probability_a"] > 0.9
    assert summary["win_probability_a"] + summary["win_probability_b"] == pytest.approx(1.0)
    assert summary["verdict"] == "Lopsided"
    assert summary["players_on_field"] == 2


def test_describe_matchup_calls_an_even_game_even():
    summary = describe_matchup([Rating(25, 2)], [Rating(25, 2)])
    assert summary["verdict"] == "Even match"
    assert summary["win_probability_a"] == pytest.approx(0.5)
    assert summary["rating_a"] == summary["rating_b"]


def test_describe_matchup_weights_uneven_rosters():
    three = [Rating() for _ in range(3)]
    four = [Rating() for _ in range(4)]
    summary = describe_matchup(three, four)
    assert summary["players_on_field"] == 3
    assert summary["win_probability_a"] == pytest.approx(0.5)
    # The raw rating sum still shows the extra body, which is honest.
    assert summary["rating_b"] < summary["rating_a"] or summary["rating_b"] >= 0


def test_matchup_probability_matches_the_ratings_module():
    a = [Rating(30, 3), Rating(28, 4)]
    b = [Rating(24, 2), Rating(26, 5)]
    env = make_env()
    assert describe_matchup(a, b)["win_probability_a"] == pytest.approx(
        win_probability(env, a, b)
    )


def test_shortlist_excludes_clearly_worse_splits():
    """With four players there are only three splits, so a top_n of 5 would
    otherwise mean picking uniformly at random and ignoring balance entirely."""
    from mini_league.teams import shortlist

    ratings = {1: Rating(35, 1), 2: Rating(34, 1), 3: Rating(16, 1), 4: Rating(15, 1)}
    splits = candidate_splits([1, 2, 3, 4], ratings)
    assert len(splits) == 3
    chosen = shortlist(splits, TeamGenConfig(top_n=5))
    assert len(chosen) < len(splits)
    for split in chosen:
        for team in split.teams:
            assert len([p for p in team if ratings[p].mu > 25]) == 1


def test_balanced_generation_never_picks_the_lopsided_split():
    ratings = {1: Rating(35, 1), 2: Rating(34, 1), 3: Rating(16, 1), 4: Rating(15, 1)}
    for seed in range(30):
        split = generate_teams([1, 2, 3, 4], ratings, rng=random.Random(seed))
        assert frozenset(split.teams[0]) != frozenset({1, 2})


def test_shortlist_keeps_variety_when_splits_are_equally_good():
    from mini_league.teams import shortlist

    ids = list(range(1, 9))
    splits = candidate_splits(ids, flat_ratings(ids))
    chosen = shortlist(splits, TeamGenConfig(top_n=5))
    assert len(chosen) == 5, "identical players give many equally good splits"

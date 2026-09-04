"""Balanced team generation (design doc section 5). Pure functions, no database.

Given the checked-in players, their ratings, and who has already played together
this session, produce candidate splits scored on two things:

- balance: how close the predicted win probability is to 50/50
- variety: how many teammate pairs are repeats from earlier rounds

The best few are kept and one is chosen at random, so the same group does not
get the same teams every round.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Mapping, Sequence

from trueskill import Rating, TrueSkill

from .ratings import make_env, partial_play_weights, win_probability
from .settings import DEFAULT_RATING_CONFIG, RatingConfig, TeamGenConfig


@dataclass(frozen=True)
class Split:
    """One way of dividing the players, with its scores."""

    teams: tuple[tuple[int, ...], ...]
    players_on_field: int
    win_probability: float  # for teams[0]
    balance_cost: float
    variety_cost: float
    total_cost: float

    @property
    def team_list(self) -> list[list[int]]:
        return [list(t) for t in self.teams]


def team_sizes(player_count: int, team_count: int = 2) -> list[int]:
    """Even split, with the earlier teams taking the extra player when it is odd."""
    if team_count < 2:
        raise ValueError("a game needs at least two teams")
    if player_count < team_count:
        raise ValueError("not enough players for that many teams")
    base, extra = divmod(player_count, team_count)
    return [base + (1 if i < extra else 0) for i in range(team_count)]


def pair_weights(history: Sequence[Sequence[Sequence[int]]]) -> dict[frozenset[int], float]:
    """How recently each pair of players shared a team.

    `history` is oldest game first, each game a list of team rosters. The most
    recent round counts about 1.0, earlier rounds progressively less, so the
    generator avoids repeating this round's pairings hardest.
    """
    weights: dict[frozenset[int], float] = {}
    total = len(history)
    if total == 0:
        return weights
    for index, game in enumerate(history):
        recency = (index + 1) / total
        for roster in game:
            for a, b in combinations(sorted(roster), 2):
                key = frozenset((a, b))
                weights[key] = weights.get(key, 0.0) + recency
    return weights


def _variety_cost(
    teams: Sequence[Sequence[int]], weights: Mapping[frozenset[int], float]
) -> float:
    """Mean repeat-weight per teammate pair, so it is comparable to balance cost."""
    total = 0.0
    pairs = 0
    for roster in teams:
        for a, b in combinations(sorted(roster), 2):
            total += weights.get(frozenset((a, b)), 0.0)
            pairs += 1
    return total / pairs if pairs else 0.0


def _score_split(
    env: TrueSkill,
    teams: Sequence[Sequence[int]],
    ratings: Mapping[int, Rating],
    weights: Mapping[frozenset[int], float],
    config: TeamGenConfig,
) -> Split:
    sizes = [len(t) for t in teams]
    on_field = min(sizes)
    play_weights = partial_play_weights(sizes, on_field)
    probability = win_probability(
        env,
        [ratings[pid] for pid in teams[0]],
        [ratings[pid] for pid in teams[1]],
        play_weights[0],
        play_weights[1],
    )
    balance = abs(2 * probability - 1)  # 0 when the match is a coin flip
    variety = _variety_cost(teams, weights)
    return Split(
        teams=tuple(tuple(t) for t in teams),
        players_on_field=on_field,
        win_probability=probability,
        balance_cost=balance,
        variety_cost=variety,
        total_cost=config.w_balance * balance + config.w_variety * variety,
    )


def _enumerate_two_team_splits(
    players: Sequence[int], sizes: Sequence[int]
) -> Iterable[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Every way to split into two teams, without mirror duplicates.

    The first player is pinned to team A, which halves the search and removes
    the A/B mirror of each split when the teams are the same size.
    """
    first, rest = players[0], list(players[1:])
    for combo in combinations(rest, sizes[0] - 1):
        team_a = (first, *combo)
        team_b = tuple(p for p in rest if p not in set(combo))
        if len(team_b) == sizes[1]:
            yield team_a, team_b


def _sample_two_team_splits(
    players: Sequence[int], sizes: Sequence[int], count: int, rng: random.Random
) -> Iterable[tuple[tuple[int, ...], tuple[int, ...]]]:
    seen: set[frozenset[int]] = set()
    pool = list(players)
    for _ in range(count):
        rng.shuffle(pool)
        team_a = tuple(pool[: sizes[0]])
        key = frozenset(team_a)
        if key in seen:
            continue
        seen.add(key)
        yield team_a, tuple(pool[sizes[0] :])


def candidate_splits(
    player_ids: Sequence[int],
    ratings: Mapping[int, Rating],
    *,
    history: Sequence[Sequence[Sequence[int]]] = (),
    team_config: TeamGenConfig | None = None,
    rating_config: RatingConfig = DEFAULT_RATING_CONFIG,
    rng: random.Random | None = None,
) -> list[Split]:
    """Every scored split, best (lowest cost) first. Two teams only for now."""
    team_config = team_config or TeamGenConfig()
    rng = rng or random.Random()
    players = list(player_ids)
    if len(players) < 2:
        raise ValueError("need at least two players to make teams")
    missing = [p for p in players if p not in ratings]
    if missing:
        raise ValueError(f"no rating supplied for players: {missing}")

    sizes = team_sizes(len(players), 2)
    env = make_env(rating_config)
    weights = pair_weights(history)

    if len(players) <= team_config.enumerate_max_players:
        raw = _enumerate_two_team_splits(players, sizes)
    else:
        raw = _sample_two_team_splits(players, sizes, team_config.sample_size, rng)

    scored = [_score_split(env, [a, b], ratings, weights, team_config) for a, b in raw]
    scored.sort(key=lambda s: (s.total_cost, s.teams))
    return scored


def generate_teams(
    player_ids: Sequence[int],
    ratings: Mapping[int, Rating],
    *,
    history: Sequence[Sequence[Sequence[int]]] = (),
    team_config: TeamGenConfig | None = None,
    rating_config: RatingConfig = DEFAULT_RATING_CONFIG,
    rng: random.Random | None = None,
) -> Split:
    """Pick one split at random from the best few, so teams are not deterministic."""
    team_config = team_config or TeamGenConfig()
    rng = rng or random.Random()
    splits = candidate_splits(
        player_ids,
        ratings,
        history=history,
        team_config=team_config,
        rating_config=rating_config,
        rng=rng,
    )
    return rng.choice(shortlist(splits, team_config))


def shortlist(splits: Sequence[Split], config: TeamGenConfig) -> list[Split]:
    """The splits worth choosing between: near the best, and at most top_n.

    Both limits matter. The count keeps the choice varied for a big group with
    thousands of near-identical splits; the tolerance stops a small group, where
    only a handful of splits exist, from filling the quota with bad teams.
    """
    if not splits:
        raise ValueError("no candidate splits")
    best = splits[0].total_cost
    near_best = [s for s in splits if s.total_cost <= best + config.cost_tolerance]
    return near_best[: max(1, config.top_n)] or [splits[0]]


def describe_matchup(
    ratings_a: Sequence[Rating],
    ratings_b: Sequence[Rating],
    *,
    rating_config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> dict:
    """Team strengths and the predicted result, for showing next to the teams."""
    from .ratings import display_rating

    env = make_env(rating_config)
    sizes = [len(ratings_a), len(ratings_b)]
    on_field = min(sizes)
    weights = partial_play_weights(sizes, on_field)
    probability = win_probability(env, ratings_a, ratings_b, weights[0], weights[1])
    gap = abs(2 * probability - 1)
    if gap < 0.10:
        verdict = "Even match"
    elif gap < 0.30:
        verdict = "Slight edge"
    else:
        verdict = "Lopsided"
    return {
        "rating_a": sum(display_rating(r, rating_config) for r in ratings_a),
        "rating_b": sum(display_rating(r, rating_config) for r in ratings_b),
        "win_probability_a": probability,
        "win_probability_b": 1.0 - probability,
        "players_on_field": on_field,
        "verdict": verdict,
    }

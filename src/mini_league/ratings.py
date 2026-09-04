"""TrueSkill rating logic (design doc section 4). Pure functions, no database.

Ratings are `trueskill.Rating` objects (mu, sigma). Teams are lists of ratings.
Ranks are 1 = winner, 2 = loser, ...; ties share a rank (disallowed when
draw_probability is 0).
"""

from __future__ import annotations

from math import sqrt
from statistics import NormalDist
from typing import Sequence

from trueskill import Rating, TrueSkill

from .settings import DEFAULT_RATING_CONFIG, RatingConfig

_STD_NORMAL = NormalDist()


def make_env(config: RatingConfig = DEFAULT_RATING_CONFIG) -> TrueSkill:
    """Build a TrueSkill environment from config. Never uses the global env."""
    return TrueSkill(
        mu=config.mu,
        sigma=config.sigma,
        beta=config.beta,
        tau=config.tau,
        draw_probability=config.draw_probability,
        backend=None,
    )


def new_rating(env: TrueSkill) -> Rating:
    return env.create_rating()


def partial_play_weights(roster_sizes: Sequence[int], players_on_field: int) -> list[list[float]]:
    """Per-player weights for uneven rosters (section 5.3).

    A team whose roster exceeds `players_on_field` is carrying a sub; each of
    its players gets weight on_field / roster_size so the team's skill sum is
    comparable to a full-strength team and its updates shrink proportionally.
    A team at or below `players_on_field` gets weight 1.0 for every player.
    """
    if players_on_field < 1:
        raise ValueError("players_on_field must be >= 1")
    weights: list[list[float]] = []
    for size in roster_sizes:
        if size < 1:
            raise ValueError("every team needs at least one player")
        w = 1.0 if size <= players_on_field else players_on_field / size
        weights.append([w] * size)
    return weights


def _validate_ranks(env: TrueSkill, ranks: Sequence[int], team_count: int) -> None:
    if len(ranks) != team_count:
        raise ValueError("one rank per team is required")
    if any((not isinstance(r, int)) or r < 1 for r in ranks):
        raise ValueError("ranks must be integers >= 1")
    if env.draw_probability == 0 and len(set(ranks)) != len(ranks):
        raise ValueError("ties are not allowed (draw_probability is 0)")


def rate_game(
    env: TrueSkill,
    teams: Sequence[Sequence[Rating]],
    ranks: Sequence[int],
    players_on_field: int | None = None,
) -> list[list[Rating]]:
    """Return post-game ratings, same shape as `teams`.

    `players_on_field` enables partial-play weights for rosters larger than it.
    None means every player plays at full weight.
    """
    if len(teams) < 2:
        raise ValueError("a game needs at least two teams")
    if any(len(t) == 0 for t in teams):
        raise ValueError("every team needs at least one player")
    _validate_ranks(env, ranks, len(teams))

    weights = None
    if players_on_field is not None:
        weights = partial_play_weights([len(t) for t in teams], players_on_field)

    rated = env.rate([list(t) for t in teams], ranks=list(ranks), weights=weights)
    return [list(team) for team in rated]


def win_probability(
    env: TrueSkill,
    team_a: Sequence[Rating],
    team_b: Sequence[Rating],
    weights_a: Sequence[float] | None = None,
    weights_b: Sequence[float] | None = None,
) -> float:
    """P(team_a beats team_b) under the TrueSkill model, with optional partial-play weights.

    Each player's performance is N(w*mu, w^2 * (sigma^2 + beta^2)); the team
    performance is the sum, and A wins when A - B > 0.
    """
    if not team_a or not team_b:
        raise ValueError("both teams need at least one player")
    wa = list(weights_a) if weights_a is not None else [1.0] * len(team_a)
    wb = list(weights_b) if weights_b is not None else [1.0] * len(team_b)
    if len(wa) != len(team_a) or len(wb) != len(team_b):
        raise ValueError("weights must match team sizes")

    beta_sq = env.beta**2
    delta_mu = sum(w * r.mu for w, r in zip(wa, team_a)) - sum(
        w * r.mu for w, r in zip(wb, team_b)
    )
    variance = sum(w * w * (r.sigma**2 + beta_sq) for w, r in zip(wa, team_a)) + sum(
        w * w * (r.sigma**2 + beta_sq) for w, r in zip(wb, team_b)
    )
    return _STD_NORMAL.cdf(delta_mu / sqrt(variance))


def win_probabilities(
    env: TrueSkill,
    teams: Sequence[Sequence[Rating]],
    players_on_field: int | None = None,
) -> tuple[float, float]:
    """Convenience for a two-team game with roster-derived partial-play weights."""
    if len(teams) != 2:
        raise ValueError("win_probabilities is defined for exactly two teams")
    if players_on_field is not None:
        weights = partial_play_weights([len(t) for t in teams], players_on_field)
        p = win_probability(env, teams[0], teams[1], weights[0], weights[1])
    else:
        p = win_probability(env, teams[0], teams[1])
    return p, 1.0 - p


def win_probability_for_gap(
    gap: float,
    *,
    sigma: float,
    team_size: int = 1,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> float:
    """How often a side ahead by `gap` displayed points is expected to win.

    For explaining the leaderboard: it turns a difference people can see into
    odds they can picture. Only the team's total skill enters the model, so the
    gap is put on one player and everybody else set level; a side carried by one
    player and a side evenly better by the same total come to the same number.
    """
    if team_size < 1:
        raise ValueError("a team needs at least one player")
    env = make_env(config)
    level = Rating(config.mu, sigma)
    ahead = [Rating(config.mu + gap / config.display_scale, sigma)]
    ahead.extend([level] * (team_size - 1))
    return win_probability(env, ahead, [level] * team_size)


def match_quality(env: TrueSkill, teams: Sequence[Sequence[Rating]]) -> float:
    """TrueSkill's draw-probability-based match quality (0..1, higher = more even)."""
    return env.quality([list(t) for t in teams])


def conservative_rating(rating: Rating, k: float = DEFAULT_RATING_CONFIG.conservative_k) -> float:
    """mu - k*sigma: penalizes uncertainty so new players don't top the board."""
    return rating.mu - k * rating.sigma


def display_rating(rating: Rating, config: RatingConfig = DEFAULT_RATING_CONFIG) -> int:
    """Single friendly number shown on the leaderboard (section 4.3)."""
    value = conservative_rating(rating, config.conservative_k)
    return round(value * config.display_scale + config.display_offset)


__all__ = [
    "Rating",
    "TrueSkill",
    "conservative_rating",
    "display_rating",
    "make_env",
    "match_quality",
    "new_rating",
    "partial_play_weights",
    "rate_game",
    "win_probabilities",
    "win_probability",
    "win_probability_for_gap",
]

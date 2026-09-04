"""Rating validation by simulation (design doc section 10).

Answers the questions the design doc asks before the league is trusted with
real people:

  - How many games does it take before the leaderboard order matches how good
    people actually are?
  - Does playing mostly 2v2 rather than 5v5 bias anyone's rating?
  - Do uneven teams systematically favour one side?
  - Which values of beta, tau and starting sigma converge fastest?

Everything here is in memory and imports `ratings` and `teams` directly, with
no database, so a parameter sweep is cheap. This is a different tool from
`scripts/simulate.py`, which writes plausible-looking sessions into a real
database for looking at in the app.

The honesty of the whole exercise rests on one thing: outcomes are drawn from
hidden true skills that the rating system never sees, using a performance model
that is stated here rather than borrowed from TrueSkill's own update rule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from statistics import fmean
from typing import Sequence

from trueskill import Rating

from .ratings import (
    display_rating,
    make_env,
    partial_play_weights,
    rate_game,
    win_probability,
)
from .settings import DEFAULT_RATING_CONFIG, RatingConfig, TeamGenConfig
from .teams import generate_teams, on_field_for, select_bench

# --- small statistics helpers ----------------------------------------------------


def rank_with_ties(values: Sequence[float]) -> list[float]:
    """Ranks, sharing the average rank across ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        shared = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = shared
        position = end + 1
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise ValueError("pearson needs two equally long sequences")
    if len(xs) < 2:
        return 0.0
    mean_x, mean_y = fmean(xs), fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    numerator = sum(a * b for a, b in zip(dx, dy))
    denominator = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    return numerator / denominator if denominator else 0.0


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation: 1.0 means the two orderings agree exactly."""
    if len(xs) < 2:
        return 0.0
    return pearson(rank_with_ties(xs), rank_with_ties(ys))


# --- configuration ---------------------------------------------------------------


@dataclass(frozen=True)
class SimulationConfig:
    """One experiment.

    `performance_sigma` is how much a player's showing varies game to game in
    the simulated world. It is deliberately separate from the rating system's
    `beta`, which is only the system's *assumption* about that variation.
    Leaving them different is how a sweep finds the beta that suits a league.
    """

    player_count: int = 24
    sessions: int = 40
    games_per_session: tuple[int, int] = (2, 4)
    attendance: tuple[int, int] = (4, 14)
    team_size: int | None = None  # None means split whoever turns up
    max_on_field: int | None = 5
    skill_spread: float = 5.0  # standard deviation of the hidden true skills
    performance_sigma: float | None = None  # defaults to the rating beta
    # Switch off to see what uneven teams would do without partial play. The
    # simulated world still rotates substitutes either way; only the rating
    # system's knowledge of it changes, which is what makes this a fair control.
    use_partial_play: bool = True
    rating: RatingConfig = field(default_factory=RatingConfig)
    team_gen: TeamGenConfig = field(default_factory=lambda: TeamGenConfig(sample_size=400))
    seed: int | None = None

    def noise(self) -> float:
        return (
            self.performance_sigma
            if self.performance_sigma is not None
            else self.rating.beta
        )


@dataclass
class PlayerOutcome:
    player_id: int
    true_skill: float
    mu: float
    sigma: float
    rating: int
    games: int
    wins: int
    losses: int
    substitute_games: int  # rounds where their roster was bigger than the field
    mean_on_field: float

    @property
    def error(self) -> float:
        """How far the estimate ended up from the truth."""
        return self.mu - self.true_skill

    @property
    def substitute_share(self) -> float:
        return self.substitute_games / self.games if self.games else 0.0


@dataclass
class SimulationResult:
    config: SimulationConfig
    players: list[PlayerOutcome]
    games_played: int
    convergence: list[tuple[float, float]]  # (mean games per player, spearman)
    calibration: list[tuple[float, float, int]]  # (predicted, actual, count)
    uneven_games: int
    bigger_roster_wins: int

    @property
    def rated(self) -> list[PlayerOutcome]:
        return [p for p in self.players if p.games > 0]

    @property
    def final_spearman(self) -> float:
        rated = self.rated
        return spearman(
            [p.true_skill for p in rated], [float(p.rating) for p in rated]
        )

    @property
    def mean_absolute_error(self) -> float:
        rated = self.rated
        return fmean([abs(p.error) for p in rated]) if rated else 0.0

    @property
    def mean_games_per_player(self) -> float:
        rated = self.rated
        return fmean([p.games for p in rated]) if rated else 0.0

    def games_to_reach(self, threshold: float = 0.9) -> float | None:
        """Mean games per player at which rank order first matched this well."""
        for games, rho in self.convergence:
            if rho >= threshold:
                return games
        return None

    def spearman_at(self, games: float) -> float | None:
        """Rank agreement once players had roughly this many games each.

        Comparing configurations at a fixed number of games is steadier than
        asking when each first crossed a threshold, which some runs never do.
        """
        for played, rho in self.convergence:
            if played >= games:
                return rho
        return None

    @property
    def calibration_error(self) -> float:
        """Weighted gap between predicted and actual win rates."""
        total = sum(count for _, _, count in self.calibration)
        if not total:
            return 0.0
        return sum(
            abs(predicted - actual) * count for predicted, actual, count in self.calibration
        ) / total

    @property
    def substitute_bias(self) -> float:
        """Correlation between rating error and time spent as a substitute.

        Near zero is the answer we want: being on an oversized roster should
        not push a rating up or down.
        """
        rated = [p for p in self.rated if p.games >= 3]
        if len(rated) < 3:
            return 0.0
        return pearson([p.substitute_share for p in rated], [p.error for p in rated])

    @property
    def team_size_bias(self) -> float:
        """Correlation between rating error and the sizes someone played at."""
        rated = [p for p in self.rated if p.games >= 3]
        if len(rated) < 3:
            return 0.0
        return pearson([p.mean_on_field for p in rated], [p.error for p in rated])

    @property
    def bigger_roster_win_rate(self) -> float | None:
        if not self.uneven_games:
            return None
        return self.bigger_roster_wins / self.uneven_games


# --- the simulation --------------------------------------------------------------


def _performance(
    team: Sequence[int],
    true_skills: dict[int, float],
    on_field: int,
    noise: float,
    rng: random.Random,
) -> float:
    """How well a roster actually plays on the day.

    A roster larger than the field rotates, so at any moment it has `on_field`
    of its members playing. Over the game that averages out to the roster's
    mean skill times the number on the field, which is the same thing as
    weighting each player by on_field / roster_size.
    """
    weight = min(1.0, on_field / len(team))
    return sum(weight * (true_skills[pid] + rng.gauss(0, noise)) for pid in team)


def simulate_league(config: SimulationConfig) -> SimulationResult:
    """Play out a whole league and measure how well the ratings tracked truth."""
    rng = random.Random(config.seed)
    env = make_env(config.rating)
    noise = config.noise()

    players = list(range(1, config.player_count + 1))
    true_skills = {
        pid: rng.gauss(config.rating.mu, config.skill_spread) for pid in players
    }
    ratings = {pid: env.create_rating() for pid in players}

    games = wins = losses = 0
    stats = {
        pid: {"games": 0, "wins": 0, "losses": 0, "subs": 0, "on_field": []}
        for pid in players
    }
    convergence: list[tuple[float, float]] = []
    buckets: dict[int, list[int]] = {}
    uneven_games = bigger_roster_wins = 0

    low, high = config.attendance
    high = min(high, config.player_count)
    if low < 4:
        low = 4
    if high < low:
        raise ValueError("attendance range is empty for this many players")

    for _ in range(config.sessions):
        attending = rng.sample(players, rng.randint(low, high))
        history: list[list[list[int]]] = []

        for _ in range(rng.randint(*config.games_per_session)):
            playing_count = (
                len(attending)
                if config.team_size is None
                else min(len(attending), config.team_size * 2)
            )
            playing, _bench = select_bench(
                attending,
                playing_count,
                {pid: stats[pid]["games"] for pid in attending},
                rng,
            )
            if len(playing) < 2:
                continue

            split = generate_teams(
                playing,
                {pid: ratings[pid] for pid in playing},
                history=history,
                team_config=config.team_gen,
                rating_config=config.rating,
                rng=rng,
            )
            team_a, team_b = [list(t) for t in split.teams]
            sizes = [len(team_a), len(team_b)]
            on_field = on_field_for(sizes, config.max_on_field)

            # What the system expected, recorded before it learns the result.
            rating_on_field = on_field if config.use_partial_play else None
            weights = (
                partial_play_weights(sizes, on_field)
                if config.use_partial_play
                else [None, None]
            )
            predicted = win_probability(
                env,
                [ratings[p] for p in team_a],
                [ratings[p] for p in team_b],
                weights[0],
                weights[1],
            )

            a_score = _performance(team_a, true_skills, on_field, noise, rng)
            b_score = _performance(team_b, true_skills, on_field, noise, rng)
            winner = 0 if a_score >= b_score else 1

            bucket = min(9, int(predicted * 10))
            buckets.setdefault(bucket, []).append(1 if winner == 0 else 0)

            if sizes[0] != sizes[1]:
                uneven_games += 1
                bigger = 0 if sizes[0] > sizes[1] else 1
                if winner == bigger:
                    bigger_roster_wins += 1

            updated = rate_game(
                env,
                [[ratings[p] for p in team_a], [ratings[p] for p in team_b]],
                ranks=[1, 2] if winner == 0 else [2, 1],
                players_on_field=rating_on_field,
            )
            for roster, new_ratings, index in (
                (team_a, updated[0], 0),
                (team_b, updated[1], 1),
            ):
                for pid, new_rating in zip(roster, new_ratings):
                    ratings[pid] = new_rating
                    entry = stats[pid]
                    entry["games"] += 1
                    entry["on_field"].append(on_field)
                    if len(roster) > on_field:
                        entry["subs"] += 1
                    if index == winner:
                        entry["wins"] += 1
                    else:
                        entry["losses"] += 1

            history.append([team_a, team_b])
            games += 1

        played = [pid for pid in players if stats[pid]["games"] > 0]
        if len(played) >= 3:
            convergence.append(
                (
                    fmean([stats[pid]["games"] for pid in played]),
                    spearman(
                        [true_skills[pid] for pid in played],
                        [float(display_rating(ratings[pid], config.rating)) for pid in played],
                    ),
                )
            )

    outcomes = [
        PlayerOutcome(
            player_id=pid,
            true_skill=true_skills[pid],
            mu=ratings[pid].mu,
            sigma=ratings[pid].sigma,
            rating=display_rating(ratings[pid], config.rating),
            games=stats[pid]["games"],
            wins=stats[pid]["wins"],
            losses=stats[pid]["losses"],
            substitute_games=stats[pid]["subs"],
            mean_on_field=fmean(stats[pid]["on_field"]) if stats[pid]["on_field"] else 0.0,
        )
        for pid in players
    ]

    calibration = [
        ((bucket + 0.5) / 10, fmean(results), len(results))
        for bucket, results in sorted(buckets.items())
    ]

    return SimulationResult(
        config=config,
        players=outcomes,
        games_played=games,
        convergence=convergence,
        calibration=calibration,
        uneven_games=uneven_games,
        bigger_roster_wins=bigger_roster_wins,
    )


__all__ = [
    "PlayerOutcome",
    "SimulationConfig",
    "SimulationResult",
    "pearson",
    "rank_with_ties",
    "simulate_league",
    "spearman",
]

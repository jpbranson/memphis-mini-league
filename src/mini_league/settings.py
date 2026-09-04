"""Single place for tunable parameters (design doc §4.2, §5.2)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RatingConfig:
    """TrueSkill parameters plus how the displayed rating is derived."""

    mu: float = 25.0  # initial mean
    sigma: float = 25.0 / 3  # initial std dev
    beta: float = 25.0 / 6  # skill difference -> win probability
    tau: float = 25.0 / 300  # dynamics: uncertainty added before each game
    draw_probability: float = 0.0  # games are played to 3 or 5; no ties

    # Displayed rating = round((mu - conservative_k * sigma) * display_scale + display_offset).
    # With the defaults a brand-new player shows 0 and an established player
    # (e.g. mu=30, sigma=2) shows 960.
    conservative_k: float = 3.0
    display_scale: float = 40.0
    display_offset: float = 0.0


@dataclass(frozen=True)
class TeamGenConfig:
    """Team generation weights (milestone 5; defined here so settings live in one file)."""

    w_balance: float = 1.0
    w_variety: float = 0.3
    # Only applies when the organizer asks for an even coed split; a round with
    # no designations supplied never scores this term at all. Set just under
    # balance, so evening up WMPs and MMPs is worth about as much as a fair
    # matchup without ever being allowed to force a lopsided one.
    w_designation: float = 0.8
    top_n: int = 5
    # A split is only a candidate if its cost is within this much of the best
    # one. Without it, a small group with few possible splits would fill the
    # top_n quota with clearly worse teams and pick one at random.
    cost_tolerance: float = 0.12
    sample_size: int = 5000
    enumerate_max_players: int = 12


@dataclass(frozen=True)
class Settings:
    rating: RatingConfig = field(default_factory=RatingConfig)
    team_gen: TeamGenConfig = field(default_factory=TeamGenConfig)
    database_url: str = "sqlite:///mini_league.db"
    # The shared organizer password. Unset means the organizer screens stay
    # closed; see web/auth.py for why that is the safe default.
    organizer_password: str | None = None
    # Signs the session cookie. Unset means a fresh key each start, which logs
    # everyone out on restart but never falls back to a guessable value.
    secret_key: str | None = None


DEFAULT_RATING_CONFIG = RatingConfig()


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def get_settings() -> Settings:
    """Settings with environment overrides applied.

    The rating parameters stay in this file as the design doc asks, but each can
    be overridden by an environment variable so a deployed instance can be tuned
    without a code change. Changing any of them needs a recompute afterwards.
    """
    defaults = RatingConfig()
    rating = RatingConfig(
        mu=_env_float("MINI_LEAGUE_MU", defaults.mu),
        sigma=_env_float("MINI_LEAGUE_SIGMA", defaults.sigma),
        beta=_env_float("MINI_LEAGUE_BETA", defaults.beta),
        tau=_env_float("MINI_LEAGUE_TAU", defaults.tau),
        draw_probability=_env_float(
            "MINI_LEAGUE_DRAW_PROBABILITY", defaults.draw_probability
        ),
        conservative_k=_env_float("MINI_LEAGUE_CONSERVATIVE_K", defaults.conservative_k),
        display_scale=_env_float("MINI_LEAGUE_DISPLAY_SCALE", defaults.display_scale),
        display_offset=_env_float("MINI_LEAGUE_DISPLAY_OFFSET", defaults.display_offset),
    )
    team_defaults = TeamGenConfig()
    team_gen = TeamGenConfig(
        w_balance=_env_float("MINI_LEAGUE_W_BALANCE", team_defaults.w_balance),
        w_variety=_env_float("MINI_LEAGUE_W_VARIETY", team_defaults.w_variety),
        w_designation=_env_float(
            "MINI_LEAGUE_W_DESIGNATION", team_defaults.w_designation
        ),
        top_n=int(_env_float("MINI_LEAGUE_TOP_N", team_defaults.top_n)),
        cost_tolerance=_env_float(
            "MINI_LEAGUE_COST_TOLERANCE", team_defaults.cost_tolerance
        ),
    )
    return Settings(
        rating=rating,
        team_gen=team_gen,
        database_url=os.environ.get("MINI_LEAGUE_DATABASE_URL", Settings.database_url),
        organizer_password=os.environ.get("MINI_LEAGUE_PASSWORD") or None,
        secret_key=os.environ.get("MINI_LEAGUE_SECRET_KEY") or None,
    )

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
    top_n: int = 5
    sample_size: int = 5000
    enumerate_max_players: int = 12


@dataclass(frozen=True)
class Settings:
    rating: RatingConfig = field(default_factory=RatingConfig)
    team_gen: TeamGenConfig = field(default_factory=TeamGenConfig)
    database_url: str = "sqlite:///mini_league.db"


DEFAULT_RATING_CONFIG = RatingConfig()


def get_settings() -> Settings:
    """Settings with environment overrides applied."""
    return Settings(
        database_url=os.environ.get("MINI_LEAGUE_DATABASE_URL", Settings.database_url),
    )

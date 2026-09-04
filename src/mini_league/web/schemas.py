"""Pydantic request and response models for the JSON API (design doc section 8)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """Response model that can be built straight from a SQLAlchemy row."""

    model_config = ConfigDict(from_attributes=True)

# Annotations are written as `dt.date` rather than a bare `date`: a field named
# `date` would otherwise shadow the imported type when the annotation is
# evaluated, and resolve to the field's default instead.


class SeasonOut(ORMModel):
    id: int
    name: str
    start_date: dt.date
    end_date: dt.date | None


class SeasonCreate(BaseModel):
    name: str
    start_date: dt.date


class PlayerOut(ORMModel):
    id: int
    name: str
    active: bool
    merged_into: int | None = None
    designation: str | None = None


class PlayerMatchOut(ORMModel):
    player: PlayerOut
    score: float
    is_duplicate: bool


class PlayerCreate(BaseModel):
    name: str
    designation: str | None = None
    force: bool = False


class SessionCreate(BaseModel):
    date: dt.date | None = None
    notes: str | None = None


class SessionUpdate(BaseModel):
    season_id: int | None = None


class SessionPlayerOut(ORMModel):
    player: PlayerOut
    checked_in_at: dt.datetime
    checked_out_at: dt.datetime | None
    # What they count as today, and the override that produced it if any.
    designation: str | None = None
    designation_override: str | None = None


class CheckInRequest(BaseModel):
    player_id: int


class DesignationRequest(BaseModel):
    """Set a player's designation for one session.

    `None` clears the override so they count as whatever they usually are;
    "none" says they have no designation today, which is a different answer.
    """

    player_id: int
    designation: str | None = None


class TeamIn(BaseModel):
    player_ids: list[int] = Field(min_length=1)
    rank: int = Field(ge=1)
    score: int | None = Field(default=None, ge=0)


class TeamOut(ORMModel):
    team_index: int
    rank: int
    score: int | None
    player_ids: list[int]


class GameOut(ORMModel):
    id: int
    session_id: int
    round_number: int
    players_on_field: int
    played_at: dt.datetime
    deleted_at: dt.datetime | None
    teams: list[TeamOut]


class GameCreate(BaseModel):
    teams: list[TeamIn] = Field(min_length=2)
    players_on_field: int | None = Field(default=None, ge=1)
    round_number: int | None = Field(default=None, ge=1)
    played_at: dt.datetime | None = None


class GameUpdate(BaseModel):
    teams: list[TeamIn] | None = None
    players_on_field: int | None = Field(default=None, ge=1)
    round_number: int | None = Field(default=None, ge=1)
    played_at: dt.datetime | None = None


class SessionOut(ORMModel):
    id: int
    season_id: int
    date: dt.date
    notes: str | None
    players: list[SessionPlayerOut]
    games: list[GameOut]


class LeaderboardRowOut(ORMModel):
    rank: int
    player: PlayerOut
    rating: int
    mu: float
    sigma: float
    games_played: int
    wins: int
    losses: int


class SeasonSummaryOut(ORMModel):
    season: SeasonOut
    rating: int
    mu: float
    sigma: float
    games_played: int
    wins: int
    losses: int


class PlayerDetailOut(ORMModel):
    player: PlayerOut
    seasons: list[SeasonSummaryOut]
    all_time: dict[str, int]


class PlayerUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None
    # None leaves the designation alone, since that is what None means for every
    # other field here. An empty string is how you clear one.
    designation: str | None = None


class MergeRequest(BaseModel):
    target_player_id: int


class AuditEntryOut(ORMModel):
    id: int
    action: str
    payload: dict
    created_at: dt.datetime


class RatingPointOut(ORMModel):
    game_id: int
    mu_before: float
    sigma_before: float
    mu_after: float
    sigma_after: float
    rating_after: int

"""SQLAlchemy models for the data model in design doc section 6.

Source of truth: players, seasons, sessions, session_players, games, game_teams,
game_team_players. Derived (rebuilt by recompute): rating_history,
player_season_ratings.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no timezone support)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        # "name unique among active players": partial unique index.
        Index(
            "ux_players_active_name",
            "name",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    active: Mapped[bool] = mapped_column(default=True)
    merged_into: Mapped[int | None] = mapped_column(ForeignKey("players.id"))

    merged_into_player: Mapped[Player | None] = relationship(remote_side="Player.id")

    def __repr__(self) -> str:
        return f"Player(id={self.id}, name={self.name!r})"


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    start_date: Mapped[date]
    end_date: Mapped[date | None]  # null = current season
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sessions: Mapped[list[LeagueSession]] = relationship(back_populates="season")


class LeagueSession(Base):
    """One morning of games. Named LeagueSession to avoid clashing with sqlalchemy.orm.Session."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    date: Mapped[date]
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Teams picked but not yet played. Held here rather than in the page so a
    # phone that locks, sleeps or drops the tab does not lose the line-up
    # between choosing sides and coming back with the score.
    pending_teams: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    season: Mapped[Season] = relationship(back_populates="sessions")
    players: Mapped[list[SessionPlayer]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    games: Mapped[list[Game]] = relationship(
        back_populates="session", order_by="Game.played_at, Game.id"
    )


class SessionPlayer(Base):
    """Who was checked in to a session."""

    __tablename__ = "session_players"

    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    checked_in_at: Mapped[datetime] = mapped_column(default=utcnow)
    checked_out_at: Mapped[datetime | None]

    session: Mapped[LeagueSession] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    round_number: Mapped[int]
    # Players per team actually on the field (e.g. 3 for 3v3). A team whose
    # roster is larger than this is carrying a sub and gets partial-play weights.
    players_on_field: Mapped[int]
    played_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]  # soft delete

    session: Mapped[LeagueSession] = relationship(back_populates="games")
    teams: Mapped[list[GameTeam]] = relationship(
        back_populates="game",
        order_by="GameTeam.team_index",
        cascade="all, delete-orphan",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class GameTeam(Base):
    __tablename__ = "game_teams"
    __table_args__ = (UniqueConstraint("game_id", "team_index", name="ux_game_teams_game_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    team_index: Mapped[int]  # 0, 1, ... (more for round-robin)
    score: Mapped[int | None]
    rank: Mapped[int]  # 1 = winner, 2 = loser; ties share a rank

    game: Mapped[Game] = relationship(back_populates="teams")
    players: Mapped[list[GameTeamPlayer]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )

    @property
    def player_ids(self) -> list[int]:
        return [gtp.player_id for gtp in self.players]


class GameTeamPlayer(Base):
    __tablename__ = "game_team_players"

    game_team_id: Mapped[int] = mapped_column(ForeignKey("game_teams.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), primary_key=True, index=True
    )

    team: Mapped[GameTeam] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()


class RatingHistory(Base):
    """One row per player per game they played. Derived; rebuilt by recompute."""

    __tablename__ = "rating_history"
    __table_args__ = (
        UniqueConstraint("player_id", "game_id", name="ux_rating_history_player_game"),
        Index("ix_rating_history_player_season", "player_id", "season_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    mu_before: Mapped[float]
    sigma_before: Mapped[float]
    mu_after: Mapped[float]
    sigma_after: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    player: Mapped[Player] = relationship()
    game: Mapped[Game] = relationship()


class PlayerSeasonRating(Base):
    """Current snapshot per (player, season). Derived; rebuilt by recompute."""

    __tablename__ = "player_season_ratings"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), primary_key=True, index=True)
    mu: Mapped[float]
    sigma: Mapped[float]
    games_played: Mapped[int] = mapped_column(default=0)
    wins: Mapped[int] = mapped_column(default=0)
    losses: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    player: Mapped[Player] = relationship()
    season: Mapped[Season] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str]  # "merge_players", "edit_game", "delete_game", ...
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

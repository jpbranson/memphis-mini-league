"""Read models for the leaderboard and player pages (design doc section 7).

All read-only. Ratings are computed by `recompute`; nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from trueskill import Rating

from .models import (
    Game,
    GameTeam,
    GameTeamPlayer,
    LeagueSession,
    Player,
    PlayerSeasonRating,
    RatingHistory,
    Season,
)
from .ratings import display_rating, make_env
from .settings import DEFAULT_RATING_CONFIG, RatingConfig


@dataclass
class LeaderboardRow:
    rank: int
    player: Player
    rating: int
    mu: float
    sigma: float
    games_played: int
    wins: int
    losses: int

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}"


def starting_rating(config: RatingConfig = DEFAULT_RATING_CONFIG) -> Rating:
    return make_env(config).create_rating()


def season_ratings(
    db: Session, season_id: int, config: RatingConfig = DEFAULT_RATING_CONFIG
) -> dict[int, PlayerSeasonRating]:
    """Stored rating snapshot per player for one season."""
    rows = db.scalars(
        select(PlayerSeasonRating).where(PlayerSeasonRating.season_id == season_id)
    ).all()
    return {row.player_id: row for row in rows}


def current_ratings(
    db: Session, season_id: int, player_ids: list[int] | None = None,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> dict[int, Rating]:
    """Rating per player, falling back to the starting rating for the unrated."""
    stored = season_ratings(db, season_id, config)
    ids = player_ids if player_ids is not None else list(stored)
    default = starting_rating(config)
    return {
        pid: (Rating(stored[pid].mu, stored[pid].sigma) if pid in stored else default)
        for pid in ids
    }


def leaderboard(
    db: Session,
    season_id: int,
    *,
    min_games: int = 0,
    include_inactive: bool = False,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> list[LeaderboardRow]:
    """Ranked standings for a season, best first.

    Ranking is on the displayed conservative rating, so a player with one lucky
    win does not outrank an established player (design doc section 4.3).
    """
    stmt = (
        select(PlayerSeasonRating, Player)
        .join(Player, Player.id == PlayerSeasonRating.player_id)
        .where(PlayerSeasonRating.season_id == season_id)
    )
    if not include_inactive:
        stmt = stmt.where(Player.active.is_(True), Player.merged_into.is_(None))

    rows = [
        LeaderboardRow(
            rank=0,
            player=player,
            rating=display_rating(Rating(snapshot.mu, snapshot.sigma), config),
            mu=snapshot.mu,
            sigma=snapshot.sigma,
            games_played=snapshot.games_played,
            wins=snapshot.wins,
            losses=snapshot.losses,
        )
        for snapshot, player in db.execute(stmt).all()
        if snapshot.games_played >= min_games
    ]
    rows.sort(key=lambda r: (-r.rating, r.player.name))
    for position, row in enumerate(rows, start=1):
        row.rank = position
    return rows


@dataclass
class SeasonSummary:
    season: Season
    rating: int
    mu: float
    sigma: float
    games_played: int
    wins: int
    losses: int


@dataclass
class GameAppearance:
    game: Game
    session_date: date
    round_number: int
    won: bool
    score_for: int | None
    score_against: int | None
    teammates: list[Player]
    opponents: list[Player]
    mu_before: float | None
    mu_after: float | None


def player_seasons(
    db: Session, player_id: int, config: RatingConfig = DEFAULT_RATING_CONFIG
) -> list[SeasonSummary]:
    """Every season this player has a rating in, newest first."""
    stmt = (
        select(PlayerSeasonRating, Season)
        .join(Season, Season.id == PlayerSeasonRating.season_id)
        .where(PlayerSeasonRating.player_id == player_id)
        .order_by(Season.start_date.desc())
    )
    return [
        SeasonSummary(
            season=season,
            rating=display_rating(Rating(row.mu, row.sigma), config),
            mu=row.mu,
            sigma=row.sigma,
            games_played=row.games_played,
            wins=row.wins,
            losses=row.losses,
        )
        for row, season in db.execute(stmt).all()
    ]


def rating_history(db: Session, player_id: int, season_id: int) -> list[RatingHistory]:
    """Every rating change for a player in a season, oldest first."""
    return list(
        db.scalars(
            select(RatingHistory)
            .join(Game, Game.id == RatingHistory.game_id)
            .where(
                RatingHistory.player_id == player_id,
                RatingHistory.season_id == season_id,
            )
            .order_by(Game.played_at, Game.id)
        )
    )


def player_games(
    db: Session, player_id: int, season_id: int, *, limit: int = 20
) -> list[GameAppearance]:
    """Recent games for a player with teammates, opponents, and scores."""
    stmt = (
        select(Game)
        .join(GameTeam, GameTeam.game_id == Game.id)
        .join(GameTeamPlayer, GameTeamPlayer.game_team_id == GameTeam.id)
        .join(LeagueSession, LeagueSession.id == Game.session_id)
        .where(
            GameTeamPlayer.player_id == player_id,
            LeagueSession.season_id == season_id,
            Game.deleted_at.is_(None),
        )
        .order_by(Game.played_at.desc(), Game.id.desc())
        .limit(limit)
        .options(
            selectinload(Game.teams)
            .selectinload(GameTeam.players)
            .selectinload(GameTeamPlayer.player),
            selectinload(Game.session),
        )
    )
    history = {h.game_id: h for h in rating_history(db, player_id, season_id)}

    appearances: list[GameAppearance] = []
    for game in db.scalars(stmt).unique().all():
        own = next(t for t in game.teams if player_id in t.player_ids)
        others = [t for t in game.teams if t.id != own.id]
        change = history.get(game.id)
        appearances.append(
            GameAppearance(
                game=game,
                session_date=game.session.date,
                round_number=game.round_number,
                won=own.rank == 1,
                score_for=own.score,
                score_against=others[0].score if others else None,
                teammates=[
                    gtp.player for gtp in own.players if gtp.player_id != player_id
                ],
                opponents=[gtp.player for t in others for gtp in t.players],
                mu_before=change.mu_before if change else None,
                mu_after=change.mu_after if change else None,
            )
        )
    return appearances


def all_time_record(db: Session, player_id: int) -> dict:
    """Totals across every season."""
    row = db.execute(
        select(
            func.coalesce(func.sum(PlayerSeasonRating.wins), 0),
            func.coalesce(func.sum(PlayerSeasonRating.losses), 0),
            func.coalesce(func.sum(PlayerSeasonRating.games_played), 0),
            func.count(PlayerSeasonRating.season_id),
        ).where(PlayerSeasonRating.player_id == player_id)
    ).one()
    return {
        "wins": row[0],
        "losses": row[1],
        "games_played": row[2],
        "seasons_played": row[3],
    }


def typical_team_size(db: Session, season_id: int, default: int = 3) -> int:
    """The size most of a season's games were played at.

    Used to explain what a rating gap is worth in the format this league
    actually plays; a 2v2 league and a 5v5 league get very different answers
    out of the same gap.
    """
    row = db.execute(
        select(Game.players_on_field, func.count())
        .join(LeagueSession, LeagueSession.id == Game.session_id)
        .where(LeagueSession.season_id == season_id, Game.deleted_at.is_(None))
        .group_by(Game.players_on_field)
        .order_by(func.count().desc(), Game.players_on_field.desc())
    ).first()
    return row[0] if row is not None else default


def typical_sigma(
    db: Session, season_id: int, config: RatingConfig = DEFAULT_RATING_CONFIG
) -> float:
    """Median uncertainty in a season, so odds are quoted for the league as it stands."""
    sigmas = [row.sigma for row in season_ratings(db, season_id, config).values()]
    return median(sigmas) if sigmas else config.sigma


def team_ratings_before_each_game(db: Session, session_id: int) -> dict[int, dict[int, int]]:
    """Each side's collective rating in a session's games, as it stood beforehand.

    Keyed by game id, then team index. The numbers come from `rating_history`,
    which holds what the league believed about every player at the moment the
    game was rated, so a session read back months later still shows the standing
    the teams were picked on rather than today's.

    The total is a plain sum of the displayed ratings, matching the team
    strengths the organizer sees when picking sides. A roster carrying a sub
    therefore sums higher than the side it faced; the page says how many were on
    the field so that reads as the extra body it is.
    """
    rows = db.execute(
        select(
            GameTeam.game_id,
            GameTeam.team_index,
            RatingHistory.mu_before,
            RatingHistory.sigma_before,
        )
        .join(GameTeamPlayer, GameTeamPlayer.game_team_id == GameTeam.id)
        .join(Game, Game.id == GameTeam.game_id)
        .join(
            RatingHistory,
            (RatingHistory.game_id == GameTeam.game_id)
            & (RatingHistory.player_id == GameTeamPlayer.player_id),
        )
        .where(Game.session_id == session_id, Game.deleted_at.is_(None))
    ).all()

    totals: dict[int, dict[int, int]] = {}
    for game_id, team_index, mu, sigma in rows:
        sides = totals.setdefault(game_id, {})
        sides[team_index] = sides.get(team_index, 0) + display_rating(Rating(mu, sigma))
    return totals

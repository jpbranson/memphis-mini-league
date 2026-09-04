"""Rebuild derived rating tables by replaying games (design doc section 4.6).

`rating_history` and `player_season_ratings` are cleared for the season and
replayed from the source-of-truth game tables, in chronological order. Every
write path (record/edit/delete game, merge players, move session) calls this.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload
from trueskill import Rating

from .models import (
    Game,
    GameTeam,
    LeagueSession,
    PlayerSeasonRating,
    RatingHistory,
    Season,
)
from .ratings import make_env, rate_game
from .settings import DEFAULT_RATING_CONFIG, RatingConfig


@dataclass
class _PlayerState:
    rating: Rating
    games_played: int = 0
    wins: int = 0
    losses: int = 0


def season_games_in_order(db: Session, season_id: int) -> list[Game]:
    """Non-deleted games for a season, oldest first (played_at, then id)."""
    stmt = (
        select(Game)
        .join(LeagueSession, Game.session_id == LeagueSession.id)
        .where(LeagueSession.season_id == season_id, Game.deleted_at.is_(None))
        .order_by(Game.played_at, Game.id)
        .options(selectinload(Game.teams).selectinload(GameTeam.players))
    )
    return list(db.scalars(stmt).all())


def _clear_season(db: Session, season_id: int) -> None:
    db.execute(delete(RatingHistory).where(RatingHistory.season_id == season_id))
    db.execute(delete(PlayerSeasonRating).where(PlayerSeasonRating.season_id == season_id))


def recompute_ratings(
    db: Session,
    season_id: int,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
    *,
    commit: bool = True,
) -> None:
    """Clear and replay one season's ratings. Commits unless commit=False."""
    if db.get(Season, season_id) is None:
        raise ValueError(f"season {season_id} does not exist")

    env = make_env(config)
    _clear_season(db, season_id)

    states: dict[int, _PlayerState] = {}

    def state_for(player_id: int) -> _PlayerState:
        st = states.get(player_id)
        if st is None:
            st = states[player_id] = _PlayerState(rating=env.create_rating())
        return st

    for game in season_games_in_order(db, season_id):
        teams = sorted(game.teams, key=lambda t: t.team_index)
        rosters = [t.player_ids for t in teams]
        if len(teams) < 2 or any(len(r) == 0 for r in rosters):
            raise ValueError(f"game {game.id} has fewer than two non-empty teams")

        before = [[state_for(pid).rating for pid in roster] for roster in rosters]
        ranks = [t.rank for t in teams]
        after = rate_game(env, before, ranks, game.players_on_field)

        for team, roster, old_ratings, new_ratings in zip(teams, rosters, before, after):
            won = team.rank == 1
            for pid, old, new in zip(roster, old_ratings, new_ratings):
                st = states[pid]
                st.rating = new
                st.games_played += 1
                if won:
                    st.wins += 1
                else:
                    st.losses += 1
                db.add(
                    RatingHistory(
                        player_id=pid,
                        game_id=game.id,
                        season_id=season_id,
                        mu_before=old.mu,
                        sigma_before=old.sigma,
                        mu_after=new.mu,
                        sigma_after=new.sigma,
                    )
                )

    for pid, st in states.items():
        db.add(
            PlayerSeasonRating(
                player_id=pid,
                season_id=season_id,
                mu=st.rating.mu,
                sigma=st.rating.sigma,
                games_played=st.games_played,
                wins=st.wins,
                losses=st.losses,
            )
        )

    db.flush()
    if commit:
        db.commit()


def recompute_all_ratings(
    db: Session,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
    *,
    commit: bool = True,
) -> None:
    """Replay every season."""
    for season_id in db.scalars(select(Season.id).order_by(Season.id)).all():
        recompute_ratings(db, season_id, config, commit=False)
    if commit:
        db.commit()

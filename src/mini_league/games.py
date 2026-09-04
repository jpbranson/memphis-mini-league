"""Write path for recording a game result. Validates, inserts, then recomputes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Game, GameTeam, GameTeamPlayer, LeagueSession, Player, utcnow
from .recompute import recompute_ratings
from .settings import DEFAULT_RATING_CONFIG, RatingConfig


@dataclass
class TeamInput:
    player_ids: list[int] = field(default_factory=list)
    rank: int = 1  # 1 = winner
    score: int | None = None


def next_round_number(db: Session, session_id: int) -> int:
    current = db.scalar(
        select(func.max(Game.round_number)).where(
            Game.session_id == session_id, Game.deleted_at.is_(None)
        )
    )
    return (current or 0) + 1


def record_game(
    db: Session,
    session_id: int,
    teams: list[TeamInput],
    *,
    players_on_field: int | None = None,
    played_at: datetime | None = None,
    round_number: int | None = None,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> Game:
    """Insert a game with its teams and players, then recompute the season. Commits.

    `players_on_field` defaults to the smallest roster (e.g. 3 for a 3v4 game).
    """
    session = db.get(LeagueSession, session_id)
    if session is None:
        raise ValueError(f"session {session_id} does not exist")
    if len(teams) < 2:
        raise ValueError("a game needs at least two teams")

    all_ids: list[int] = []
    for i, team in enumerate(teams):
        if not team.player_ids:
            raise ValueError(f"team {i} has no players")
        if len(set(team.player_ids)) != len(team.player_ids):
            raise ValueError(f"team {i} lists a player twice")
        all_ids.extend(team.player_ids)
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("a player cannot be on more than one team")

    ranks = [t.rank for t in teams]
    if any((not isinstance(r, int)) or r < 1 for r in ranks):
        raise ValueError("ranks must be integers >= 1")
    if config.draw_probability == 0 and len(set(ranks)) != len(ranks):
        raise ValueError("ties are not allowed")
    if 1 not in ranks:
        raise ValueError("one team must have rank 1 (the winner)")

    players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_(all_ids)))}
    missing = sorted(set(all_ids) - players.keys())
    if missing:
        raise ValueError(f"unknown player ids: {missing}")
    merged = sorted(pid for pid in all_ids if players[pid].merged_into is not None)
    if merged:
        raise ValueError(f"players were merged into another record: {merged}")

    roster_sizes = [len(t.player_ids) for t in teams]
    if players_on_field is None:
        players_on_field = min(roster_sizes)
    if players_on_field < 1 or players_on_field > max(roster_sizes):
        raise ValueError("players_on_field must be between 1 and the largest roster")

    game = Game(
        session_id=session_id,
        round_number=(
            round_number if round_number is not None else next_round_number(db, session_id)
        ),
        players_on_field=players_on_field,
        played_at=played_at or utcnow(),
    )
    for idx, team in enumerate(teams):
        gt = GameTeam(team_index=idx, rank=team.rank, score=team.score)
        for pid in team.player_ids:
            gt.players.append(GameTeamPlayer(player_id=pid))
        game.teams.append(gt)
    db.add(game)
    db.flush()

    recompute_ratings(db, session.season_id, config, commit=False)
    db.commit()
    return game

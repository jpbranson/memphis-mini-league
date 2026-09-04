"""Write paths for game results: record, edit, delete, restore.

Every path validates, writes, then replays the season's ratings, because
`rating_history` and `player_season_ratings` are derived from the game tables
(design doc section 4.6). Edits and deletions write an audit entry carrying the
full before-state so milestone 4 can offer undo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import game_snapshot, log_action
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


def get_game(db: Session, game_id: int, *, include_deleted: bool = True) -> Game:
    game = db.get(Game, game_id)
    if game is None or (game.deleted_at is not None and not include_deleted):
        raise LookupError(f"game {game_id} does not exist")
    return game


def session_games(db: Session, session_id: int, *, include_deleted: bool = False) -> list[Game]:
    """Games in a session, most recent round first."""
    stmt = select(Game).where(Game.session_id == session_id)
    if not include_deleted:
        stmt = stmt.where(Game.deleted_at.is_(None))
    return list(db.scalars(stmt.order_by(Game.round_number.desc(), Game.id.desc())))


def _validate_team_inputs(
    db: Session, teams: Sequence[TeamInput], config: RatingConfig
) -> None:
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

    for i, team in enumerate(teams):
        if team.score is not None and team.score < 0:
            raise ValueError(f"team {i} has a negative score")

    # Cross-check the winner against the scores, naming the discrepancy so the
    # organizer can see which of the two they got wrong.
    if len(teams) == 2 and all(t.score is not None for t in teams):
        letters = ("A", "B")
        winner_index = min(range(2), key=lambda i: teams[i].rank)
        loser_index = 1 - winner_index
        winner, loser = teams[winner_index], teams[loser_index]
        if winner.score == loser.score:
            raise ValueError(
                f"Both teams are down as scoring {winner.score}. "
                "Games cannot end in a tie, so one score needs to change."
            )
        if winner.score < loser.score:
            raise ValueError(
                f"Team {letters[winner_index]} is marked as the winner but scored "
                f"{winner.score}, while Team {letters[loser_index]} scored {loser.score}. "
                "Change the winner, or swap the scores."
            )

    players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_(all_ids)))}
    missing = sorted(set(all_ids) - players.keys())
    if missing:
        raise ValueError(f"unknown player ids: {missing}")
    merged = sorted(pid for pid in all_ids if players[pid].merged_into is not None)
    if merged:
        raise ValueError(f"players were merged into another record: {merged}")


def _resolve_players_on_field(
    teams: Sequence[TeamInput], players_on_field: int | None
) -> int:
    roster_sizes = [len(t.player_ids) for t in teams]
    if players_on_field is None:
        return min(roster_sizes)
    if players_on_field < 1 or players_on_field > max(roster_sizes):
        raise ValueError("players_on_field must be between 1 and the largest roster")
    return players_on_field


def _replace_teams(db: Session, game: Game, teams: Sequence[TeamInput]) -> None:
    """Swap a game's teams for new ones.

    The old rows are deleted and flushed first: (game_id, team_index) is unique,
    so inserting team 0 before the previous team 0 is gone would collide.
    """
    for existing in list(game.teams):
        game.teams.remove(existing)
        db.delete(existing)
    db.flush()
    _apply_teams(game, teams)
    db.flush()


def _apply_teams(game: Game, teams: Sequence[TeamInput]) -> None:
    """Attach teams to a game that has none."""
    for idx, team in enumerate(teams):
        gt = GameTeam(team_index=idx, rank=team.rank, score=team.score)
        for pid in team.player_ids:
            gt.players.append(GameTeamPlayer(player_id=pid))
        game.teams.append(gt)


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

    _validate_team_inputs(db, teams, config)
    resolved_on_field = _resolve_players_on_field(teams, players_on_field)

    game = Game(
        session_id=session_id,
        round_number=(
            round_number if round_number is not None else next_round_number(db, session_id)
        ),
        players_on_field=resolved_on_field,
        played_at=played_at or utcnow(),
    )
    _apply_teams(game, teams)
    db.add(game)
    db.flush()

    recompute_ratings(db, session.season_id, config, commit=False)
    db.commit()
    return game


def edit_game(
    db: Session,
    game_id: int,
    *,
    teams: list[TeamInput] | None = None,
    players_on_field: int | None = None,
    round_number: int | None = None,
    played_at: datetime | None = None,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
) -> Game:
    """Correct a recorded game, then replay the season. Commits.

    Passing `teams` replaces the rosters, ranks, and scores entirely. Omitting
    it keeps them and edits only the fields given.
    """
    game = get_game(db, game_id)
    before = game_snapshot(game)

    if teams is not None:
        _validate_team_inputs(db, teams, config)
        game.players_on_field = _resolve_players_on_field(teams, players_on_field)
        _replace_teams(db, game, teams)
    elif players_on_field is not None:
        sizes = [len(t.players) for t in game.teams]
        if players_on_field < 1 or players_on_field > max(sizes):
            raise ValueError("players_on_field must be between 1 and the largest roster")
        game.players_on_field = players_on_field

    if round_number is not None:
        if round_number < 1:
            raise ValueError("round_number must be >= 1")
        game.round_number = round_number
    if played_at is not None:
        game.played_at = played_at

    db.flush()
    db.refresh(game)
    log_action(db, "edit_game", {"before": before, "after": game_snapshot(game)})

    recompute_ratings(db, game.session.season_id, config, commit=False)
    db.commit()
    return game


def delete_game(
    db: Session, game_id: int, *, config: RatingConfig = DEFAULT_RATING_CONFIG
) -> Game:
    """Soft-delete a game and replay the season without it. Commits."""
    game = get_game(db, game_id)
    if game.deleted_at is not None:
        raise ValueError(f"game {game_id} is already deleted")

    before = game_snapshot(game)
    game.deleted_at = utcnow()
    db.flush()
    log_action(db, "delete_game", {"before": before})

    recompute_ratings(db, game.session.season_id, config, commit=False)
    db.commit()
    return game


def restore_game(
    db: Session, game_id: int, *, config: RatingConfig = DEFAULT_RATING_CONFIG
) -> Game:
    """Undo a soft delete and replay the season with the game back. Commits."""
    game = get_game(db, game_id)
    if game.deleted_at is None:
        raise ValueError(f"game {game_id} is not deleted")

    game.deleted_at = None
    db.flush()
    log_action(db, "restore_game", {"after": game_snapshot(game)})

    recompute_ratings(db, game.session.season_id, config, commit=False)
    db.commit()
    return game

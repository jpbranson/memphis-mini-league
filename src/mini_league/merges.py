"""Player management: rename, deactivate, merge duplicates, undo (design doc 6.1).

Organizers add players by name on the day, so the same person ends up with two
records. Merging folds one into the other and replays every rating. Each merge
writes an audit entry holding enough before-state to put it back exactly.

`rating_history` and `player_season_ratings` are not reassigned by hand: they
are derived tables that `recompute_all_ratings` clears and rebuilds from the
game tables, which is the same result with no chance of drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .audit import log_action
from .models import (
    AuditLog,
    GameTeam,
    GameTeamPlayer,
    Player,
    SessionPlayer,
    utcnow,
)
from .players import get_player, normalize
from .recompute import recompute_all_ratings
from .settings import DEFAULT_RATING_CONFIG, RatingConfig

MERGE_ACTION = "merge_players"
UNDO_ACTION = "undo_merge"


class MergeConflictError(ValueError):
    """The merge would put the surviving player on both sides of a game."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# --- rename and activation -------------------------------------------------------


def rename_player(db: Session, player_id: int, name: str, *, commit: bool = True) -> Player:
    """Rename in place. All history hangs off the id, so nothing else moves."""
    name = name.strip()
    if not name:
        raise ValueError("player name is required")

    player = get_player(db, player_id)
    clash = db.scalars(
        select(Player).where(
            Player.active.is_(True), Player.name == name, Player.id != player_id
        )
    ).first()
    if clash is not None:
        raise ValueError(f"an active player is already named {name!r}")

    before = player.name
    player.name = name
    db.flush()
    log_action(
        db,
        "rename_player",
        {"player_id": player_id, "before": before, "after": name},
    )
    if commit:
        db.commit()
    return player


def set_player_active(
    db: Session, player_id: int, active: bool, *, commit: bool = True
) -> Player:
    """Retire or reinstate a player. History is untouched either way."""
    player = get_player(db, player_id)
    if active and player.merged_into is not None:
        raise ValueError(
            f"{player.name!r} was merged into another player; undo the merge instead"
        )
    if active:
        clash = db.scalars(
            select(Player).where(
                Player.active.is_(True), Player.name == player.name, Player.id != player_id
            )
        ).first()
        if clash is not None:
            raise ValueError(
                f"an active player is already named {player.name!r}; rename one of them first"
            )

    was = player.active
    player.active = active
    db.flush()
    log_action(
        db,
        "set_player_active",
        {"player_id": player_id, "before": was, "after": active},
    )
    if commit:
        db.commit()
    return player


# --- merge -----------------------------------------------------------------------


@dataclass
class MergePlan:
    """What a merge would do, so it can be shown before it happens."""

    moved_game_teams: list[int]
    removed_game_teams: list[int]
    moved_sessions: list[int]
    removed_sessions: list[int]
    conflicts: list[int]

    @property
    def is_safe(self) -> bool:
        return not self.conflicts

    @property
    def games_affected(self) -> int:
        return len(self.moved_game_teams) + len(self.removed_game_teams)


def plan_merge(db: Session, source_id: int, target_id: int) -> MergePlan:
    """Work out which rows move, which collapse, and which games would clash."""
    rows = db.execute(
        select(GameTeamPlayer.player_id, GameTeam.id, GameTeam.game_id)
        .join(GameTeam, GameTeam.id == GameTeamPlayer.game_team_id)
        .where(GameTeamPlayer.player_id.in_([source_id, target_id]))
    ).all()

    source_teams = {team_id: game_id for pid, team_id, game_id in rows if pid == source_id}
    target_teams = {team_id: game_id for pid, team_id, game_id in rows if pid == target_id}
    target_games = {game_id: team_id for team_id, game_id in target_teams.items()}

    moved, removed, conflicts = [], [], []
    for team_id, game_id in source_teams.items():
        other = target_games.get(game_id)
        if other is None:
            moved.append(team_id)  # target not in this game, just reassign
        elif other == team_id:
            removed.append(team_id)  # same team twice, collapse into one
        else:
            conflicts.append(game_id)  # opposite teams, cannot be one person

    source_sessions = set(
        db.scalars(
            select(SessionPlayer.session_id).where(SessionPlayer.player_id == source_id)
        )
    )
    target_sessions = set(
        db.scalars(
            select(SessionPlayer.session_id).where(SessionPlayer.player_id == target_id)
        )
    )
    return MergePlan(
        moved_game_teams=sorted(moved),
        removed_game_teams=sorted(removed),
        moved_sessions=sorted(source_sessions - target_sessions),
        removed_sessions=sorted(source_sessions & target_sessions),
        conflicts=sorted(set(conflicts)),
    )


def merge_players(
    db: Session,
    source_id: int,
    target_id: int,
    *,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
    commit: bool = True,
) -> AuditLog:
    """Fold `source` into `target`, replay every season, and log the undo.

    Designations follow the record they belong to. The target keeps its own
    standing designation, since the merge says the two records are one person
    and the target is the one being kept. A day-of override travels with the
    session row it was set on, so where only the source was checked in the
    override moves across, and where both were the target's row is the one that
    survives. The snapshot carries the discarded override so undo puts it back.
    """
    if source_id == target_id:
        raise ValueError("a player cannot be merged into themselves")

    source = get_player(db, source_id)
    target = get_player(db, target_id)
    if source.merged_into is not None:
        raise ValueError(f"{source.name!r} has already been merged")
    if target.merged_into is not None:
        raise ValueError(f"{target.name!r} has itself been merged into another player")

    plan = plan_merge(db, source_id, target_id)
    if plan.conflicts:
        raise MergeConflictError(
            f"{source.name!r} and {target.name!r} played against each other in "
            f"game(s) {', '.join(str(g) for g in plan.conflicts)}, so they cannot "
            "be the same person. Check the games before merging."
        )

    removed_sessions = [
        {
            "session_id": entry.session_id,
            "checked_in_at": _iso(entry.checked_in_at),
            "checked_out_at": _iso(entry.checked_out_at),
            "designation_override": entry.designation_override,
        }
        for entry in db.scalars(
            select(SessionPlayer).where(
                SessionPlayer.player_id == source_id,
                SessionPlayer.session_id.in_(plan.removed_sessions or [-1]),
            )
        )
    ]

    payload = {
        "source": {
            "id": source.id,
            "name": source.name,
            "active": source.active,
            "merged_into": source.merged_into,
        },
        "target": {"id": target.id, "name": target.name},
        "moved_game_teams": plan.moved_game_teams,
        "removed_game_teams": plan.removed_game_teams,
        "moved_sessions": plan.moved_sessions,
        "removed_sessions": removed_sessions,
        "merged_at": _iso(utcnow()),
    }

    if plan.moved_game_teams:
        db.execute(
            update(GameTeamPlayer)
            .where(
                GameTeamPlayer.player_id == source_id,
                GameTeamPlayer.game_team_id.in_(plan.moved_game_teams),
            )
            .values(player_id=target_id)
        )
    if plan.removed_game_teams:
        db.execute(
            delete(GameTeamPlayer).where(
                GameTeamPlayer.player_id == source_id,
                GameTeamPlayer.game_team_id.in_(plan.removed_game_teams),
            )
        )
    if plan.moved_sessions:
        db.execute(
            update(SessionPlayer)
            .where(
                SessionPlayer.player_id == source_id,
                SessionPlayer.session_id.in_(plan.moved_sessions),
            )
            .values(player_id=target_id)
        )
    if plan.removed_sessions:
        db.execute(
            delete(SessionPlayer).where(
                SessionPlayer.player_id == source_id,
                SessionPlayer.session_id.in_(plan.removed_sessions),
            )
        )

    source.active = False
    source.merged_into = target_id
    db.flush()

    entry = log_action(db, MERGE_ACTION, payload)
    recompute_all_ratings(db, config, commit=False)
    db.flush()
    if commit:
        db.commit()
    return entry


def undo_merge(
    db: Session,
    audit_id: int,
    *,
    config: RatingConfig = DEFAULT_RATING_CONFIG,
    commit: bool = True,
) -> AuditLog:
    """Put a merge back exactly, using the before-state it recorded."""
    entry = db.get(AuditLog, audit_id)
    if entry is None:
        raise LookupError(f"audit entry {audit_id} does not exist")
    if entry.action != MERGE_ACTION:
        raise ValueError(f"audit entry {audit_id} is not a merge")
    if is_merge_undone(db, audit_id):
        raise ValueError(f"merge {audit_id} has already been undone")

    payload = entry.payload
    source_id = payload["source"]["id"]
    target_id = payload["target"]["id"]

    source = db.get(Player, source_id)
    if source is None:
        raise LookupError(f"player {source_id} no longer exists")

    moved_game_teams = payload.get("moved_game_teams") or []
    if moved_game_teams:
        db.execute(
            update(GameTeamPlayer)
            .where(
                GameTeamPlayer.player_id == target_id,
                GameTeamPlayer.game_team_id.in_(moved_game_teams),
            )
            .values(player_id=source_id)
        )
    for team_id in payload.get("removed_game_teams") or []:
        db.add(GameTeamPlayer(game_team_id=team_id, player_id=source_id))

    moved_sessions = payload.get("moved_sessions") or []
    if moved_sessions:
        db.execute(
            update(SessionPlayer)
            .where(
                SessionPlayer.player_id == target_id,
                SessionPlayer.session_id.in_(moved_sessions),
            )
            .values(player_id=source_id)
        )
    for row in payload.get("removed_sessions") or []:
        db.add(
            SessionPlayer(
                session_id=row["session_id"],
                player_id=source_id,
                checked_in_at=_parse(row["checked_in_at"]) or utcnow(),
                checked_out_at=_parse(row["checked_out_at"]),
                designation_override=row.get("designation_override"),
            )
        )

    source.active = payload["source"].get("active", True)
    source.merged_into = payload["source"].get("merged_into")
    db.flush()

    undo_entry = log_action(
        db,
        UNDO_ACTION,
        {
            "merge_audit_id": audit_id,
            "source_player_id": source_id,
            "target_player_id": target_id,
        },
    )
    recompute_all_ratings(db, config, commit=False)
    db.flush()
    if commit:
        db.commit()
    return undo_entry


def is_merge_undone(db: Session, audit_id: int) -> bool:
    for entry in db.scalars(select(AuditLog).where(AuditLog.action == UNDO_ACTION)):
        if entry.payload.get("merge_audit_id") == audit_id:
            return True
    return False


def merge_candidates(db: Session, player_id: int, limit: int = 8) -> list[Player]:
    """Other live players, closest name first, as merge targets."""
    from .players import similarity

    player = get_player(db, player_id)
    others = [
        p
        for p in db.scalars(select(Player).where(Player.merged_into.is_(None)))
        if p.id != player_id
    ]
    others.sort(key=lambda p: (-similarity(player.name, p.name), normalize(p.name)))
    return others[:limit]


def audit_entries(db: Session, limit: int = 50) -> list[AuditLog]:
    """Newest first."""
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)))

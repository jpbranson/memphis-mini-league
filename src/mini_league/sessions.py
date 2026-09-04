"""Session creation and check-in/check-out (design doc sections 5.3, 7).

A session is one morning. Players check in when they arrive and check out when
they leave; only checked-in players are offered for the next round's teams.
Past games are never affected by a later check-out.
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import designations
from .models import LeagueSession, Player, SessionPlayer, utcnow
from .players import get_player
from .seasons import season_for_date


def create_session(
    db: Session,
    on: date_type,
    *,
    notes: str | None = None,
    season_id: int | None = None,
    commit: bool = True,
) -> LeagueSession:
    """Create a session. The season is inferred from the date unless given."""
    if season_id is None:
        season_id = season_for_date(db, on).id
    session = LeagueSession(season_id=season_id, date=on, notes=notes or None)
    db.add(session)
    db.flush()
    if commit:
        db.commit()
    return session


def get_session(db: Session, session_id: int) -> LeagueSession:
    session = db.get(LeagueSession, session_id)
    if session is None:
        raise LookupError(f"session {session_id} does not exist")
    return session


def list_sessions(db: Session, *, season_id: int | None = None) -> list[LeagueSession]:
    """Most recent first."""
    stmt = select(LeagueSession).order_by(LeagueSession.date.desc(), LeagueSession.id.desc())
    if season_id is not None:
        stmt = stmt.where(LeagueSession.season_id == season_id)
    return list(db.scalars(stmt))


def check_in(db: Session, session_id: int, player_id: int, *, commit: bool = True) -> SessionPlayer:
    """Check a player in. Re-checking in someone who left clears their check-out."""
    get_session(db, session_id)
    player = get_player(db, player_id)
    if player.merged_into is not None:
        raise ValueError(f"player {player.name!r} was merged into another record")

    entry = db.get(SessionPlayer, (session_id, player_id))
    if entry is None:
        entry = SessionPlayer(session_id=session_id, player_id=player_id)
        db.add(entry)
    else:
        entry.checked_out_at = None
        entry.checked_in_at = utcnow()
    db.flush()
    if commit:
        db.commit()
    return entry


def check_out(db: Session, session_id: int, player_id: int, *, commit: bool = True) -> SessionPlayer:
    """Check a player out. They are excluded from later rounds; past games stand."""
    entry = db.get(SessionPlayer, (session_id, player_id))
    if entry is None:
        raise LookupError(f"player {player_id} is not checked in to session {session_id}")
    entry.checked_out_at = utcnow()
    db.flush()
    if commit:
        db.commit()
    return entry


def session_roster(db: Session, session_id: int) -> list[SessionPlayer]:
    """Everyone who has been checked in, present or not, ordered by name."""
    return list(
        db.scalars(
            select(SessionPlayer)
            .join(Player, SessionPlayer.player_id == Player.id)
            .where(SessionPlayer.session_id == session_id)
            .order_by(Player.name)
            .options(selectinload(SessionPlayer.player))
        )
    )


def checked_in_players(db: Session, session_id: int) -> list[Player]:
    """Players currently present, ordered by name."""
    return [sp.player for sp in session_roster(db, session_id) if sp.checked_out_at is None]


def rounds_played(db: Session, session_id: int) -> dict[int, int]:
    """How many rounds each player has already played in this session."""
    from .models import Game, GameTeam, GameTeamPlayer

    rows = db.execute(
        select(GameTeamPlayer.player_id, func.count())
        .join(GameTeam, GameTeam.id == GameTeamPlayer.game_team_id)
        .join(Game, Game.id == GameTeam.game_id)
        .where(Game.session_id == session_id, Game.deleted_at.is_(None))
        .group_by(GameTeamPlayer.player_id)
    ).all()
    return {player_id: count for player_id, count in rows}


def move_session_to_season(
    db: Session, session_id: int, season_id: int, *, commit: bool = True
) -> LeagueSession:
    """Move a session between seasons and replay both (design doc section 6.1).

    Rare, but a session created on the wrong side of a season boundary would
    otherwise be stuck in the wrong standings.
    """
    from .audit import log_action
    from .models import Season
    from .recompute import recompute_ratings

    session = get_session(db, session_id)
    if db.get(Season, season_id) is None:
        raise LookupError(f"season {season_id} does not exist")

    previous = session.season_id
    if previous == season_id:
        return session

    session.season_id = season_id
    db.flush()
    log_action(
        db,
        "move_session_season",
        {"session_id": session_id, "before": previous, "after": season_id},
    )
    for affected in (previous, season_id):
        recompute_ratings(db, affected, commit=False)
    if commit:
        db.commit()
    return session


def save_pending_teams(
    db: Session,
    session_id: int,
    assignment: dict[int, int],
    *,
    team_size: str = "",
    max_on_field: str = "",
    even_designations: bool = False,
    commit: bool = True,
) -> LeagueSession:
    """Remember the line-up chosen for the round that has not been played yet.

    The organizer picks sides, pockets the phone, plays, and comes back. The
    page is long gone by then, so the choice has to live on the server.
    """
    session = get_session(db, session_id)
    session.pending_teams = {
        "assignment": {str(pid): side for pid, side in sorted(assignment.items())},
        "team_size": team_size,
        "max_on_field": max_on_field,
        "even_designations": bool(even_designations),
        "saved_at": utcnow().isoformat(),
    }
    db.flush()
    if commit:
        db.commit()
    return session


def load_pending_teams(db: Session, session_id: int) -> dict:
    """The saved line-up, dropping anyone who has since left."""
    session = get_session(db, session_id)
    stored = session.pending_teams or {}
    present = {p.id for p in checked_in_players(db, session_id)}
    assignment = {
        int(pid): side
        for pid, side in (stored.get("assignment") or {}).items()
        if int(pid) in present
    }
    return {
        "assignment": assignment,
        "team_size": stored.get("team_size") or "",
        "max_on_field": stored.get("max_on_field") or "",
        "even_designations": bool(stored.get("even_designations")),
    }


def clear_pending_teams(db: Session, session_id: int, *, commit: bool = True) -> None:
    """Called once the round has actually been recorded."""
    session = db.get(LeagueSession, session_id)
    if session is None or session.pending_teams is None:
        return
    session.pending_teams = None
    db.flush()
    if commit:
        db.commit()


def set_session_designation(
    db: Session,
    session_id: int,
    player_id: int,
    designation: str | None,
    *,
    commit: bool = True,
) -> SessionPlayer:
    """Set a player's designation for this session only.

    Three answers are storable and all three are different. WMP or MMP override
    what the player usually is; "none" says they have no designation today, which
    is not the same as never having had one; and clearing the override entirely
    hands them back to their standing designation.
    """
    entry = db.get(SessionPlayer, (session_id, player_id))
    if entry is None:
        raise LookupError(f"player {player_id} is not checked in to session {session_id}")

    entry.designation_override = designations.parse_override(designation)
    db.flush()
    if commit:
        db.commit()
    return entry


def clear_session_designation(
    db: Session, session_id: int, player_id: int, *, commit: bool = True
) -> SessionPlayer:
    """Drop today's override so the player counts as whatever they usually are."""
    return set_session_designation(db, session_id, player_id, None, commit=commit)


def session_designations(db: Session, session_id: int) -> dict[int, str | None]:
    """Effective designation per checked-in player, for the team balancer."""
    return {
        sp.player_id: sp.designation
        for sp in session_roster(db, session_id)
        if sp.checked_out_at is None
    }

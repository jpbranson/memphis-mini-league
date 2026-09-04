"""Session creation and check-in/check-out (design doc sections 5.3, 7).

A session is one morning. Players check in when they arrive and check out when
they leave; only checked-in players are offered for the next round's teams.
Past games are never affected by a later check-out.
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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

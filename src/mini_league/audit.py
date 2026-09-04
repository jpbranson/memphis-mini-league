"""Audit trail helpers (design doc section 6).

Every destructive or corrective organizer action writes a row with enough
before-state to reverse it. The undo UI itself lands in milestone 4.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, Game


def log_action(db: Session, action: str, payload: dict[str, Any]) -> AuditLog:
    """Record an organizer action. Does not commit."""
    entry = AuditLog(action=action, payload=payload)
    db.add(entry)
    return entry


def game_snapshot(game: Game) -> dict[str, Any]:
    """Full state of a game, sufficient to restore it exactly."""
    return {
        "game_id": game.id,
        "session_id": game.session_id,
        "round_number": game.round_number,
        "players_on_field": game.players_on_field,
        "played_at": game.played_at.isoformat() if game.played_at else None,
        "deleted_at": game.deleted_at.isoformat() if game.deleted_at else None,
        "teams": [
            {
                "team_index": t.team_index,
                "rank": t.rank,
                "score": t.score,
                "player_ids": list(t.player_ids),
            }
            for t in sorted(game.teams, key=lambda t: t.team_index)
        ],
    }

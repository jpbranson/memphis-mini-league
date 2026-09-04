"""Player search and creation with duplicate prevention (design doc section 6.1).

Organizers add players by name on the day, so duplicates are the norm rather
than the exception. Creation therefore refuses a near-duplicate name unless it
is forced, and returns the candidate matches so the caller can offer them.
Merging duplicates after the fact is milestone 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Player

# A name at or above this similarity blocks creation unless forced.
DUPLICATE_THRESHOLD = 0.8
# A name at or above this similarity is worth showing in the search box.
SEARCH_THRESHOLD = 0.45


def normalize(name: str) -> str:
    """Case- and whitespace-insensitive form used for comparison only."""
    return " ".join(name.lower().split())


def similarity(a: str, b: str) -> float:
    """0..1 name similarity, tuned for the "Justin" vs "Justin M." case."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    score = SequenceMatcher(None, na, nb).ratio()
    # One name contained in the other is a strong signal: a bare first name
    # against the same first name plus a surname initial.
    if na in nb or nb in na:
        score = max(score, 0.9)
    # A shared first token is a weaker but real signal ("Jon Smith"/"Jon Baker").
    first_a, first_b = na.split()[0], nb.split()[0]
    if first_a == first_b:
        score = max(score, 0.75)
    return score


@dataclass
class PlayerMatch:
    player: Player
    score: float

    @property
    def is_duplicate(self) -> bool:
        return self.score >= DUPLICATE_THRESHOLD


class DuplicatePlayerError(ValueError):
    """A near-duplicate name was rejected. Carries the matches to offer instead."""

    def __init__(self, name: str, matches: list[PlayerMatch]) -> None:
        self.name = name
        self.matches = matches
        names = ", ".join(repr(m.player.name) for m in matches)
        super().__init__(
            f"a player named {names} already exists; check them in instead, "
            f"or create {name!r} anyway"
        )


def search_players(
    db: Session,
    q: str,
    *,
    limit: int = 10,
    include_inactive: bool = True,
) -> list[PlayerMatch]:
    """Fuzzy search, best match first. Includes inactive players by design.

    An organizer needs to see a retired or merged-away player so they check that
    person in rather than creating a second record for them.
    """
    q = q.strip()
    if not q:
        return []
    stmt = select(Player)
    if not include_inactive:
        stmt = stmt.where(Player.active.is_(True))

    matches = [
        PlayerMatch(player=p, score=similarity(q, p.name))
        for p in db.scalars(stmt)
    ]
    matches = [m for m in matches if m.score >= SEARCH_THRESHOLD]
    matches.sort(key=lambda m: (-m.score, normalize(m.player.name)))
    return matches[:limit]


def find_duplicates(db: Session, name: str) -> list[PlayerMatch]:
    """Existing players close enough to `name` to warrant a warning."""
    return [m for m in search_players(db, name, limit=5) if m.is_duplicate]


def create_player(db: Session, name: str, *, force: bool = False, commit: bool = True) -> Player:
    """Add a player, refusing a near-duplicate name unless forced.

    Raises DuplicatePlayerError with the candidate matches when a similar name
    exists and force is False.
    """
    name = name.strip()
    if not name:
        raise ValueError("player name is required")

    if not force:
        duplicates = find_duplicates(db, name)
        if duplicates:
            raise DuplicatePlayerError(name, duplicates)

    # Even when forced, the schema forbids two active players sharing a name.
    exact = db.scalars(
        select(Player).where(Player.active.is_(True), Player.name == name)
    ).first()
    if exact is not None:
        raise ValueError(f"an active player is already named {name!r}")

    player = Player(name=name)
    db.add(player)
    db.flush()
    if commit:
        db.commit()
    return player


def get_player(db: Session, player_id: int) -> Player:
    player = db.get(Player, player_id)
    if player is None:
        raise LookupError(f"player {player_id} does not exist")
    return player


def list_players(db: Session, *, include_inactive: bool = False) -> list[Player]:
    stmt = select(Player).order_by(Player.name)
    if not include_inactive:
        stmt = stmt.where(Player.active.is_(True))
    return list(db.scalars(stmt))

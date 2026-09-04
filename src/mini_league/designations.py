"""Player designations for coed rounds (design doc section 5.4).

A designation says who a player matches up against: WMP for a woman matching
player, MMP for a man matching player. It is optional, and plenty of leagues
never set one.

This is matchmaking information and nothing else. No function here is reachable
from `ratings`, `recompute` or `rating_history`: a designation changes who ends
up on which side, never what a result is worth.

Two places can carry one. A player has a standing designation, and a session can
override it for the day, because someone can turn up and play the other side of
a match-up without that being a permanent change to their record. The override
has a third value, NONE, meaning "no designation today": without it there would
be no way to tell a player who never had one from a player whose designation was
deliberately dropped for the morning.
"""

from __future__ import annotations

from typing import Iterable, Mapping

WMP = "WMP"
MMP = "MMP"

#: The designations a player can hold, in the order they are offered.
DESIGNATIONS = (WMP, MMP)

#: Session-override-only value: no designation for this session.
NONE = "NONE"

LABELS = {WMP: "WMP", MMP: "MMP"}


class UnknownDesignationError(ValueError):
    """A designation outside WMP, MMP and blank."""


def parse(value: str | None) -> str | None:
    """Read a designation off a form or JSON payload. Blank means none.

    Accepts any casing and surrounding space, so "wmp" from a hand-written API
    call lands the same as the button on the check-in list.
    """
    text = (value or "").strip().upper()
    if not text or text in {"-", "NONE", "NULL"}:
        return None
    if text not in DESIGNATIONS:
        raise UnknownDesignationError(
            f"{value!r} is not a designation; use {' or '.join(DESIGNATIONS)}, or leave it blank"
        )
    return text


def parse_override(value: str | None) -> str | None:
    """Read a session override. Blank means "use the player's own".

    The difference from `parse` is the whole point of the override column: here
    an explicit "none" is a real answer that has to be stored, not an absence.
    """
    text = (value or "").strip().upper()
    if not text:
        return None
    if text in {"-", "NONE", "NULL"}:
        return NONE
    if text not in DESIGNATIONS:
        raise UnknownDesignationError(
            f"{value!r} is not a designation; use {' or '.join(DESIGNATIONS)}, "
            "none, or leave it blank to keep theirs"
        )
    return text


def resolve(standing: str | None, override: str | None) -> str | None:
    """What a player counts as in one session: their own unless the day says otherwise."""
    if override is None:
        return standing
    if override == NONE:
        return None
    return override


def counts(values: Iterable[str | None]) -> dict[str, int]:
    """How many of each designation, plus how many have none."""
    tally = {name: 0 for name in DESIGNATIONS}
    tally["none"] = 0
    for value in values:
        tally[value if value in tally else "none"] += 1
    return tally


def imbalance(rosters: Iterable[Iterable[int]], designations: Mapping[int, str | None]) -> float:
    """How unevenly the designations are spread across teams, from 0 to 1.

    Both designations are counted rather than just one, because with players who
    have no designation at all an even split of WMPs does not imply an even split
    of MMPs. Dividing by the number of players makes the result comparable to the
    balance and variety costs, which are also 0..1.
    """
    teams = [list(roster) for roster in rosters]
    total = sum(len(team) for team in teams)
    if total == 0 or len(teams) < 2:
        return 0.0

    spread = 0
    for name in DESIGNATIONS:
        held = [sum(1 for pid in team if designations.get(pid) == name) for team in teams]
        spread += max(held) - min(held)
    return spread / total


__all__ = [
    "DESIGNATIONS",
    "LABELS",
    "MMP",
    "NONE",
    "UnknownDesignationError",
    "WMP",
    "counts",
    "imbalance",
    "parse",
    "parse_override",
    "resolve",
]

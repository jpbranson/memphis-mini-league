"""Season lookup and creation (design doc section 4.5).

Sessions infer their season from their date, so there must always be a season
covering today. Full season management pages are milestone 7; this is the
minimum needed to run an organizer session.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Season


class NoSeasonError(LookupError):
    """No season covers the requested date."""


def list_seasons(db: Session) -> list[Season]:
    """Newest first."""
    return list(db.scalars(select(Season).order_by(Season.start_date.desc(), Season.id.desc())))


def current_season(db: Session) -> Season | None:
    """The open season (no end date), most recent first."""
    return db.scalars(
        select(Season)
        .where(Season.end_date.is_(None))
        .order_by(Season.start_date.desc(), Season.id.desc())
    ).first()


def season_for_date(db: Session, on: date) -> Season:
    """The season whose range contains `on`. Latest start wins if ranges overlap."""
    season = db.scalars(
        select(Season)
        .where(
            Season.start_date <= on,
            (Season.end_date.is_(None)) | (Season.end_date >= on),
        )
        .order_by(Season.start_date.desc(), Season.id.desc())
    ).first()
    if season is None:
        raise NoSeasonError(f"no season covers {on.isoformat()}; create one first")
    return season


def create_season(
    db: Session,
    name: str,
    start_date: date,
    *,
    close_current: bool = True,
    commit: bool = True,
) -> Season:
    """Start a season. By default ends the open one the day before this one starts.

    Creating a season never alters games or history (design doc section 4.5);
    ratings are simply computed per season.
    """
    name = name.strip()
    if not name:
        raise ValueError("season name is required")
    if db.scalars(select(Season).where(Season.name == name)).first() is not None:
        raise ValueError(f"a season named {name!r} already exists")

    if close_current:
        open_season = current_season(db)
        if open_season is not None:
            if open_season.start_date >= start_date:
                raise ValueError(
                    f"the open season {open_season.name!r} starts on "
                    f"{open_season.start_date.isoformat()}, on or after {start_date.isoformat()}"
                )
            open_season.end_date = start_date - timedelta(days=1)

    season = Season(name=name, start_date=start_date)
    db.add(season)
    db.flush()
    if commit:
        db.commit()
    return season


def rename_season(db: Session, season_id: int, name: str, *, commit: bool = True) -> Season:
    """Rename a season. Nothing else moves; sessions point at the id."""
    name = name.strip()
    if not name:
        raise ValueError("season name is required")
    season = db.get(Season, season_id)
    if season is None:
        raise LookupError(f"season {season_id} does not exist")

    clash = db.scalars(
        select(Season).where(Season.name == name, Season.id != season_id)
    ).first()
    if clash is not None:
        raise ValueError(f"a season named {name!r} already exists")

    from .audit import log_action

    before = season.name
    season.name = name
    db.flush()
    log_action(
        db, "rename_season", {"season_id": season_id, "before": before, "after": name}
    )
    if commit:
        db.commit()
    return season

"""Manual smoke test for milestone 1: run one morning of games end to end.

Builds a throwaway database (schema created by the real Alembic migrations),
plays a few rounds including an uneven 3v4, prints the leaderboard, then
corrects a mis-recorded result to show that ratings replay from the games.

    uv run python scripts/demo.py

Writes ./demo.db, deleting any previous one first. Nothing else is touched.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from mini_league.db import make_engine, make_session_factory
from mini_league.games import TeamInput, record_game
from mini_league.models import (
    LeagueSession,
    Player,
    PlayerSeasonRating,
    RatingHistory,
    Season,
    SessionPlayer,
    utcnow,
)
from mini_league.ratings import Rating, display_rating, make_env, win_probabilities

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "demo.db"

NAMES = ["Ada", "Ben", "Cleo", "Dev", "Erin", "Femi", "Gus"]


def build_database() -> str:
    """Fresh file, schema applied by the real migration chain."""
    DB_PATH.unlink(missing_ok=True)
    url = f"sqlite:///{DB_PATH.as_posix()}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["sqlalchemy.url"] = url
    command.upgrade(cfg, "head")
    return url


def rating_of(db, player_id: int, season_id: int) -> Rating:
    snap = db.get(PlayerSeasonRating, (player_id, season_id))
    env = make_env()
    return env.create_rating() if snap is None else Rating(snap.mu, snap.sigma)


def print_leaderboard(db, season: Season, title: str) -> None:
    rows = db.scalars(
        select(PlayerSeasonRating).where(PlayerSeasonRating.season_id == season.id)
    ).all()
    ranked = sorted(
        rows,
        key=lambda r: display_rating(Rating(r.mu, r.sigma)),
        reverse=True,
    )
    print(f"\n{title}")
    print(f"{'#':>2}  {'name':<6} {'rating':>7} {'mu':>7} {'sigma':>6} {'W-L':>7} {'games':>6}")
    print("-" * 50)
    for i, row in enumerate(ranked, start=1):
        name = db.get(Player, row.player_id).name
        rating = display_rating(Rating(row.mu, row.sigma))
        print(
            f"{i:>2}  {name:<6} {rating:>7} {row.mu:>7.2f} {row.sigma:>6.2f} "
            f"{f'{row.wins}-{row.losses}':>7} {row.games_played:>6}"
        )


def main() -> int:
    url = build_database()
    print(f"database: {DB_PATH}")

    engine = make_engine(url)
    with make_session_factory(engine)() as db:
        season = Season(name="Fall 2026", start_date=date(2026, 9, 1))
        db.add(season)
        db.commit()

        players = [Player(name=n) for n in NAMES]
        db.add_all(players)
        db.commit()
        by_name = {p.name: p for p in players}

        session = LeagueSession(season_id=season.id, date=date(2026, 9, 5))
        db.add(session)
        db.commit()

        # Everyone checks in.
        for p in players:
            db.add(SessionPlayer(session_id=session.id, player_id=p.id))
        db.commit()
        print(f"session {session.id} on {session.date}: {len(players)} checked in")

        def ids(*names: str) -> list[int]:
            return [by_name[n].id for n in names]

        env = make_env()
        clock = datetime(2026, 9, 5, 9, 0)

        def play(team_a, team_b, winner: int, score, note: str):
            """winner: 0 or 1. Prints the predicted win % before recording."""
            nonlocal clock
            ratings_a = [rating_of(db, pid, season.id) for pid in team_a]
            ratings_b = [rating_of(db, pid, season.id) for pid in team_b]
            on_field = min(len(team_a), len(team_b))
            pa, pb = win_probabilities(env, [ratings_a, ratings_b], on_field)
            names_a = "/".join(db.get(Player, i).name for i in team_a)
            names_b = "/".join(db.get(Player, i).name for i in team_b)
            print(
                f"\n{note}: {names_a} vs {names_b}"
                f"  predicted {pa:.0%}-{pb:.0%}  ->  team {winner + 1} wins {score[0]}-{score[1]}"
            )
            clock = clock + timedelta(minutes=20)
            return record_game(
                db,
                session.id,
                [
                    TeamInput(team_a, rank=1 if winner == 0 else 2, score=score[0]),
                    TeamInput(team_b, rank=2 if winner == 0 else 1, score=score[1]),
                ],
                played_at=clock,
            )

        # Round 1 and 2: even 3v3, Gus sits out.
        play(ids("Ada", "Ben", "Cleo"), ids("Dev", "Erin", "Femi"), 0, (5, 3), "round 1")
        play(ids("Ada", "Dev", "Erin"), ids("Ben", "Cleo", "Femi"), 0, (5, 4), "round 2")

        # Round 3: 7 players, so one team carries a sub (3 on the field, 4 on the roster).
        upset = play(
            ids("Ada", "Ben", "Dev"),
            ids("Cleo", "Erin", "Femi", "Gus"),
            1,
            (2, 5),
            "round 3 (3v4, partial play)",
        )

        print_leaderboard(db, season, "Leaderboard after 3 rounds")

        # A correction: round 3 was entered backwards. Soft-delete and re-record.
        print("\n--- correcting round 3 (it was entered backwards) ---")
        upset.deleted_at = utcnow()
        db.commit()
        play(
            ids("Ada", "Ben", "Dev"),
            ids("Cleo", "Erin", "Femi", "Gus"),
            0,
            (5, 2),
            "round 3 (corrected)",
        )

        print_leaderboard(db, season, "Leaderboard after the correction")

        history = db.scalars(
            select(RatingHistory)
            .where(RatingHistory.player_id == by_name["Ada"].id)
            .order_by(RatingHistory.id)
        ).all()
        print(f"\nAda's rating history ({len(history)} games, soft-deleted game excluded):")
        for h in history:
            print(
                f"  game {h.game_id}: mu {h.mu_before:.2f} -> {h.mu_after:.2f}   "
                f"sigma {h.sigma_before:.2f} -> {h.sigma_after:.2f}"
            )

    engine.dispose()
    print(
        f"\nOK. Inspect the tables with:\n"
        f'  uv run python -c "import sqlite3;'
        f"print([r[0] for r in sqlite3.connect(r'{DB_PATH.name}')"
        f".execute('select name from sqlite_master where type=\\'table\\'')])\""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

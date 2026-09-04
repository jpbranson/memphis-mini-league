"""Generate plausible sessions so the app has something to look at.

This fills the database with realistic mornings. It is not the parameter-tuning
simulator from design doc section 10; that one is milestone 6 and answers a
different question (how fast do ratings converge on hidden true skill).

Each player is given a hidden "true skill", anchored on whatever the league
already believes about them, and results are drawn from those hidden values the
way TrueSkill assumes they are. Teams come from the app's own balancer, so the
generated history looks like a league that used the app.

    uv run python scripts/simulate.py --days 5 --min-games 2 --max-games 4

Rules applied here:
  - Between 4 players and the whole roster turn up each day.
  - At most 5 a side on the field. Extra players stay on the team roster and
    substitute in and out, which TrueSkill models as partial play.
  - Games run to 5 when it is 4 a side or bigger, otherwise to 3.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from trueskill import Rating

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_league.db import make_engine, make_session_factory  # noqa: E402
from mini_league.games import TeamInput, record_game  # noqa: E402
from mini_league.leaderboard import current_ratings, leaderboard  # noqa: E402
from mini_league.models import LeagueSession, Player  # noqa: E402
from mini_league.ratings import display_rating, make_env  # noqa: E402
from mini_league.seasons import create_season, current_season  # noqa: E402
from mini_league.sessions import check_in, create_session  # noqa: E402
from mini_league.settings import DEFAULT_RATING_CONFIG, get_settings  # noqa: E402
from mini_league.teams import generate_teams  # noqa: E402

MAX_ON_FIELD = 5
SCORE_TO_WHEN_BIG = 5
SCORE_TO_WHEN_SMALL = 3
BIG_GAME_ON_FIELD = 4


def hidden_skills(db, season_id: int, players: list[Player], rng: random.Random) -> dict[int, float]:
    """A true skill per player, anchored on what the league already believes.

    Anchoring keeps the new games consistent with the existing record instead of
    contradicting it, while the noise gives the ratings something to converge on.
    """
    known = current_ratings(db, season_id, [p.id for p in players])
    return {p.id: known[p.id].mu + rng.gauss(0, 2.0) for p in players}


def play_out(
    team_a: list[int],
    team_b: list[int],
    skills: dict[int, float],
    on_field: int,
    beta: float,
    rng: random.Random,
) -> int:
    """Return the winning team index, drawn from the hidden skills.

    Each player performs at their true skill plus noise, weighted down when
    their team carries more players than fit on the field.
    """

    def performance(team: list[int]) -> float:
        weight = min(1.0, on_field / len(team))
        return sum(weight * (skills[pid] + rng.gauss(0, beta)) for pid in team)

    return 0 if performance(team_a) >= performance(team_b) else 1


def score_for(on_field: int, rng: random.Random) -> tuple[int, int]:
    target = SCORE_TO_WHEN_BIG if on_field >= BIG_GAME_ON_FIELD else SCORE_TO_WHEN_SMALL
    return target, rng.randint(0, target - 1)


def simulate(
    db,
    *,
    days: int,
    min_games: int,
    max_games: int,
    min_players: int,
    rng: random.Random,
) -> list[dict]:
    season = current_season(db)
    if season is None:
        season = create_season(db, "Fall 2026", date(2026, 9, 1))

    pool = list(db.scalars(select(Player).where(Player.active.is_(True), Player.merged_into.is_(None))))
    if len(pool) < min_players:
        raise SystemExit(
            f"only {len(pool)} active players; need at least {min_players}"
        )

    last = db.scalars(
        select(LeagueSession.date).order_by(LeagueSession.date.desc())
    ).first()
    start = (last or season.start_date) + timedelta(days=7)

    skills = hidden_skills(db, season.id, pool, rng)
    beta = DEFAULT_RATING_CONFIG.beta
    report: list[dict] = []

    for day in range(days):
        on = start + timedelta(days=7 * day)
        session = create_session(db, on, notes="Simulated")
        attending = rng.sample(pool, rng.randint(min_players, len(pool)))
        for player in attending:
            check_in(db, session.id, player.id)

        rounds = []
        for _ in range(rng.randint(min_games, max_games)):
            ids = [p.id for p in attending]
            split = generate_teams(
                ids,
                current_ratings(db, season.id, ids),
                history=[
                    [list(t.player_ids) for t in sorted(g.teams, key=lambda t: t.team_index)]
                    for g in sorted(session.games, key=lambda g: (g.played_at, g.id))
                    if g.deleted_at is None
                ],
                rng=rng,
            )
            team_a, team_b = [list(t) for t in split.teams]
            on_field = min(MAX_ON_FIELD, len(team_a), len(team_b))
            winner = play_out(team_a, team_b, skills, on_field, beta, rng)
            winning_score, losing_score = score_for(on_field, rng)

            teams = [
                TeamInput(
                    team_a,
                    rank=1 if winner == 0 else 2,
                    score=winning_score if winner == 0 else losing_score,
                ),
                TeamInput(
                    team_b,
                    rank=1 if winner == 1 else 2,
                    score=winning_score if winner == 1 else losing_score,
                ),
            ]
            game = record_game(db, session.id, teams, players_on_field=on_field)
            db.refresh(session)
            rounds.append(
                {
                    "round": game.round_number,
                    "sizes": (len(team_a), len(team_b)),
                    "on_field": on_field,
                    "winner": "A" if winner == 0 else "B",
                    "score": f"{winning_score}-{losing_score}",
                }
            )

        report.append(
            {
                "session_id": session.id,
                "date": on,
                "attending": len(attending),
                "rounds": rounds,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--min-games", type=int, default=2)
    parser.add_argument("--max-games", type=int, default=4)
    parser.add_argument("--min-players", type=int, default=4)
    parser.add_argument("--seed", type=int, default=None, help="for a repeatable run")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    url = args.database_url or get_settings().database_url
    rng = random.Random(args.seed)
    engine = make_engine(url)
    print(f"database: {url}")

    with make_session_factory(engine)() as db:
        report = simulate(
            db,
            days=args.days,
            min_games=args.min_games,
            max_games=args.max_games,
            min_players=args.min_players,
            rng=rng,
        )

        for day in report:
            print(f"\n{day['date']}  session {day['session_id']}  {day['attending']} players")
            for r in day["rounds"]:
                a, b = r["sizes"]
                subs = "" if max(a, b) <= r["on_field"] else "  (subs rotating)"
                print(
                    f"   round {r['round']}: {a}v{b} rosters, {r['on_field']} a side"
                    f"  team {r['winner']} won {r['score']}{subs}"
                )

        season = current_season(db)
        print(f"\nStandings for {season.name}")
        print(f"{'#':>3}  {'name':<8}{'rating':>8}{'W-L':>8}{'games':>7}")
        for row in leaderboard(db, season.id, min_games=0):
            print(
                f"{row.rank:>3}  {row.player.name:<8}{row.rating:>8}"
                f"{row.record:>8}{row.games_played:>7}"
            )

    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())

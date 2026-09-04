"""Wipe the local database and fill it with one made-up season.

Unlike `scripts/simulate.py`, which adds mornings to whatever is already there,
this starts from nothing: it deletes the database file, rebuilds the schema with
the real migrations, invents a roster, and plays a whole season out.

    uv run python scripts/seed_season.py --seed 7

The season it builds by default:

  - 15 players. 8 of them are regulars who turn up to about 70% of sessions;
    the other 7 drift in and out in streaks, and average roughly a third.
  - 15 sessions, one a day, the last of them today.
  - 50 games spread over those sessions, 80% 3v3, 10% 2v2 and 10% 4v4.

Everyone is given a hidden "true skill" the rating system never sees, and
results are drawn from those skills the way TrueSkill assumes they are. Teams
come from the app's own balancer and who sits out comes from its own bench
rule, so the history looks like a league that used the app all season.

This deletes the database it points at. It refuses to run against anything but
a local SQLite file.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_league.db import make_engine, make_session_factory  # noqa: E402
from mini_league.games import TeamInput, record_game  # noqa: E402
from mini_league.leaderboard import current_ratings, leaderboard  # noqa: E402
from mini_league.models import Player  # noqa: E402
from mini_league.seasons import create_season, current_season  # noqa: E402
from mini_league.sessions import check_in, create_session, rounds_played  # noqa: E402
from mini_league.settings import DEFAULT_RATING_CONFIG, get_settings  # noqa: E402
from mini_league.teams import generate_teams, select_bench  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

NAMES = [
    "Ada", "Ben", "Cleo", "Dev", "Erin", "Femi", "Gus", "Hana",
    "Ivo", "Jae", "Kit", "Lena", "Milo", "Nia", "Omar",
]

# Share of games at each team size. Keys are players per side.
SIZE_MIX = {3: 0.80, 2: 0.10, 4: 0.10}

REGULAR_ATTENDANCE = 0.70

# The sporadic players are modelled as a two-state chain rather than a coin
# flip per session: someone who came last time is likelier to come again, so
# they appear in runs of two or three weeks and then vanish for a fortnight,
# which is what "in and out" actually looks like on a sign-up sheet. These two
# numbers settle at an attendance rate of 0.2 / (0.2 + 0.5) = 29%.
SPORADIC_RETURN = 0.20  # missed the last session -> comes to this one
SPORADIC_STAY = 0.50  # came to the last session -> comes again

# Spread of the hidden true skills, in the rating system's own units. Wide
# enough that the standings end up with a real order to them, narrow enough
# that the bottom of the table still wins games.
SKILL_SPREAD = 4.0

MIN_TO_PLAY = 4  # two a side
SCORE_TO_WHEN_BIG = 5
SCORE_TO_WHEN_SMALL = 3
BIG_GAME_ON_FIELD = 4
FIRST_PULL = time(9, 0)
MINUTES_PER_GAME = 25


class InfeasibleSeason(Exception):
    """This attendance draw cannot host the requested mix of game sizes."""


def build_database(url: str) -> Path:
    """Delete the SQLite file behind `url` and rebuild it from the migrations."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise SystemExit(f"refusing to wipe a non-SQLite database: {url}")
    path = Path(url[len(prefix) :])
    if not path.is_absolute():
        path = ROOT / path
    path.unlink(missing_ok=True)

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["sqlalchemy.url"] = url
    command.upgrade(cfg, "head")
    return path


def plan_attendance(
    rng: random.Random, days: int, regulars: list[int], sporadic: list[int]
) -> list[list[int]]:
    """Who turns up on each day, as indexes into the roster.

    A day that draws fewer than four people is topped up from whoever stayed
    away, because a session that cannot field two a side would not have been
    written down as a session at all.
    """
    came_last_time = {index: False for index in sporadic}
    schedule: list[list[int]] = []

    for _ in range(days):
        attending = [index for index in regulars if rng.random() < REGULAR_ATTENDANCE]
        for index in sporadic:
            chance = SPORADIC_STAY if came_last_time[index] else SPORADIC_RETURN
            here = rng.random() < chance
            came_last_time[index] = here
            if here:
                attending.append(index)

        if len(attending) < MIN_TO_PLAY:
            absent = [i for i in regulars + sporadic if i not in set(attending)]
            rng.shuffle(absent)
            attending.extend(absent[: MIN_TO_PLAY - len(attending)])

        schedule.append(sorted(attending))
    return schedule


def game_size_pool(total_games: int) -> list[int]:
    """The exact multiset of team sizes for the season, matching SIZE_MIX.

    Rounding is settled by giving the largest share whatever the others leave
    over, so the counts always add up to `total_games`.
    """
    minor = sorted(size for size in SIZE_MIX if size != max(SIZE_MIX, key=SIZE_MIX.get))
    pool: list[int] = []
    for size in minor:
        pool.extend([size] * round(SIZE_MIX[size] * total_games))
    main = max(SIZE_MIX, key=SIZE_MIX.get)
    pool.extend([main] * (total_games - len(pool)))
    return pool


def games_per_session(rng: random.Random, days: int, total_games: int) -> list[int]:
    """Split the season's games across the days as evenly as they will go."""
    base, extra = divmod(total_games, days)
    counts = [base] * days
    for index in rng.sample(range(days), extra):
        counts[index] += 1
    return counts


def plan_sizes(
    rng: random.Random, attendance: list[list[int]], counts: list[int], total_games: int
) -> list[list[int]]:
    """Decide the team size of every game, respecting how many turned up.

    Days are filled from the thinnest turnout upwards. A day with five players
    can only host 2v2, so it has to take its games from the small end of the
    pool before a day with nine spends them.
    """
    pool = game_size_pool(total_games)
    plan: list[list[int]] = [[] for _ in attendance]
    order = sorted(range(len(attendance)), key=lambda day: len(attendance[day]))

    for day in order:
        capacity = len(attendance[day]) // 2
        for _ in range(counts[day]):
            fits = [size for size in pool if size <= capacity]
            if not fits:
                raise InfeasibleSeason(
                    f"day {day + 1} had {len(attendance[day])} players and the "
                    "remaining games are all too big for it"
                )
            chosen = rng.choice(fits)
            pool.remove(chosen)
            plan[day].append(chosen)
        rng.shuffle(plan[day])

    return plan


def plan_season(
    rng: random.Random,
    *,
    days: int,
    total_games: int,
    regulars: list[int],
    sporadic: list[int],
    attempts: int = 200,
) -> tuple[list[list[int]], list[list[int]]]:
    """Draw attendance until it can host the requested mix of sizes."""
    last: InfeasibleSeason | None = None
    for _ in range(attempts):
        attendance = plan_attendance(rng, days, regulars, sporadic)
        counts = games_per_session(rng, days, total_games)
        try:
            return attendance, plan_sizes(rng, attendance, counts, total_games)
        except InfeasibleSeason as exc:  # too many thin days; draw again
            last = exc
    raise SystemExit(
        f"could not fit {total_games} games into {days} sessions at this "
        f"attendance after {attempts} tries ({last}). Try another seed."
    )


def play_out(
    team_a: list[int],
    team_b: list[int],
    skills: dict[int, float],
    beta: float,
    rng: random.Random,
) -> int:
    """The winning team index, drawn from the hidden skills the app never sees."""

    def performance(team: list[int]) -> float:
        return sum(skills[pid] + rng.gauss(0, beta) for pid in team)

    return 0 if performance(team_a) >= performance(team_b) else 1


def score_for(on_field: int, rng: random.Random) -> tuple[int, int]:
    target = SCORE_TO_WHEN_BIG if on_field >= BIG_GAME_ON_FIELD else SCORE_TO_WHEN_SMALL
    return target, rng.randint(0, target - 1)


def seed(
    db,
    *,
    rng: random.Random,
    names: list[str],
    regular_count: int,
    days: int,
    total_games: int,
    start: date,
    season_name: str,
) -> tuple[list[dict], list[dict]]:
    season = create_season(db, season_name, start)

    players = [Player(name=name) for name in names]
    db.add_all(players)
    db.commit()

    indexes = list(range(len(players)))
    regulars = sorted(rng.sample(indexes, regular_count))
    sporadic = [i for i in indexes if i not in set(regulars)]

    attendance, sizes = plan_season(
        rng,
        days=days,
        total_games=total_games,
        regulars=regulars,
        sporadic=sporadic,
    )

    skills = {
        player.id: rng.gauss(DEFAULT_RATING_CONFIG.mu, SKILL_SPREAD) for player in players
    }
    beta = DEFAULT_RATING_CONFIG.beta
    report: list[dict] = []
    sessions_attended: Counter[int] = Counter()

    for day in range(days):
        on = start + timedelta(days=day)
        session = create_session(db, on, notes="Simulated")
        attending = [players[i].id for i in attendance[day]]
        for player_id in attending:
            check_in(db, session.id, player_id)

        history: list[list[list[int]]] = []
        rounds: list[dict] = []

        for index, team_size in enumerate(sizes[day]):
            playing, benched = select_bench(
                attending,
                team_size * 2,
                rounds_played(db, session.id),
                rng,
            )
            split = generate_teams(
                playing,
                current_ratings(db, season.id, playing),
                history=history,
                rng=rng,
            )
            team_a, team_b = [list(team) for team in split.teams]
            winner = play_out(team_a, team_b, skills, beta, rng)
            winning_score, losing_score = score_for(team_size, rng)

            game = record_game(
                db,
                session.id,
                [
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
                ],
                players_on_field=team_size,
                played_at=datetime.combine(on, FIRST_PULL)
                + timedelta(minutes=MINUTES_PER_GAME * index),
            )
            history.append([team_a, team_b])
            rounds.append(
                {
                    "round": game.round_number,
                    "size": team_size,
                    "winner": "A" if winner == 0 else "B",
                    "score": f"{winning_score}-{losing_score}",
                    "benched": len(benched),
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
        for player_id in attending:
            sessions_attended[player_id] += 1

    roster = [
        {
            "name": player.name,
            "regular": index in set(regulars),
            "sessions": sessions_attended[player.id],
        }
        for index, player in enumerate(players)
    ]
    return report, roster


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, default=15)
    parser.add_argument("--regulars", type=int, default=8)
    parser.add_argument("--days", type=int, default=15, help="sessions, one a day")
    parser.add_argument("--games", type=int, default=50, help="games in the season")
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="first session (default: so the last session is today)",
    )
    parser.add_argument("--season-name", default="Fall 2026")
    parser.add_argument("--seed", type=int, default=None, help="for a repeatable run")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    if args.regulars > args.players:
        raise SystemExit("there cannot be more regulars than players")
    if args.players > len(NAMES):
        raise SystemExit(f"only {len(NAMES)} names are on hand")

    url = args.database_url or get_settings().database_url
    start = args.start or date.today() - timedelta(days=args.days - 1)
    rng = random.Random(args.seed)

    path = build_database(url)
    print(f"database: {path} (wiped and rebuilt)")

    engine = make_engine(url)
    with make_session_factory(engine)() as db:
        report, roster = seed(
            db,
            rng=rng,
            names=NAMES[: args.players],
            regular_count=args.regulars,
            days=args.days,
            total_games=args.games,
            start=start,
            season_name=args.season_name,
        )

        sizes = Counter(r["size"] for day in report for r in day["rounds"])
        played = sum(len(day["rounds"]) for day in report)

        print(f"\n{args.season_name}: {args.days} sessions, {played} games")
        for day in report:
            line = "  ".join(
                f"{r['size']}v{r['size']} {r['score']}" for r in day["rounds"]
            )
            print(
                f"  {day['date']}  session {day['session_id']:>2}"
                f"  {day['attending']:>2} in   {line}"
            )

        print("\nGame sizes")
        for size in sorted(sizes, reverse=True):
            share = sizes[size] / played
            print(f"  {size}v{size}: {sizes[size]:>2} games  {share:>5.0%}")

        turnouts = {entry["name"]: entry for entry in roster}
        current = current_season(db)

        print(f"\nStandings for {current.name}")
        print(
            f"{'#':>3}  {'name':<8}{'rating':>8}{'W-L':>8}{'games':>7}"
            f"{'sessions':>9}  who"
        )
        for row in leaderboard(db, current.id, min_games=0):
            entry = turnouts[row.player.name]
            turnout = f"{entry['sessions']}/{args.days}"
            print(
                f"{row.rank:>3}  {row.player.name:<8}{row.rating:>8}"
                f"{row.record:>8}{row.games_played:>7}{turnout:>9}"
                f"  {'regular' if entry['regular'] else 'sporadic'}"
            )

    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())

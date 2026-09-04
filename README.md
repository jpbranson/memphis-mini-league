# Mini League

Ultimate frisbee mini-league app: TrueSkill ratings across variable team sizes,
balanced team generation, and a leaderboard. See
[frisbee-mini-league-design.md](frisbee-mini-league-design.md) for the design.

## Status

Milestones 1, 2 and 3 are done.

1. **Core**: data model, Alembic migrations, TrueSkill ratings, `recompute_ratings()`.
2. **Organizer flow**: create a session, check players in with fuzzy search and
   duplicate warnings, record results, edit and delete rounds.
3. **Leaderboard and player pages**: season standings, per-player rating history
   with a chart, reachable from a standings drawer on every page.

The session board also shows each player's rating, balances teams on request,
and previews the matchup as players are moved. Team balancing was brought
forward from milestone 5; the swap-and-regenerate refinements there are still
open.

Not built yet: player merge (milestone 4), the rest of team generation
(milestone 5), the simulator (milestone 6), and the organizer password
(milestone 7). Every page is currently unauthenticated, so do not put this on a
public address yet.

## Run the app

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn mini_league.web.app:app --factory --port 8022
```

Open http://localhost:8022. Create a season first, since sessions infer their
season from their date. Then create a session and work down the board: check
players in, assign them to team A or B, pick the winner, and record. The
interactive API docs are at http://localhost:8022/docs.

## Setup

Requires [uv](https://docs.astral.sh/uv/). It installs the right Python itself,
so no separate Python install is needed.

```bash
uv sync
```

That creates `.venv` and installs SQLAlchemy, Alembic, trueskill, and pytest.
Prefix commands with `uv run` and the virtualenv is used automatically; there is
nothing to activate.

## Test

```bash
uv run pytest
```

253 tests, about 20 seconds. Coverage by file:

| File | What it checks |
|---|---|
| `tests/test_ratings.py` | TrueSkill maths against known values, partial-play weights, win probability, displayed rating |
| `tests/test_models.py` | Schema constraints: uniqueness, foreign keys, composite keys |
| `tests/test_recompute.py` | Replay correctness, chaining, soft deletes, season isolation, validation |
| `tests/test_migrations.py` | The migration chain reproduces the models exactly and downgrades cleanly |
| `tests/test_players.py` | Fuzzy name matching and duplicate prevention |
| `tests/test_sessions.py` | Season inference, check-in and check-out |
| `tests/test_game_edits.py` | Editing, deleting, restoring games, and the audit trail |
| `tests/test_api.py` | The JSON API end to end |
| `tests/test_pages.py` | The organizer pages and their HTMX partials |
| `tests/test_teams.py` | Balancing: split enumeration, balance and variety costs, uneven teams |
| `tests/test_leaderboard.py` | Standings, filters, player history and game lists |
| `tests/test_board_teams.py` | Ratings on the board, the balance button, the matchup panel |
| `tests/test_public_pages.py` | Leaderboard, player pages, and the standings drawer |

Useful variants:

```bash
uv run pytest -v
```

```bash
uv run pytest tests/test_ratings.py -k partial_play -v
```

## Run the demo

This script plays one morning of games end to end against a throwaway database,
without touching the web layer. Useful for checking the rating maths on its own.

```bash
uv run python scripts/demo.py
```

It creates a throwaway `demo.db` using the real migrations, then plays three
rounds with seven players, including an uneven 3v4 that exercises partial-play
weights. It prints the leaderboard, soft-deletes a mis-recorded game, re-records
it, and prints the leaderboard again to show ratings replaying from scratch.

Two things worth looking for in the output. A player with one game sits below
players with lower raw skill because the displayed rating subtracts three sigma.
And the corrected game gets a new id, with the deleted one absent from the
rating history.

## Migrations

Create or update the real database:

```bash
uv run alembic upgrade head
```

That writes `./mini_league.db`. To point at a different file, set
`MINI_LEAGUE_DATABASE_URL` (`sqlite:///path/to.db`).

```bash
uv run alembic current
```

```bash
uv run alembic check
```

`check` reports whether the models have drifted from the migrations. After
changing anything in `models.py`, generate a migration and review it before
committing:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

## Layout

- `src/mini_league/settings.py` - TrueSkill parameters, display scaling, team-gen weights
- `src/mini_league/models.py` - SQLAlchemy models (schema from design doc section 6)
- `src/mini_league/ratings.py` - pure TrueSkill functions, no database
- `src/mini_league/recompute.py` - `recompute_ratings(season_id)` and `recompute_all_ratings()`
- `src/mini_league/games.py` - `record_game()` write path (validates, inserts, recomputes)
- `src/mini_league/db.py` - engine and session factory, with SQLite foreign keys enabled
- `src/mini_league/players.py` - fuzzy search and duplicate-safe player creation
- `src/mini_league/sessions.py` - session creation, check-in and check-out
- `src/mini_league/seasons.py` - season lookup and creation
- `src/mini_league/audit.py` - audit entries carrying full before-state
- `src/mini_league/teams.py` - balanced team generation, a pure function
- `src/mini_league/leaderboard.py` - read models for standings and player pages
- `src/mini_league/web/` - FastAPI app, JSON API, Jinja2 templates, vendored htmx and Chart.js
- `alembic/` - migrations
- `scripts/demo.py` - manual end-to-end smoke test

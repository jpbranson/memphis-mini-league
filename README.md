# Mini League

Ultimate frisbee mini-league app: TrueSkill ratings across variable team sizes,
balanced team generation, and a leaderboard. See
[frisbee-mini-league-design.md](frisbee-mini-league-design.md) for the design.

## Status

All seven milestones are done.

1. **Core**: data model, Alembic migrations, TrueSkill ratings, `recompute_ratings()`.
2. **Organizer flow**: create a session, check players in with fuzzy search and
   duplicate warnings, record results, edit and delete rounds.
3. **Leaderboard and player pages**: season standings, per-player rating history
   with a chart, reachable from a standings drawer on every page. Each player
   page explains what rating and skill mean, worked through their own numbers.
4. **Player management**: rename, retire and reinstate, merge duplicates with a
   confirmation that shows the consequences, an audit log, and undo.

5. **Team generation**: a format picker for team size and how many fit on the
   field, balancing that benches whoever has played most, a one-for-one swap,
   and a live predicted win percentage.

6. **Simulator**: validates the rating system against hidden true skills and
   sweeps the parameters, with no database involved.

7. **Polish**: organizer sign-in, public session history, seasons and settings
   screens, and deployment.

Since then, and not part of any milestone:

- **Designations**: an optional WMP or MMP per player, which the balancer
  evens up across sides when asked, without ever being allowed to force a
  lopsided match. A session can override one for the day; a player's own page
  sets the standing one. Designations never reach a rating.
- **Sharing**: a favicon and masthead mark for each palette, and Open Graph
  tags so a link sent in a message arrives as a card rather than a bare URL.
- **Analytics**: optional Google Analytics, loaded only by an instance that
  sets a measurement id. See [DEPLOY.md](DEPLOY.md).

See [DEPLOY.md](DEPLOY.md) for hosting it.

## Run the app

```bash
uv run alembic upgrade head
```

```bash
cp .env.example .env.local
```

Edit `.env.local` to set an organizer password, then:

```bash
uv run --env-file .env.local uvicorn mini_league.web.app:app --factory --port 8022
```

Open http://localhost:8022. The leaderboard, player pages and session history
are public. Everything that changes a result is behind the organizer password,
and without one set those screens stay closed rather than open.

Create a season first, since sessions infer their season from their date. Then
create a session and work down the board: check players in, balance the teams,
pick the winner, and record. The interactive API docs are at
http://localhost:8022/docs.

## Setup

Requires [uv](https://docs.astral.sh/uv/). It installs the right Python itself,
so no separate Python install is needed.

```bash
uv sync
```

That creates `.venv` and installs FastAPI, uvicorn, Jinja2, SQLAlchemy,
Alembic, trueskill, and pytest.
Prefix commands with `uv run` and the virtualenv is used automatically; there is
nothing to activate.

## Test

```bash
uv run pytest
```

546 tests, about 30 seconds. Coverage by file:

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
| `tests/test_public_pages.py` | Leaderboard, player pages, the rating explanation, and the drawer |
| `tests/test_merges.py` | Rename, retire, merge planning, merging, and undo |
| `tests/test_admin_players.py` | Player management pages, the audit log, and its API |
| `tests/test_bench_and_format.py` | Format picker, bench selection, the swap control, and the on-field limit |
| `tests/test_designations.py` | WMP and MMP: parsing, session overrides, even coed splits, and that none of it reaches a rating |
| `tests/test_simulation.py` | The simulator, checked against cases with known answers |
| `tests/test_auth_and_polish.py` | Sign-in and what it protects, session history, seasons, settings |

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

## Generate sample data

```bash
uv run python scripts/simulate.py --days 5 --min-games 2 --max-games 4
```

Adds realistic mornings to whatever database is configured. Turnout varies, at
most five a side take the field with bigger rosters rotating substitutes, and
games run to 5 at four a side or more and to 3 below that. Pass `--seed` for a
repeatable run. This is sample data, not the parameter-tuning simulator from
milestone 6.

## Seed a whole season

Where `simulate.py` adds mornings to whatever is already there, this starts
from nothing: it deletes the database, rebuilds the schema from the real
migrations, invents a roster and plays a season out.

```bash
uv run python scripts/seed_season.py --seed 65
```

Fifteen players, eight of them regulars at about 70% attendance and the rest
drifting in and out in streaks; fifteen sessions, one a day, the last of them
today; fifty games at 80% 3v3, 10% 2v2 and 10% 4v4; and 7 WMPs to 8 MMPs, with
every morning balanced as a coed one. Teams come from the app's own balancer
and the bench from its own fairness rule, so the history looks like a league
that used the app all season.

**This deletes the database it points at.** It refuses to run against anything
but a local SQLite file, but point `--database-url` somewhere you care about
and it will happily wipe it.

## Validate the rating system

```bash
uv run python scripts/validate_ratings.py
```

Simulates leagues where each player has a hidden true skill the rating system
never sees, then measures how well the leaderboard recovers it. Reports how many
games are needed before the order is right, whether team size or substituting
biases anyone, whether the predicted win percentages are honest, and how the
parameters compare. Takes about a minute. Use `--check` for one section and
`--repeats` to narrow the margins.

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
- `src/mini_league/merges.py` - rename, retire, merge duplicates, undo
- `src/mini_league/teams.py` - balanced team generation, a pure function
- `src/mini_league/designations.py` - WMP and MMP, and how evenly a split spreads them
- `src/mini_league/leaderboard.py` - read models for standings and player pages
- `src/mini_league/simulation.py` - in-memory league simulation for validating the ratings
- `src/mini_league/web/` - FastAPI app, JSON API, Jinja2 templates
- `src/mini_league/web/auth.py` - organizer sign-in and which routes it guards
- `src/mini_league/web/static/` - vendored htmx and Chart.js, the two favicons, the share card
- `Dockerfile`, `fly.toml`, `DEPLOY.md` - hosting
- `alembic/` - migrations
- `scripts/demo.py` - manual end-to-end smoke test
- `scripts/simulate.py` - fills the database with plausible sessions
- `scripts/seed_season.py` - wipes the database and plays a whole season into it
- `scripts/validate_ratings.py` - checks and tunes the rating system
- `scripts/make_share_images.py` - redraws the link-preview PNGs from the palette

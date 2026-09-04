# Mini League

Ultimate frisbee mini-league app: TrueSkill ratings across variable team sizes,
balanced team generation, and a leaderboard. See
[frisbee-mini-league-design.md](frisbee-mini-league-design.md) for the design.

## Status

Milestone 1 (core + tests) is done: data model, Alembic migrations, TrueSkill
ratings module, and `recompute_ratings()`. There is no web layer yet, so the
app is exercised through the test suite and `scripts/demo.py`.

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

63 tests, about 1.5 seconds. Coverage by file:

| File | What it checks |
|---|---|
| `tests/test_ratings.py` | TrueSkill maths against known values, partial-play weights, win probability, displayed rating |
| `tests/test_models.py` | Schema constraints: uniqueness, foreign keys, composite keys |
| `tests/test_recompute.py` | Replay correctness, chaining, soft deletes, season isolation, validation |
| `tests/test_migrations.py` | The migration chain reproduces the models exactly and downgrades cleanly |

Useful variants:

```bash
uv run pytest -v
```

```bash
uv run pytest tests/test_ratings.py -k partial_play -v
```

## Run the demo

No UI exists yet, so this script plays one morning of games end to end and is
the way to see the system work by hand.

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
- `alembic/` - migrations
- `scripts/demo.py` - manual end-to-end smoke test

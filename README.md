# Mini League

Ultimate frisbee mini-league app: TrueSkill ratings across variable team sizes,
balanced team generation, and a leaderboard. See
[frisbee-mini-league-design.md](frisbee-mini-league-design.md) for the design.

## Status

Milestone 1 (core + tests): data model, Alembic migrations, TrueSkill ratings
module, `recompute_ratings()`.

## Layout

- `src/mini_league/settings.py` - TrueSkill parameters, display scaling, team-gen weights
- `src/mini_league/models.py` - SQLAlchemy models (schema from design doc section 6)
- `src/mini_league/ratings.py` - pure TrueSkill functions (rate a game, win probability, partial-play weights, displayed rating)
- `src/mini_league/recompute.py` - `recompute_ratings(season_id)` and `recompute_all_ratings()`
- `src/mini_league/games.py` - `record_game()` write path (validates, inserts, recomputes)
- `alembic/` - migrations

## Commands

```bash
uv sync
uv run pytest
uv run alembic upgrade head          # creates ./mini_league.db
MINI_LEAGUE_DATABASE_URL=sqlite:///path/to.db uv run alembic upgrade head
```

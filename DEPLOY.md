# Deploying

The app is one container plus one SQLite file. The file must live on a volume,
or every result is lost on the next deploy.

## Two secrets you must set

| Variable | What happens without it |
|---|---|
| `MINI_LEAGUE_PASSWORD` | The organizer screens stay closed. The leaderboard still works. |
| `MINI_LEAGUE_SECRET_KEY` | A new signing key is generated each start, so everyone is signed out on every restart. |

Generate a key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

There is one shared password for the whole league, which is what the design
calls for. Anyone who has it can record and delete results, so treat it as you
would a door key rather than a personal login.

## Fly.io

```bash
fly launch --no-deploy --copy-config
```

```bash
fly volumes create mini_league_data --size 1
```

```bash
fly secrets set MINI_LEAGUE_PASSWORD=... MINI_LEAGUE_SECRET_KEY=...
```

```bash
fly deploy
```

The included `fly.toml` caps the app at one machine on purpose. SQLite is a
single file, so two machines would race each other and lose games. It also
suspends when idle, which suits a league that plays once a week.

Migrations run at every start, so a deploy that adds a migration applies it
before serving.

## Any Docker host

```bash
docker build -t mini-league .
```

```bash
docker run -d --name mini-league -p 8080:8080 \
  -v mini_league_data:/data \
  -e MINI_LEAGUE_PASSWORD=your-password \
  -e MINI_LEAGUE_SECRET_KEY=your-generated-key \
  mini-league
```

## Analytics

Off unless you ask for it. Set a measurement id and the app loads Google
Analytics on every page; leave it unset and no third-party script is fetched and
no analytics cookie is set.

```bash
fly secrets set MINI_LEAGUE_GA_MEASUREMENT_ID=G-XXXXXXXXXX
```

A measurement id is not a secret, since it is visible in the page source of
every site that uses one. `fly secrets` is only the least fiddly way to set an
environment variable; putting it under `[env]` in `fly.toml` works as well and
keeps it in version control, which is arguably the more honest place for it.

Worth knowing before you turn it on. The leaderboard and player pages are
public, so Google will receive the URL and the page title of every page anyone
opens, and a player page's title is that player's name. If the league would
rather that did not leave the building, leave this unset.

## Backups

The whole league is one file at `/data/mini_league.db`. Copy it and you have
everything.

This app suspends when idle, and `fly ssh console` cannot reach a machine that
is asleep. Load the site first, or start it by hand:

```bash
fly machine start $(fly machines list -q)
```

Take the copy with SQLite's own backup, which is consistent even if someone
records a game while it runs, then pull it down:

```bash
fly ssh console -C "python -c \"import sqlite3; src = sqlite3.connect('file:/data/mini_league.db?mode=ro', uri=True); dst = sqlite3.connect('/tmp/backup.db'); src.backup(dst); dst.close()\""
```

```bash
fly ssh sftp get /tmp/backup.db ./backup.db
```

Python does the work because the image is `python:3.12-slim`, which carries the
SQLite library the app uses but not the `sqlite3` command line tool.

Pulling the live file straight off the volume is quicker and usually fine on a
league that plays once a week, but it copies a file that may be being written
to, so keep it for a look rather than for the backup you would restore from:

```bash
fly ssh sftp get /data/mini_league.db
```

Check a backup before trusting it. This prints the schema version and what is
in it, and says `ok` if the file is sound:

```bash
uv run python -c "import sqlite3; c = sqlite3.connect('file:backup.db?mode=ro', uri=True); q = lambda s: c.execute(s).fetchone()[0]; print(q('select version_num from alembic_version'), q('select count(*) from games where deleted_at is null'), 'games', q('select count(*) from players'), 'players', q('pragma integrity_check'))"
```

Games, teams and check-ins are the source of truth. Ratings are derived, so a
restored backup can always be rebuilt with the replay button on the settings
screen.

## Tuning after deployment

The rating parameters live in `src/mini_league/settings.py`, and each can be
overridden with an environment variable without touching code. For example:

```bash
fly secrets set MINI_LEAGUE_BETA=5.0
```

The settings screen shows what is currently in use next to the defaults.
Changing a rating parameter only affects past games once you press replay on
that screen.

Use `scripts/validate_ratings.py` before changing anything. It simulates leagues
where the right answer is known and reports whether a change actually helps.

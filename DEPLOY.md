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

## Backups

The whole league is one file at `/data/mini_league.db`. Copy it and you have
everything.

```bash
fly ssh console -C "sqlite3 /data/mini_league.db .dump" > backup.sql
```

Or pull the file directly:

```bash
fly ssh sftp get /data/mini_league.db
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

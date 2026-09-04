#!/bin/sh
# Bring the schema up to date, then hand over to the server.
#
# Migrations run on every start rather than at build time: the database lives on
# a mounted volume that does not exist when the image is built.
set -e

if [ -z "${MINI_LEAGUE_PASSWORD:-}" ]; then
  echo "WARNING: MINI_LEAGUE_PASSWORD is not set." >&2
  echo "         The leaderboard will work, but the organizer screens stay closed." >&2
fi

if [ -z "${MINI_LEAGUE_SECRET_KEY:-}" ]; then
  echo "WARNING: MINI_LEAGUE_SECRET_KEY is not set, so a new key is generated on" >&2
  echo "         each start and every sign-in ends at the next restart." >&2
fi

echo "Applying migrations to ${MINI_LEAGUE_DATABASE_URL:-the configured database}..."
alembic upgrade head

exec "$@"

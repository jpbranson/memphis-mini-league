# Single container: FastAPI plus a SQLite file on a mounted volume.
FROM python:3.12-slim AS base

# uv manages the environment here exactly as it does locally.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    # The volume mount point; override if you mount somewhere else.
    MINI_LEAGUE_DATABASE_URL="sqlite:////data/mini_league.db"

WORKDIR /app

# Dependencies first so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The database lives on a volume; without one, results vanish on redeploy.
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
# --forwarded-allow-ips is not optional here. uvicorn trusts X-Forwarded-* only
# from 127.0.0.1 by default, and the proxy terminating TLS in front of this
# reaches the container from somewhere else, so without it every request looks
# like plain http from inside. Nothing user-facing broke on that until the link
# preview tags, which are absolute: they would advertise http:// URLs for an
# https:// site, and a scraper handed a mixed-content image shows a blank card.
# Trusting any client is safe because nothing reaches this port except the
# proxy; if that ever stops being true, name the proxy instead of using *.
CMD ["uvicorn", "mini_league.web.app:app", "--factory", "--host", "0.0.0.0", \
     "--port", "8080", "--forwarded-allow-ips", "*"]

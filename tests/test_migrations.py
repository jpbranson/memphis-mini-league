"""The Alembic migration chain must produce exactly the schema the models declare."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from mini_league.db import make_engine
from mini_league.models import Base

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["sqlalchemy.url"] = url
    return cfg


def test_upgrade_head_matches_models_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    cfg = alembic_config(url)

    command.upgrade(cfg, "head")

    engine = make_engine(url)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()

    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    assert diff == [], f"migrations drift from models: {diff}"

    command.downgrade(cfg, "base")
    engine = make_engine(url)
    with engine.connect() as conn:
        remaining = set(inspect(conn).get_table_names())
    engine.dispose()
    assert remaining == {"alembic_version"}


def test_partial_unique_index_survives_migration(tmp_path):
    url = f"sqlite:///{(tmp_path / 'idx.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    with engine.connect() as conn:
        indexes = {i["name"]: i for i in inspect(conn).get_indexes("players")}
    engine.dispose()
    idx = indexes["ux_players_active_name"]
    assert idx["unique"]
    assert idx["column_names"] == ["name"]
    assert "active" in str(idx.get("dialect_options", {}).get("sqlite_where", ""))

"""The whole migration chain, walked once (#770).

Every migration test walks only its own step: up from the revision before
its migration and back down to it. The chain as a whole, every later
migration walking back over every earlier one and forward again, is proven
here once per run: up from an empty file to head (the suite serves that
from its cache), down to the migration floor, up again, and the schema is
the one a fresh head has.

The walk starts at the floor (#769): 2.29.0's head, the oldest revision
this build migrates a company file from. The migrations before it stay in
the repo and still build a fresh file, which is the walk up from empty;
the suite no longer walks back over them.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.services.migration_floor import MIGRATION_FLOOR

ROOT = Path(__file__).resolve().parents[1]


def _cfg(db: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def _schema(db: Path) -> dict:
    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        insp = sa.inspect(engine)
        return {
            table: {
                "columns": sorted(
                    (c["name"], str(c["type"]), bool(c["nullable"]))
                    for c in insp.get_columns(table)
                ),
                "indexes": sorted(
                    (ix["name"] or "", tuple(ix["column_names"]))
                    for ix in insp.get_indexes(table)
                ),
            }
            for table in sorted(insp.get_table_names())
        }
    finally:
        engine.dispose()


def _current(db: Path) -> str:
    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def test_the_chain_walks_back_to_the_migration_floor_and_up_again(
    tmp_path,
):
    db = tmp_path / "chain.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    older = [r.revision for r in script.walk_revisions(base="base", head="head")]
    assert MIGRATION_FLOOR in older
    command.upgrade(cfg, "head")
    fresh = _schema(db)
    assert len(fresh) > 100
    command.downgrade(cfg, MIGRATION_FLOOR)
    assert _current(db) == MIGRATION_FLOOR
    command.upgrade(cfg, "head")
    assert _current(db) == script.get_current_head()
    assert _schema(db) == fresh

"""The app refuses to start against a database behind the migration head
(issue #132).

Found by the macOS QA agent while gating 2.11.0. `_create_missing_tables()`
runs `create_all()` on every start. Point that at a database behind head and
it half-upgrades it: tables the new revision ADDS are created, tables it
ALTERS are untouched, and `alembic_version` does not move. Alembic can then
never run on it again, because the upgrade tries to create tables that
already exist.

The agent's own company file ended up carrying three 2.11 tables, missing
2.10's `accounts.bank_kind`, and still stamped at a 2.9-era revision. The
app refused to start, and the start that failed was not the start that
caused it.

Neither shipped path reaches this — `desktop_launcher.py` migrates first and
`docker-entrypoint.sh` runs `alembic upgrade head` at line 21 — but a
self-managed deployment that sets DATABASE_URL and treats migrations as a
separate operational step is on exactly that path.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def _cfg(db_path):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db_path).as_posix()
    return cfg


def _head():
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_cfg("/tmp/unused.db")).get_current_head()


def test_a_database_at_head_is_allowed(tmp_path, monkeypatch):
    from alembic import command
    import app.main as main

    db = tmp_path / "at_head.db"
    command.upgrade(_cfg(db), "head")

    monkeypatch.setattr(main, "engine", create_engine(f"sqlite:///{db}"))
    main._refuse_a_database_behind_head()  # must not raise


def test_a_fresh_database_is_allowed(tmp_path, monkeypatch):
    """No `alembic_version` at all means a NEW database — that is how a fresh
    company file and a fresh Docker volume both start, and `create_all()` is
    how they get built."""
    import app.main as main

    db = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    monkeypatch.setattr(main, "engine", engine)
    main._refuse_a_database_behind_head()  # must not raise


def test_a_database_behind_head_is_refused(tmp_path, monkeypatch):
    """The case that matters. Before this, the app started and silently made
    the database unmigratable."""
    from alembic import command
    import app.main as main

    db = tmp_path / "behind.db"
    command.upgrade(_cfg(db), "head")
    # Wind the stamp back to something older without touching the schema —
    # the same end state as a file that was created by an older build. The
    # migration floor (2.29.0's head): a file below it gets its own sentence
    # (#769, tests/test_migration_floor_769.py).
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'c4e5d6f7a8b9'"))

    monkeypatch.setattr(main, "engine", engine)
    with pytest.raises(RuntimeError) as e:
        main._refuse_a_database_behind_head()

    detail = str(e.value)
    assert "c4e5d6f7a8b9" in detail, "say which revision the database is at"
    assert _head() in detail, "say which revision the build expects"
    assert "alembic upgrade head" in detail, "say what to do about it"


def test_an_unreadable_database_is_reported_not_silently_passed(
    tmp_path, monkeypatch, caplog
):
    """The agent's own version of this guard had `except: return`, which let a
    file through BY FAILING TO READ IT — it was mid-copy at the time. A guard
    that cannot read the thing it guards must say so.

    It still lets the start proceed: refusing here would turn a transient
    database blip into a failure to boot, which is a worse trade than the
    narrow window this guard covers.
    """
    import app.main as main

    class _Exploding:
        def connect(self, *a, **k):
            raise OSError("disk went away")

        dialect = None

    monkeypatch.setattr(main, "engine", _Exploding())
    with caplog.at_level("ERROR"):
        main._refuse_a_database_behind_head()  # must not raise
    assert any(
        "alembic_version" in r.message for r in caplog.records
    ), "the guard failed to read the database and said nothing"


def test_the_shipped_entrypoints_migrate_before_serving():
    """The reason this is a guard and not a fix: both shipped paths already
    do the right thing, so the guard is for the deployment shapes we do not
    control."""
    launcher = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert 'command.upgrade(cfg, "head")' in launcher

    entry = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in entry

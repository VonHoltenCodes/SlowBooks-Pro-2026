"""Recovering a half-upgraded database (issue #132, second half).

The guard added in this release refuses to start against a database behind
head, which stops the damage happening again. It does nothing for a file it
has already happened to — and @skytech pointed out the distinction while
gating 2.12.0:

    `alembic upgrade head` recovers an *ordinary* old file and can never
    recover a *half-upgraded* one, because the tables the next revision
    creates already exist.

Measured before building this: a file at `e7f8a9b0c1d2` that a server touched
gains three empty tables, keeps its revision, and then

    alembic upgrade head
    -> OperationalError: table vendor_credits already exists

So the refusal was printing advice that fails for exactly the people who hit
it — the same shape as #139's delete error saying "deactivate it instead"
when nothing on the page could deactivate.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.services.migration_floor import MIGRATION_FLOOR

ROOT = Path(__file__).resolve().parents[1]

# The files these tests damage start at the migration floor (#769): a file
# below it is refused before any migration or repair runs on it.
OLD = MIGRATION_FLOOR


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def _head():
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_cfg("/tmp/x.db")).get_current_head()


def _half_upgraded(tmp_path, name="halfup.db", at=OLD):
    """A file at an older revision that a server has started against — the
    exact damage, produced the way it actually happens."""
    from alembic import command

    from app.database import Base
    import app.models  # noqa: F401  — registers every table

    db = tmp_path / name
    command.upgrade(_cfg(db), at)
    engine = create_engine(f"sqlite:///{db}")
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)  # what a server start does
    added = set(inspect(engine).get_table_names()) - before
    engine.dispose()
    assert added, "the fixture did not reproduce the damage"
    return db, added


def test_the_damage_really_blocks_an_ordinary_upgrade(tmp_path):
    """The premise. If this ever stops being true the repair is unnecessary."""
    from alembic import command

    db, _ = _half_upgraded(tmp_path)
    with pytest.raises(Exception) as e:
        command.upgrade(_cfg(db), "head")
    assert "already exists" in str(e.value)


def test_repair_drops_the_empty_tables_and_reaches_head(tmp_path):
    from app.services.schema_repair import repair

    db, added = _half_upgraded(tmp_path)
    result = repair(f"sqlite:///{db}")

    assert result.ok, result.message
    assert result.now_at == _head()
    assert set(result.dropped) <= added
    # And the file is genuinely usable afterwards, not merely stamped.
    engine = create_engine(f"sqlite:///{db}")
    tables = set(inspect(engine).get_table_names())
    assert added <= tables, "the dropped tables were not recreated by the upgrade"
    cols = {c["name"] for c in inspect(engine).get_columns("accounts")}
    assert "bank_kind" in cols, "an ALTER that create_all could not do is still missing"
    engine.dispose()


def test_repair_refuses_to_drop_a_table_with_rows(tmp_path):
    """The whole safety argument. `create_all()` only ever creates, so
    anything it left behind is empty; a table with data in it was made by
    something else and dropping it loses records."""
    from app.services.schema_repair import repair

    db, added = _half_upgraded(tmp_path)
    # Pick the table the upgrade hits first and give it a row that satisfies
    # its NOT NULL columns, so the refusal is about data rather than about a
    # constraint.
    victim = "trade_packages"  # the first table the floor's upgrade creates
    assert victim in added
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "trade_packages" '
                "(name, is_active, sort_order) VALUES ('Framing', 1, 0)"
            )
        )
    engine.dispose()

    result = repair(f"sqlite:///{db}")
    assert not result.ok
    assert "row(s)" in result.message and victim in result.message
    assert "needs a person" in result.message
    # And it left the table alone.
    engine = create_engine(f"sqlite:///{db}")
    assert inspect(engine).has_table(victim)
    engine.dispose()


def test_repair_is_a_no_op_on_a_healthy_file(tmp_path):
    from alembic import command

    from app.services.schema_repair import repair

    db = tmp_path / "healthy.db"
    command.upgrade(_cfg(db), "head")
    result = repair(f"sqlite:///{db}")
    assert result.ok and result.dropped == []


def test_repair_also_upgrades_an_ordinary_old_file(tmp_path):
    """It replaces `alembic upgrade head` rather than sitting beside it, so an
    operator does not have to work out which situation they are in."""
    from alembic import command

    from app.services.schema_repair import repair

    db = tmp_path / "old.db"
    command.upgrade(_cfg(db), OLD)
    result = repair(f"sqlite:///{db}")
    assert result.ok and result.now_at == _head() and result.dropped == []


def test_dry_run_changes_nothing(tmp_path):
    from app.services.schema_repair import repair

    db, added = _half_upgraded(tmp_path)
    before = _rev(db)
    result = repair(f"sqlite:///{db}", dry_run=True)
    assert "dry run" in result.message
    assert _rev(db) == before
    engine = create_engine(f"sqlite:///{db}")
    assert added <= set(inspect(engine).get_table_names())
    engine.dispose()


def _rev(db):
    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    finally:
        engine.dispose()


def test_the_startup_refusal_names_the_repair_for_a_half_upgraded_file(
    tmp_path, monkeypatch
):
    """The message is the only way an operator finds out which situation they
    are in, so it has to differ."""
    import app.main as main

    db, _ = _half_upgraded(tmp_path)
    monkeypatch.setattr(main, "engine", create_engine(f"sqlite:///{db}"))
    with pytest.raises(RuntimeError) as e:
        main._refuse_a_database_behind_head()
    assert "repair-schema.py" in str(e.value)
    assert "will fail" in str(e.value)


def test_the_startup_refusal_still_says_upgrade_for_an_ordinary_old_file(
    tmp_path, monkeypatch
):
    from alembic import command

    import app.main as main

    db = tmp_path / "ordinary.db"
    command.upgrade(_cfg(db), OLD)
    monkeypatch.setattr(main, "engine", create_engine(f"sqlite:///{db}"))
    with pytest.raises(RuntimeError) as e:
        main._refuse_a_database_behind_head()
    detail = str(e.value)
    assert "alembic upgrade head" in detail
    assert "repair-schema.py" not in detail


# ── #144: the message must name a path that exists ───────────────────────


def test_the_refusal_names_a_command_that_actually_runs(tmp_path, monkeypatch):
    """Take the command OUT of the refusal message and execute it.

    @macbase1's framing, and it is the reason this is written this way:

        it does not ask "is repair-schema.py in the bundle". It takes the
        path out of the refusal message, stats it, and then executes it — so
        any future message naming any path is measured the same way.

    2.12.1 shipped the script and named it, and both agents found nothing on
    the machine could run it: `_internal/app/` holds only static and
    templates, and on Windows the printed `python3` is the Store alias stub.
    My test asserted the named path **existed** — which is exactly the
    assertion that passes while the instruction still fails. Fourth
    appearance of that class, inside the fix meant to close it.
    """
    import re
    import subprocess
    import sys

    import app.main as main

    db, _ = _half_upgraded(tmp_path, name="from_message.db")
    monkeypatch.setattr(main, "engine", create_engine(f"sqlite:///{db}"))
    with pytest.raises(RuntimeError) as e:
        main._refuse_a_database_behind_head()
    message = str(e.value)

    # The line the operator is told to type.
    line = [ln.strip() for ln in message.splitlines() if "--database-url" in ln]
    assert line, f"the refusal names no command:\n{message}"
    command = line[0]

    m = re.match(r"python3?\s+(\S.*?)\s+--database-url", command)
    assert m, f"not a runnable python invocation on a checkout: {command!r}"
    script = Path(m.group(1))
    assert script.exists(), f"the refusal names {script}, which is not there"

    # And it works when run exactly as instructed.
    out = subprocess.run(
        [sys.executable, str(script), "--database-url", f"sqlite:///{db}"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert _rev(db) == _head(), "the command ran and did not repair the file"


def test_a_frozen_build_is_told_to_use_the_launcher(monkeypatch):
    """There is no interpreter inside a bundle and `app` is not importable
    from disk, so a frozen install is given the launcher's own flag instead of
    a python invocation that cannot work."""
    import sys

    from app.services import schema_repair

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Apps/SlowBooksPro.exe", raising=False)
    cmd = schema_repair.repair_command()
    assert cmd == "SlowBooksPro.exe --_repair-schema"
    assert "python" not in cmd


def test_the_launcher_exposes_that_flag():
    """The other half: the flag has to exist, and be handled before argparse
    like `--_serve`, or the message names something the binary rejects."""
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert '"--_repair-schema" in sys.argv' in src
    assert "def _repair_schema(" in src
    # Before argparse, same as --_serve.
    assert src.index('"--_repair-schema" in sys.argv') < src.index(
        "parser = argparse.ArgumentParser"
    )


def test_the_repair_is_linear_in_blocking_tables(tmp_path):
    """@skytech measured the old loop at 2**N - 1 drops, not N: every retry
    re-ran the migration from the start and SQLite, whose DDL is not
    transactional, recreated what the last round dropped. Three tables cost
    seven rounds of twenty-five; five would need thirty-one and give up.

    The blockers are cleared in one pass now, so N tables cost N drops."""
    from sqlalchemy import inspect as _inspect

    from app.database import Base
    from app.services.schema_repair import (
        repair,
        tables_pending_revisions_would_create,
    )

    pending = sorted(
        tables_pending_revisions_would_create(
            "sqlite:///" + str(tmp_path / "probe.db"), OLD
        )
    )
    assert len(pending) >= 2, "need a multi-table revision to measure this"

    from alembic import command

    # 1, 2 and 3 blockers tell one pass (n drops) from the old retry loop
    # (2**n - 1: 1, 3, 7), and the full count checks the whole revision. Each
    # n runs the chain to OLD again, so every n up to the full count
    # grew with each new table until the Windows job's time limit cut it off.
    for n in sorted({1, 2, min(3, len(pending)), len(pending)}):
        db = tmp_path / f"linear{n}.db"
        command.upgrade(_cfg(db), OLD)
        engine = create_engine(f"sqlite:///{db}")
        for name in pending[:n]:
            Base.metadata.tables[name].create(bind=engine)
        assert len(set(_inspect(engine).get_table_names()) & set(pending)) == n
        engine.dispose()

        result = repair(f"sqlite:///{db}")
        assert result.ok, result.message
        assert len(result.dropped) == n, (
            f"{n} blocking tables cost {len(result.dropped)} drops; the "
            f"one-pass clearing has regressed to the retry loop"
        )


def test_both_specs_ship_the_repair_script():
    """The other half: it has to be IN the bundle for the resolved path to
    find it. Guarded in the spec rather than trusted, because the only way
    to notice it was missing was to unzip a released artifact."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for spec in (
        "packaging/windows/SlowBooksPro.spec",
        "packaging/macos/SlowBooksPro-mac.spec",
    ):
        src = (root / spec).read_text(encoding="utf-8")
        assert "repair-schema.py" in src, f"{spec} does not ship the repair script"


def test_nothing_is_dropped_when_the_migration_cannot_run(tmp_path, monkeypatch):
    """@skytech, 2.12.1 gate: on a frozen build the migration import died on
    psycopg2 **after the drop phase had completed**, leaving the file worse
    than it started — nothing left to drop, so the next start reported it as
    an ordinary old database and offered a command that still could not run.
    A loop, entered by following the instructions.

    A repair whose first act is irreversible and whose second act may fail
    for an unrelated reason is not a repair.
    """
    from sqlalchemy import inspect as _inspect

    from app.services import schema_repair

    db, added = _half_upgraded(tmp_path, name="cannot_run.db")
    before = set(_inspect(create_engine(f"sqlite:///{db}")).get_table_names())

    # Stand in for the import that failed on the frozen bundle.
    real = schema_repair._alembic_cfg

    def boom(url):
        raise ModuleNotFoundError("No module named 'psycopg2'")

    monkeypatch.setattr(schema_repair, "_alembic_cfg", boom)
    result = schema_repair.repair(f"sqlite:///{db}")
    monkeypatch.setattr(schema_repair, "_alembic_cfg", real)

    assert not result.ok
    assert result.dropped == [], "it dropped tables it could not then replace"
    assert "nothing was changed" in result.message
    assert "psycopg2" in result.message, "say what actually stopped it"

    after = set(_inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert after == before, "the file was modified by a repair that failed"
    assert _rev(db) != _head()


def test_the_repair_entry_point_sets_the_database_url_first():
    """The cause, guarded at the source.

    `app/config.py` reads `BASE_DIR/.env`, which is inside the bundle when
    frozen rather than in the user's data directory, so `DATABASE_URL` was
    unset and fell back to the PostgreSQL default — and `app/database.py`
    builds its engine at module scope, which `migrations/env.py` imports. The
    import died before the migration looked at the URL we passed.

    So the assignment has to come before the import, and a later edit that
    reorders them would reintroduce a defect no test on this machine can
    reach, because a checkout always has a working `DATABASE_URL`.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "desktop_launcher.py").read_text(
        encoding="utf-8"
    )
    body = src[src.index("def _repair_schema(") :]
    body = body[: body.index("\ndef ")]

    assert 'os.environ["DATABASE_URL"] = url' in body
    assert body.index('os.environ["DATABASE_URL"] = url') < body.index(
        "from app.services.schema_repair import repair"
    ), "the import happens before the URL is set; that is the original defect"

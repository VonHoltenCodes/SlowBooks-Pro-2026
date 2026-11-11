"""a4c8e2f6b1d3 — estimates priced by a markup on each line (#774): the
estimate_lines.markup_pct column. The estimate_style setting defaults to the
commercial bid every construction company has today, so the upgrade writes no
style. Exercised on a fresh SQLite at the previous revision, upgraded to this
one and walked back."""

import shutil
import sqlite3
from pathlib import Path

import pytest

PREV = "f3b9d2e6a1c7"
REVISION = "a4c8e2f6b1d3"

_TEMPLATE = {}


def _cfg(db):
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


@pytest.fixture(scope="module", autouse=True)
def _books_at_the_revision_before(tmp_path_factory):
    from alembic import command

    path = tmp_path_factory.mktemp("before") / "before.db"
    command.upgrade(_cfg(path), PREV)
    _TEMPLATE["db"] = path
    yield
    _TEMPLATE.clear()


def _old_books(tmp_path, settings=(), estimate=False):
    """A copy of the books at PREV with these settings rows and, when asked,
    one estimate."""
    db = tmp_path / "old.db"
    shutil.copyfile(_TEMPLATE["db"], db)
    con = sqlite3.connect(db)
    for key, value in settings:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    if estimate:
        con.execute(
            "INSERT INTO estimates (estimate_number, customer_id, status, date, "
            "subtotal, tax_rate, tax_amount, total, gc_pct, insurance_pct, "
            "fee_pct, bond_pct, markup_allowances, show_markup_rows) VALUES "
            "('E-1001', 1, 'PENDING', '2026-10-01', 100, 0, 0, 100, 0, 0, 0, 0, 0, 1)"
        )
    con.commit()
    con.close()
    return db


def _style(db):
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'estimate_style'"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _columns(db):
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(estimate_lines)")}
    finally:
        con.close()


def _upgrade(db):
    from alembic import command

    command.upgrade(_cfg(db), REVISION)


def test_upgrade_adds_the_column_and_writes_no_style(tmp_path):
    db = _old_books(tmp_path, [("company_type", "construction")], estimate=True)
    assert "markup_pct" not in _columns(db)
    _upgrade(db)
    assert "markup_pct" in _columns(db)
    assert _style(db) is None


def test_downgrade_drops_the_column_and_a_saved_style(tmp_path):
    from alembic import command

    db = _old_books(tmp_path, estimate=True)
    _upgrade(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO settings (key, value) VALUES ('estimate_style', 'line_markup')"
    )
    con.commit()
    con.close()
    command.downgrade(_cfg(db), PREV)
    assert "markup_pct" not in _columns(db)
    assert _style(db) is None

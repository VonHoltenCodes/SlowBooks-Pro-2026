"""a4c8e2f6b1d3 — estimates priced by a markup on each line (#774): the
estimate_lines.markup_pct column, and estimate_style = commercial_bid written
for a construction company that already has estimates, so its screen does not
change under it. Exercised on a fresh SQLite migrated to the previous head,
populated the way old installs are, then upgraded."""

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


def test_upgrade_adds_the_column_and_keeps_a_bidding_company_on_the_bid(tmp_path):
    db = _old_books(tmp_path, [("company_type", "construction")], estimate=True)
    assert "markup_pct" not in _columns(db)
    _upgrade(db)
    assert "markup_pct" in _columns(db)
    assert _style(db) == "commercial_bid"


@pytest.mark.parametrize(
    "settings, estimate",
    [
        # a construction company with no estimates gets the new default
        ([("company_type", "construction")], False),
        # a business company's estimates are not construction bids
        ([("company_type", "business")], True),
        ([], True),
    ],
    ids=["construction-no-estimates", "business", "no-company-type"],
)
def test_upgrade_leaves_other_companies_alone(tmp_path, settings, estimate):
    db = _old_books(tmp_path, settings, estimate)
    _upgrade(db)
    assert _style(db) is None


def test_upgrade_keeps_a_style_already_saved(tmp_path):
    db = _old_books(
        tmp_path,
        [("company_type", "construction"), ("estimate_style", "line_markup")],
        estimate=True,
    )
    _upgrade(db)
    assert _style(db) == "line_markup"


def test_downgrade_drops_the_column(tmp_path):
    from alembic import command

    db = _old_books(tmp_path, [("company_type", "construction")], estimate=True)
    _upgrade(db)
    command.downgrade(_cfg(db), PREV)
    assert "markup_pct" not in _columns(db)
    assert _style(db) is None

"""Subcontractor vendor fields (#431): vendor_type plus the license and
certificate-of-insurance columns round-trip through the API, and the
migration adds and drops them cleanly."""

import sqlite3
from pathlib import Path

PREV = "4ac01efbd72a"
HEAD = "b7d3f1e2a9c4"
COLS = (
    "vendor_type",
    "license_number",
    "coi_carrier",
    "coi_policy_number",
    "coi_expires",
)


def _cfg(db):
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


# ---------------------------------------------------------------------------
# API round trip
# ---------------------------------------------------------------------------


def test_sub_fields_round_trip(client):
    v = client.post(
        "/api/vendors",
        json={
            "name": "Ace Electric",
            "vendor_type": "sub",
            "license_number": "EC13001234",
            "coi_carrier": "Travelers",
            "coi_policy_number": "POL-99812",
            "coi_expires": "2027-03-31",
        },
    )
    assert v.status_code == 201, v.text
    body = v.json()
    assert body["vendor_type"] == "sub"
    assert body["license_number"] == "EC13001234"
    assert body["coi_carrier"] == "Travelers"
    assert body["coi_policy_number"] == "POL-99812"
    assert body["coi_expires"] == "2027-03-31"

    got = client.get(f"/api/vendors/{body['id']}").json()
    assert got["vendor_type"] == "sub"
    assert got["coi_expires"] == "2027-03-31"


def test_ordinary_vendor_leaves_sub_fields_blank(client):
    v = client.post("/api/vendors", json={"name": "Paper Co"})
    assert v.status_code == 201, v.text
    body = v.json()
    for col in COLS:
        assert body[col] is None, col


# ---------------------------------------------------------------------------
# Migration up / down
# ---------------------------------------------------------------------------


def test_migration_adds_and_drops_the_columns(tmp_path):
    from alembic import command

    db = tmp_path / "vendors.db"
    cfg = _cfg(db)
    command.upgrade(cfg, PREV)

    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("INSERT INTO vendors (name, is_active, balance) VALUES ('Ace', 1, 0)")
    con.commit()

    def columns():
        return {r[1] for r in cur.execute("PRAGMA table_info(vendors)")}

    assert not (set(COLS) & columns())

    command.upgrade(cfg, HEAD)
    assert set(COLS) <= columns()
    # the row survived and the new columns are nullable (blank on an old row)
    cur.execute(
        "UPDATE vendors SET vendor_type='sub', coi_expires='2027-03-31' WHERE name='Ace'"
    )
    con.commit()
    row = dict(
        zip(
            [c[1] for c in cur.execute("PRAGMA table_info(vendors)")],
            cur.execute("SELECT * FROM vendors WHERE name='Ace'").fetchone(),
        )
    )
    assert row["vendor_type"] == "sub"

    command.downgrade(cfg, PREV)
    assert not (set(COLS) & columns())
    assert cur.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 1
    con.close()

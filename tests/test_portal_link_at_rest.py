"""An employee's self-service portal link is never kept as issued (2.18.0).

employees.portal_token held every link as issued, so a copy of the company
file or a backup handed over every employee's working link, and a link signs
in as that employee, the bank account their pay goes to included. The portal
now finds a link by its SHA-256; the copy an administrator can show again is
encrypted with the payroll key, which is kept outside the database."""

import hashlib
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.services.encryption import decrypt

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "c5e1f7a9b3d2"
REVISION = "e2b7c4d9a1f3"  # portal links kept as a digest and an encrypted copy
# a link as an earlier release stored it (token_urlsafe(24): 32 characters)
OLD_LINK = "Qm9va3NLZWVwVGhpc0xpbmtGb3JMZW5h"


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def _employee(client) -> dict:
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Lena",
            "last_name": "Ortiz",
            "pay_type": "hourly",
            "pay_rate": 20,
            "work_state": "OR",
            "residence_state": "OR",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _stored(db_session, emp_id: int):
    return (
        db_session.execute(
            sa.text("SELECT * FROM employees WHERE id = :i"), {"i": emp_id}
        )
        .mappings()
        .one()
    )


def test_a_new_link_is_not_kept_as_issued(client, db_session):
    emp = _employee(client)
    token = client.post(f"/api/employees/{emp['id']}/portal-token").json()[
        "portal_token"
    ]
    row = _stored(db_session, emp["id"])
    assert row["portal_token"] is None
    assert row["portal_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert decrypt(row["portal_token_enc"]) == token
    assert not any(token in str(v) for v in row.values() if v is not None)


def test_the_link_works_and_can_be_shown_again(client, db_session):
    emp = _employee(client)
    first = client.post(f"/api/employees/{emp['id']}/portal-token").json()
    again = client.get(f"/api/employees/{emp['id']}/portal-token").json()
    assert again["portal_url"] == first["portal_url"]
    assert first["portal_url"].endswith("/portal/" + first["portal_token"])
    r = client.get(f"/portal/{first['portal_token']}", follow_redirects=False)
    assert r.status_code == 303
    assert "slowbooks_portal=" in r.headers.get("set-cookie", "")
    rotated = client.post(f"/api/employees/{emp['id']}/portal-token").json()
    assert rotated["portal_token"] != first["portal_token"]
    old = client.get(f"/portal/{first['portal_token']}", follow_redirects=False)
    assert old.status_code == 404
    new = client.get(f"/portal/{rotated['portal_token']}", follow_redirects=False)
    assert new.status_code == 303


def test_a_copy_this_install_cannot_open_still_lets_the_employee_in(client, db_session):
    emp = _employee(client)
    token = client.post(f"/api/employees/{emp['id']}/portal-token").json()[
        "portal_token"
    ]
    db_session.execute(
        sa.text(
            "UPDATE employees SET portal_token_enc = 'v1:not-a-fernet-token' WHERE id = :i"
        ),
        {"i": emp["id"]},
    )
    db_session.commit()
    shown = client.get(f"/api/employees/{emp['id']}/portal-token").json()
    assert shown["portal_token"] is None and shown["portal_url"] is None
    assert "Rotate Token" in shown["note"]
    # not re-minted: the link the employee has still works, and using it
    # keeps a copy again under this install's key
    assert client.get(f"/portal/{token}", follow_redirects=False).status_code == 303
    healed = client.get(f"/api/employees/{emp['id']}/portal-token").json()
    assert healed["portal_token"] == token


def test_the_upgrade_moves_each_link_over_and_back(tmp_path, monkeypatch):
    from app import config

    # The migration reads the secret from the environment, as the app did at
    # start-up; alembic's env.py would otherwise load a checkout's own .env.
    monkeypatch.setenv("PAYROLL_ENCRYPTION_SECRET", config.PAYROLL_ENCRYPTION_SECRET)
    url = "sqlite:///" + (tmp_path / "books.db").as_posix()
    command.upgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO employees (first_name, last_name, portal_token) "
                    "VALUES ('Marisol', 'Vance', :t), ('Jonah', 'Vance', NULL)"
                ),
                {"t": OLD_LINK},
            )
    finally:
        engine.dispose()

    command.upgrade(_cfg(url), REVISION)  # its own step, not the chain (#770)
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            marisol, jonah = conn.execute(
                sa.text(
                    "SELECT portal_token, portal_token_hash, portal_token_enc "
                    "FROM employees ORDER BY id"
                )
            ).all()
    finally:
        engine.dispose()
    assert marisol.portal_token is None
    assert marisol.portal_token_hash == hashlib.sha256(OLD_LINK.encode()).hexdigest()
    assert decrypt(marisol.portal_token_enc) == OLD_LINK
    assert tuple(jonah) == (None, None, None)

    command.downgrade(_cfg(url), BEFORE)
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            links = (
                conn.execute(sa.text("SELECT portal_token FROM employees ORDER BY id"))
                .scalars()
                .all()
            )
    finally:
        engine.dispose()
    assert links == [OLD_LINK, None]


def test_the_employee_page_says_why_a_link_cannot_be_shown():
    js = (ROOT / "app/static/js/employees.js").read_text(encoding="utf-8")
    assert "if (!url) {" in js
    assert "escapeHtml(token.note ||" in js


def test_migrations_read_the_env_file_the_app_reads():
    # A migration that encrypts must use the payroll secret the server uses:
    # env.py read the checkout's own .env even when the launcher (or a
    # restore, the suite, the QA harness) had named another in
    # SLOWBOOKS_ENV_FILE, which app/config.py reads.
    env_py = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert 'load_dotenv(os.environ["SLOWBOOKS_ENV_FILE"], override=False)' in env_py

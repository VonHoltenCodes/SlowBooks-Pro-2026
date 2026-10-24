"""An API token's "Can make backups" permission, and revocation that sticks
(#434, finishing #443).

A token is never an administrator. A program that takes a backup before it
writes (an import tool's safety net) gets the one permission it needs: an
administrator ticks "Can make backups" on the token, and it may make, list
and download this company's backups on top of its role. Restoring one
replaces the whole company and stays with a signed-in administrator; no
token restores, whatever its role or permission. A revoked token stays
revoked, and is told so.
"""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api_tokens import ApiToken
from app.models.audit import AuditLog
from app.services import backup_service


def _mint(client, label, role="bookkeeper", **extra):
    r = client.post("/api/tokens", json={"label": label, "role": role, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(secret):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {secret}"
    return c


class _KeepTheTestConnection:
    """A restore disposes the app's engine; the suite's must stay open."""

    def dispose(self):
        pass


@pytest.fixture
def books(tmp_path, monkeypatch, client):
    import app.database as db_module

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    live = tmp_path / "companies" / "harbor-light-bakery.db"
    live.parent.mkdir()
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        conn.execute(
            "INSERT INTO settings VALUES ('company_name', 'Harbor Light Bakery')"
        )
        conn.commit()
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    r = client.put("/api/settings", json={"company_name": "Harbor Light Bakery"})
    assert r.status_code == 200, r.text
    return SimpleNamespace(live=live, backups=backups)


def test_a_token_allowed_backups_makes_lists_and_downloads_one(
    client, books, db_session
):
    created = _mint(client, "qbd-import", allow_backups=True)
    assert created["allow_backups"] is True
    api = _bearer(created["token"])

    made = api.post("/api/backups", json={"notes": "before the import"})
    assert made.status_code == 200, made.text
    name = made.json()["filename"]
    assert name in [b["filename"] for b in api.get("/api/backups").json()]

    got = api.get(f"/api/backups/download/{name}")
    assert got.status_code == 200, got.text
    assert got.content.startswith(b"SQLite format 3")

    # the audit log names the program: the backup made, and the copy taken
    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "backups")
        .order_by(AuditLog.id)
        .all()
    )
    assert [(r.action, r.username) for r in rows] == [
        ("INSERT", "token:qbd-import"),
        ("EXPORT", "token:qbd-import"),
    ]


def test_without_the_permission_a_token_makes_and_downloads_none(client, books):
    made = client.post("/api/backups", json={})  # the administrator's
    assert made.status_code == 200, made.text
    name = made.json()["filename"]
    for role in ("bookkeeper", "accountant", "readonly"):
        api = _bearer(_mint(client, f"no-backups-{role}", role)["token"])
        assert api.post("/api/backups", json={}).status_code == 403, role
        r = api.get(f"/api/backups/download/{name}")
        assert r.status_code == 403, role
        assert not r.content.startswith(b"SQLite format 3"), role


def test_the_permission_reaches_backups_and_nothing_else(client, books):
    api = _bearer(
        _mint(client, "reader-backups", "readonly", allow_backups=True)["token"]
    )
    assert api.post("/api/backups", json={}).status_code == 200
    # still read-only everywhere else, and still no settings or payroll
    assert api.post("/api/customers", json={"name": "Nope"}).status_code == 403
    assert api.put("/api/settings", json={"invoice_prefix": "X-"}).status_code == 403
    assert api.get("/api/payroll").status_code == 403


def test_no_token_restores_a_backup(client, books, legacy_admin_token):
    made = client.post("/api/backups", json={})
    name = made.json()["filename"]
    keeper = _bearer(_mint(client, "keeper-backups", allow_backups=True)["token"])
    old_admin = _bearer(legacy_admin_token())
    for api in (keeper, old_admin):
        r = api.post("/api/backups/restore", json={"filename": name})
        assert r.status_code == 403, r.text
        assert "cannot restore a backup" in r.json()["detail"]
    # nothing was restored, and no safety backup was taken on the way
    assert [b["filename"] for b in client.get("/api/backups").json()] == [name]


def test_only_an_administrator_sets_the_permission(client, db_session):
    from app.models.users import ROLE_BOOKKEEPER, User
    from app.services import auth as auth_service

    plain = _mint(client, "plain")
    assert plain["allow_backups"] is False
    # a token, even one allowed backups, cannot give itself or another more
    allowed = _mint(client, "allowed", allow_backups=True)
    api = _bearer(allowed["token"])
    assert (
        api.put(f"/api/tokens/{plain['id']}", json={"allow_backups": True}).status_code
        == 403
    )
    # the administrator turns it on and off
    on = client.put(f"/api/tokens/{plain['id']}", json={"allow_backups": True})
    assert on.status_code == 200 and on.json()["allow_backups"] is True
    listed = {t["label"]: t["allow_backups"] for t in client.get("/api/tokens").json()}
    assert listed == {"plain": True, "allowed": True}
    off = client.put(f"/api/tokens/{plain['id']}", json={"allow_backups": False})
    assert off.json()["allow_backups"] is False

    # a bookkeeper's sign-in cannot
    db_session.add(
        User(
            username="keeper",
            display_name="Keeper",
            password_hash=auth_service.hash_password("keeper-password-1"),
            role=ROLE_BOOKKEEPER,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"username": "keeper", "password": "keeper-password-1"}
    )
    r = client.post(
        "/api/tokens",
        json={"label": "sneak", "role": "bookkeeper", "allow_backups": True},
    )
    assert r.status_code == 403
    r = client.put(f"/api/tokens/{plain['id']}", json={"allow_backups": True})
    assert r.status_code == 403


def test_a_revoked_token_stays_revoked(client, db_session, legacy_admin_token):
    created = _mint(client, "leaked-bot")
    api = _bearer(created["token"])
    assert api.get("/api/customers").status_code == 200
    assert (
        client.put(f"/api/tokens/{created['id']}", json={"is_active": False}).json()[
            "is_active"
        ]
        is False
    )
    back = client.put(f"/api/tokens/{created['id']}", json={"is_active": True})
    assert back.status_code == 409, back.text
    assert "stays revoked" in back.json()["detail"]
    r = api.get("/api/customers")
    assert r.status_code == 401
    assert r.json()["detail"] == (
        'The API token "leaked-bot" was revoked. '
        "Ask an administrator for a new one under Settings, API Tokens."
    )

    # an admin token made before #434 works until revoked, then is done too
    secret = legacy_admin_token("old-admin")
    old = _bearer(secret)
    assert old.get("/api/settings").status_code == 200
    row = db_session.query(ApiToken).filter_by(label="old-admin").one()
    client.put(f"/api/tokens/{row.id}", json={"is_active": False})
    assert (
        client.put(f"/api/tokens/{row.id}", json={"is_active": True}).status_code == 409
    )
    assert old.get("/api/settings").status_code == 401

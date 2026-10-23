"""Scoped API tokens (v2.5.1): issuance, bearer auth, role enforcement,
no-self-escalation, revocation, and audit attribution."""

from fastapi.testclient import TestClient

from app.main import app
from app.models.api_tokens import ApiToken
from app.models.audit import AuditLog


def _mint(client, label="agent", role="readonly"):
    r = client.post("/api/tokens", json={"label": label, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(token):
    """Fresh cookie-less client so the Bearer path (not the session) auths."""
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


# ---------------------------------------------------------------------------
# Issuance + management (admin sessions only)
# ---------------------------------------------------------------------------


def test_create_returns_secret_once(client, db_session):
    created = _mint(client, "claude-code", "readonly")
    assert created["token"].startswith("sbp_")
    assert created["token_hint"] == created["token"][:10]

    listed = client.get("/api/tokens").json()
    assert len(listed) == 1
    assert "token" not in listed[0]  # never again
    row = db_session.query(ApiToken).one()
    assert created["token"] not in (row.token_hash, row.token_hint)  # hashed at rest


def test_validation_and_dupes(client):
    r = client.post("/api/tokens", json={"label": "x1", "role": "boss"})
    assert r.status_code == 400
    _mint(client, "dupe", "readonly")
    r = client.post("/api/tokens", json={"label": "dupe", "role": "readonly"})
    assert r.status_code == 409


def test_non_admin_sessions_cannot_manage(client, db_session):
    from app.models.users import ROLE_BOOKKEEPER, User
    from app.services import auth as auth_service

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
    assert client.get("/api/tokens").status_code == 403
    assert (
        client.post("/api/tokens", json={"label": "sneak", "role": "admin"}).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Bearer authentication + role enforcement
# ---------------------------------------------------------------------------


def test_readonly_token_reads_but_never_writes(client):
    tok = _mint(client, "reporter", "readonly")["token"]
    api = _bearer(tok)
    assert api.get("/api/customers").status_code == 200
    assert api.post("/api/customers", json={"name": "Nope"}).status_code == 403


def test_bookkeeper_token_daily_writes_only(client):
    tok = _mint(client, "keeper-bot", "bookkeeper")["token"]
    api = _bearer(tok)
    assert api.post("/api/customers", json={"name": "Bot Corp"}).status_code in (
        200,
        201,
    )
    assert api.put("/api/settings", json={"invoice_prefix": "T-"}).status_code == 403


def test_an_admin_token_is_refused(client):
    """A token is never an administrator (#434): roles are its scope, and
    the one that reaches settings and identities stays with a person."""
    r = client.post("/api/tokens", json={"label": "root-bot", "role": "admin"})
    assert r.status_code == 400, r.text
    assert "cannot be an administrator" in r.json()["detail"]
    assert _mint(client, "keeper-bot", "bookkeeper")["role"] == "bookkeeper"


def test_tokens_cannot_manage_identities_even_as_admin(client, db_session):
    # An admin token made before 2.27.0 still works, and still cannot
    # manage identities; it is made in the database as one would be.
    from app.services.api_token_service import generate_token, hash_token

    tok = generate_token()
    db_session.add(
        ApiToken(
            label="root-bot",
            token_hash=hash_token(tok),
            token_hint=tok[:10],
            role="admin",
            is_active=True,
            created_by="test",
        )
    )
    db_session.commit()
    api = _bearer(tok)
    assert api.put("/api/settings", json={"invoice_prefix": "A-"}).status_code == 200
    # The no-self-escalation wall
    assert api.get("/api/users").status_code == 403
    assert api.get("/api/tokens").status_code == 403
    assert (
        api.post("/api/tokens", json={"label": "evil", "role": "admin"}).status_code
        == 403
    )


def test_bad_and_revoked_tokens_rejected(client):
    assert _bearer("sbp_totally-fake").get("/api/customers").status_code == 401
    created = _mint(client, "shortlived", "readonly")
    api = _bearer(created["token"])
    assert api.get("/api/customers").status_code == 200
    client.put(f"/api/tokens/{created['id']}", json={"is_active": False})
    r = api.get("/api/customers")
    assert r.status_code == 401
    # a revoked token is told so (#434); a secret that matches nothing is not
    assert "shortlived" in r.json()["detail"] and "revoked" in r.json()["detail"]
    fake = _bearer("sbp_totally-fake").get("/api/customers")
    assert fake.json()["detail"] == "Not authenticated"


def test_no_credentials_still_401(client):
    assert TestClient(app).get("/api/customers").status_code == 401


# ---------------------------------------------------------------------------
# Attribution + telemetry
# ---------------------------------------------------------------------------


def test_token_writes_attributed_in_audit(client, db_session):
    tok = _mint(client, "receipt-service", "bookkeeper")["token"]
    _bearer(tok).post("/api/customers", json={"name": "Attributed Bot Inc"})
    row = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "customers")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.username == "token:receipt-service"


def test_last_used_recorded(client, db_session):
    created = _mint(client, "tracked", "readonly")
    assert (
        db_session.query(ApiToken).filter_by(label="tracked").one().last_used_at is None
    )
    _bearer(created["token"]).get("/api/customers")
    db_session.expire_all()
    assert (
        db_session.query(ApiToken).filter_by(label="tracked").one().last_used_at
        is not None
    )

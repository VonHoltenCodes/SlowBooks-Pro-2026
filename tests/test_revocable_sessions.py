"""Revocable sessions: a signed-in session is re-checked against
the users table on every request, not trusted from the signed cookie alone.
Deactivating a user, changing their role, or changing/resetting their password
ends their existing sessions on the next request; the role is taken from the DB
row, not the cookie, so a demotion takes effect at once. Single-user desktop
and API-token paths are untouched.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.services import auth as auth_service
from tests import test_served_companies as served_tests

# The Server Edition harness (two companies on one server) for the switch
# tests, bound by attribute so the fixture name is not an unused import.
served = served_tests.served
A_PW = served_tests.A_PW
_new_company = served_tests._new_company
_sign_in = served_tests._sign_in

FIXTURE_PW = "test-password-123"
# Fixture passwords live in named constants, away from any username key,
# so the secret scanner's username/password pair detector stays quiet.
RENITA_PW = "renita-password-1"
RENITA_NEW_PW = "renita-password-2"
KEEPER_PW = "keeper-password-1"
PAT_PW = "pat-password-123"
RESET_PW = "brand-new-password-9"
OPERATOR_NEW_PW = "new-operator-pw-7"


def _mk_user(db, username, password, role=ROLE_BOOKKEEPER, active=True):
    u = User(
        username=username,
        display_name=username.title(),
        password_hash=auth_service.hash_password(password),
        role=role,
        is_active=active,
    )
    db.add(u)
    db.commit()
    return u


def _signed_in(username, password):
    """A fresh cookie jar signed in as this user — a session of its own, so
    one person's session can be ended while another's keeps working."""
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


# ---------------------------------------------------------------------------
# A security event ends the existing session on the next request
# ---------------------------------------------------------------------------


def test_deactivated_user_session_dies_next_request(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    rc = _signed_in("renita", RENITA_PW)
    assert rc.get("/api/customers").status_code == 200

    # The admin (the fixture `client`) deactivates renita.
    r = client.put(f"/api/users/{renita.id}", json={"is_active": False})
    assert r.status_code == 200, r.text

    # Her existing session is signed out on its very next request.
    gone = rc.get("/api/customers")
    assert gone.status_code == 401, gone.text


def test_role_change_session_dies_next_request(client, db_session):
    keeper = _mk_user(db_session, "keeper", KEEPER_PW, ROLE_BOOKKEEPER)
    kc = _signed_in("keeper", KEEPER_PW)
    assert kc.get("/api/customers").status_code == 200

    r = client.put(f"/api/users/{keeper.id}", json={"role": ROLE_READONLY})
    assert r.status_code == 200, r.text

    assert kc.get("/api/customers").status_code == 401


def test_password_reset_by_admin_session_dies_next_request(client, db_session):
    keeper = _mk_user(db_session, "keeper", KEEPER_PW, ROLE_BOOKKEEPER)
    kc = _signed_in("keeper", KEEPER_PW)
    assert kc.get("/api/customers").status_code == 200

    r = client.put(f"/api/users/{keeper.id}", json={"password": RESET_PW})
    assert r.status_code == 200, r.text

    assert kc.get("/api/customers").status_code == 401


# ---------------------------------------------------------------------------
# Other people's sessions are untouched
# ---------------------------------------------------------------------------


def test_other_users_sessions_unaffected(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    _mk_user(db_session, "pat", PAT_PW, ROLE_BOOKKEEPER)
    rc = _signed_in("renita", RENITA_PW)
    pc = _signed_in("pat", PAT_PW)

    client.put(f"/api/users/{renita.id}", json={"is_active": False})

    assert rc.get("/api/customers").status_code == 401
    # Pat was never touched; her session keeps working.
    assert pc.get("/api/customers").status_code == 200


# ---------------------------------------------------------------------------
# The role is read from the DB row, not the signed cookie
# ---------------------------------------------------------------------------


def test_role_taken_from_db_not_cookie(client, db_session):
    keeper = _mk_user(db_session, "keeper", KEEPER_PW, ROLE_BOOKKEEPER)
    kc = _signed_in("keeper", KEEPER_PW)
    # A bookkeeper may create a customer.
    assert kc.post("/api/customers", json={"name": "Daily Books LLC"}).status_code in (
        200,
        201,
    )

    # Demote the row directly WITHOUT bumping the version (so the session is
    # not revoked): the cookie still says "bookkeeper", the row says readonly.
    keeper.role = ROLE_READONLY
    db_session.commit()

    # The write is refused — the middleware trusts the row, not the cookie.
    assert kc.post("/api/customers", json={"name": "Blocked Corp"}).status_code == 403
    # And still a live session (read allowed), not a 401.
    assert kc.get("/api/customers").status_code == 200


# ---------------------------------------------------------------------------
# A user's own password change keeps their session but ends their others
# ---------------------------------------------------------------------------


def test_self_password_change_keeps_this_session_ends_others(client, db_session):
    _mk_user(db_session, "renita", RENITA_PW)
    here = _signed_in("renita", RENITA_PW)
    elsewhere = _signed_in("renita", RENITA_PW)

    r = here.post(
        "/api/auth/password",
        json={
            "current_password": RENITA_PW,
            "new_password": RENITA_NEW_PW,
        },
    )
    assert r.status_code == 200, r.text

    # The session that made the change keeps working...
    assert here.get("/api/customers").status_code == 200
    # ...but a copy of the old session, elsewhere, is signed out.
    assert elsewhere.get("/api/customers").status_code == 401


# ---------------------------------------------------------------------------
# Desktop single-user and API tokens are untouched
# ---------------------------------------------------------------------------


def test_single_user_desktop_flow_unaffected(client):
    # The fixture client is a single-user (password-only) install; it stays
    # signed in across requests and can change its own password without being
    # kicked out of the current window.
    assert client.get("/api/customers").status_code == 200
    r = client.post(
        "/api/auth/password",
        json={
            "current_password": FIXTURE_PW,
            "new_password": OPERATOR_NEW_PW,
        },
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/customers").status_code == 200


def test_api_token_unaffected_by_revocation(client):
    created = client.post(
        "/api/tokens", json={"label": "agent", "role": "readonly"}
    ).json()
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {created['token']}"
    # No session, no user row named — the token path never loads a user and
    # keeps reading.
    assert api.get("/api/customers").status_code == 200


# ---------------------------------------------------------------------------
# A session from before this feature (no stored version) is not signed out
# just for the upgrade — it adopts the row's current version.
# ---------------------------------------------------------------------------


def test_legacy_session_without_version_is_not_revoked(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    status, role, version = auth_service.check_session_user(
        {"user_id": renita.id}  # no session_version key, as an old cookie
    )
    assert status == "ok"
    assert role == ROLE_BOOKKEEPER
    assert version == renita.session_version


def test_check_session_user_states(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    # Matching version: ok
    assert auth_service.check_session_user(
        {"user_id": renita.id, "session_version": renita.session_version}
    ) == ("ok", ROLE_BOOKKEEPER, renita.session_version)
    # Stale version: revoked
    assert (
        auth_service.check_session_user(
            {"user_id": renita.id, "session_version": renita.session_version - 1}
        )[0]
        == "revoked"
    )
    # Inactive: revoked
    renita.is_active = False
    db_session.commit()
    assert (
        auth_service.check_session_user(
            {"user_id": renita.id, "session_version": renita.session_version}
        )[0]
        == "revoked"
    )
    # No user named: skip
    assert auth_service.check_session_user({})[0] == "skip"
    # Unknown id: revoked
    assert auth_service.check_session_user({"user_id": 999999})[0] == "revoked"


# ---------------------------------------------------------------------------
# A sign-in past its end date is signed out; setting one in the past ends the
# sessions at once (the login path already refused such a user)
# ---------------------------------------------------------------------------


def _yesterday():
    return date.today() - timedelta(days=1)


def test_expired_user_session_dies_next_request(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    rc = _signed_in("renita", RENITA_PW)
    assert rc.get("/api/customers").status_code == 200

    # The end date passes (set directly, without a version bump).
    renita.expires_on = _yesterday()
    db_session.commit()

    assert (
        auth_service.check_session_user(
            {"user_id": renita.id, "session_version": renita.session_version}
        )[0]
        == "revoked"
    )
    assert rc.get("/api/customers").status_code == 401


def test_setting_a_past_end_date_bumps_the_version(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    before = renita.session_version
    rc = _signed_in("renita", RENITA_PW)

    r = client.put(
        f"/api/users/{renita.id}", json={"expires_on": _yesterday().isoformat()}
    )
    assert r.status_code == 200, r.text
    db_session.refresh(renita)
    assert renita.session_version == before + 1
    assert rc.get("/api/customers").status_code == 401


def test_a_future_end_date_keeps_the_session(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    before = renita.session_version
    rc = _signed_in("renita", RENITA_PW)

    later = (date.today() + timedelta(days=30)).isoformat()
    r = client.put(f"/api/users/{renita.id}", json={"expires_on": later})
    assert r.status_code == 200, r.text
    db_session.refresh(renita)
    assert renita.session_version == before
    assert rc.get("/api/customers").status_code == 200


# ---------------------------------------------------------------------------
# Sign out ends the session for good: a cookie copied before it is refused
# ---------------------------------------------------------------------------


def test_logout_ends_a_copied_cookie(client, db_session):
    renita = _mk_user(db_session, "renita", RENITA_PW)
    before = renita.session_version
    rc = _signed_in("renita", RENITA_PW)
    copied = rc.cookies.get(auth_service.SESSION_COOKIE_NAME)
    assert copied

    assert rc.post("/api/auth/logout").status_code == 200
    db_session.refresh(renita)
    assert renita.session_version == before + 1

    # The cookie as it was before Sign out, put back: no longer signed in.
    rc.cookies.set(auth_service.SESSION_COOKIE_NAME, copied)
    assert rc.get("/api/customers").status_code == 401


def test_logout_succeeds_without_a_user_row_or_a_database(client, monkeypatch):
    from app.routes import auth as auth_routes

    # No row by that id, and a session naming no row at all: nothing to bump.
    auth_routes._end_sessions_of(999999)
    auth_routes._end_sessions_of(None)

    # The books cannot be reached: Sign out still signs out.
    import app.database as db_module

    def unreachable(*args, **kwargs):
        raise RuntimeError("the books are not available")

    monkeypatch.setattr(db_module, "SessionLocal", unreachable)
    assert client.post("/api/auth/logout").status_code == 200


# ---------------------------------------------------------------------------
# A switch to another company re-checks the session first: a demoted or
# deactivated administrator cannot set up a new company from a stale cookie
# ---------------------------------------------------------------------------


def _change_in_alpha(username, **fields):
    """Change a row in Alpha directly (as an administrator elsewhere would),
    without the session that carries the old role knowing."""
    import app.database as db_module

    db = db_module.SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        for name, value in fields.items():
            setattr(user, name, value)
        db.commit()
    finally:
        db.close()


def test_a_deactivated_admin_cannot_set_up_a_new_company_at_a_switch(served):
    _new_company(served, "Charlie Cafe", "charlie.db")
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    _change_in_alpha("pat", is_active=False)

    r = client.post("/api/auth/company", json={"file": "charlie.db"})
    assert r.status_code == 200, r.text
    # signed out, not offered the setup: the target asks for its own password
    assert r.json() == {"status": "ok", "authenticated": False, "username": ""}
    r = client.post("/api/auth/setup", json={"password": "stranger-password"})
    assert r.status_code == 403


def test_a_demoted_admin_cannot_set_up_a_new_company_at_a_switch(served):
    _new_company(served, "Charlie Cafe", "charlie.db")
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    # Demoted on the row only (no version bump): the row's role is the one
    # that counts, not the "admin" the cookie still carries.
    _change_in_alpha("pat", role=ROLE_BOOKKEEPER)

    r = client.post("/api/auth/company", json={"file": "charlie.db"})
    assert r.status_code == 200, r.text
    assert "setup" not in r.json()
    assert r.json()["authenticated"] is False


def test_an_admin_still_sets_a_new_company_up_at_a_switch(served):
    # the re-check leaves a sign-in that is still good alone
    _new_company(served, "Charlie Cafe", "charlie.db")
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    r = client.post("/api/auth/company", json={"file": "charlie.db"})
    assert r.json() == {"status": "ok", "authenticated": False, "setup": True}

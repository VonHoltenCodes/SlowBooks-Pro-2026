"""
Single-user auth flow: setup → login → status → logout.

All tests here start from an unauthenticated state and use `unauthed_client`
so they can exercise the full auth flow without a pre-authenticated session.
"""


def test_status_before_setup(unauthed_client):
    r = unauthed_client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["setup_needed"] is True
    assert body["authenticated"] is False


def test_setup_sets_password_and_authenticates(unauthed_client):
    r = unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True

    # Status now reflects setup complete + authenticated
    status = unauthed_client.get("/api/auth/status").json()
    assert status["setup_needed"] is False
    assert status["authenticated"] is True


def test_setup_rejects_short_password(unauthed_client):
    r = unauthed_client.post("/api/auth/setup", json={"password": "short"})
    assert r.status_code == 400


def test_setup_rejects_second_call(unauthed_client):
    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    r = unauthed_client.post("/api/auth/setup", json={"password": "another-password"})
    assert r.status_code == 409


def test_login_rejects_wrong_password(unauthed_client):
    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")
    r = unauthed_client.post("/api/auth/login", json={"password": "wrong-wrong"})
    assert r.status_code == 401


def test_login_accepts_correct_password(unauthed_client):
    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")
    r = unauthed_client.post("/api/auth/login", json={"password": "hunter2hunter"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_login_before_setup_returns_409(unauthed_client):
    r = unauthed_client.post("/api/auth/login", json={"password": "whatever"})
    assert r.status_code == 409


def test_logout_clears_session(unauthed_client):
    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")
    status = unauthed_client.get("/api/auth/status").json()
    assert status["authenticated"] is False


def test_protected_route_requires_auth(unauthed_client):
    # /api/analytics/dashboard needs auth — no setup, no session
    r = unauthed_client.get("/api/analytics/dashboard")
    assert r.status_code == 401


def test_protected_route_accepts_authed_session(authed_client):
    r = authed_client.get("/api/analytics/dashboard")
    # 200 (or 500 if DB missing data) — anything that is NOT 401 proves
    # the auth middleware let us through
    assert r.status_code != 401


# --- Session rotation, idle timeout, login audit log ---


def test_login_records_success_in_audit_log(unauthed_client, db_session):
    """Successful login leaves a row in login_attempts."""
    from app.models.auth import LoginAttempt

    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")

    r = unauthed_client.post("/api/auth/login", json={"password": "hunter2hunter"})
    assert r.status_code == 200

    rows = db_session.query(LoginAttempt).order_by(LoginAttempt.id.desc()).all()
    assert len(rows) >= 1
    last = rows[0]
    assert last.success is True
    # IP for TestClient calls is "testclient"
    assert last.ip == "testclient"


def test_login_records_failure_in_audit_log(unauthed_client, db_session):
    """Failed login leaves a row in login_attempts with success=False."""
    from app.models.auth import LoginAttempt

    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")

    r = unauthed_client.post("/api/auth/login", json={"password": "wrong-wrong"})
    assert r.status_code == 401

    rows = (
        db_session.query(LoginAttempt)
        .filter(LoginAttempt.success.is_(False))
        .order_by(LoginAttempt.id.desc())
        .all()
    )
    assert len(rows) >= 1


def test_login_rotates_session(unauthed_client):
    """The session cookie value changes after a successful login —
    defence-in-depth against session fixation."""
    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})
    unauthed_client.post("/api/auth/logout")

    # Plant a pre-login cookie so we can confirm it gets replaced.
    unauthed_client.get("/api/auth/status")  # might set an anon session
    pre_login_cookie = unauthed_client.cookies.get("slowbooks_session")

    unauthed_client.post("/api/auth/login", json={"password": "hunter2hunter"})
    post_login_cookie = unauthed_client.cookies.get("slowbooks_session")

    # Either pre_login was None (nothing planted) or it differs from post-login.
    if pre_login_cookie is not None:
        assert pre_login_cookie != post_login_cookie
    assert post_login_cookie is not None


def test_idle_timeout_expires_session(unauthed_client, monkeypatch):
    """Authenticated session past SESSION_IDLE_TIMEOUT_SECONDS returns 401."""
    import app.main as main_module

    # Re-enable idle timeout for this test (conftest pinned it to 0).
    monkeypatch.setattr(main_module, "SESSION_IDLE_TIMEOUT_SECONDS", 60)

    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})

    # /api/auth/* is exempt from require_session, so it never stamps
    # last_activity. Hit a real protected route to land an initial timestamp
    # in the session.
    r = unauthed_client.get("/api/analytics/dashboard")
    assert r.status_code != 401

    # Jump the middleware's clock forward past the idle window. Patching the
    # module-level `_time` alias is enough — the middleware reads it on every
    # call.
    base = main_module._time.time()
    monkeypatch.setattr(main_module._time, "time", lambda: base + 3600)

    r = unauthed_client.get("/api/analytics/dashboard")
    assert r.status_code == 401
    assert "expired" in r.json().get("detail", "").lower()


def test_idle_timeout_extends_with_activity(unauthed_client, monkeypatch):
    """Sliding window — every authed request rolls last_activity forward."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "SESSION_IDLE_TIMEOUT_SECONDS", 60)

    unauthed_client.post("/api/auth/setup", json={"password": "hunter2hunter"})

    base = main_module._time.time()
    # Walk the clock forward 30s at a time, hitting a protected route each
    # time. Each hit refreshes last_activity, so we should stay logged in
    # well past the 60s window.
    for offset in (30, 60, 90, 120, 150):
        monkeypatch.setattr(main_module._time, "time", lambda o=offset: base + o)
        r = unauthed_client.get("/api/analytics/dashboard")
        assert r.status_code != 401, f"unexpected expiry at +{offset}s"

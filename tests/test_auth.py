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

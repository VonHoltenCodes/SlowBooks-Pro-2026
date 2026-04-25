"""
/health endpoint must be always-on, never require auth, and return 200
with a JSON body that downstream monitors can parse.
"""


def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_reports_ok(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_does_not_require_auth(client):
    # No setup, no login — still 200
    r = client.get("/health")
    assert r.status_code == 200

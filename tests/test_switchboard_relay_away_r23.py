# ruff: noqa: F811  (the relay fixture is imported from test_switchboard_connect_1087)
"""Round 23, W-208: Connect to AI assistant with the relay out of reach.

The press waited on the vendored client's 30 seconds (Windows gave up first,
at about 21), then showed the OS's own "[WinError 10060] …" with the relay's
address for three seconds before the panel's refresh dropped it. Now each
press (Connect, Show a sign-in code, Disconnect) waits PRESS_TIMEOUT at most,
says one plain sentence (the detail goes to the log), and leaves nothing
half-made. A relay answering a server error (5xx: just updated, or busy)
says so in its own sentence; Disconnect then undoes nothing, so it can be
pressed again.
"""

import json
import os
import logging
import socket
import time

import pytest

from app.models.api_tokens import ApiToken
from app.services import switchboard_link as link
from tests.test_switchboard_connect_1087 import (  # noqa: F401  (fixture)
    _connected,
    _free_port,
    _wait,
    relay,
)


@pytest.fixture
def silent_relay(monkeypatch):
    """An address that takes the connection and never answers, as a relay
    behind a firewall that drops the packets looks to a read."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.5)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        sock.close()


def test_a_press_waits_ten_seconds_at_most():
    assert 0 < link.PRESS_TIMEOUT <= 10


@pytest.mark.parametrize("where", ["silent", "refused"])
def test_connect_with_the_relay_away_says_so_and_leaves_nothing(
    relay, silent_relay, client, db_session, monkeypatch, caplog, where
):
    address = silent_relay if where == "silent" else f"http://127.0.0.1:{_free_port()}"
    monkeypatch.setenv(link.RELAY_ENV, address)
    caplog.set_level(logging.WARNING)
    started = time.monotonic()
    r = client.post("/api/switchboard/connect")
    took = time.monotonic() - started
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == (
        "The relay could not be reached. SlowBooks Pro is working normally; try "
        "again in a few minutes."
    )
    # bounded by the press's own timeout, not the OS's or the client's 30 s
    assert took < 5
    # no address and no OS text in the sentence; the log has the detail
    assert "127.0.0.1" not in r.text and "Errno" not in r.text
    assert "did not answer" in caplog.text
    # nothing half-made: no link file, no token, no line, Connect offered
    assert not link.link_path().exists()
    assert db_session.query(ApiToken).count() == 0
    st = client.get("/api/switchboard/status").json()
    assert st["state"] == "not_connected" and st["linked"] is False
    assert st["can_manage"] is True
    # and the next press, with the relay back, connects
    monkeypatch.setenv(link.RELAY_ENV, relay.url)
    _connected(relay, client)


def test_a_server_error_on_connect_says_the_relay_was_updated(
    relay, client, db_session
):
    relay.fail["/v1/link"] = 503
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == (
        "The relay was just updated or is busy. Try again in a couple of minutes."
    )
    assert "7f3a" not in r.text
    assert not link.link_path().exists()
    assert db_session.query(ApiToken).count() == 0


def test_a_sign_in_code_with_the_relay_away_or_busy(relay, client, monkeypatch):
    _connected(relay, client)
    relay.fail["/v1/code"] = 502
    r = client.post("/api/switchboard/code")
    assert r.status_code == 503 and r.json()["detail"] == link.RELAY_BUSY
    del relay.fail["/v1/code"]
    record = json.loads(link.link_path().read_text())
    record["relay"] = f"http://127.0.0.1:{_free_port()}"
    link._save(record)
    r = client.post("/api/switchboard/code")
    assert r.status_code == 503 and r.json()["detail"] == link.RELAY_AWAY
    assert relay.codes == []


def test_disconnect_on_a_server_error_undoes_nothing_and_can_be_pressed_again(
    relay, client, db_session
):
    _, line = _connected(relay, client)
    relay.fail["/v1/forget"] = 500
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.RELAY_BUSY
    # still connected: the file, the token and (again) the line
    assert link.link_path().exists()
    row = db_session.query(ApiToken).one()
    assert row.is_active is True
    relay.next_line()
    assert _wait(
        lambda: client.get("/api/switchboard/status").json()["state"] == "connected"
    )
    # the relay is back: Disconnect does it all
    del relay.fail["/v1/forget"]
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert not link.link_path().exists()


def test_disconnect_with_the_relay_silent_is_quick_and_gives_the_fallback(
    relay, silent_relay, client
):
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    record["relay"] = silent_relay
    link._save(record)
    started = time.monotonic()
    r = client.post("/api/switchboard/disconnect")
    assert time.monotonic() - started < 5
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unlinked"] is False and got["revoked"] is True
    assert relay.slug in got["sentence"] and "support@" in got["sentence"]
    assert not link.link_path().exists()


def test_no_secret_reaches_the_log_when_the_relay_is_away(
    relay, silent_relay, client, caplog
):
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    record["relay"] = silent_relay
    link._save(record)
    caplog.set_level(logging.DEBUG)
    client.post("/api/switchboard/code")
    client.post("/api/switchboard/disconnect")
    for secret in (record["credential"], record["token"]):
        assert secret not in caplog.text


# -- NEW-269: a deletion the relay has not heard is kept and retried ----------------


@pytest.fixture
def quick_retry(monkeypatch):
    monkeypatch.setattr(link, "RETRY_FIRST", 0.1)
    monkeypatch.setattr(link, "RETRY_MAX", 0.4)
    yield
    link.stop_retry()


def _away(record):
    away = dict(record, relay=f"http://127.0.0.1:{_free_port()}")
    link._save(away)
    return away


def test_the_relay_name_outlives_a_restart_and_is_deleted_when_the_relay_is_back(
    relay, quick_retry, client, monkeypatch
):
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    away = _away(record)
    r = client.post("/api/switchboard/disconnect")
    assert r.status_code == 200 and r.json()["unlinked"] is False
    # kept, this user's only, with no token in it
    path = link.pending_path()
    assert path.exists()
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o777 == 0o600
    kept = json.loads(path.read_text())
    assert record["token"] not in path.read_text()
    assert [p["slug"] for p in kept["pending"]] == [relay.slug]
    # the panel says the name, with the same sentence, for any reader
    st = client.get("/api/switchboard/status").json()
    assert st["pending"] == [
        {
            "slug": relay.slug,
            "sentence": r.json()["sentence"],
            "dismissed": False,
        }
    ]
    assert record["credential"] not in json.dumps(st)
    # Dismiss hides it; the retry goes on
    assert client.post("/api/switchboard/dismiss", json={"slug": relay.slug}).json()[
        "pending"
    ][0]["dismissed"]
    # a restart (the retry loop ended, as the app's shutdown ends it) still
    # has it
    link.stop_retry()
    assert link._retry_thread is None
    assert client.get("/api/switchboard/status").json()["pending"][0]["slug"] == (
        relay.slug
    )
    # a new Connect is not held up by it
    _connected(relay, client)
    client.post("/api/switchboard/disconnect")
    # the relay is back where the old link pointed: the retry deletes it
    forgets_before = relay.paths().count(("POST", "/v1/forget"))
    kept = json.loads(path.read_text())
    for p in kept["pending"]:
        p["relay"] = relay.url
    path.write_text(json.dumps(kept))
    if os.name != "nt":
        os.chmod(path, 0o600)
    link.start_in_background()
    assert _wait(lambda: not path.exists(), timeout=10)
    assert relay.paths().count(("POST", "/v1/forget")) > forgets_before
    assert client.get("/api/switchboard/status").json()["pending"] == []
    assert away["relay"] != relay.url


def test_only_an_administrator_dismisses(relay, client):
    from tests.test_switchboard_connect_1087 import _sign_in

    person = _sign_in("bookkeeper", client)
    r = person.post("/api/switchboard/dismiss", json={"slug": "abcdefghjk"})
    assert r.status_code == 403


def test_the_retry_says_in_the_launcher_log_that_it_deleted_the_record(
    relay, quick_retry, client, caplog
):
    """W-212 (round 24): the retry's outcome reaches the log a person reads.
    The served app (the desktop's --_serve child, a server) sets no handler
    on the root logger, so Python's last-resort handler writes WARNING and up
    to launcher.log and nothing below; the line is logged at a level it
    keeps, and names no credential, token or device."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    _away(record)
    client.post("/api/switchboard/disconnect")
    link.stop_retry()
    path = link.pending_path()
    kept = json.loads(path.read_text())
    for p in kept["pending"]:
        p["relay"] = relay.url
    path.write_text(json.dumps(kept))
    caplog.clear()
    caplog.set_level(logging.WARNING)
    assert link.retry_pending_once() == 0
    said = [
        r
        for r in caplog.records
        if "deleted" in r.getMessage() and "left by a disconnect" in r.getMessage()
    ]
    assert said and all(r.levelno >= logging.lastResort.level for r in said)
    assert relay.slug in said[0].getMessage()
    for secret in (record["credential"], record["token"]):
        assert secret not in caplog.text

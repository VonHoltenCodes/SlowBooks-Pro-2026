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
import secrets
import socket
import threading
from types import SimpleNamespace

import pytest

from app.models.api_tokens import ApiToken
from app.services import switchboard_link as link
from tests.switchboard_clock import BACKSTOP, Holder, PressClock, hold_the_clock
from tests.test_switchboard_connect_1087 import (  # noqa: F401  (fixture)
    _connected,
    _free_port,
    _wait,
    relay,
)


@pytest.fixture
def silent_relay(monkeypatch):
    """An address that takes the connection, reads the request and never
    answers, as a relay behind a firewall that drops the packets looks to a
    read.

    The press's deadline is a PressClock (tests/switchboard_clock.py): it
    runs out when the silent relay has the request, and at no other moment.
    A real half-second deadline here also covered the Connect each test
    makes first against the fake relay that answers, and a slow runner used
    it up there: "The relay could not be reached" before the step the test
    was about (CI, test_disconnect_with_the_relay_silent_is_quick...)."""
    hold_the_clock(monkeypatch)
    holder = Holder()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)

    def hear_and_say_nothing(conn):
        try:
            conn.settimeout(BACKSTOP)
            head = b""
            while b"\r\n\r\n" not in head:
                got = conn.recv(65536)
                if not got:
                    return
                head += got
            holder.hold()
        except OSError:
            pass
        finally:
            conn.close()

    def accept():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(
                target=hear_and_say_nothing, args=(conn,), daemon=True
            ).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{sock.getsockname()[1]}", holder=holder
        )
    finally:
        holder.let_go()
        sock.close()


def _ended_at_its_deadline(silent_relay, presses: int = 1) -> None:
    """Each press against the silent relay answered at its deadline, while
    the relay was still holding the connection without a word: it waited
    for nothing else (no timer of its own, no socket timeout: the relay's
    hold reaches its backstop only for a press that waits past its
    deadline)."""
    assert silent_relay.holder.holding.is_set()
    assert len(PressClock.ran_out) == presses
    assert not silent_relay.holder.held_out.is_set()


def test_a_press_waits_ten_seconds_at_most():
    assert 0 < link.PRESS_TIMEOUT <= 10


@pytest.mark.parametrize("where", ["silent", "refused"])
def test_connect_with_the_relay_away_says_so_and_leaves_nothing(
    relay, silent_relay, client, db_session, monkeypatch, caplog, where
):
    address = (
        silent_relay.url if where == "silent" else f"http://127.0.0.1:{_free_port()}"
    )
    monkeypatch.setenv(link.RELAY_ENV, address)
    caplog.set_level(logging.WARNING)
    r = client.post("/api/switchboard/connect")
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == (
        "The relay could not be reached. SlowBooks Pro is working normally; try "
        "again in a few minutes."
    )
    # bounded by the press's own deadline, not the OS's or the client's 30 s:
    # silent, it ended there; refused, the refusal was the answer and its
    # time never had to run out
    if where == "silent":
        _ended_at_its_deadline(silent_relay)
    else:
        assert PressClock.ran_out == []
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
    record["relay"] = silent_relay.url
    link._save(record)
    r = client.post("/api/switchboard/disconnect")
    _ended_at_its_deadline(silent_relay)
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
    record["relay"] = silent_relay.url
    link._save(record)
    caplog.set_level(logging.DEBUG)
    client.post("/api/switchboard/code")
    client.post("/api/switchboard/disconnect")
    _ended_at_its_deadline(silent_relay, presses=2)
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


@pytest.fixture
def held_retry(tmp_path, monkeypatch):
    """A pending deletion whose try at the relay is held until released, as
    a try at a relay that is away can take as long as the press's deadline
    (Windows refuses a closed port only after a few seconds)."""
    monkeypatch.setattr(link, "link_path", lambda: tmp_path / link.LINK_FILE)
    monkeypatch.setattr(link, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(link, "RETRY_MAX", 0.1)
    held = {"entered": threading.Event(), "release": threading.Event()}
    held["tries"], held["saves"] = [], []

    def forget(relay, credential, deadline=None):
        held["tries"].append(threading.current_thread().name)
        held["entered"].set()
        held["release"].wait(10)
        return link._Forgot(link.AWAY)

    save = link._save_pending

    def saved(items):
        held["saves"].append(threading.current_thread().name)
        save(items)

    monkeypatch.setattr(link, "_relay_forget", forget)
    monkeypatch.setattr(link, "_save_pending", saved)
    held["record"] = {
        "relay": f"http://127.0.0.1:{_free_port()}",
        "slug": "".join(secrets.choice("abcdefghjkmnpqrstvwxyz") for _ in range(10)),
        "credential": "sbd_" + secrets.token_urlsafe(32),
    }
    try:
        yield held
    finally:
        held["release"].set()
        link.stop_retry()


def test_a_stop_that_runs_out_of_time_still_ends_the_retry(held_retry):
    """The app's shutdown gives the retry a second; a try can take ten. A
    stop that ran out of time used to undo itself: the retry went on trying
    and writing the pending file after it, over what came next (in the
    tests, a pending file a test had just rewritten: Windows CI, once). Now
    the retry ends with the try it was on, and writes nothing after its
    stop."""
    link._add_pending(held_retry["record"])
    retry = link._retry_thread
    assert held_retry["entered"].wait(5)
    link.stop_retry(timeout=0.2)
    del held_retry["saves"][:]
    # what comes after the stop has the file: here, Dismiss
    link.dismiss_pending(held_retry["record"]["slug"])
    held_retry["release"].set()
    retry.join(5)
    assert not retry.is_alive()
    assert held_retry["tries"] == ["claude-relay-forget"]
    assert "claude-relay-forget" not in held_retry["saves"]
    assert link._load_pending()[0]["dismissed"] is True
    assert link._retry_thread is None


def test_the_first_try_waits_its_time_after_an_earlier_stop(held_retry, monkeypatch):
    """A stop with no retry running (the app's shutdown, a test's) left its
    wake-up set, and the next retry tried at once instead of after
    RETRY_FIRST: the try was then under way when the next stop came.

    RETRY_FIRST is one no machine reaches during the test (it was 3 s: a
    runner that stalled the test that long saw the try come on time and
    failed), and the wake-up itself is checked, not only that no try came
    in half a second."""
    monkeypatch.setattr(link, "RETRY_FIRST", 3600.0)
    link.stop_retry()
    link._add_pending(held_retry["record"])
    assert link._retry_thread is not None
    assert not link._retry_events["wake"].is_set()
    assert not held_retry["entered"].wait(0.5)


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

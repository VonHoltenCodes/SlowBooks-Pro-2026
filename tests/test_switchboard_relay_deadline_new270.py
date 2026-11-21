"""Round 24, NEW-270: with the relay out of reach, a press answers within
PRESS_TIMEOUT whatever the number of addresses the relay's name has.

The relay's name has four addresses (two IPv4, two IPv6). urllib's
connection, through socket.create_connection, gave each address the whole
limit in turn, so with every one of them dropped by a firewall Connect and
Show a sign-in code answered after about 41 s on the Mac, not about 10 s.
The product's relay calls (Connect, a sign-in code, Disconnect, the key,
the quiet retry of a pending deletion: all through _relay_call) now have
one deadline for the whole attempt; each address gets what is left.
"""

import time
import types
import urllib.error

import pytest

from app.services import switchboard_link as link

ADDRESSES = [
    (2, 1, 6, "", ("192.0.2.10", 443)),
    (2, 1, 6, "", ("192.0.2.11", 443)),
    (10, 1, 6, "", ("2001:db8::10", 443, 0, 0)),
    (10, 1, 6, "", ("2001:db8::11", 443, 0, 0)),
]


class _Dropped:
    """A socket whose connect waits out its timeout, as an address whose
    packets a firewall drops looks; ``refused`` addresses fail at once."""

    tried: list = []
    refused: set = set()

    def __init__(self, family, kind, proto):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def bind(self, _where):
        pass

    def connect(self, where):
        type(self).tried.append((where[0], self.timeout))
        if where[0] in type(self).refused:
            raise ConnectionRefusedError(111, "Connection refused")
        time.sleep(self.timeout)
        raise TimeoutError("timed out")

    def close(self):
        pass


@pytest.fixture
def four_dropped(monkeypatch):
    """The relay's name resolves to four addresses, none of which answers."""
    _Dropped.tried, _Dropped.refused = [], set()
    fake = types.SimpleNamespace(
        getaddrinfo=lambda host, port, *a, **k: list(ADDRESSES),
        socket=_Dropped,
        SOCK_STREAM=1,
    )
    monkeypatch.setattr(link, "socket", fake)
    monkeypatch.setattr(link, "PRESS_TIMEOUT", 0.6)
    monkeypatch.setenv(link.RELAY_ENV, "https://relay.example.test")
    return _Dropped


def test_one_deadline_for_every_address(four_dropped):
    started = time.monotonic()
    with pytest.raises(urllib.error.URLError):
        link._relay_call("https://relay.example.test", "/v1/code", None, {})
    took = time.monotonic() - started
    # the whole attempt, not 0.6 s for each of four addresses (2.4 s)
    assert took < 0.6 * 1.5, took
    # the first address had all of the limit, and nothing more was given
    assert four_dropped.tried and four_dropped.tried[0][1] <= 0.6
    assert sum(t for _a, t in four_dropped.tried) <= 0.6 + 0.05


def test_an_address_that_refuses_leaves_the_rest_of_the_time_to_the_next(
    four_dropped,
):
    four_dropped.refused = {"192.0.2.10", "192.0.2.11", "2001:db8::10"}
    started = time.monotonic()
    with pytest.raises(urllib.error.URLError):
        link._relay_call("https://relay.example.test", "/v1/link", {}, {})
    took = time.monotonic() - started
    assert [a for a, _t in four_dropped.tried] == [a[4][0] for a in ADDRESSES]
    assert 0.4 < four_dropped.tried[-1][1] <= 0.6
    assert took < 0.6 * 1.5, took


def test_connect_with_four_dropped_addresses_answers_within_the_limit(
    four_dropped, client
):
    client.get("/api/switchboard/status")  # the app warmed up first
    started = time.monotonic()
    r = client.post("/api/switchboard/connect")
    took = time.monotonic() - started
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.RELAY_AWAY
    # one relay call, its first address given all of the limit (four times
    # the limit before)
    assert len(four_dropped.tried) == 1, four_dropped.tried
    assert took < 0.6 * 2.5, took
    assert not link.link_path().exists()


def test_a_pending_deletion_retry_is_bound_the_same_way(four_dropped):
    started = time.monotonic()
    item = {"relay": "https://relay.example.test", "credential": "x" * 8}
    assert link._tell_relay_forget(item) is False
    assert time.monotonic() - started < 0.6 * 1.5


def test_every_relay_call_of_the_product_goes_through_the_deadline():
    import inspect

    src = inspect.getsource(link)
    # one place opens a connection to the relay, and it is the bound one
    assert src.count("account._opener(") == 1
    assert "_opener_by(deadline).open(" in src
    assert "urlopen(" not in src
    assert src.count(").open(") == 1
    # one function asks the relay anything (_relay_post, which classifies
    # the answer), and it is the only caller of the bound call
    assert src.count("_relay_call(") == 2  # its definition and that one call
    assert "_relay_call(" in inspect.getsource(link._relay_post)
    # the presses ask through it: Connect and a sign-in code directly,
    # Disconnect and the quiet retry through _relay_forget
    for name in ("connect", "new_code", "_relay_forget"):
        assert "_relay_post(" in inspect.getsource(getattr(link, name)), name
    for name in ("_disconnect", "_tell_relay_forget", "retry_pending_once"):
        assert "_relay_forget(" in inspect.getsource(getattr(link, name)), name
    # and a press that makes more than one call hands its one deadline on
    assert "deadline=deadline" in inspect.getsource(link.connect)
    assert inspect.getsource(link._relay_forget).count("deadline=deadline") == 2

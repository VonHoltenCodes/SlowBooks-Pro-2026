"""The SimpleFIN request timeout (#1002, from Discussion #1001): 120 s by
default, SLOWBOOKS_SIMPLEFIN_TIMEOUT overrides it, and a bridge that does not
answer in time says how long it waited and names the setting."""

import httpx
import pytest

from app.services import simplefin_service as sf


def test_the_default_and_the_setting(monkeypatch):
    monkeypatch.delenv(sf.TIMEOUT_SETTING, raising=False)
    assert sf.request_timeout() == 120.0
    monkeypatch.setenv(sf.TIMEOUT_SETTING, "240")
    assert sf.request_timeout() == 240.0
    monkeypatch.setenv(sf.TIMEOUT_SETTING, "3")
    assert sf.request_timeout() == 10.0  # the floor
    monkeypatch.setenv(sf.TIMEOUT_SETTING, "not a number")
    assert sf.request_timeout() == 120.0


def test_a_slow_bridge_says_how_long_it_waited(monkeypatch):
    monkeypatch.setenv(sf.TIMEOUT_SETTING, "45")
    monkeypatch.setattr(sf, "_assert_public_https", lambda url: "93.184.216.34")
    monkeypatch.setattr(
        sf,
        "_pin",
        lambda url, address: (
            url,
            {"host": "bridge.simplefin.org", "sni": "bridge.simplefin.org"},
        ),
    )
    seen = {}

    class Client:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **kw):
            raise httpx.ReadTimeout("slow bridge")

    monkeypatch.setattr(sf.httpx, "Client", Client)
    with pytest.raises(sf.SimpleFINError) as exc:
        sf.send(
            {"url": "https://bridge.simplefin.org/simplefin/accounts", "method": "GET"}
        )
    assert seen["timeout"] == 45.0
    assert "did not answer within 45 seconds" in str(exc.value)
    assert sf.TIMEOUT_SETTING in str(exc.value)


def test_the_guide_names_the_setting():
    from pathlib import Path

    guide = (
        Path(__file__).resolve().parents[1] / "docs/setup-bank-feeds.md"
    ).read_text(encoding="utf-8")
    assert (
        "SLOWBOOKS_SIMPLEFIN_TIMEOUT=240" in guide
        and "did not answer within 120 seconds" in guide
    )


def test_a_bridge_that_cannot_be_reached_says_so_without_the_setting():
    # #1017: a connect timeout, or any timeout well inside the configured
    # wait, names the measured time and not the setting
    s = sf.timeout_sentence(httpx.ConnectTimeout("no answer"), 21.4, 120.0)
    assert s.startswith(
        "Could not reach the SimpleFIN bridge: the connection was not answered after 21 seconds"
    )
    assert sf.TIMEOUT_SETTING not in s
    # a read timeout only fires after the configured wait: the slow-bridge
    # sentence, which names the setting
    s = sf.timeout_sentence(httpx.ReadTimeout("slow"), 119.2, 120.0)
    assert (
        s.startswith("SimpleFIN did not answer within 120 seconds")
        and sf.TIMEOUT_SETTING in s
    )
    s = sf.timeout_sentence(httpx.ReadTimeout("slow"), 10.1, 10.0)
    assert "within 10 seconds" in s


def test_the_guide_tells_the_two_sentences_apart():
    from pathlib import Path

    guide = (
        Path(__file__).resolve().parents[1] / "docs/setup-bank-feeds.md"
    ).read_text(encoding="utf-8")
    assert (
        "the connection was not answered" in guide
        and "raising the timeout does not help here" in guide
    )

"""What the guides say about one AI connection for several companies
matches what the code does (2.32.0-beta.2).

- On the desktop, an administrator of the open company may add it while
  it is not on the connection; showing a sign-in code and Disconnect need
  the open company to be on it (`_may_manage`, which Add calls with
  ``adding=True``). On a server serving several companies every one of
  those needs an administrator of every company on the connection.
- When a company turns its token away, the secret is deleted at once, or
  a moment later when another change to the connection is under way
  (#1210: the drop waits for the record's lock).
"""

import re
from pathlib import Path

from app.services import switchboard_link as link

ROOT = Path(__file__).resolve().parents[1]


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _read(name):
    return _flat((ROOT / name).read_text(encoding="utf-8"))


def _unreleased():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = log.index("### Unreleased")
    return _flat(log[start : log.index("\n### ", start + 1)])


FEATURES = _read("docs/features.md")
NOTES = _read("docs/release-notes/2.32.0-beta.2.md")


def test_on_the_desktop_add_needs_only_an_administrator_of_the_open_company(
    monkeypatch,
):
    monkeypatch.setattr(link, "_session_admin", lambda session: True)
    monkeypatch.setattr(link, "_several_served", lambda: False)
    monkeypatch.setattr(link, "_company_here", lambda: "here.db")
    elsewhere = [{"file": "other.db"}]
    assert link._may_manage({}, elsewhere, reachable_only=False, adding=True)[0]
    assert not link._may_manage({}, elsewhere, reachable_only=False)[0]
    assert link._may_manage({}, [{"file": "here.db"}], reachable_only=False)[0]


def test_the_guides_say_the_desktop_rule_for_add():
    unreleased = _unreleased()
    for name, doc in (
        ("features", FEATURES),
        ("changelog", unreleased),
        ("notes", NOTES),
    ):
        assert "while it is on the connection, sees every company" not in doc, name
        assert "while it is on the connection, does all of this" not in doc, name
        assert "does these things while that company is on" not in doc, name
    assert (
        "an administrator of the open company may add it while it is not on "
        "the connection; once it is on, that administrator sees every company "
        "on it and may show a sign-in code and disconnect" in FEATURES
    )
    assert (
        "an administrator of the open company may add it while it is not on "
        "the connection (or when that company turns its token away), and once "
        "it is on, sees every company on it and may show a sign-in code and "
        "Disconnect" in unreleased
    )
    assert (
        "an administrator of the open company can add it, and once it is on "
        "the connection can show a sign-in code or disconnect" in NOTES
    )
    assert (
        "offers **Add** again at once: on a server serving several companies, "
        "to someone who is an administrator of it and of every other company "
        "on the connection; on the desktop, to an administrator of that "
        "company." in FEATURES
    )


def test_the_guides_say_a_refused_secret_may_be_deleted_a_moment_later():
    later = (
        "at once, or a moment later when another change to the connection is under way"
    )
    unreleased = _unreleased()
    assert "even while another change to the connection is under way" not in unreleased
    assert later in unreleased
    assert later in FEATURES
    assert (
        "from this computer at once, or a moment later when another change "
        "to the connection is under way." in NOTES
    )

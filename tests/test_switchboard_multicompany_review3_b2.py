# ruff: noqa: F811
"""2.32.0-beta.2 review, third round: the AI connection across companies.

- A connection made in beta.1 names nobody as the person who added its
  company; the first time it is written in the new format, that company is
  given its token's maker, so Disconnect still works for them when that
  company cannot be opened.
- The sign-in status refuses a program's token by the gate's own check.
- What each company's gate makes of its token is not asked again on every
  call while nothing in that company has changed.
- A token whose maker is no longer an administrator is said to be that, not
  "turned off".
- Take off keeps the token to turn off later before it changes the record,
  so a failure to keep it leaves everything as it was.
"""

import json
import os
import secrets

from fastapi.testclient import TestClient

from app.main import app
from app.services import switchboard_link as link
from tests.test_switchboard_multicompany_b2 import (  # noqa: F401
    _connected_with,
    _record,
    _rows,
    _set_role,
    _signed_in,
    _switch,
    relay,
    three,
)
from tests.test_switchboard_multicompany_followups_b2 import (
    REFUSED_HEADER,
    _bot,
    _hand_token,
)


def _as_beta1():
    """The kept record as beta.1 wrote it: one company, no list."""
    path = link.link_path()
    record = json.loads(path.read_text())
    for key in ("companies", "format", "rev"):
        record.pop(key, None)
    path.write_text(json.dumps(record))
    os.chmod(path, 0o600)


# -- a connection made in beta.1 -----------------------------------------------------


def test_a_beta1_company_is_given_its_tokens_maker_as_the_one_who_added_it(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay)
    _as_beta1()
    for company_file in ("smith.db", "lake.db"):
        _switch(jdoe, company_file)
        r = jdoe.post("/api/switchboard/add")
        assert r.status_code == 200, r.text
    _switch(jdoe, "harbor.db")
    kept = {e["file"]: e["added_by"] for e in _record()["companies"]}
    assert kept == {"harbor.db": "jdoe", "smith.db": "jdoe", "lake.db": "jdoe"}
    # Harbor cannot be opened now: the person who made its token may still
    # Disconnect, from Smith
    monkeypatch.setattr(link, "_locked", lambda f: f == "harbor.db")
    _switch(jdoe, "smith.db")
    assert jdoe.get("/api/switchboard/status").json()["can_disconnect"] is True
    r = jdoe.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text


# -- the sign-in status a program reads ----------------------------------------------


def _both(bot):
    gate = bot.get("/api/customers")
    said = bot.get("/api/auth/status")
    return gate, said


def _same_refusal(gate, said):
    assert gate.status_code in (401, 403), gate.text
    assert said.status_code == gate.status_code, (gate.text, said.text)
    assert said.json()["detail"] == gate.json()["detail"]
    assert said.headers.get(REFUSED_HEADER) == gate.headers.get(REFUSED_HEADER)
    assert "Harbor Books" not in said.text


def test_the_sign_in_status_refuses_every_token_the_gate_refuses(three, relay):
    _id, secret = _hand_token("harbor.db", "office script")
    lower = TestClient(app)
    lower.headers["Authorization"] = f"bearer {secret}"
    lower.headers["X-SlowBooks-Company"] = "harbor.db"
    _same_refusal(*_both(lower))
    lower.headers["Authorization"] = "bearer sbp_" + secrets.token_urlsafe(32)
    _same_refusal(*_both(lower))
    _id, admin_secret = _hand_token("harbor.db", "old admin", role="admin")
    _same_refusal(*_both(_bot(admin_secret, "harbor.db")))
    _connected_with(three, relay)
    link_secret = _record()["companies"][0]["token"]
    _same_refusal(*_both(_bot(link_secret, "harbor.db", client=("192.168.1.50", 5555))))
    # a token the gate lets in reads its own company's status
    gate, said = _both(_bot(link_secret, "harbor.db"))
    assert gate.status_code == 200 and said.status_code == 200
    assert said.json()["company_name"] == "Harbor Books"


# -- what each company's gate makes of its token --------------------------------------


def test_the_gate_is_not_asked_again_while_nothing_changed(three, relay, monkeypatch):
    _connected_with(three, relay, "smith.db", "lake.db")
    books = link.LinkBooks()
    assert all(e["answers"] for e in books.entries())
    asked = []
    real = link._ask_refusal
    monkeypatch.setattr(
        link, "_ask_refusal", lambda f, d: asked.append(f) or real(f, d)
    )
    for _ in range(3):
        assert all(e["answers"] for e in books.entries())
    assert asked == []
    # a change written to Smith's file is seen at once
    _set_role(three, "smith.db", "jdoe", "bookkeeper")
    states = {e["file"]: e for e in books.entries()}
    assert asked == ["smith.db"]
    assert states["smith.db"]["answers"] is False
    assert states["harbor.db"]["answers"] and states["lake.db"]["answers"]


def test_a_change_saved_by_this_app_is_seen_at_once(three, relay):
    _connected_with(three, relay, "smith.db")
    books = link.LinkBooks()
    assert all(e["answers"] for e in books.entries())
    from app.models.api_tokens import ApiToken

    smith = _record()["companies"][1]
    with link._in_company("smith.db") as db:
        row = db.query(ApiToken).filter_by(token_hash=smith["token_sha256"]).one()
        row.is_active = False
        db.commit()
    states = {e["file"]: e for e in books.entries()}
    assert states["smith.db"]["answers"] is False
    assert states["smith.db"]["why"] == "turned off in that company"


# -- a token whose maker is no longer an administrator ------------------------------


def test_a_demoted_makers_company_is_not_said_to_be_turned_off(three, relay):
    _connected_with(three, relay, "smith.db")
    _set_role(three, "smith.db", "jdoe", "bookkeeper")
    states = {e["file"]: e for e in link.LinkBooks().entries()}
    assert states["smith.db"]["answers"] is False
    assert states["smith.db"]["why"] == (
        "the person who added it is no longer an administrator there"
    )
    smith = _record()["companies"][1]
    # the state the panel lists it with (to a manager)
    assert link._entry_state(smith) == "maker_gone"
    assert [
        t.active for t in _rows(three, "smith.db") if t.hash == smith["token_sha256"]
    ] == [True]


# -- Take off ----------------------------------------------------------------------


def test_take_off_that_cannot_keep_the_leftover_changes_nothing(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    before = _record()["companies"]

    def cannot_turn_off(db, entry):
        raise RuntimeError("the company's books could not be written")

    def cannot_write(found):
        raise OSError("the data folder is read-only")

    monkeypatch.setattr(link, "_revoke_by_hash", cannot_turn_off)
    monkeypatch.setattr(link, "_save_leftovers", cannot_write)
    r = jdoe.post("/api/switchboard/take-off", json={"file": "lake.db"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.NOT_WRITTEN
    assert _record()["companies"] == before
    lake = before[2]
    assert [
        t.active for t in _rows(three, "lake.db") if t.hash == lake["token_sha256"]
    ] == [True]


def test_take_off_keeps_a_leftover_only_for_a_token_not_turned_off(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    smith, lake = _record()["companies"][1:]
    r = jdoe.post("/api/switchboard/take-off", json={"file": "smith.db"})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["result"] == "ended"
    assert link._load_leftovers() == {}

    def cannot_turn_off(db, entry):
        raise RuntimeError("the company's books could not be written")

    monkeypatch.setattr(link, "_revoke_by_hash", cannot_turn_off)
    r = jdoe.post("/api/switchboard/take-off", json={"file": "lake.db"})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["result"] == "later"
    assert [e["file"] for e in _record()["companies"]] == ["harbor.db"]
    # the leftover kept for it turned it off as soon as Lake could be swept
    # (it is open here), and was forgotten
    assert [
        t.active for t in _rows(three, "lake.db") if t.hash == lake["token_sha256"]
    ] == [False]
    assert link._load_leftovers() == {}


def test_take_off_that_cannot_write_the_record_forgets_the_leftover(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    before = _record()["companies"]

    def cannot_save(record):
        raise OSError("the data folder is read-only")

    monkeypatch.setattr(link, "_save", cannot_save)
    r = jdoe.post("/api/switchboard/take-off", json={"file": "lake.db"})
    assert r.status_code == 503, r.text
    assert _record()["companies"] == before
    assert link._load_leftovers() == {}

# ruff: noqa: F811
"""2.32.0-beta.2 review: who sets a company up, what a token is told, and
what the AI assistant panel says and offers.

- A company with no password yet is set up only from the sign-in that
  opened it under Companies, whether the request names it by cookie or by
  its company header.
- The sign-in status a program reads answers only for the company its own
  token opens.
- An AI assistant's token whose maker is no longer an administrator says
  so, in the gate's answer and in Settings, API Tokens, and the panel offers
  Add again at once.
- A client company's administrator learns nothing of the other companies
  when they cannot be opened now, and gets no Disconnect.
- Take off changes nothing when the record cannot be written, and a full
  connection says it is full.

Every password and token is made at run time.
"""

import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.database import Base
from app.main import app
from app.services import switchboard_link as link
from tests.test_switchboard_multicompany_b2 import (  # noqa: F401
    COMPANIES,
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


def _list_companies(host, extra):
    rows = [{"name": n, "file": f} for f, n in COMPANIES]
    rows += [{"name": n, "file": f} for f, n in extra]
    (host.root / "companies.json").write_text(
        json.dumps({"companies": rows, "last_opened": "harbor.db"}), encoding="utf-8"
    )


# -- setting a company up --------------------------------------------------------------


def _blank(path, name):
    from app.services.settings_service import set_setting

    engine = db_module.create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as db:
        set_setting(db, "company_name", name)
        db.commit()
    engine.dispose()


def test_a_company_with_no_password_is_not_set_up_by_its_header(three):
    _blank(three.folder / "fresh.db", "Fresh Co")
    _list_companies(three, [("fresh.db", "Fresh Co")])
    pw = secrets.token_urlsafe(16)
    anon = TestClient(app)
    r = anon.post(
        "/api/auth/setup",
        json={"password": pw, "company_name": "Fresh Co"},
        headers={"X-SlowBooks-Company": "fresh.db"},
    )
    assert r.status_code == 403, r.text
    assert anon.get("/api/auth/status").json()["authenticated"] is False
    from app.services.auth import password_is_set

    with link._in_company("fresh.db") as db:
        assert password_is_set(db) is False


# -- the sign-in status a program reads ----------------------------------------------


def test_a_token_is_told_only_of_the_company_it_opens(three):
    _id, secret = _hand_token("harbor.db", "office script")
    r = _bot(secret, "lake.db").get("/api/auth/status")
    assert r.status_code == 401, r.text
    assert "Lake" not in r.text and "cli" not in r.text
    r = _bot(secret, "harbor.db").get("/api/auth/status")
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == "Harbor Books"
    assert "usernames" not in r.json()


def test_the_sign_in_status_refuses_a_token_as_the_gate_does(three):
    """The sign-in status is the first thing a program reads; its 401 for a
    token this company turns away carries the gate's header and sentence,
    so the program can tell its token was refused from a route that failed."""
    _id, secret = _hand_token("harbor.db", "office script")
    r = _bot(secret, "lake.db").get("/api/auth/status")
    assert r.status_code == 401, r.text
    assert r.headers.get(REFUSED_HEADER) == "1"
    with link._in_company("harbor.db") as db:
        from app.models.api_tokens import ApiToken

        db.get(ApiToken, _id).is_active = False
        db.commit()
    r = _bot(secret, "harbor.db").get("/api/auth/status")
    assert r.status_code == 401, r.text
    assert r.headers.get(REFUSED_HEADER) == "1"
    assert r.json()["detail"] == (
        'The API token "office script" was revoked. Ask an administrator for a '
        "new one under Settings, API Tokens."
    )
    _id, secret = _hand_token("harbor.db", "AI assistant (Switchboard)")
    _set_role(three, "harbor.db", "jdoe", "bookkeeper")
    _set_role(three, "harbor.db", "bkpr", "admin")
    r = _bot(secret, "harbor.db").get("/api/auth/status")
    assert r.status_code == 401, r.text
    assert r.headers.get(REFUSED_HEADER) == "1"
    assert r.json()["detail"].startswith(
        'The API token "AI assistant (Switchboard)" stopped working because the '
        "administrator who made it is no longer an administrator of this company."
    )


# -- an AI assistant's token whose maker is no longer an administrator --------------


def test_a_token_whose_maker_was_demoted_says_so_and_the_list_shows_it(three):
    jdoe = _signed_in(three, "jdoe")
    r = jdoe.post(
        "/api/tokens",
        json={"label": "AI assistant (Switchboard)", "role": "bookkeeper"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["maker_gone"] is False
    secret = r.json()["token"]
    _set_role(three, "harbor.db", "jdoe", "bookkeeper")
    _set_role(three, "harbor.db", "bkpr", "admin")
    r = _bot(secret).get("/api/customers")
    assert r.status_code == 401
    assert r.headers.get(REFUSED_HEADER) == "1"
    assert r.json()["detail"] == (
        'The API token "AI assistant (Switchboard)" stopped working because the '
        "administrator who made it is no longer an administrator of this company. "
        "Nothing was done. An administrator can make a new one under Settings, "
        "API Tokens."
    )
    assert "revoked" not in r.text
    bkpr = _signed_in(three, "bkpr")
    listed = {t["label"]: t for t in bkpr.get("/api/tokens").json()}
    row = listed["AI assistant (Switchboard)"]
    assert row["is_active"] is True and row["maker_gone"] is True


def test_the_panel_offers_add_at_once_when_the_maker_was_demoted(three, relay):
    jdoe = _signed_in(three, "jdoe", company="lake.db")
    r = jdoe.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    first = _record()["companies"][0]
    _set_role(three, "lake.db", "jdoe", "bookkeeper")
    cli = _signed_in(three, "cli", company="lake.db")
    st = cli.get("/api/switchboard/status").json()
    assert st["can_add"] is True, st
    assert [c["state"] for c in st["companies"]] == ["maker_gone"]
    r = cli.post("/api/switchboard/add")
    assert r.status_code == 200, r.text
    again = _record()["companies"]
    assert len(again) == 1 and again[0]["token_sha256"] != first["token_sha256"]
    rows = {t.hash: t for t in _rows(three, "lake.db")}
    assert rows[first["token_sha256"]].active is False
    assert rows[again[0]["token_sha256"]].active is True
    assert rows[again[0]["token_sha256"]].created_by == "cli"


# -- what a client company's administrator learns ------------------------------------


def test_a_client_administrator_learns_nothing_when_the_others_are_locked(
    three, relay, monkeypatch
):
    _connected_with(three, relay, "smith.db", "lake.db")
    cli = _signed_in(three, "cli", company="lake.db")
    monkeypatch.setattr(link, "_locked", lambda f: f in ("harbor.db", "smith.db"))
    r = cli.get("/api/switchboard/status")
    assert r.status_code == 200, r.text
    st = r.json()
    for name in ("Harbor", "Smith", "harbor", "smith"):
        assert name not in r.text
    assert st["can_disconnect"] is False and "companies" not in st
    assert "count_others" not in st and "code_needs" not in st
    r = cli.post("/api/switchboard/disconnect")
    assert r.status_code == 403, r.text
    assert len(_record()["companies"]) == 3


def test_the_person_who_added_a_locked_company_may_still_disconnect(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    monkeypatch.setattr(link, "_locked", lambda f: f == "lake.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_disconnect"] is True
    assert [c["state"] for c in st["companies"]] == ["ready", "ready", "locked"]


# -- Take off and Add say what was done ----------------------------------------------


def test_take_off_that_cannot_be_written_changes_nothing(three, relay, monkeypatch):
    jdoe = _connected_with(three, relay, "smith.db")

    def cannot_write(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(link, "_save", cannot_write)
    r = jdoe.post("/api/switchboard/take-off", json={"file": "harbor.db"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.NOT_WRITTEN
    assert [t.active for t in _rows(three, "harbor.db")] == [True]
    assert [e["file"] for e in _record()["companies"]] == ["harbor.db", "smith.db"]


def test_a_full_connection_says_it_is_full(three, relay, monkeypatch):
    jdoe = _connected_with(three, relay)
    monkeypatch.setattr(link, "MAX_COMPANIES", 1)
    _switch(jdoe, "smith.db")
    full = (
        "The AI assistant connection already reaches 1 companies, the most it can. "
        "Nothing was done."
    )
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_add"] is False and st["sentence"] == full
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 409 and r.json()["detail"] == full
    assert "data folder" not in r.text


@pytest.mark.parametrize(
    "sentence",
    [
        link.ALREADY,
        link.ALREADY_ON,
        link.NOT_ON,
        link.NOT_ADMIN_THERE,
        link.NOT_A_MANAGER,
        link.EDIT_BUSY,
        link.DAMAGED_ELSEWHERE,
    ],
)
def test_a_refusal_says_nothing_was_done(sentence):
    assert "Nothing was done." in sentence or "nothing was done" in sentence


def test_refusals_shared_with_the_panel_say_nothing_was_done_when_raised(three, relay):
    _connected_with(three, relay, "smith.db")
    cli = _signed_in(three, "cli", company="lake.db")
    r = cli.post("/api/switchboard/add")
    assert r.status_code == 403
    assert r.json()["detail"].endswith(" Nothing was done.")


def test_renaming_refusals_say_nothing_was_changed():
    from app.main import LINK_TOKEN_ADDRESS_REFUSAL
    from app.routes import api_tokens

    for sentence in (
        api_tokens.LINK_LABEL_LOCKED,
        api_tokens.LINK_LABEL_TAKEN,
        api_tokens.AI_LABEL_KEPT,
    ):
        assert sentence.endswith(" Nothing was changed.")
    assert LINK_TOKEN_ADDRESS_REFUSAL.endswith(" Nothing was done.")

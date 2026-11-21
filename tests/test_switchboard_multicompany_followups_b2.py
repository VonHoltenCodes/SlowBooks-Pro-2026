# The fixtures three and relay are imported, so every test that takes one
# names it again as an argument.
# ruff: noqa: F811
"""One AI assistant connection, every company it was granted: the edges.

- A 401 ends a company's place on the connection only when the gate turned
  that token away (it says so in X-SlowBooks-Token-Refused); a sign-in
  route's or a webhook's own 401 is a refusal of that request alone, and
  the tools do not send to the sign-in routes at all.
- A request with no sign-in that carries a token or a company header is
  answered by the header's rules, whatever company its cookie once chose.
- The names the connection gives are the companies' names now.
- A token reads only its own company from the sign-in status too, and no
  usernames.
- A company that is served but cannot be opened is skipped by Disconnect,
  like one that is not served, and the panel says why a code is not
  offered.
- Only an administrator of a company takes it off; anyone else hears the
  same sentence whether it is on the connection or not.
- A company whose token was turned off is added again in place, and the
  sentences say who can do it.
- On Server Edition the manager rule holds however many companies are
  listed.
- The connection's tokens are known by their hash in the kept record, not
  only by their name, and a token named for an AI assistant works only
  while its maker is an administrator there.
- On a server, a company's books are restored only from its own backups.

Every password and token is made at run time with `secrets`.
"""

import json
import secrets

from fastapi.testclient import TestClient

import app.database as db_module
from app.main import app
from app.services import served_companies
from app.services import switchboard_link as link
from app.switchboard.server import build_registry
from tests.test_switchboard_connect_1087 import _THIS_COMPUTER
from tests.test_switchboard_multicompany_b2 import (  # noqa: F401  (fixtures)
    COMPANIES,
    _connected_with,
    _record,
    _rows,
    _serve,
    _set_role,
    _signed_in,
    _sql,
    _switch,
    relay,
    three,
)

LAN = ("192.168.1.50", 50000)
REFUSED_HEADER = "X-SlowBooks-Token-Refused"


def _bot(secret, company=None, client=_THIS_COMPUTER):
    bot = TestClient(app, client=client)
    bot.headers["Authorization"] = f"Bearer {secret}"
    if company:
        bot.headers["X-SlowBooks-Company"] = company
    return bot


def _hand_token(company_file, label, created_by="jdoe", role="bookkeeper"):
    from app.routes.api_tokens import issue_token

    with link._in_company(company_file) as db:
        row, secret = issue_token(db, label=label, role=role, created_by=created_by)
        return row.id, secret


def _last(result) -> str:
    return result["content"][-1]["text"]


# -- a 401 the gate did not give ---------------------------------------------------


def test_the_gate_says_when_it_turned_a_token_away(three, relay):
    _connected_with(three, relay)
    harbor = _record()["companies"][0]
    ok = _bot(harbor["token"], "harbor.db").get("/api/whoami")
    assert ok.status_code == 200 and REFUSED_HEADER not in ok.headers
    unknown = _bot("sbp_" + secrets.token_urlsafe(32), "harbor.db").get("/api/whoami")
    assert unknown.status_code == 401 and unknown.headers[REFUSED_HEADER] == "1"
    # a sign-in route's own 401 is not the gate's
    login = _bot(harbor["token"], "harbor.db").post(
        "/api/auth/login",
        json={"username": "nobody", "password": secrets.token_urlsafe(12)},
    )
    assert login.status_code == 401 and REFUSED_HEADER not in login.headers
    _set_role(three, "harbor.db", "jdoe", "bookkeeper")
    gone = _bot(harbor["token"], "harbor.db").get("/api/whoami")
    assert gone.status_code == 401 and gone.headers[REFUSED_HEADER] == "1"


def test_a_401_that_is_not_the_gates_keeps_the_company_on(three, relay, monkeypatch):
    _connected_with(three, relay, "smith.db")
    smith = _record()["companies"][1]
    registry = build_registry(link.LinkBooks())
    result = registry.call(
        "post_operation",
        {
            "books": smith["books"],
            "path": "/api/auth/login",
            "body": {"username": "nobody", "password": secrets.token_urlsafe(12)},
        },
    )
    assert result["isError"] is True
    assert "sign in" in _last(result)
    # an answer of 401 from a route the gate lets through is that route's
    sent = []
    real = link._books_transport

    def answers_401(entry):
        def send(method, path, params, body, headers):
            sent.append(path)
            if path == "/api/customers":
                return 401, {"detail": "Signature does not match"}, {}
            return real(entry)(method, path, params, body, headers)

        return send

    monkeypatch.setattr(link, "_books_transport", answers_401)
    registry = build_registry(link.LinkBooks())
    result = registry.call(
        "list_records", {"books": smith["books"], "kind": "customers"}
    )
    assert result["isError"] is True and "no longer accepts" not in str(result)
    kept = _record()["companies"][1]
    assert kept["off"] == "" and kept["token"] == smith["token"]
    assert [t.active for t in _rows(three, "smith.db")] == [True]


# -- the cookie of a request with no sign-in -------------------------------------------


def test_a_token_request_follows_its_header_not_a_cookies_choice(three):
    _id, secret = _hand_token("smith.db", "probe script")
    bot = _bot(secret)
    r = bot.post("/api/auth/company", json={"file": "smith.db"})
    assert r.status_code == 200 and r.json()["authenticated"] is False
    # the header names another company: its token is looked up there
    r = bot.get("/api/whoami", headers={"X-SlowBooks-Company": "lake.db"})
    assert r.status_code == 401, r.text
    r = bot.get("/api/whoami", headers={"X-SlowBooks-Company": "bogus/../x"})
    assert r.status_code == 421, r.text
    # no header is the company the server was started for
    assert bot.get("/api/whoami").status_code == 401
    r = bot.get("/api/whoami", headers={"X-SlowBooks-Company": "smith.db"})
    assert r.status_code == 200 and r.json()["company_file"] == "smith.db"
    # a person signing in still has the company the cookie chose
    person = TestClient(app)
    assert person.post("/api/auth/company", json={"file": "smith.db"}).json()
    assert person.get("/api/auth/status").json()["company_name"] == "Smith Plumbing"


# -- names as they are now -------------------------------------------------------------


def test_a_renamed_company_is_named_as_it_is_now(three, relay):
    jdoe = _connected_with(three, relay, "smith.db")
    smith = _record()["companies"][1]
    _switch(jdoe, "smith.db")
    r = jdoe.put("/api/settings", json={"company_name": "Smith Plumbing (closed)"})
    assert r.status_code == 200, r.text
    assert any(
        row["name"] == "Smith Plumbing (closed)" for row in served_companies.listed()
    )
    registry = build_registry(link.LinkBooks())
    overview = json.loads(registry.call("context", {})["content"][0]["text"])
    assert [b["name"] for b in overview["books"]] == [
        "Harbor Books",
        "Smith Plumbing (closed)",
    ]
    result = registry.call("context", {"books": smith["books"]})
    assert result["content"][0]["text"] == (
        f"Books: Smith Plumbing (closed) ({smith['books']})."
    )
    assert json.loads(result["content"][1]["text"])["company"] == (
        "Smith Plumbing (closed)"
    )
    # the old name is no longer one of the connection's names
    result = registry.call("list_records", {"books": "Smith Plumbing"})
    assert "Name the books by their handle" not in _last(result)
    _switch(jdoe, "harbor.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert [c["name"] for c in st["companies"]] == [
        "Harbor Books",
        "Smith Plumbing (closed)",
    ]
    r = jdoe.post("/api/switchboard/disconnect")
    assert [x["name"] for x in r.json()["results"]] == [
        "Harbor Books",
        "Smith Plumbing (closed)",
    ]


# -- the sign-in status and a token --------------------------------------------------------


def test_a_token_reads_only_its_own_company_from_the_sign_in_status(three, relay):
    _connected_with(three, relay)
    harbor = _record()["companies"][0]
    st = _bot(harbor["token"], "harbor.db").get("/api/auth/status").json()
    assert [c["file"] for c in st["companies"]] == ["harbor.db"]
    assert st.get("usernames", []) == []
    for name in ("Smith", "Lake", "jdoe", "kim"):
        assert name not in json.dumps(st)
    registry = build_registry(link.LinkBooks())
    result = registry.call("api_get", {"path": "/api/auth/status"})
    assert "Smith" not in str(result) and "jdoe" not in str(result)
    # the sign-in screen itself still offers every company it serves
    st = TestClient(app).get("/api/auth/status").json()
    assert [c["file"] for c in st["companies"]] == [f for f, _ in COMPANIES]


# -- a company that is served and cannot be opened ---------------------------------------------


def _behind(company_file):
    """The company file as a build this one does not know left it."""
    _sql(
        company_file,
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)",
    )
    _sql(company_file, "DELETE FROM alembic_version")
    _sql(company_file, "INSERT INTO alembic_version VALUES ('ffffffffffff')")
    db_module.drop_company_engines({company_file})


def test_a_company_that_cannot_be_opened_is_skipped_by_disconnect(three, relay):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    _behind("smith.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert [c["state"] for c in st["companies"]] == ["ready", "locked", "ready"]
    assert st["can_disconnect"] is True and st["can_code"] is False
    assert st["code_needs"] == ["Smith Plumbing"]
    assert jdoe.post("/api/switchboard/code").status_code == 403
    r = jdoe.post("/api/switchboard/disconnect")
    assert r.status_code == 200, r.text
    assert [(x["file"], x["result"]) for x in r.json()["results"]] == [
        ("harbor.db", "ended"),
        ("smith.db", "later"),
        ("lake.db", "ended"),
    ]


def test_a_locked_company_says_why_to_the_assistant(three, relay):
    from app.services.migration_floor import NEWER_VERSION_MESSAGE
    from app.services.served_companies import CompanyUnavailable, sentence_of

    assert sentence_of(CompanyUnavailable(NEWER_VERSION_MESSAGE)) == (
        NEWER_VERSION_MESSAGE
    )


# -- take off: who, and what they learn ---------------------------------------------------------


def test_take_off_needs_that_companys_administrator_and_tells_no_one_else(three, relay):
    _connected_with(three, relay, "smith.db")
    cli = _signed_in(three, "cli", company="lake.db")
    on = cli.post("/api/switchboard/take-off", json={"file": "smith.db"})
    off = cli.post("/api/switchboard/take-off", json={"file": "acme.db"})
    assert (on.status_code, off.status_code) == (403, 403)
    assert on.json() == off.json() == {"detail": link.NOT_ADMIN_THERE}
    assert len(_record()["companies"]) == 2


# -- a company whose token was turned off is added again ---------------------------------------


def test_a_turned_off_company_is_added_again_in_place(three, relay):
    jdoe = _connected_with(three, relay)
    old = _record()["companies"][0]
    registry = build_registry(link.LinkBooks())
    _set_role(three, "harbor.db", "jdoe", "bookkeeper")
    result = registry.call("context", {})
    assert result["isError"] is True
    assert _last(result) == link.BOOKS_LOST.format(name="Harbor Books")
    assert _record()["companies"][0]["off"] == "refused"
    _set_role(three, "harbor.db", "jdoe", "admin")
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_add"] is True and st["on_link"] is True
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 200, r.text
    assert r.json()["results"] == [
        {"file": "harbor.db", "name": "Harbor Books", "result": "added"}
    ]
    entry = _record()["companies"][0]
    assert entry["off"] == "" and entry["token"]
    assert entry["books"] != old["books"]
    rows = _rows(three, "harbor.db")
    assert [(t.hash == old["token_sha256"], t.active) for t in rows] == [
        (True, False),
        (False, True),
    ]
    result = build_registry(link.LinkBooks()).call("context", {})
    assert result["isError"] is False, result


def test_the_turned_off_sentence_says_who_adds_it_again(three, relay):
    _connected_with(three, relay, "smith.db", "lake.db")
    assert "every other company on this connection" in link.BOOKS_LOST
    _set_role(three, "lake.db", "jdoe", "bookkeeper")
    lake = _record()["companies"][2]
    result = build_registry(link.LinkBooks()).call(
        "list_records", {"books": lake["books"], "kind": "customers"}
    )
    assert _last(result) == link.BOOKS_LOST.format(name="Lake Dental")
    # Lake's own administrator, who is not one of the others, is told the
    # same rule by the panel and the press
    cli = _signed_in(three, "cli", company="lake.db")
    st = cli.get("/api/switchboard/status").json()
    assert st["can_add"] is False
    assert cli.post("/api/switchboard/add").status_code == 403


# -- Server Edition with one company listed ------------------------------------------------------


def test_the_manager_rule_holds_with_one_company_listed(three, relay):
    _connected_with(three, relay, "smith.db")
    _set_role(three, "harbor.db", "kim", "admin")
    _serve(three, ["harbor.db"])
    kim = _signed_in(three, "kim")
    st = kim.get("/api/switchboard/status").json()
    # Disconnect stays hers (an administrator of every company that can be
    # opened now), and a code, which would reach Smith when it is listed
    # again, does not
    assert st["can_code"] is False and st["code_needs"] == ["Smith Plumbing"]
    assert st["can_disconnect"] is True
    assert kim.post("/api/switchboard/code").status_code == 403
    assert relay.codes == []


# -- the connection's tokens, known by their hash -----------------------------------------------


def test_a_renamed_connection_token_is_still_the_connections(three, relay):
    """A token beta.1 let be renamed is still the connection's: it ends when
    its maker is no longer an administrator, and works only from here."""
    _connected_with(three, relay)
    harbor = _record()["companies"][0]
    _sql(
        "harbor.db",
        "UPDATE api_tokens SET label = 'Books assistant' WHERE id = ?",
        harbor["token_id"],
    )
    r = _bot(harbor["token"], "harbor.db", client=LAN).get("/api/whoami")
    assert r.status_code == 401
    assert r.json()["detail"] == (
        "This token works only for the AI assistant connection on this computer."
    )
    assert _bot(harbor["token"], "harbor.db").get("/api/whoami").status_code == 200
    _set_role(three, "harbor.db", "kim", "admin")
    _sql("harbor.db", "UPDATE users SET is_active = 0 WHERE username = 'jdoe'")
    r = _bot(harbor["token"], "harbor.db").get("/api/whoami")
    assert r.status_code == 401 and r.headers[REFUSED_HEADER] == "1"


def test_an_ai_assistant_token_ends_with_its_maker(three):
    """A token named for an AI assistant (Switchboard's, by its name) works
    only while the administrator who made it is one there; any other token
    is not tied to its maker, as before."""
    _id, bound = _hand_token("smith.db", "AI assistant (Switchboard)")
    _id2, plain = _hand_token("smith.db", "office script")
    r = _bot(bound, "smith.db", client=LAN).get("/api/whoami")
    assert r.status_code == 200, r.text
    assert r.json()["ends_with_maker"] is True
    assert _bot(plain, "smith.db").get("/api/whoami").json()["ends_with_maker"] is False
    _sql("smith.db", "UPDATE users SET role = 'bookkeeper' WHERE username = 'jdoe'")
    r = _bot(bound, "smith.db", client=LAN).get("/api/whoami")
    assert r.status_code == 401 and r.headers[REFUSED_HEADER] == "1"
    assert _bot(plain, "smith.db").get("/api/whoami").status_code == 200


def test_an_ai_assistant_token_keeps_its_name(three):
    token_id, _secret = _hand_token("harbor.db", "AI assistant (Switchboard)")
    other_id, _other = _hand_token("harbor.db", "office script")
    jdoe = _signed_in(three, "jdoe")
    r = jdoe.put(f"/api/tokens/{token_id}", json={"label": "Office script 2"})
    assert r.status_code == 400, r.text
    # a name may become one, which ties it to its maker
    r = jdoe.put(f"/api/tokens/{other_id}", json={"label": "AI assistant (laptop)"})
    assert r.status_code == 200, r.text


# -- the next token's name, and the panel's reasons ------------------------------------------------


def test_add_names_the_token_it_will_make(three, relay):
    jdoe = _connected_with(three, relay)
    _hand_token("smith.db", "AI assistant (relay)")  # left by a beta.1 disconnect
    _switch(jdoe, "smith.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_add"] is True and st["next_label"] == "AI assistant (relay) 2"
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 200
    assert _record()["companies"][1]["token_label"] == "AI assistant (relay) 2"


def test_the_panel_names_what_stops_a_code(three, relay):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    _serve(three, ["harbor.db", "smith.db"])
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_code"] is False and st["code_needs"] == ["Lake Dental"]
    _serve(three, [f for f, _ in COMPANIES])
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_code"] is True and st["code_needs"] == []


# -- restoring another company's backup on a server -----------------------------------------------


def test_a_server_restores_a_company_only_from_its_own_backups(three):
    jdoe = _signed_in(three, "jdoe")
    _switch(jdoe, "smith.db")
    made = jdoe.post("/api/backups")
    assert made.status_code == 200, made.text
    filename = made.json()["filename"]
    cli = _signed_in(three, "cli", company="lake.db")
    assert filename not in [b["filename"] for b in cli.get("/api/backups").json()]
    r = cli.post(
        "/api/backups/restore",
        json={"filename": filename, "allow_other_company": True},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "other_company_server"
    assert cli.get("/api/settings").json()["company_name"] == "Lake Dental"

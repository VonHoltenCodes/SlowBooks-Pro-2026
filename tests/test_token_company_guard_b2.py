"""The guards a token's request passes before one AI assistant connection can
reach several companies' books.

- A request with no sign-in whose X-SlowBooks-Company header is repeated,
  blank, not a company file's name, not on the list, or not served now is
  refused with 421 before its token is looked up, with a sentence that
  names no company. It used to fall back to the company the server was
  started for.
- Every answer to a token names the company it came from in
  X-SlowBooks-Company-Served; a signed-in person's answers do not carry it.
- GET /api/whoami says which company, role and token a request carries. It
  needs a sign-in or a token, unlike /api/auth/.
- A token reads only its own company from GET /api/companies.
- The AI assistant connection's tokens work only from this computer's own
  address, and only while the administrator who made them still is one in
  that company.
- No token can take the connection's name, and the connection's tokens
  cannot be renamed.
"""

import secrets
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.database as db_module
import app.main as main_module
from app.main import app
from app.models.users import User
from app.routes.api_tokens import issue_token
from app.services import api_token_service, served_companies
from app.services import switchboard_link
from tests.test_served_companies import (  # noqa: F401  (the fixture and helpers)
    A_PW,
    B_PW,
    _sign_in,
    served as served_fixture,
)

UNSERVED = "This server does not serve that company for this request. Nothing was done."
LAN_ONLY = (
    "This token works only for the AI assistant connection on this computer. "
    "Nothing was done."
)
LINK = api_token_service.LINK_TOKEN_LABEL
LOOPBACK = ("127.0.0.1", 50000)
LAN = ("192.168.1.50", 50000)
HEADER = "X-SlowBooks-Company"
SERVED = "X-SlowBooks-Company-Served"


def _token(company, label, role="bookkeeper", created_by="pat"):
    """A token made in a company's own database: the secret."""
    with db_module.use_company(company):
        with db_module.SessionLocal() as db:
            _, secret = issue_token(db, label=label, role=role, created_by=created_by)
    return secret


def _set_user(company, username, **values):
    with db_module.use_company(company):
        with db_module.SessionLocal() as db:
            db.query(User).filter(User.username == username).update(values)
            db.commit()


def _link_itself() -> dict:
    """The header the connection's own requests carry."""
    return {
        api_token_service.LINK_ORIGIN_HEADER: api_token_service.link_origin_secret()
    }


def _bot(secret, client=LOOPBACK, company=None, as_the_link=True):
    """A program with the token; by default it is the connection itself,
    sending the connection's own header."""
    bot = TestClient(app, client=client)
    bot.headers["Authorization"] = f"Bearer {secret}"
    if as_the_link:
        bot.headers.update(_link_itself())
    if company:
        bot.headers[HEADER] = company
    return bot


@pytest.fixture
def served(served_fixture):  # noqa: F811
    """The Server Edition host with two companies, started for alpha.db."""
    return served_fixture


@pytest.fixture
def no_lookup(monkeypatch):
    """Record every token lookup the gate makes."""
    calls = []

    def resolve(bearer):
        calls.append(bearer)
        return None

    monkeypatch.setattr(main_module, "_resolve_api_token", resolve)
    return calls


# -- the company header -----------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        [(HEADER, "bravo.db"), (HEADER, "bravo.db")],  # repeated
        [(HEADER, "alpha.db"), (HEADER, "bravo.db")],
        [(HEADER, "")],  # blank
        [(HEADER, "   ")],
        [(HEADER, "../bravo.db")],  # not a company file's name
        [(HEADER, "companies/bravo.db")],
        [(HEADER, ".hidden.db")],
        [(HEADER, "charlie.db")],  # not on the list
    ],
)
def test_a_header_the_server_cannot_honor_is_refused_unread(served, no_lookup, headers):
    secret = "sbp_" + secrets.token_urlsafe(32)
    r = TestClient(app).get(
        "/api/customers", headers=[("Authorization", f"Bearer {secret}"), *headers]
    )
    assert r.status_code == 421
    assert r.json() == {"detail": UNSERVED}
    for name in ("Alpha", "Bravo", "alpha.db", "bravo.db"):
        assert name not in r.text
    assert SERVED not in r.headers
    assert no_lookup == []


def test_a_listed_company_not_served_now_is_refused(served, no_lookup, monkeypatch):
    # one company served at a time (a desktop): the other is on the list but
    # not served by this process
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE")
    secret = "sbp_" + secrets.token_urlsafe(32)
    r = _bot(secret, company="bravo.db").get("/api/customers")
    assert (r.status_code, r.json()) == (421, {"detail": UNSERVED})
    assert no_lookup == []


def test_the_desktop_honors_only_its_own_company(served, monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE")
    secret = _token("alpha.db", "office script", created_by="pat")
    assert _bot(secret, company="bravo.db").get("/api/customers").status_code == 421
    r = _bot(secret, company="alpha.db").get("/api/customers")
    assert r.status_code == 200, r.text
    assert r.headers[SERVED] == "alpha.db"
    # no header is the company the server was started for, as before
    assert _bot(secret).get("/api/customers").status_code == 200


def test_a_served_company_is_reached_by_its_header(served):
    secret = _token("bravo.db", "pantry script", created_by="lee")
    r = _bot(secret, company="bravo.db").get("/api/settings")
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == "Bravo Food Pantry"
    assert r.headers[SERVED] == "bravo.db"
    # its token is not Alpha's, so without the header it is not found
    assert _bot(secret).get("/api/settings").status_code == 401


def test_a_signed_in_person_ignores_the_header(served):
    client = TestClient(app)
    assert _sign_in(client, "pat", A_PW).status_code == 200
    for value in ("charlie.db", "", "../x.db"):
        r = client.get("/api/settings", headers={HEADER: value})
        assert r.status_code == 200, (value, r.text)
        assert r.json()["company_name"] == "Alpha Hardware"
        assert SERVED not in r.headers


# -- the served header --------------------------------------------------------------


def test_every_token_answer_names_its_company_and_no_session_answer_does(served):
    secret = _token("alpha.db", "office script", role="readonly", created_by="pat")
    bot = _bot(secret, company="alpha.db")
    ok = bot.get("/api/customers")
    refused = bot.post("/api/customers", json={"name": "x"})  # read-only
    missing = bot.get("/api/customers/999999")
    assert (ok.status_code, refused.status_code, missing.status_code) == (
        200,
        403,
        404,
    )
    for r in (ok, refused, missing):
        assert r.headers[SERVED] == "alpha.db"
    person = TestClient(app)
    assert _sign_in(person, "pat", A_PW).status_code == 200
    for r in (person.get("/api/customers"), person.get("/api/whoami")):
        assert r.status_code == 200
        assert SERVED not in r.headers
    # a secret that opens nothing is not told which company it reached
    r = _bot("sbp_" + secrets.token_urlsafe(32)).get("/api/customers")
    assert r.status_code == 401 and SERVED not in r.headers


# -- who am I -----------------------------------------------------------------------


def test_whoami_says_which_company_role_and_token(served):
    secret = _token("bravo.db", "pantry script", role="readonly", created_by="lee")
    r = _bot(secret, company="bravo.db").get("/api/whoami")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "company_file": "bravo.db",
        "company_name": "Bravo Food Pantry",
        "role": "readonly",
        "label": "pantry script",
        "ends_with_maker": False,
    }
    assert r.headers[SERVED] == "bravo.db"
    person = TestClient(app)
    assert _sign_in(person, "lee", B_PW, company="bravo.db").status_code == 200
    assert person.get("/api/whoami").json() == {
        "company_file": "bravo.db",
        "company_name": "Bravo Food Pantry",
        "role": "admin",
        "label": None,
        "ends_with_maker": False,
    }


def test_whoami_needs_a_sign_in_or_a_token(served):
    assert TestClient(app).get("/api/whoami").status_code == 401
    r = _bot("sbp_" + secrets.token_urlsafe(32)).get("/api/whoami")
    assert r.status_code == 401
    # the probe is not under /api/auth/, which answers anyone
    assert not any(
        "/api/whoami".startswith(p) for p in main_module._AUTH_EXEMPT_PREFIXES
    )
    secret = _token("alpha.db", "office script", created_by="pat")
    assert _bot(secret).get("/api/auth/whoami").status_code == 404


# -- the companies list -------------------------------------------------------------


def test_a_token_reads_only_its_own_company_from_the_list(served):
    secret = _token("bravo.db", "pantry script", created_by="lee")
    r = _bot(secret, company="bravo.db").get("/api/companies")
    assert r.status_code == 200, r.text
    assert [(c["file"], c["name"]) for c in r.json()] == [
        ("bravo.db", "Bravo Food Pantry")
    ]
    assert "Alpha" not in r.text
    person = TestClient(app)
    assert _sign_in(person, "pat", A_PW).status_code == 200
    assert [c["file"] for c in person.get("/api/companies").json()] == [
        "alpha.db",
        "bravo.db",
    ]


# -- the connection's tokens: from this computer only --------------------------------


def test_the_label_is_the_connections_own():
    assert switchboard_link.TOKEN_LABEL == LINK


def test_a_connection_token_from_the_network_is_refused(served):
    link = _token("alpha.db", LINK, created_by="pat")
    other = _token("alpha.db", "office script", created_by="pat")
    r = _bot(link, client=LAN).get("/api/customers")
    assert (r.status_code, r.json()) == (401, {"detail": LAN_ONLY})
    assert SERVED not in r.headers
    assert _bot(link, client=LOOPBACK).get("/api/customers").status_code == 200
    assert _bot(link, client=("::1", 50000)).get("/api/customers").status_code == 200
    # a numbered one is the connection's too
    second = _token("alpha.db", LINK + " 2", created_by="pat")
    assert _bot(second, client=LAN).get("/api/customers").status_code == 401
    # any other token keeps working from the network, as before
    assert _bot(other, client=LAN).get("/api/customers").status_code == 200


def test_the_address_the_app_listens_on_counts_as_its_own(served, monkeypatch):
    """With APP_HOST one network address, the connection calls the app
    there, so that address is the app's own."""
    monkeypatch.setenv("APP_HOST", LAN[0])
    link = _token("alpha.db", LINK, created_by="pat")
    assert _bot(link, client=LAN).get("/api/customers").status_code == 200
    r = _bot(link, client=("192.168.1.77", 50000)).get("/api/customers")
    assert r.status_code == 401


def test_a_connection_token_through_a_forward_on_this_computer_is_refused(served):
    """A tunnel or a plain TCP forward on this computer that adds no
    X-Forwarded-For delivers an outside request from loopback. Only the
    connection's own header lets a connection token in; a program without
    it is refused with the same sentence, from every address."""
    link = _token("alpha.db", LINK, created_by="pat")
    for client in (LOOPBACK, ("::1", 50000), ("::ffff:127.0.0.1", 50000), LAN):
        r = _bot(link, client=client, as_the_link=False).get("/api/customers")
        assert (r.status_code, r.json()) == (401, {"detail": LAN_ONLY}), client
        assert SERVED not in r.headers
    r = _bot(link, as_the_link=False).post(
        "/api/customers", json={"name": "Quillfeather Bakery"}
    )
    assert (r.status_code, r.json()) == (401, {"detail": LAN_ONLY})
    # a wrong, empty or repeated value is no better than none
    key = api_token_service.LINK_ORIGIN_HEADER
    right = api_token_service.link_origin_secret()
    for headers in (
        [(key, "0" * len(right))],
        [(key, right[:-1])],
        [(key, "")],
        [(key, right), (key, right)],
    ):
        bot = _bot(link, as_the_link=False)
        r = bot.get("/api/customers", headers=headers)
        assert (r.status_code, r.json()) == (401, {"detail": LAN_ONLY}), headers
    # the connection itself, from this app's own address, is let in
    assert _bot(link).get("/api/customers").status_code == 200
    assert _bot(link, client=("::1", 50000)).get("/api/customers").status_code == 200


def test_the_connections_header_does_not_open_the_network(served):
    """The header adds to the address rule, it does not replace it: with
    the right value from another computer a connection token is still
    refused (the value never leaves this computer; if it ever did, the
    address rule still holds where no forward is in between)."""
    link = _token("alpha.db", LINK, created_by="pat")
    r = _bot(link, client=LAN).get("/api/customers")
    assert (r.status_code, r.json()) == (401, {"detail": LAN_ONLY})


def test_a_hand_made_token_ignores_the_connections_header(served):
    other = _token("alpha.db", "office script", created_by="pat")
    switchboard = _token("alpha.db", "AI assistant (Switchboard)", created_by="pat")
    for secret in (other, switchboard):
        for client in (LOOPBACK, LAN):
            for as_the_link in (True, False):
                r = _bot(secret, client=client, as_the_link=as_the_link).get(
                    "/api/customers"
                )
                assert r.status_code == 200, (secret is other, client, as_the_link)


def test_the_header_value_is_the_same_in_every_process_of_a_server():
    """Docker runs two uvicorn workers and the line runs in one of them:
    the value comes from the session key all of a server's processes
    share, not from one process's start."""
    import hashlib
    import hmac

    from app.services.auth import get_session_secret

    expected = hmac.new(
        get_session_secret().encode("utf-8"),
        api_token_service._LINK_ORIGIN_CONTEXT,
        hashlib.sha256,
    ).hexdigest()
    assert api_token_service.link_origin_secret() == expected
    assert expected != get_session_secret() and len(expected) == 64


def test_the_header_value_is_never_answered_logged_or_audited(served, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    right = api_token_service.link_origin_secret()
    link = _token("alpha.db", LINK, created_by="pat")
    bot = _bot(link)
    answers = [
        bot.get("/api/whoami"),
        bot.get("/api/customers"),
        bot.post("/api/customers", json={"name": "Quillfeather Bakery"}),
        bot.get("/api/auth/status"),
        _bot(link, as_the_link=False).get("/api/customers"),
        _bot(link, client=LAN).get("/api/customers"),
    ]
    assert answers[2].status_code in (200, 201), answers[2].text
    for r in answers:
        assert right not in r.text
        assert all(right not in v for v in r.headers.values())
    assert right not in caplog.text
    with db_module.use_company("alpha.db"):
        with db_module.SessionLocal() as db:
            from app.models.audit import AuditLog

            rows = db.query(AuditLog).all()
            assert rows, "the customer's creation is audited"
            assert all(right not in repr(vars(row)) for row in rows)


def test_the_connections_own_transport_sends_the_header(monkeypatch):
    """switchboard_link._books_transport, the real one (the other tests
    stand an in-process one in for it), sends the header with this
    server's value to this app's own address, and a header of that name
    handed to it from elsewhere is replaced, never passed on."""
    import http.server
    import threading

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get_all(api_token_service.LINK_ORIGIN_HEADER))
            body = b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("APP_HOST", "0.0.0.0")
        monkeypatch.setenv("APP_PORT", str(server.server_address[1]))
        send = switchboard_link._books_transport(
            {"token": "sbp_" + secrets.token_urlsafe(24), "file": "alpha.db"}
        )
        status, _payload, _headers = send(
            "GET",
            "/api/customers",
            None,
            None,
            {api_token_service.LINK_ORIGIN_HEADER.lower(): "planted"},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert seen == [[api_token_service.link_origin_secret()]]


# -- the connection's tokens: only while their maker is an administrator -------------


def _maker_gone(label):
    return {
        "detail": (
            f'The API token "{label}" stopped working because the administrator '
            "who made it is no longer an administrator of this company. Nothing "
            "was done. An administrator can make a new one under Settings, API "
            "Tokens."
        )
    }


@pytest.mark.parametrize(
    "change, restore",
    [
        ({"is_active": False}, {"is_active": True}),
        ({"role": "bookkeeper"}, {"role": "admin"}),
        (
            {"expires_on": date.today() - timedelta(days=1)},
            {"expires_on": date.today() + timedelta(days=30)},
        ),
    ],
    ids=["deactivated", "demoted", "expired"],
)
def test_a_connection_token_ends_with_its_makers_admin(served, change, restore):
    link = _token("bravo.db", LINK, created_by="lee")
    other = _token("bravo.db", "pantry script", created_by="lee")
    bot = _bot(link, company="bravo.db")
    assert bot.get("/api/customers").status_code == 200
    _set_user("bravo.db", "lee", **change)
    r = bot.get("/api/customers")
    assert (r.status_code, r.json()) == (401, _maker_gone(LINK))
    # a token made by hand is a program's, not its maker's: it keeps working
    assert _bot(other, company="bravo.db").get("/api/customers").status_code == 200
    # the row says what it is now, wherever that came from: a restore, an
    # edit, a change under Settings, Users
    _set_user("bravo.db", "lee", **restore)
    assert bot.get("/api/customers").status_code == 200


def test_a_connection_token_whose_maker_is_gone_is_refused(served):
    link = _token("alpha.db", LINK, created_by="nobody-here")
    r = _bot(link).get("/api/customers")
    assert (r.status_code, r.json()) == (401, _maker_gone(LINK))
    # the maker's admin is the company's own: pat is Alpha's administrator
    # and only a bookkeeper in Bravo
    in_bravo = _token("bravo.db", LINK, created_by="pat")
    assert _bot(in_bravo, company="bravo.db").get("/api/customers").status_code == 401


def test_the_maker_is_found_whatever_the_letter_case(served):
    link = _token("alpha.db", LINK, created_by="Pat")
    assert _bot(link).get("/api/customers").status_code == 200


def test_a_company_with_no_named_users_keeps_its_operators_token(served):
    with db_module.use_company("alpha.db"):
        with db_module.SessionLocal() as db:
            db.query(User).delete()
            db.commit()
    link = _token("alpha.db", LINK, created_by="operator")
    assert _bot(link).get("/api/customers").status_code == 200
    other = _token("alpha.db", LINK + " 2", created_by="someone")
    assert _bot(other).get("/api/customers").status_code == 401


# -- the connection's name ---------------------------------------------------------


def test_no_token_takes_the_connections_name_and_its_own_keep_theirs(served):
    admin = TestClient(app)
    assert _sign_in(admin, "pat", A_PW).status_code == 200
    taken = (
        "That name belongs to the AI assistant connection's tokens. Choose another "
        "name. Nothing was changed."
    )
    for label in (LINK, LINK + " 2", "ai assistant (relay) mine", "  " + LINK):
        r = admin.post("/api/tokens", json={"label": label, "role": "bookkeeper"})
        assert (r.status_code, r.json()) == (400, {"detail": taken}), label
    mine = admin.post(
        "/api/tokens", json={"label": "office script", "role": "bookkeeper"}
    ).json()
    r = admin.put(f"/api/tokens/{mine['id']}", json={"label": LINK})
    assert (r.status_code, r.json()) == (400, {"detail": taken})

    _token("alpha.db", LINK, created_by="pat")
    row = next(t for t in admin.get("/api/tokens").json() if t["label"] == LINK)
    for label in ("Assistant on the office phone", LINK + " 2"):
        r = admin.put(f"/api/tokens/{row['id']}", json={"label": label})
        assert r.status_code == 400
        assert r.json()["detail"] == (
            "This token belongs to the AI assistant connection; its name can't be "
            "changed. Nothing was changed."
        )
    # its own name again changes nothing and is fine; turning it off still is
    r = admin.put(f"/api/tokens/{row['id']}", json={"label": LINK})
    assert r.status_code == 200
    r = admin.put(f"/api/tokens/{row['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False


def test_the_started_company_is_unchanged_without_a_header(served):
    """A token with no header still works in the company the server was
    started for, as a beta.1 connection's does."""
    assert served_companies.started_file() == "alpha.db"
    secret = _token("alpha.db", "office script", created_by="pat")
    r = _bot(secret).get("/api/settings")
    assert r.status_code == 200
    assert r.json()["company_name"] == "Alpha Hardware"
    assert r.headers[SERVED] == "alpha.db"

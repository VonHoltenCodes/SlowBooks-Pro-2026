"""The closing-date override password works (explore 2.17.3, skytech M7).

`check_closing_date()` accepted a password, but no route passed one and no
screen asked for it, so Settings -> Closing Date -> "Password (optional)" did
nothing. A refusal inside the closed period now says, in a header, that an
override password is set; the page asks for it and sends the same change
again with X-Closing-Date-Password. A signed-in person only: an API token's
header is ignored. With no password set, the refusal is what it was.
"""

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]

# Module constants, never a literal beside a username key.
OVERRIDE_PW = "close-the-books-26"
UNICODE_PW = "Müller-Ölkännchen 26"
WRONG_PW = "not-the-password"


def _customer(client):
    r = client.post("/api/customers", json={"name": "Salt & Pine Catering"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _invoice(customer_id, day="2026-03-01"):
    return {
        "customer_id": customer_id,
        "date": day,
        "lines": [{"description": "Cake", "quantity": 1, "rate": 450}],
    }


def _close(client, password=None):
    body = {"closing_date": "2026-06-30"}
    if password is not None:
        body["closing_date_password"] = password
    r = client.put("/api/settings", json=body)
    assert r.status_code == 200, r.text


def _with_password(value):
    return {"X-Closing-Date-Password": quote(value)}


def test_a_refusal_says_a_password_can_override_it(client, seed_accounts):
    _close(client, OVERRIDE_PW)
    r = client.post("/api/invoices", json=_invoice(_customer(client)))
    assert r.status_code == 403, r.text
    assert r.headers.get("X-Closing-Date-Override") == "password"
    assert "closing date (2026-06-30)" in r.json()["detail"]
    assert "Enter the closing-date password" in r.json()["detail"]


def test_the_right_password_lets_the_change_through_and_is_recorded(
    client, seed_accounts, db_session
):
    from app.models.audit import AuditLog

    _close(client, OVERRIDE_PW)
    cid = _customer(client)
    r = client.post(
        "/api/invoices",
        json=_invoice(cid),
        headers=_with_password(OVERRIDE_PW),
    )
    assert r.status_code == 201, r.text
    assert r.json()["date"] == "2026-03-01"
    # the override itself is on the audit trail, once, without the password
    rows = db_session.query(AuditLog).filter(AuditLog.action == "OVERRIDE").all()
    assert len(rows) == 1
    assert rows[0].new_values == {
        "closing_date": "2026-06-30",
        "transaction_date": "2026-03-01",
    }
    assert OVERRIDE_PW not in json.dumps(rows[0].new_values)
    # ...and it was for that one request only
    again = client.post("/api/invoices", json=_invoice(cid))
    assert again.status_code == 403


def test_a_password_with_any_characters_works(client, seed_accounts):
    _close(client, UNICODE_PW)
    r = client.post(
        "/api/invoices",
        json=_invoice(_customer(client)),
        headers=_with_password(UNICODE_PW),
    )
    assert r.status_code == 201, r.text


def test_a_wrong_password_is_refused_and_never_echoed(client, seed_accounts):
    _close(client, OVERRIDE_PW)
    r = client.post(
        "/api/invoices",
        json=_invoice(_customer(client)),
        headers=_with_password(WRONG_PW),
    )
    assert r.status_code == 403, r.text
    assert r.headers.get("X-Closing-Date-Override") == "wrong-password"
    assert "password you entered is not correct" in r.json()["detail"]
    everything = r.text + json.dumps(dict(r.headers))
    assert WRONG_PW not in everything and OVERRIDE_PW not in everything


def test_without_a_password_set_the_refusal_is_unchanged(client, seed_accounts):
    _close(client)
    r = client.post(
        "/api/invoices",
        json=_invoice(_customer(client)),
        headers=_with_password(OVERRIDE_PW),
    )
    assert r.status_code == 403, r.text
    assert "X-Closing-Date-Override" not in r.headers
    assert r.json()["detail"] == (
        "Transaction date 2026-03-01 is on or before the closing date "
        "(2026-06-30). Modifications to closed periods are not allowed."
    )


def test_an_api_token_cannot_use_the_override(client, seed_accounts):
    _close(client, OVERRIDE_PW)
    cid = _customer(client)
    minted = client.post("/api/tokens", json={"label": "agent", "role": "admin"})
    assert minted.status_code == 201, minted.text
    agent = TestClient(app)
    agent.headers["Authorization"] = f"Bearer {minted.json()['token']}"
    r = agent.post(
        "/api/invoices", json=_invoice(cid), headers=_with_password(OVERRIDE_PW)
    )
    assert r.status_code == 403, r.text


def test_the_other_closed_period_paths_take_the_override_too(client, seed_accounts):
    """The header is read where every check reads it, not route by route:
    a journal entry posts through create_journal_entry's own check."""
    _close(client, OVERRIDE_PW)
    acct = {a["account_number"]: a["id"] for a in client.get("/api/accounts").json()}
    entry = {
        "date": "2026-05-31",
        "description": "Accrual",
        "lines": [
            {"account_id": acct["6000"], "debit": "50.00", "credit": "0"},
            {"account_id": acct["1000"], "debit": "0", "credit": "50.00"},
        ],
    }
    assert client.post("/api/journal", json=entry).status_code == 403
    r = client.post("/api/journal", json=entry, headers=_with_password(OVERRIDE_PW))
    assert r.status_code in (200, 201), r.text


def test_get_db_carries_the_password_for_a_signed_in_person_only():
    """The Session carries it (the path that holds on frozen Windows, where
    a contextvar did not); a token's request never does."""
    from starlette.requests import Request

    from app.database import get_db
    from app.services.closing_date import SESSION_INFO_KEY

    def request(session, header):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/invoices",
            "headers": [(b"x-closing-date-password", header.encode("latin-1"))],
            "session": session,
            "state": {},
        }
        return Request(scope)

    gen = get_db(request({"authenticated": True}, quote(UNICODE_PW)))
    db = next(gen)
    try:
        assert db.info[SESSION_INFO_KEY] == UNICODE_PW
    finally:
        gen.close()

    gen = get_db(request({}, quote(UNICODE_PW)))
    db = next(gen)
    try:
        assert SESSION_INFO_KEY not in db.info
    finally:
        gen.close()


# ---------------------------------------------------------------------------
# The page: api.js asks, resends, or leaves the refusal as it was.
# ---------------------------------------------------------------------------


def _probe(scenario):
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "api_request_probe.js"),
            json.dumps(scenario),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _refusal(client, cid, password=None):
    headers = _with_password(password) if password else None
    r = client.post("/api/invoices", json=_invoice(cid), headers=headers)
    assert r.status_code == 403
    return {"status": 403, "body": r.json(), "headers": dict(r.headers)}


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@needs_node
def test_the_page_asks_for_the_password_and_sends_the_change_again(
    client, seed_accounts
):
    _close(client, OVERRIDE_PW)
    cid = _customer(client)
    first = _refusal(client, cid)
    out = _probe(
        {
            "call": ["POST", "/invoices", _invoice(cid)],
            "responses": [first, {"status": 201, "body": {"id": 7}}],
            "answers": [UNICODE_PW],
        }
    )
    assert out["value"] == {"id": 7}
    assert len(out["prompts"]) == 1 and out["prompts"][0]["wrong"] is False
    assert "closing date (2026-06-30)" in out["prompts"][0]["message"]
    sent, resent = out["requests"]
    assert "X-Closing-Date-Password" not in sent["headers"]
    assert resent["headers"]["X-Closing-Date-Password"] == quote(UNICODE_PW, safe="")
    assert resent["body"] == sent["body"] and resent["method"] == "POST"


@needs_node
def test_a_wrong_password_asks_again_and_cancel_keeps_the_refusal(
    client, seed_accounts
):
    _close(client, OVERRIDE_PW)
    cid = _customer(client)
    first = _refusal(client, cid)
    wrong = _refusal(client, cid, WRONG_PW)
    out = _probe(
        {
            "call": ["POST", "/invoices", _invoice(cid)],
            "responses": [first, wrong],
            "answers": [WRONG_PW, None],
        }
    )
    assert [p["wrong"] for p in out["prompts"]] == [False, True]
    assert out["error"]["status"] == 403
    assert "not correct" in out["error"]["message"]
    assert len(out["requests"]) == 2


@needs_node
def test_no_password_set_means_no_question(client, seed_accounts):
    _close(client)
    cid = _customer(client)
    out = _probe(
        {
            "call": ["POST", "/invoices", _invoice(cid)],
            "responses": [_refusal(client, cid)],
            "answers": [OVERRIDE_PW],
        }
    )
    assert out["prompts"] == []
    assert out["error"]["status"] == 403
    assert "Modifications to closed periods are not allowed." in out["error"]["message"]


def test_the_password_prompt_is_a_masked_input_that_is_never_kept():
    api = (ROOT / "app" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    body = api[api.index("askClosingDatePassword(message, wrong) {") :]
    assert "input.type = 'password'" in body
    assert "localStorage.setItem" not in api and "sessionStorage" not in api
    assert "console." not in api


def test_wrong_passwords_lock_the_override_for_a_while(
    client, seed_accounts, monkeypatch
):
    # After five wrong passwords the override is refused for ten minutes,
    # the right password included, so the lock can't be guessed open.
    import app.services.closing_date as cd

    clock = [1000.0]
    monkeypatch.setattr(cd.time, "monotonic", lambda: clock[0])
    _close(client, password=OVERRIDE_PW)
    cid = _customer(client)
    for _ in range(cd.WRONG_LIMIT):
        r = client.post(
            "/api/invoices", json=_invoice(cid), headers=_with_password(WRONG_PW)
        )
        assert r.status_code == 403
        assert r.headers.get("X-Closing-Date-Override") == "wrong-password"
    r = client.post(
        "/api/invoices", json=_invoice(cid), headers=_with_password(OVERRIDE_PW)
    )
    assert r.status_code == 403
    assert r.headers.get("X-Closing-Date-Override") == "locked"
    assert "Too many wrong closing-date passwords" in r.json()["detail"]
    assert OVERRIDE_PW not in r.text
    clock[0] += cd.LOCK_SECONDS + 1
    r = client.post(
        "/api/invoices", json=_invoice(cid), headers=_with_password(OVERRIDE_PW)
    )
    assert r.status_code == 201, r.text


def test_a_right_password_clears_the_wrong_count(client, seed_accounts):
    import app.services.closing_date as cd

    _close(client, password=OVERRIDE_PW)
    cid = _customer(client)
    for _ in range(cd.WRONG_LIMIT - 1):
        client.post(
            "/api/invoices", json=_invoice(cid), headers=_with_password(WRONG_PW)
        )
    r = client.post(
        "/api/invoices", json=_invoice(cid), headers=_with_password(OVERRIDE_PW)
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/invoices", json=_invoice(cid), headers=_with_password(WRONG_PW)
    )
    assert r.headers.get("X-Closing-Date-Override") == "wrong-password"


def test_a_locked_override_is_not_asked_for_again():
    js = (ROOT / "app/static/js/api.js").read_text(encoding="utf-8")
    assert "override === 'password' || override === 'wrong-password'" in js

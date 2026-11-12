"""Print windows at a real address (#823).

The desktop app opened every Print in an html= window, which has no address:
the print dialog's footer printed a long base64 data: string. The shim now
asks `POST /api/print-tokens` for a one-time token under its own sign-in, and
the launcher opens http://127.0.0.1:<port>/print/<token> as a normal window.
The token serves the sheet once, within a minute, with no cookie; a second
open or a late one is a 404 with a sentence; only a print or print-preview
sheet of this app is ever made one; and the sheet is drawn under the caller's
role, so a read-only sign-in prints what it can read and nothing more."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import _role_allows, app
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.users import User
from app.routes import print_tokens
from app.routes._print import PRINT_SCRIPT
from app.services import accountants_copy
from app.services import auth as auth_service

PW = "print-token-password-823"
NOT_A_SHEET = (
    "Only a print or print-preview page of SlowBooks Pro can be opened for "
    "printing this way."
)


@pytest.fixture(autouse=True)
def _fresh_tokens():
    print_tokens._tokens.clear()
    yield
    print_tokens._tokens.clear()


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Print Token Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="PT-823",
        customer_id=customer.id,
        date=date(2026, 11, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("120.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("120.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("120.00"),
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _signed_in(db_session, role):
    username = f"{role}-823"
    db_session.add(
        User(
            username=username,
            display_name=username,
            password_hash=auth_service.hash_password(PW),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _token(client, role):
    r = client.post("/api/tokens", json={"label": f"{role}-823", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _mint(who, path, query=""):
    return who.post("/api/print-tokens", json={"path": path, "query": query})


def _address(r):
    """The served path the answered URL names."""
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"].endswith("/print/" + body["token"])
    assert body["expires_in"] == 60
    return "/print/" + body["token"]


def test_a_token_serves_the_sheet_once_with_no_cookie(client, unauthed_client, invoice):
    direct = client.get(f"/api/invoices/{invoice.id}/print-preview")
    assert direct.status_code == 200
    address = _address(_mint(client, f"/api/invoices/{invoice.id}/print-preview"))
    assert len(address) < 64  # a short address for the footer
    # no cookie, no token: the address is the permission
    r = unauthed_client.get(address)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.text == direct.text
    assert "PT-823" in r.text and r.text.count(PRINT_SCRIPT) == 1
    # a second open is refused in a sentence
    again = unauthed_client.get(address)
    assert again.status_code == 404
    assert again.json() == {"detail": print_tokens.ALREADY_OPENED}


def test_a_reports_sheet_with_its_query_equals_the_direct_print_route(
    client, unauthed_client, seed_accounts
):
    direct = client.get("/api/budgets/print", params={"year": 2026})
    assert direct.status_code == 200
    r = unauthed_client.get(_address(_mint(client, "/api/budgets/print", "year=2026")))
    assert r.status_code == 200 and r.text == direct.text
    # a leading "?" is the same query
    r = unauthed_client.get(
        _address(_mint(client, "/api/budgets/variance/print", "?year=2026"))
    )
    assert r.text == client.get("/api/budgets/variance/print?year=2026").text


def test_a_token_expires_after_a_minute(client, unauthed_client, invoice, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(print_tokens, "_now", lambda: clock[0])
    late = _address(_mint(client, f"/api/invoices/{invoice.id}/print-preview"))
    in_time = _address(_mint(client, f"/api/invoices/{invoice.id}/print-preview"))
    clock[0] += 59
    assert unauthed_client.get(in_time).status_code == 200
    clock[0] += 1  # sixty seconds after it was made
    r = unauthed_client.get(late)
    assert r.status_code == 404
    assert r.json() == {"detail": print_tokens.EXPIRED}
    # and stays expired, never served
    assert unauthed_client.get(late).json() == {"detail": print_tokens.EXPIRED}


def test_a_token_never_made_is_a_404_with_a_sentence(unauthed_client):
    r = unauthed_client.get("/print/not-a-token-at-all-823")
    assert r.status_code == 404
    assert r.json() == {"detail": print_tokens.UNKNOWN}


@pytest.mark.parametrize(
    "path",
    [
        "/api/invoices/1",
        "/api/invoices/1/pdf",
        "/api/csv/export/customers",
        "/api/print-tokens",
        "/print/abc",
        "/api/invoices/../print",
        "/api/invoices/1/print-preview?x=1",
        "/static/index.html",
        "/api/donors/giving-statement/preview",
        "/api/checks/test-print",
        "/portal/print",
        "",
    ],
)
def test_anything_but_a_print_sheet_is_refused_in_a_sentence(client, path):
    r = _mint(client, path)
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": NOT_A_SHEET}
    assert print_tokens._tokens == {}


def test_a_print_route_that_answers_no_page_is_refused_too(client, monkeypatch):
    async def a_pdf(request, path, query):
        return 200, "application/pdf", b"%PDF-1.7"

    monkeypatch.setattr(print_tokens, "_draw", a_pdf)
    r = _mint(client, "/api/checks/print", "transaction_id=1")
    assert r.status_code == 400 and r.json() == {"detail": NOT_A_SHEET}
    assert print_tokens._tokens == {}


def test_the_sheets_own_refusal_is_answered_as_it_is(client, seed_accounts):
    r = _mint(client, "/api/invoices/99999/print-preview")
    assert r.status_code == 404
    assert r.json() == {
        "detail": client.get("/api/invoices/99999/print-preview").json()["detail"]
    }
    assert print_tokens._tokens == {}


def test_no_sign_in_makes_no_token(unauthed_client, invoice):
    r = _mint(unauthed_client, f"/api/invoices/{invoice.id}/print-preview")
    assert r.status_code == 401
    assert print_tokens._tokens == {}


@pytest.mark.parametrize("role", ["bookkeeper", "readonly", "accountant"])
@pytest.mark.parametrize("how", ["session", "token"])
def test_every_role_prints_what_it_can_read(
    client, unauthed_client, db_session, invoice, role, how
):
    who = _signed_in(db_session, role) if how == "session" else _token(client, role)
    direct = who.get(f"/api/invoices/{invoice.id}/print-preview")
    assert direct.status_code == 200, direct.text
    r = _mint(who, f"/api/invoices/{invoice.id}/print-preview")
    page = unauthed_client.get(_address(r))
    assert page.status_code == 200 and page.text == direct.text


@pytest.mark.parametrize("role", ["readonly", "accountant"])
@pytest.mark.parametrize("how", ["session", "token"])
def test_a_role_refused_the_sheet_is_refused_the_token(client, db_session, role, how):
    """The token inherits the caller's role: payroll stays an
    administrator's, so its sheet is refused with the route's own sentence."""
    who = _signed_in(db_session, role) if how == "session" else _token(client, role)
    path = "/api/payroll/runs/1/print"
    direct = who.get(path)
    assert direct.status_code == 403
    r = _mint(who, path)
    assert r.status_code == 403
    assert r.json() == {"detail": direct.json()["detail"]}
    assert print_tokens._tokens == {}


def test_the_role_table_and_the_copy_take_the_token_as_a_read():
    for role in ("admin", "bookkeeper", "readonly", "accountant"):
        assert _role_allows(role, "POST", "/api/print-tokens"), role
    # only the mint itself; nothing under it, and no other method
    assert not _role_allows("readonly", "POST", "/api/print-tokens/x")
    assert not _role_allows("readonly", "DELETE", "/api/print-tokens")
    assert accountants_copy.copy_gate("POST", "/api/print-tokens")
    assert not accountants_copy.copy_gate("POST", "/api/print-tokens/x")


def test_the_print_script_counts_the_address_as_the_desktop_shell():
    """At /print/<token> the protocol is http: and window.pywebview arrives
    after load on a Mac: the page must still wait for pywebviewready
    (NEW-208), so the path says it is the shell's window."""
    assert "/^\\/print\\//.test(location.pathname)" in PRINT_SCRIPT


def test_the_page_is_public_in_the_spec(client):
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/print/{token}"]["get"].get("security") == []
    # the mint is a signed-in call like any other
    assert spec["paths"]["/api/print-tokens"]["post"].get("security") != []


def test_the_launcher_opens_only_this_servers_print_addresses(monkeypatch):
    import sys
    import types

    import desktop_launcher

    opened = []
    fake = types.SimpleNamespace(
        create_window=lambda title, url=None, **kw: opened.append((title, url, kw))
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    api = desktop_launcher.PickerApi(3001)
    good = "http://127.0.0.1:3001/print/" + "a" * 43
    assert api.open_document_url("SlowBooks Pro 2026", good) == {"success": True}
    assert opened == [("SlowBooks Pro 2026", good, {"zoomable": True})]
    for bad in (
        "http://127.0.0.1:3002/print/" + "a" * 43,  # another port
        "http://localhost:3001/print/" + "a" * 43,
        "http://127.0.0.1:3001/api/invoices/1/print-preview",
        "http://127.0.0.1:3001/print/short",
        "http://127.0.0.1:3001/print/" + "a" * 43 + "/../x",
        "https://example.com/print/" + "a" * 43,
        "",
    ):
        result = api.open_document_url("x", bad)
        assert result["success"] is False and result["error"], bad
    assert len(opened) == 1

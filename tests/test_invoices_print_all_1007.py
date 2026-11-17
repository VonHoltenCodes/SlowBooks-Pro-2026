"""Invoices list: Print on each row, and Print all (#1007).

Print lived only inside an open invoice. Each row of the Invoices list now
has a Print beside View, opening that invoice's own print preview, and the
list has Print all: GET /api/invoices/print-preview?ids=1,2,3 draws the
invoices the list shows, in that order, as one print preview with each
invoice starting on a page of its own. Every reading role can print, and
printing marks nothing as sent.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.routes.invoices.print_all import MAX_INVOICES, TOO_MANY
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/invoices.js").read_text(encoding="utf-8")
BREAK = ".sb-print-doc+.sb-print-doc{page-break-before:always"


def _invoice(client, cid, rate=100):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-11-01",
            "tax_rate": 0,
            "lines": [{"description": "Service call", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def three(client, seed_accounts, seed_customer):
    return [_invoice(client, seed_customer.id, rate) for rate in (100, 200, 300)]


def test_three_invoices_print_as_one_preview_a_page_each(client, three):
    ids = ",".join(str(inv["id"]) for inv in three)
    r = client.get(f"/api/invoices/print-preview?ids={ids}")
    assert r.status_code == 200, r.text
    page = r.text
    # one section per invoice, every one after the first on a new page
    assert page.count('<section class="sb-print-doc">') == 3
    assert BREAK in page
    assert page.count("<body") == 1 and page.count("</body>") == 1
    for inv in three:
        assert inv["invoice_number"] in page
    # in the order asked
    spots = [page.index(f">#{inv['invoice_number']}<") for inv in three]
    assert spots == sorted(spots)
    # the same print script as one invoice's preview
    assert 'id="sb-print-again"' in page and "window.print()" in page


def test_the_order_asked_is_the_order_printed(client, three):
    ids = ",".join(str(inv["id"]) for inv in reversed(three))
    page = client.get(f"/api/invoices/print-preview?ids={ids}").text
    spots = [page.index(f">#{inv['invoice_number']}<") for inv in reversed(three)]
    assert spots == sorted(spots)


def test_printing_marks_nothing_sent(client, three):
    ids = ",".join(str(inv["id"]) for inv in three)
    assert client.get(f"/api/invoices/print-preview?ids={ids}").status_code == 200
    for inv in three:
        assert client.get(f"/api/invoices/{inv['id']}").json()["status"] == "draft"


def test_an_unknown_invoice_is_a_404(client, three):
    r = client.get(f"/api/invoices/print-preview?ids={three[0]['id']},999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Invoice not found"


def test_nothing_named_or_not_ids_is_a_sentence(client, three):
    r = client.get("/api/invoices/print-preview")
    assert r.status_code == 400 and r.json()["detail"].endswith(".")
    r = client.get("/api/invoices/print-preview?ids=1,two")
    assert r.status_code == 400 and r.json()["detail"].endswith(".")


def test_more_than_the_cap_is_refused_in_a_sentence(client, three):
    ids = ",".join(str(n) for n in range(1, MAX_INVOICES + 2))
    r = client.get(f"/api/invoices/print-preview?ids={ids}")
    assert r.status_code == 400
    assert r.json()["detail"] == TOO_MANY
    assert str(MAX_INVOICES) in TOO_MANY


def test_one_invoices_own_preview_still_answers(client, three):
    r = client.get(f"/api/invoices/{three[0]['id']}/print-preview")
    assert r.status_code == 200
    assert three[0]["invoice_number"] in r.text


def _login_as(username, password, role, db_session):
    db_session.add(
        User(
            username=username,
            display_name=username.title(),
            password_hash=auth_service.hash_password(password),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    signed_in = TestClient(app)
    r = signed_in.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return signed_in


@pytest.mark.parametrize(
    "role", [ROLE_READONLY, ROLE_ACCOUNTANT], ids=["read-only", "accountant"]
)
def test_reading_sign_ins_can_print(client, db_session, three, role):
    reader = _login_as(f"reader-{role}", "reader-password-1", role, db_session)
    ids = ",".join(str(inv["id"]) for inv in three)
    r = reader.get(f"/api/invoices/print-preview?ids={ids}")
    assert r.status_code == 200, r.text
    assert r.text.count('<section class="sb-print-doc">') == 3
    r = reader.get(f"/api/invoices/{three[0]['id']}/print-preview")
    assert r.status_code == 200, r.text


def test_a_read_only_token_can_print(client, three):
    tok = client.post("/api/tokens", json={"label": "viewer", "role": "readonly"})
    assert tok.status_code in (200, 201), tok.text
    viewer = TestClient(app)
    viewer.headers["Authorization"] = f"Bearer {tok.json()['token']}"
    r = viewer.get(f"/api/invoices/print-preview?ids={three[1]['id']}")
    assert r.status_code == 200, r.text
    assert three[1]["invoice_number"] in r.text


def test_the_print_all_address_opens_in_the_desktop_print_window():
    from app.routes.print_tokens import _SHEET_PATH_RE

    assert _SHEET_PATH_RE.match("/api/invoices/print-preview")


def test_each_row_has_a_print_beside_view():
    row = JS[JS.index('row: inv => `<tr class="inv-row"') :]
    row = row[: row.index("</tr>`")]
    assert 'data-id="${inv.id}"' in row
    view = row.index("InvoicesPage.view(${inv.id})")
    printing = row.index(
        "window.open('/api/invoices/${inv.id}/print-preview','_blank')\">Print</button>"
    )
    assert view < printing < row.index("InvoicesPage.showForm(${inv.id})")


def test_print_all_is_in_the_list_header_and_prints_the_shown_rows():
    header = JS[
        JS.index("headerHtml:") : JS.index("filter: {", JS.index("headerHtml:"))
    ]
    assert 'onclick="InvoicesPage.printAll()"' in header
    assert ">Print all</button>" in header
    body = JS[JS.index("    printAll() {") :]
    body = body[: body.index("\n    },")]
    # the rows the status filter left showing, in the order on screen
    assert "$$('.inv-row')" in body
    assert "row.style.display !== 'none'" in body
    assert (
        "window.open(`/api/invoices/print-preview?ids=${ids.join(',')}`, '_blank')"
        in body
    )
    assert "InvoicesPage.PRINT_ALL_MAX" in body
    assert f"PRINT_ALL_MAX: {MAX_INVOICES}," in JS

"""Check position (#727) and the payee's address on the check (#726).

QuickBooks Desktop's standard voucher check stock has the check face on the
top third and two vouchers below it; SlowBooks printed stub / stub / check
only. A per-company check position under Settings -> Check Printing chooses
"bottom" (today's layout, the default) or "top" (check / voucher / voucher),
with the fields placed where that stock expects them. The payee's postal
address prints under the amount in words on every check path, and not at
all when the vendor has none.

Rendered to HTML (the PDF is WeasyPrint over the same HTML), so these run
without WeasyPrint's native stack.
"""

import datetime
import re
from types import SimpleNamespace

import pytest

import app.routes.checks as checks
from app.routes.checks import (
    bill_payment_check_data,
    register_check_data,
    vendor_address_fields,
)
from app.services.pdf_service import check_position, render_checks_html

_COMPANY = {
    "company_name": "Acme",
    "company_address1": "1 Main St",
    "company_city": "Tampa",
    "company_state": "FL",
    "company_zip": "33601",
}
_ADDRESS = ["Gulf Supply", "200 Harbor Rd", "Suite 4", "Tampa, FL 33601"]


def _check(**overrides):
    data = {
        "payee": "Gulf Supply",
        "date": datetime.date(2026, 10, 24),
        "amount": 1234.56,
        "check_number": "1072",
        "memo": "Invoice 55",
        "details": [{"description": "Bill #B-1", "amount": 1234.56}],
        "bank_account": "Operating Checking",
        "payee_address": [],
    }
    data.update(overrides)
    return data


def _render(check=None, **settings):
    return render_checks_html([check or _check()], {**_COMPANY, **settings})


def _face(html):
    """The check face's markup alone."""
    m = re.search(
        r'<div class="check-area[^"]*".*?<div class="signature-line">', html, re.S
    )
    assert m, "no check face"
    return m.group(0)


# ----- the setting ------------------------------------------------------


def test_the_position_defaults_to_bottom():
    assert check_position({}) == "bottom"
    assert check_position({"check_position": "banana"}) == "bottom"
    assert check_position({"check_position": "top"}) == "top"


def test_the_setting_round_trips(client):
    assert client.get("/api/settings").json()["check_position"] == "bottom"
    r = client.put("/api/settings", json={"check_position": "top"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["check_position"] == "top"
    r = client.put("/api/settings", json={"check_position": "bottom"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["check_position"] == "bottom"


def test_the_setting_refuses_anything_else(client):
    r = client.put("/api/settings", json={"check_position": "middle"})
    assert r.status_code == 422, r.text
    assert "check_position must be one of: bottom, top" in r.json()["detail"]
    assert client.get("/api/settings").json()["check_position"] == "bottom"


def test_the_settings_page_has_a_labelled_position_select():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text()
    assert '<label for="settings-check-position">Check Position</label>' in js
    assert 'name="check_position" id="settings-check-position"' in js
    # the test print sends the form's position with the stock and nudges
    assert "'check_stock', 'check_position', 'check_nudge_x_in'" in js


# ----- the two layouts --------------------------------------------------


def test_bottom_prints_stub_stub_check():
    html = _render()
    assert html.index("<!-- Top Stub -->") < html.index("<!-- Middle Stub -->")
    assert html.index("<!-- Middle Stub -->") < html.index('class="check-area"')
    assert "voucher" not in html
    assert "check-top" not in html


def test_top_prints_the_check_then_two_vouchers():
    html = _render(check_position="top")
    assert "Stub -->" not in html
    face = html.index('class="check-area check-top"')
    vouchers = [m.start() for m in re.finditer('<div class="voucher">', html)]
    assert len(vouchers) == 2
    assert face < vouchers[0] < vouchers[1]


def test_top_places_the_fields_as_quickbooks_voucher_stock_does():
    html = _render(_check(payee_address=_ADDRESS), check_position="top")
    face = _face(html)
    # top to bottom on the face: date, payee with the amount, words, address
    order = [
        face.index('class="check-date"'),
        face.index('class="pay-to-line"'),
        face.index('class="amount-words"'),
        face.index('class="payee-address"'),
        face.index('class="memo-line"'),
    ]
    assert order == sorted(order)
    # the date at the top right; the payee and the amount on one line with
    # the amount at the right edge; words below; the address under, at left
    assert (
        ".check-top .check-date { top: 0.65in; right: 0.4in; text-align: right; }"
        in html
    )
    assert ".check-top .pay-to { position: absolute; top: 1.15in;" in html
    assert ".check-top .amount-box { top: 1.09in; right: 0.1in;" in html
    assert (
        ".check-top .amount-words { position: absolute; top: 1.48in; left: 0.1in;"
        in html
    )
    assert (
        ".check-top .payee-address { position: absolute; top: 1.8in; left: 0.75in;"
        in html
    )
    assert "Oct 24, 2026" in face
    assert "$1,234.56" in face
    assert "One Thousand Two Hundred Thirty-Four and 56/100" in face


def test_each_voucher_has_payee_date_lines_and_bank_total():
    html = _render(check_position="top")
    for v in re.findall(r'<div class="voucher">.*?</div>\s*</div>', html, re.S):
        head = re.search(r'class="voucher-head">(.*?)</div>', v, re.S).group(1)
        assert "Gulf Supply" in head and "Oct 24, 2026" in head
        assert "<td>Bill #B-1</td>" in v
        foot = re.search(r'class="voucher-foot">(.*?)</div>', v, re.S).group(1)
        assert "Operating Checking" in foot and "$1,234.56" in foot


def test_a_voucher_with_no_detail_lines_lists_the_payment():
    html = _render(_check(details=[]), check_position="top")
    assert html.count("<td>Payment</td>") == 2


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_preprinted_stock_hides_the_labels_in_either_position(position):
    html = _render(check_position=position, check_stock="preprinted")
    assert "{ visibility: hidden; }" in html
    assert ".pay-to-line, .amount-words, .memo-text { border-bottom: none; }" in html
    face = _face(html)
    # the values still print on the face
    assert "Gulf Supply" in face and "$1,234.56" in face and "Invoice 55" in face


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_the_nudges_move_the_face_only_in_either_position(position):
    html = _render(
        check_position=position, check_nudge_x_in="0.1", check_nudge_y_in="-0.05"
    )
    assert html.count("transform: translate(0.1in, -0.05in)") == 1
    assert re.search(
        r'<div class="check-area[^"]*" style="transform: translate\(0.1in, -0.05in\);">',
        html,
    )


def test_top_without_a_nudge_emits_no_transform():
    assert "transform" not in _render(check_position="top")


def test_a_batch_prints_each_check_in_the_chosen_position():
    html = render_checks_html(
        [_check(), _check(check_number="1073", payee="Bay Rentals")],
        {**_COMPANY, "check_position": "top"},
    )
    assert html.count('class="check-area check-top"') == 2
    assert html.count('<div class="voucher">') == 4
    assert html.count('class="check-page"') == 2


# ----- the payee's address (#726) --------------------------------------


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_a_check_with_an_address_prints_the_block_under_the_words(position):
    html = _render(_check(payee_address=_ADDRESS), check_position=position)
    face = _face(html)
    block = '<div class="payee-address">' + "<br>".join(_ADDRESS) + "</div>"
    assert block in face
    assert face.index('class="amount-words"') < face.index(block)


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_a_check_without_an_address_prints_no_block_and_is_otherwise_the_same(
    position,
):
    without = _render(check_position=position)
    assert '<div class="payee-address">' not in without
    with_it = _render(_check(payee_address=_ADDRESS), check_position=position)
    # taking the block (and its style) out leaves exactly the face without it
    stripped = re.sub(
        r"\n            <div class=\"payee-address\">.*?</div>", "", with_it
    )
    stripped = re.sub(
        r"\n<style>\s*/\* The payee's postal address.*?</style>",
        "",
        stripped,
        flags=re.S,
    )
    assert stripped == without


def test_the_address_helper():
    v = SimpleNamespace(
        name="Gulf Supply",
        address1="200 Harbor Rd",
        address2=" ",
        city="Tampa",
        state="FL",
        zip="33601",
    )
    assert vendor_address_fields(v) == {
        "payee_address": ["Gulf Supply", "200 Harbor Rd", "Tampa, FL 33601"]
    }
    blank = SimpleNamespace(
        name="Gulf Supply", address1=None, address2="", city=None, state="", zip=None
    )
    assert vendor_address_fields(blank) == {"payee_address": []}
    assert vendor_address_fields(None) == {"payee_address": []}
    only_city = SimpleNamespace(
        name="X", address1=None, address2=None, city="Tampa", state=None, zip=None
    )
    assert vendor_address_fields(only_city)["payee_address"] == ["X", "Tampa"]


def _bill_payment(client, seed_accounts, vendor_body):
    v = client.post("/api/vendors", json=vendor_body)
    assert v.status_code in (200, 201), v.text
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.json()["id"],
            "bill_number": "B-1",
            "date": "2026-09-02",
            "lines": [
                {
                    "description": "lumber",
                    "quantity": 1,
                    "rate": 100,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    paid = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.json()["id"],
            "date": "2026-09-03",
            "amount": 100,
            "check_number": "2001",
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill.json()["id"], "amount": 100}],
        },
    )
    assert paid.status_code == 201, paid.text
    return paid.json()["id"]


def test_a_bill_payment_check_carries_the_vendor_address_and_bank(
    client, db_session, seed_accounts
):
    from app.models.bills import BillPayment

    bp_id = _bill_payment(
        client,
        seed_accounts,
        {
            "name": "Gulf Supply",
            "address1": "200 Harbor Rd",
            "city": "Tampa",
            "state": "FL",
            "zip": "33601",
        },
    )
    bp = db_session.query(BillPayment).filter(BillPayment.id == bp_id).first()
    data = bill_payment_check_data(db_session, bp)
    assert data["payee_address"] == ["Gulf Supply", "200 Harbor Rd", "Tampa, FL 33601"]
    assert data["bank_account"] == seed_accounts["1000"].name
    html = render_checks_html([data], {**_COMPANY, "check_position": "top"})
    assert "200 Harbor Rd" in _face(html)


def test_a_bill_payment_to_a_vendor_with_no_address_prints_no_block(
    client, db_session, seed_accounts
):
    from app.models.bills import BillPayment

    bp_id = _bill_payment(client, seed_accounts, {"name": "Bay Rentals"})
    bp = db_session.query(BillPayment).filter(BillPayment.id == bp_id).first()
    data = bill_payment_check_data(db_session, bp)
    assert data["payee_address"] == []
    assert "payee-address" not in render_checks_html([data], _COMPANY)


def _entry(client, seed_accounts, **overrides):
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-09-14",
        "amount": "-250.00",
        "category_account_id": seed_accounts["6500"].id,
        "payee": "Main Street Properties",
        "description": "October rent",
        "check_number": "1072",
    }
    body.update(overrides)
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_register_check_to_a_vendor_prints_its_address(
    client, db_session, seed_accounts
):
    v = client.post(
        "/api/vendors",
        json={
            "name": "Main Street Properties",
            "address1": "9 Elm St",
            "city": "Joliet",
            "state": "IL",
            "zip": "60435",
        },
    )
    assert v.status_code in (200, 201), v.text
    e = _entry(client, seed_accounts, vendor_id=v.json()["id"])
    data = register_check_data(db_session, e["id"])
    assert data["payee_address"] == [
        "Main Street Properties",
        "9 Elm St",
        "Joliet, IL 60435",
    ]
    assert data["bank_account"] == seed_accounts["1000"].name
    assert "9 Elm St" in _face(render_checks_html([data], _COMPANY))


def test_a_register_check_with_no_vendor_prints_as_today(
    client, db_session, seed_accounts
):
    e = _entry(client, seed_accounts, payee="Someone New")
    data = register_check_data(db_session, e["id"])
    assert data["payee_address"] == []
    html = render_checks_html([data], _COMPANY)
    assert "payee-address" not in html
    assert "Someone New" in html


# ----- the test print follows the form ----------------------------------


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_pdf(check_data, company):
        seen["html"] = render_checks_html([check_data], company)
        return b"%PDF-fake"

    monkeypatch.setattr(checks, "generate_check_pdf", fake_pdf)
    return seen


def test_the_test_print_follows_the_unsaved_position(client, captured):
    r = client.get("/api/checks/test-print?check_position=top")
    assert r.status_code == 200, r.text
    assert 'class="check-area check-top"' in captured["html"]
    assert client.get("/api/settings").json()["check_position"] == "bottom"


def test_the_test_print_follows_the_saved_position(client, captured):
    assert client.put("/api/settings", json={"check_position": "top"}).is_success
    assert client.get("/api/checks/test-print").status_code == 200
    assert 'class="check-area check-top"' in captured["html"]
    assert client.get("/api/checks/test-print?check_position=bottom").is_success
    assert "check-top" not in captured["html"]


def test_the_test_print_shows_sample_address_lines(client, captured):
    assert client.get("/api/checks/test-print").status_code == 200
    assert (
        '<div class="payee-address">Sample Payee<br>123 Any Street<br>'
        "Anytown, ST 00000</div>"
    ) in captured["html"]
    assert "Sample Checking" not in _face(captured["html"])


def test_the_test_print_refuses_a_bad_position(client):
    r = client.get("/api/checks/test-print?check_position=middle")
    assert r.status_code == 422, r.text
    assert "check_position must be one of: bottom, top" in r.json()["detail"]

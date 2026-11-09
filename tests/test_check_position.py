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
import io
import re
from types import SimpleNamespace

import pytest

import app.routes.checks as checks
from app.routes.checks import (
    bill_payment_check_data,
    register_check_data,
    vendor_address_fields,
)
from app.services.pdf_service import (
    check_position,
    generate_checks_pdf,
    render_checks_html,
)

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

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
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
        ".check-top .check-date { top: 0.57in; right: 0.4in; text-align: right; }"
        in html
    )
    assert ".check-top .pay-to { position: absolute; top: 1.07in;" in html
    assert ".check-top .amount-box { top: 1.01in; right: 0.1in;" in html
    assert (
        ".check-top .amount-words { position: absolute; top: 1.4in; left: 0.1in;"
        in html
    )
    assert (
        ".check-top .payee-address { position: absolute; top: 1.72in; left: 0.75in;"
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
    assert html.count('class="check-page check-page-top"') == 2


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


def test_top_panels_end_where_a_sheet_in_thirds_tears():
    """W-128 (the 2.31.9 gate): the face ends at 3.67 in from the top of the
    sheet and the first voucher at 7.33 in, where voucher stock perforated in
    thirds tears; the second voucher runs to the foot of the printable page."""
    html = _render(check_position="top")
    assert ".check-page-top { height: 10.42in; }" in html
    assert ".check-top { padding: 0; height: 3.34in; }" in html
    assert ".voucher { height: 3.66in;" in html
    assert ".voucher + .voucher { height: 3.42in; }" in html
    assert '<div class="check-page check-page-top">' in html
    bottom = _render(check_position="bottom")
    assert "check-page-top" not in bottom and ".voucher" not in bottom


# ----- the bottom layout's panels (#732) --------------------------------


def test_bottom_panels_end_where_a_sheet_in_thirds_tears():
    """#732: the first stub ends at 3.67 in from the top of the sheet and the
    second at 7.33 in (0.25 in of page margin and the body's 8 px counted),
    where stock perforated in thirds tears; the face runs to the foot of the
    printable page, as the top layout's panels do (#727)."""
    html = _render()
    assert ".check-page-bottom { height: 10.42in; }" in html
    assert ".check-page-bottom .stub { height: 3.34in; }" in html
    assert ".check-page-bottom .stub + .stub { height: 3.66in; }" in html
    assert ".check-page-bottom .check-area { height: 3.42in; }" in html
    assert html.count('<div class="check-page check-page-bottom">') == 1
    # the panels fill the page exactly: 3.34 + 3.66 + 3.42 = 10.42 in
    assert round(3.34 + 3.66 + 3.42, 2) == 10.42
    top = _render(check_position="top")
    assert "check-page-bottom" not in top


def test_bottom_face_fields_keep_their_places_on_the_face():
    """The face is 0.09 in taller, so what was placed from its foot (the memo
    and the signature, 0.5 in up on a 3.33 in face) is placed 0.59 in up:
    2.83 in from the face's top either way. Everything placed from the top
    of the face is untouched, so the pre-printed stock and the nudges (#440)
    line up as before."""
    html = _render(_check(payee_address=_ADDRESS))
    assert (
        ".check-page-bottom .memo-line, .check-page-bottom .signature-line "
        "{ bottom: 0.59in; }"
    ) in html
    assert round(3.42 - 0.59, 2) == round(3.33 - 0.5, 2)
    for rule in (
        ".check-number { position: absolute; top: 0.25in; right: 0.25in;",
        ".check-date { position: absolute; top: 0.25in; right: 2.5in;",
        ".pay-to { margin-top: 0.5in; }",
        ".amount-box { position: absolute; top: 1in; right: 0.25in;",
        ".check-area { height: 3.33in; padding: 0.25in 0.25in;",
    ):
        assert rule in html, rule
    pre = _render(check_stock="preprinted", check_nudge_y_in="0.1")
    assert "{ bottom: 0.59in; }" in pre
    assert re.search(
        r'<div class="check-page check-page-bottom">\s*<!-- Top Stub -->', pre
    )
    assert 'style="transform: translate(0.0in, 0.1in);"' in _face(pre)


def test_a_batch_and_the_test_print_follow_the_bottom_panels(client, captured):
    html = render_checks_html(
        [_check(), _check(check_number="1073", payee="Bay Rentals")], _COMPANY
    )
    assert html.count('<div class="check-page check-page-bottom">') == 2
    assert html.count(".check-page-bottom .stub { height: 3.34in; }") == 1
    assert client.get("/api/checks/test-print").status_code == 200
    assert '<div class="check-page check-page-bottom">' in captured["html"]
    assert ".check-page-bottom .stub + .stub { height: 3.66in; }" in captured["html"]


# ----- the Pay to line names the vendor when no payee was typed (#733) --


def _vendor(client, name="Main Street Properties"):
    v = client.post("/api/vendors", json={"name": name, "address1": "9 Elm St"})
    assert v.status_code in (200, 201), v.text
    return v.json()["id"]


def test_a_register_check_with_a_vendor_and_no_payee_pays_the_vendor(
    client, db_session, seed_accounts
):
    """#733: an entry saved through the API with a vendor and no payee text
    printed a blank Pay to line over the vendor's address."""
    e = _entry(
        client,
        seed_accounts,
        payee=None,
        description="",
        vendor_id=_vendor(client),
    )
    data = register_check_data(db_session, e["id"])
    assert data["payee"] == "Main Street Properties"
    html = render_checks_html([data], _COMPANY)
    face = _face(html)
    assert '<span class="pay-to-line">Main Street Properties</span>' in face
    # the stubs name the vendor too
    assert html.count("<strong>Pay To:</strong> Main Street Properties") == 2
    top = render_checks_html([data], {**_COMPANY, "check_position": "top"})
    assert top.count('<span class="voucher-payee">Main Street Properties</span>') == 2


def test_a_register_check_with_a_vendor_and_a_typed_payee_pays_the_payee(
    client, db_session, seed_accounts
):
    e = _entry(
        client, seed_accounts, payee="M. S. Properties LLC", vendor_id=_vendor(client)
    )
    data = register_check_data(db_session, e["id"])
    assert data["payee"] == "M. S. Properties LLC"
    assert '<span class="pay-to-line">M. S. Properties LLC</span>' in _face(
        render_checks_html([data], _COMPANY)
    )


def test_a_register_check_with_neither_prints_as_today(
    client, db_session, seed_accounts
):
    e = _entry(client, seed_accounts, payee=None, description="")
    data = register_check_data(db_session, e["id"])
    assert data["payee"] == ""
    assert data["payee_address"] == []
    assert '<span class="pay-to-line"></span>' in _face(
        render_checks_html([data], _COMPANY)
    )


def test_an_expense_check_with_a_vendor_and_no_payee_pays_the_vendor(
    client, db_session, seed_accounts
):
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-09-02",
            "vendor_id": _vendor(client, "Sweet Forest Cafe"),
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "30.30",
            "reference": "1074",
        },
    )
    assert r.status_code == 201, r.text
    data = register_check_data(db_session, r.json()["id"])
    assert data["payee"] == "Sweet Forest Cafe"
    assert data["memo"] == ""


# ----- every page of a batch starts where the first does (#740) ---------


def _page_runs(pdf):
    """Each page's text runs as (text, x, y) in points from the page's
    top-left corner, read from the PDF's word boxes."""
    pypdf = pytest.importorskip("pypdf")
    pages = []
    for page in pypdf.PdfReader(io.BytesIO(pdf)).pages:
        runs = []

        def visit(text, cm, tm, font, size, runs=runs):
            if not text.strip():
                return
            # text space -> page space: tm then the page's cm
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            runs.append((text.strip(), round(x, 2), round(792.0 - y, 2)))

        page.extract_text(visitor_text=visit)
        pages.append(runs)
    return pages


def _batch(position, count=2):
    # the same check under consecutive numbers, so every page carries the
    # same text in the same widths and only a shift could move a field
    return [
        _check(check_number=str(1072 + n), payee_address=_ADDRESS) for n in range(count)
    ]


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_a_two_check_batch_prints_both_pages_at_the_same_height(position):
    """#740: page 2 of a batch printed 0.08 in higher than page 1, because
    only the first page got the body's top margin. Every stub, voucher and
    face field sits at the same place on both pages."""
    pytest.importorskip("weasyprint")
    pdf = generate_checks_pdf(
        _batch(position), {**_COMPANY, "check_position": position}
    )
    first, second = _page_runs(pdf)
    assert len(first) == len(second) > 10
    for a, b in zip(first, second):
        assert a[0].replace("1072", "1073") == b[0], (a, b)
        assert (a[1], a[2]) == (b[1], b[2]), (a, b)


def _check_page_tops(html):
    """Where each page's check-page box starts, in CSS px from the top of
    the sheet (WeasyPrint's layout, before the PDF)."""
    weasyprint = pytest.importorskip("weasyprint")
    tops = []
    for page in weasyprint.HTML(string=html).render().pages:
        for box in page._page_box.descendants():
            element = getattr(box, "element", None)
            if (
                element is not None
                and "check-page" in (element.get("class") or "").split()
            ):
                tops.append(box.content_box_y())
                break
    return tops


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_every_page_of_a_batch_starts_where_a_single_check_does(position):
    """The panels are measured from 0.25 in of page margin and 8 px (#732,
    #727): 32 px from the top of the sheet. A single check keeps that, and
    so does every page of a batch, so the tears land at 3.67 in and 7.33 in
    on each of them."""
    settings = {**_COMPANY, "check_position": position}
    single = _check_page_tops(render_checks_html(_batch(position, 1), settings))
    assert single == [pytest.approx(32.0)]
    batch = _check_page_tops(render_checks_html(_batch(position, 3), settings))
    assert batch == [pytest.approx(32.0)] * 3


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_a_single_check_prints_where_a_batch_prints_its_first_page(position):
    pytest.importorskip("weasyprint")
    settings = {**_COMPANY, "check_position": position}
    (single,) = _page_runs(generate_checks_pdf(_batch(position, 1), settings))
    first = _page_runs(generate_checks_pdf(_batch(position), settings))[0]
    assert single == first

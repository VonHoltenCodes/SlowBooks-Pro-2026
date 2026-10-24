"""Pre-printed check stock (#440, from discussion #354).

@KWBsupport's check stock already carries "Pay to the order of", the memo
and signature lines and the amount border. A new per-company setting prints
the values alone onto that stock, with a small alignment nudge to line them
up, and a test print so the nudge can be dialled in on plain paper held
against the stock. One helper reads the setting for every check print path
(bill payment, Pay Bills, a register check, an expense check), so none of
them can drift.
"""

import datetime
import io

import pytest

from app.services.pdf_service import check_print_options, render_check_html

_CHECK = {
    "payee": "Sample Payee",
    "date": datetime.date(2026, 10, 24),
    "amount": 1234.56,
    "check_number": "1072",
    "memo": "Alignment test",
    "details": [{"description": "Rent", "amount": 1234.56}],
}
_COMPANY = {
    "company_name": "Acme",
    "company_address1": "1 Main St",
    "company_city": "Tampa",
    "company_state": "FL",
    "company_zip": "33601",
}

_LABELS = ("PAY TO THE ORDER OF", ">DATE<", ">MEMO<")


def _render(**company_overrides):
    return render_check_html(dict(_CHECK), {**_COMPANY, **company_overrides})


# ----- the helper reads the settings, once ------------------------------


def test_the_helper_defaults_to_plain_stock_and_no_nudge():
    assert check_print_options({}) == {"stock": "plain", "nudge_x": 0.0, "nudge_y": 0.0}


def test_the_helper_reads_the_stock_and_nudge():
    opts = check_print_options(
        {
            "check_stock": "preprinted",
            "check_nudge_x_in": "0.1",
            "check_nudge_y_in": "-0.05",
        }
    )
    assert opts == {"stock": "preprinted", "nudge_x": 0.1, "nudge_y": -0.05}


def test_an_unknown_stock_reads_as_plain():
    assert check_print_options({"check_stock": "banana"})["stock"] == "plain"


# ----- the template -----------------------------------------------------


def test_plain_stock_is_byte_for_byte_what_it_always_was():
    # options={} renders the pre-#440 face — no stock/nudge context at all.
    # Plain stock with a zero nudge must come out identical to it, so adding
    # the setting changed nothing for the default.
    before = render_check_html(dict(_CHECK), _COMPANY, options={})
    assert _render() == before


def test_plain_stock_prints_every_label_and_the_signature_line():
    html = _render()
    for label in _LABELS:
        assert label in html
    assert '<div class="signature-line"></div>' in html


def test_plain_stock_has_no_transform_and_no_override_style():
    html = _render()
    assert "translate(" not in html
    # the preprinted-only border overrides are not emitted for plain stock
    assert "border-bottom: none" not in html


def test_preprinted_stock_prints_no_labels():
    html = _render(check_stock="preprinted")
    for label in _LABELS:
        assert label not in html


def test_preprinted_stock_still_prints_the_values():
    html = _render(check_stock="preprinted")
    assert "Sample Payee" in html
    assert "One Thousand Two Hundred Thirty-Four and 56/100" in html
    assert "Oct 24, 2026" in html
    assert "Alignment test" in html


def test_preprinted_stock_drops_the_lines_and_borders():
    html = _render(check_stock="preprinted")
    # the pre-printed stock carries the lines/box; ours are turned off, and
    # the signature rule is left out of the markup entirely.
    assert ".pay-to-line, .amount-words, .memo-text { border-bottom: none; }" in html
    assert '<div class="signature-line"></div>' not in html


def test_the_nudge_appears_in_the_css_of_the_check_area_only():
    html = _render(check_nudge_x_in="0.1", check_nudge_y_in="-0.05")
    assert 'class="check-area" style="transform: translate(0.1in, -0.05in);"' in html
    # the stubs are not nudged
    assert html.count("transform: translate") == 1


def test_a_zero_nudge_emits_no_transform():
    assert "transform" not in _render()


# ----- the test-print route --------------------------------------------


def test_test_print_returns_a_pdf(client):
    # Renders with WeasyPrint; without its native stack conftest turns the
    # ImportError into a skip (the endpoint still runs in CI).
    r = client.get("/api/checks/test-print")
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


def test_test_print_shows_the_placeholder_values(client):
    pypdf = pytest.importorskip("pypdf")
    r = client.get("/api/checks/test-print")
    assert r.status_code == 200, r.text[:300]
    text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert "Sample Payee" in text
    assert "1,234.56" in text
    assert "Alignment test" in text


def test_a_bill_payment_check_follows_the_setting_too(client, seed_accounts):
    # The bill-payment / Pay Bills path reads the setting through the same
    # helper: with pre-printed stock chosen, its check prints no label.
    pypdf = pytest.importorskip("pypdf")
    v = client.post("/api/vendors", json={"name": "Gulf Supply"})
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
            "allocations": [{"bill_id": bill.json()["id"], "amount": 100}],
        },
    )
    assert paid.status_code == 201, paid.text

    assert client.put("/api/settings", json={"check_stock": "preprinted"}).is_success
    r = client.get(f"/api/checks/print?bill_payment_id={paid.json()['id']}")
    assert r.status_code == 200, r.text[:300]
    text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert "PAY TO THE ORDER OF" not in text
    assert "Gulf Supply" in text

"""The dollar sign on a check's amount (#799).

Some pre-printed check stock prints its own "$" beside the amount box, so a
check printed "$1,234.56" next to it. Settings -> Check Printing -> Leave the
dollar sign off the amount (off by default) prints the amount in figures as
"1,234.56"; the amount in words, the stubs, the vouchers and every layout
are unchanged, on plain, pre-printed and blank stock and on the test check.

These read the rendered HTML (no WeasyPrint), so they run on every runner.
"""

import datetime
from decimal import Decimal

import pytest

from app.services.pdf_service import check_amount_figures, render_checks_html

_COMPANY = {
    "company_name": "Acme Builders",
    "company_address1": "1 Main St",
    "company_city": "Tampa",
    "company_state": "FL",
    "company_zip": "33601",
}
_BANK = {
    "bank_name": "First Prairie Bank",
    "bank_city_state": "Joliet, IL",
    "fractional_routing": "70-1234/719",
    "routing_number": "123456780",
    "account_number": "4455667788",
}
_OFF_BOX = '<div class="amount-box">$1,234.56</div>'
_ON_BOX = '<div class="amount-box">1,234.56</div>'
_WORDS = "One Thousand Two Hundred Thirty-Four and 56/100 ******"
# the stock kinds: plain, pre-printed (values only) and blank (#739)
_STOCKS = {
    "plain": {},
    "preprinted": {"check_stock": "preprinted"},
    "blank": {"check_blank_stock": "true"},
}


def _check(**overrides):
    data = {
        "payee": "Gulf Supply",
        "date": datetime.date(2026, 10, 24),
        "amount": Decimal("1234.56"),
        "check_number": "1072",
        "memo": "Invoice 55",
        "details": [{"description": "Bill #B-1", "amount": Decimal("1234.56")}],
        "bank_account": "Operating Checking",
        "payee_address": ["Gulf Supply", "200 Harbor Rd", "Gulfport, MS 39501"],
        "bank_details": dict(_BANK),
    }
    data.update(overrides)
    return data


def _html(**settings):
    return render_checks_html([_check()], {**_COMPANY, **settings})


# ----- the setting -------------------------------------------------------


def test_the_setting_is_off_by_default_and_round_trips(client):
    assert client.get("/api/settings").json()["check_amount_no_dollar_sign"] == "false"
    r = client.put("/api/settings", json={"check_amount_no_dollar_sign": "true"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["check_amount_no_dollar_sign"] == "true"
    r = client.put("/api/settings", json={"check_amount_no_dollar_sign": "false"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["check_amount_no_dollar_sign"] == "false"


@pytest.mark.parametrize("value", ["yes", "", "TRUE", True, 1, None])
def test_the_setting_refuses_anything_but_true_or_false(client, value):
    r = client.put("/api/settings", json={"check_amount_no_dollar_sign": value})
    assert r.status_code == 422, r.text
    assert "check_amount_no_dollar_sign must be one of: false, true" in r.text
    assert client.get("/api/settings").json()["check_amount_no_dollar_sign"] == "false"


def test_the_settings_page_has_the_box_and_the_test_print_sends_it():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    assert 'name="check_amount_no_dollar_sign" value="true" data-bool' in js
    assert (
        "${s.check_amount_no_dollar_sign === 'true' ? 'checked' : ''}" in js
    ), "the box is ticked only when the setting is on"
    assert (
        '<label for="settings-check-amount-no-dollar-sign" '
        'style="font-weight:normal; margin:0;">'
        "Leave the dollar sign off the amount</label>"
    ) in js
    assert "'check_field_layout', 'check_amount_no_dollar_sign']" in js


# ----- the amount in figures ---------------------------------------------


def test_the_figures_with_and_without_the_sign():
    assert check_amount_figures(Decimal("1234.56")) == "$1,234.56"
    assert check_amount_figures(Decimal("1234.56"), True) == "1,234.56"
    assert check_amount_figures(Decimal("0.5"), True) == "0.50"
    assert check_amount_figures(Decimal("1000000"), True) == "1,000,000.00"


@pytest.mark.parametrize("position", ["bottom", "top"])
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_off_the_amount_prints_with_its_dollar_sign(stock, position):
    html = _html(check_position=position, **_STOCKS[stock])
    assert _OFF_BOX in html
    # "false" is the face it always was, byte for byte
    assert html == _html(
        check_position=position, check_amount_no_dollar_sign="false", **_STOCKS[stock]
    )


@pytest.mark.parametrize("position", ["bottom", "top"])
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_on_the_amount_prints_without_it_and_nothing_else_changes(stock, position):
    off = _html(check_position=position, **_STOCKS[stock])
    on = _html(
        check_position=position, check_amount_no_dollar_sign="true", **_STOCKS[stock]
    )
    assert _ON_BOX in on and _OFF_BOX not in on
    # the amount in words keeps its place and its text
    assert _WORDS in on
    # the one change is the amount box: the stubs and vouchers keep "$"
    assert on == off.replace(_OFF_BOX, _ON_BOX)
    assert on.count("$1,234.56") == off.count("$1,234.56") - 1 > 0


def test_on_the_amount_in_a_moved_and_sized_field_drops_its_sign_too():
    # the per-field layout (#749) wraps the value in a span; the span holds
    # the figures without the sign
    layout = '{"amount": {"size": 16}}'
    on = _html(check_field_layout=layout, check_amount_no_dollar_sign="true")
    assert '<span class="check-field-amount">1,234.56</span>' in on
    off = _html(check_field_layout=layout)
    assert '<span class="check-field-amount">$1,234.56</span>' in off


def test_on_every_check_of_a_batch_drops_the_sign():
    checks = [
        _check(),
        _check(check_number="1073", amount=Decimal("88.10"), details=[]),
    ]
    on = render_checks_html(checks, {**_COMPANY, "check_amount_no_dollar_sign": "true"})
    assert _ON_BOX in on
    assert '<div class="amount-box">88.10</div>' in on
    # the second check has no detail lines, so its stubs print "Payment $88.10"
    assert "$88.10" in on


def test_on_the_micr_amount_is_unchanged():
    # the MICR line's amount field never carried a dollar sign
    settings = {"check_blank_stock": "true", "check_micr_amount": "true"}
    off = _html(**settings)
    on = _html(check_amount_no_dollar_sign="true", **settings)
    assert on == off.replace(_OFF_BOX, _ON_BOX)


# ----- the test check ----------------------------------------------------


@pytest.fixture
def seen_html(monkeypatch):
    import app.routes.checks as checks
    from app.services.pdf_service import render_check_html

    seen = {}

    def fake_pdf(check_data, company):
        seen["html"] = render_check_html(check_data, company)
        return b"%PDF-fake"

    monkeypatch.setattr(checks, "generate_check_pdf", fake_pdf)
    return seen


def test_the_test_check_follows_the_saved_setting(client, seen_html):
    assert client.get("/api/checks/test-print").status_code == 200
    assert _OFF_BOX in seen_html["html"]
    client.put("/api/settings", json={"check_amount_no_dollar_sign": "true"})
    assert client.get("/api/checks/test-print").status_code == 200
    assert _ON_BOX in seen_html["html"]


@pytest.mark.parametrize("stock", list(_STOCKS))
def test_the_test_check_uses_the_unsaved_box_on_each_stock(client, seen_html, stock):
    params = {
        "check_stock": "preprinted" if stock == "preprinted" else "plain",
        "check_blank_stock": "true" if stock == "blank" else "false",
        "check_amount_no_dollar_sign": "true",
    }
    r = client.get("/api/checks/test-print", params=params)
    assert r.status_code == 200, r.text
    assert _ON_BOX in seen_html["html"]
    assert _WORDS in seen_html["html"]
    params["check_amount_no_dollar_sign"] = "false"
    assert client.get("/api/checks/test-print", params=params).status_code == 200
    assert _OFF_BOX in seen_html["html"]
    # nothing was saved
    assert client.get("/api/settings").json()["check_amount_no_dollar_sign"] == "false"


def test_the_test_check_refuses_a_bad_unsaved_value(client):
    r = client.get("/api/checks/test-print?check_amount_no_dollar_sign=maybe")
    assert r.status_code == 422, r.text
    assert (
        r.json()["detail"] == "check_amount_no_dollar_sign must be one of: false, true"
    )

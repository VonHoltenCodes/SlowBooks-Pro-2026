"""The pre-printed deposit slip prints a deposit's other lines (#883).

#798 lays a deposit's figures onto the bank's pre-printed slip; #792 adds
lines to a deposit for money that was never invoiced. The stock slip prints
those lines with the payments, after them in deposit order: a cash line adds
to the cash, any other line goes on a check line (with its check number when
it has one), and they count toward the check lines, the checks subtotal and
the total. A deposit of other lines alone prints the same way, the test
slip is unchanged, and the printed figures always add up to the deposit's
total.

Every test reads the rendered HTML (render_pdf replaced), so nothing new
renders with WeasyPrint and nothing skips on Windows.
"""

import re
from decimal import Decimal

import pytest

from app.services import pdf_service
from app.services.settings_service import DEPOSIT_SLIP_DEFAULTS

D = Decimal
_FIELD = re.compile(
    r'<div class="slip-field slip-(\w+)( slip-amount)?" data-field="\w+" '
    r'style="left: ([-\d.]+)in; top: ([-\d.]+)in; font-size: ([\d.]+)pt;">([^<]*)</div>'
)
SPACING = DEPOSIT_SLIP_DEFAULTS["check_lines"]["spacing"]
FIRST = DEPOSIT_SLIP_DEFAULTS["check_amount"]["y"]


def _pages(html):
    """Each page's figures: [(field, text, top)]."""
    return [
        [(f, text, float(top)) for f, _a, _l, top, _s, text in _FIELD.findall(page)]
        for page in html.split('<div class="slip-page">')[1:]
    ]


def _one(page, field):
    found = [text for f, text, _ in page if f == field]
    assert len(found) <= 1, (field, found)
    return found[0] if found else None


def _check_lines(page):
    """The page's check lines in line order: [(number or None, amount)]."""
    numbers = {round(top, 4): t for f, t, top in page if f == "check_number"}
    amounts = sorted((top, t) for f, t, top in page if f == "check_amount")
    return [(numbers.get(round(top, 4)), amount) for top, amount in amounts]


def _money(text):
    return D(text.replace(",", "")) if text else D("0")


@pytest.fixture
def as_html(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


@pytest.fixture
def preprinted(client):
    r = client.put("/api/settings", json={"deposit_slip_stock": "preprinted"})
    assert r.status_code == 200, r.text


@pytest.fixture
def acme(client):
    return client.post("/api/customers", json={"name": "Acme Diner"}).json()["id"]


def _receive(client, cid, amount, check=None, method="Check"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-09-10",
            "amount": amount,
            "method": method,
            "check_number": check,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text


def _deposit(client, accounts, take_pending=True, other_lines=()):
    pending = client.get("/api/deposits/pending").json() if take_pending else []
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": accounts["1000"].id,
            "date": "2026-09-12",
            "reference": "SLIP-883",
            "line_ids": [p["transaction_line_id"] for p in pending],
            "other_lines": list(other_lines),
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["transaction_id"]


def _slip(client, deposit_id):
    html = client.get(f"/api/deposits/{deposit_id}/pdf").text
    return _pages(html), client.get(f"/api/deposits/{deposit_id}").json()


def _adds_up(pages, detail):
    """The printed figures add up to the deposit's total: the cash plus the
    check lines is the total, the check lines are the subtotal."""
    first = pages[0]
    lines = [ln for page in pages for ln in _check_lines(page)]
    checks = sum((_money(a) for _, a in lines), D("0"))
    cash = _money(_one(first, "cash"))
    total = _money(_one(first, "total"))
    if lines:
        assert _money(_one(first, "checks_subtotal")) == checks
    else:
        assert _one(first, "checks_subtotal") is None
    assert cash + checks == total == D(str(detail["amount"]))
    return lines, cash, total


def _other(accounts, code, amount, number=None, method="Check", **extra):
    return {
        "account_id": accounts[code].id,
        "amount": amount,
        "check_number": number,
        "method": method,
        **extra,
    }


def test_payments_and_other_lines_print_on_one_slip(
    client, seed_accounts, acme, preprinted, as_html
):
    _receive(client, acme, "300.00", check="4420")
    _receive(client, acme, "100.00", check="4421")
    _receive(client, acme, "25.00", method="Cash")
    dep = _deposit(
        client,
        seed_accounts,
        other_lines=[
            _other(seed_accounts, "6300", "45.50", "88123", received_from="Refund"),
            _other(seed_accounts, "4900", "10.00", method="Cash", memo="Bake sale"),
            _other(seed_accounts, "4900", "1200.00", "5001", memo="Oven rebate"),
            _other(seed_accounts, "4900", "20.00", method=None, memo="No method"),
        ],
    )
    pages, detail = _slip(client, dep)
    assert len(pages) == 1
    lines, cash, total = _adds_up(pages, detail)
    # the payments' checks first, then the other lines in deposit order
    assert lines == [
        ("4420", "300.00"),
        ("4421", "100.00"),
        ("88123", "45.50"),
        ("5001", "1,200.00"),
        (None, "20.00"),
    ]
    assert cash == D("35.00") and total == D("1700.50")
    assert _one(pages[0], "checks_subtotal") == "1,665.50"
    # the same check-line spacing the payments use
    tops = sorted(top for f, _, top in pages[0] if f == "check_amount")
    assert [round(t, 4) for t in tops] == [
        round(FIRST + i * SPACING, 4) for i in range(5)
    ]


def test_a_deposit_of_other_lines_alone_prints_the_same_way(
    client, seed_accounts, preprinted, as_html
):
    dep = _deposit(
        client,
        seed_accounts,
        take_pending=False,
        other_lines=[
            _other(seed_accounts, "4900", "120.00", "5001"),
            _other(seed_accounts, "4900", "15.00", method="Cash"),
            _other(seed_accounts, "6300", "45.50", "88123"),
        ],
    )
    pages, detail = _slip(client, dep)
    assert len(pages) == 1
    lines, cash, total = _adds_up(pages, detail)
    assert lines == [("5001", "120.00"), ("88123", "45.50")]
    assert cash == D("15.00") and total == D("180.50")
    assert _one(pages[0], "date") == "09/12/2026"
    assert _one(pages[0], "account") == "Checking"


def test_a_cash_other_line_with_a_check_number_is_a_check_line(
    client, seed_accounts, preprinted, as_html
):
    dep = _deposit(
        client,
        seed_accounts,
        take_pending=False,
        other_lines=[_other(seed_accounts, "4900", "60.00", "7007", method="Cash")],
    )
    pages, detail = _slip(client, dep)
    lines, cash, _ = _adds_up(pages, detail)
    assert lines == [("7007", "60.00")] and cash == 0
    assert _one(pages[0], "cash") is None


def test_a_deposit_of_payments_alone_is_unchanged(
    client, seed_accounts, acme, preprinted, as_html
):
    _receive(client, acme, "332.30", check="4420")
    _receive(client, acme, "100.00", check="4421")
    _receive(client, acme, "25.00", method="Cash")
    pages, detail = _slip(client, _deposit(client, seed_accounts))
    lines, cash, total = _adds_up(pages, detail)
    assert lines == [("4420", "332.30"), ("4421", "100.00")]
    assert cash == D("25.00") and total == D("457.30")
    assert len(pages[0]) == 9  # date, account, cash, two check lines, the totals


def test_other_lines_past_the_slips_lines_continue_in_order(
    client, seed_accounts, acme, preprinted, as_html
):
    r = client.put(
        "/api/settings", json={"deposit_slip_layout": '{"check_lines":{"count":2}}'}
    )
    assert r.status_code == 200, r.text
    _receive(client, acme, "300.00", check="4420")
    dep = _deposit(
        client,
        seed_accounts,
        other_lines=[
            _other(seed_accounts, "4900", "10.00", "1"),
            _other(seed_accounts, "4900", "20.00", "2"),
            _other(seed_accounts, "4900", "30.00", "3"),
        ],
    )
    pages, detail = _slip(client, dep)
    assert [_check_lines(p) for p in pages] == [
        [("4420", "300.00"), ("1", "10.00")],
        [("2", "20.00"), ("3", "30.00")],
    ]
    _adds_up(pages, detail)
    # the subtotal and total print on the first page only
    assert _one(pages[1], "total") is None


def test_the_test_slip_is_unchanged(client, as_html):
    pages = _pages(client.get("/api/deposits/test-slip").text)
    (page,) = pages
    assert _check_lines(page) == [("1001", "1,234.56"), ("1002", "250.00")]
    assert _one(page, "cash") == "75.00"
    assert _one(page, "checks_subtotal") == "1,484.56"
    assert _one(page, "total") == "1,559.56"

"""A vendor's card says one thing about what was paid (#491), in a real
browser with the API stubbed: paid this year and all time count bill
payments and bank-feed entries both, and the payment history under it
(#569) lists both, so a vendor paid only through the feed no longer reads
"Paid this year: $57.00" over "No payments yet". With nothing paid it says
so once."""

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

YEAR = dt.date.today().year


@pytest.fixture(autouse=True)
def _this_year():
    """The app reads its own today on every request, so the test's year is
    read when each test starts, not once at import: a run that crosses New
    Year's must not leave the later tests a year behind the app."""
    global YEAR
    YEAR = dt.date.today().year


VENDOR = {"id": 7, "name": "QA Paper Co", "balance": "0", "is_active": True}


def _entry(tid, amount):
    return {
        "kind": "bank_entry",
        "id": tid,
        "date": f"{YEAR}-03-0{tid % 9 + 1}",
        "method": "Bank entry",
        "reference": "",
        "paid_from": "Checking",
        "memo": "",
        "amount": amount,
        "is_voided": False,
        "link": f"/#/banking/1?entry={tid}",
    }


def _history(rows):
    """What /payment-history answers for the rows, year to date."""
    total = 0.0
    for r in rows:
        total += float(r["amount"])
        r["running_total"] = f"{total:.2f}"
    return {
        "vendor_id": 7,
        "start_date": f"{YEAR}-01-01",
        "end_date": dt.date.today().isoformat(),
        "rows": rows,
        "totals": {
            "bill_payment": "0.00",
            "expense": "0.00",
            "bank_entry": f"{total:.2f}",
        },
        "total": f"{total:.2f}",
        "all_time": f"{total:.2f}",
    }


def _card(browser, payments, entries):
    page = _open(browser, 1280, 900, "#/")
    page.route(
        "**/api/vendors/7/payment-history*",
        lambda r: r.fulfill(json=_history(payments + entries)),
    )
    page.route("**/api/vendors/7", lambda r: r.fulfill(json=VENDOR))
    page.route("**/api/bills?*", lambda r: r.fulfill(json=[]))
    page.route("**/api/vendor-credits?*", lambda r: r.fulfill(json=[]))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=[]))
    page.evaluate("() => VendorsPage.showDetails(7)")
    page.wait_for_selector("text=Paid this year")
    text = page.evaluate("() => document.getElementById('modal-body').innerText")
    heads = page.evaluate(
        "() => [...document.querySelectorAll('#modal-body h4, #modal-body h5')].map(h => h.textContent.trim())"
    )
    page.close()
    return text, heads


def test_feed_entries_are_the_payments_the_paid_line_counts(browser):
    text, heads = _card(browser, [], [_entry(1, "45.00"), _entry(2, "12.00")])
    assert "Paid this year: $57.00" in text
    assert "No payments yet" not in text
    assert "Payment history (2)" in heads
    assert text.count("Bank entry") == 2
    assert not any(h.startswith("Recent payments") for h in heads)


def test_nothing_paid_says_so_once(browser):
    text, heads = _card(browser, [], [])
    assert "Paid this year: $0.00" in text
    assert text.count("No payments yet") == 1
    assert "Payment history (0)" in heads

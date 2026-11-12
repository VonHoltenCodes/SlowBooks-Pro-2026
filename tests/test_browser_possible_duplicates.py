"""The Possible Duplicates report in a real browser (#414), on the bakery's
books: the Report Center's card opens it with its own address, a group of
two copies shows each entry with Open and Void, "Not a duplicate" takes the
group off (and "Show groups marked not a duplicate" brings it back with Put
back), and Void posts the reversal through the entry's own route."""

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import _hash, _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

TITLE = "() => document.getElementById('modal-title').textContent"
GROUPS = "() => document.querySelectorAll('#report-content tr.dup-group').length"


def _entry(client, account_id, category_id, payee, amount="-42.00"):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": account_id,
            "date": dt.date.today().isoformat(),
            "amount": amount,
            "category_account_id": category_id,
            "payee": payee,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_card_opens_the_report_and_its_buttons_work(
    browser, company, seed_accounts
):
    checking = seed_accounts["1000"].id
    category = seed_accounts["6000"].id
    _entry(company, checking, category, "Flour Mill Supply")
    _entry(company, checking, category, "FLOUR MILL SUPPLY #12")
    _entry(company, checking, category, "Sparkle Window Co", "-90.00")
    keep = _entry(company, checking, category, "Sparkle Window Co", "-90.00")

    page, handled = _open_at(browser, company, "#/reports")
    try:
        page.click("#all-reports-toggle")
        page.click("a[data-row-key='report:possible-duplicates']")
        page.wait_for_selector("#report-content tr.dup-group")
        settle(page, handled)
        assert page.evaluate(TITLE) == "Possible Duplicates"
        assert _hash(page).startswith("#/reports/possible-duplicates?account_id=")
        assert page.locator("#dup-account").input_value() == str(checking)
        assert page.evaluate(GROUPS) == 2
        group = page.locator("tr.dup-group").first
        assert "Payment" in group.inner_text() and "2 entries" in group.inner_text()
        # each entry: its source, an Open link and a Void button
        assert page.locator("tr[data-dup-entry] a:has-text('Open')").count() == 4
        assert page.locator("tr[data-dup-entry] button:has-text('Void')").count() == 4
        assert page.locator("#report-content button:has-text('Save CSV')").count() == 1

        # not a duplicate: the window cleaner's two visits
        sparkle = page.locator("tr.dup-group", has_text="$90.00")
        sparkle.locator("button:has-text('Not a duplicate')").click()
        page.wait_for_function(f"{GROUPS} === 1")
        settle(page, handled)
        assert "1 marked not a duplicate" in page.inner_text("#report-content")
        page.check("#dup-show-dismissed")
        page.wait_for_function(f"{GROUPS} === 2")
        settle(page, handled)
        assert "include_dismissed=1" in _hash(page)
        assert page.locator("button:has-text('Put back')").count() == 1
        # the line counts the shown marked group apart, never "0 groups"
        # above it (2.27.0 gate, W-32, #458)
        line = page.inner_text("#report-content")
        assert "1 group · 1 marked not a duplicate (shown)" in line, line
        page.uncheck("#dup-show-dismissed")
        page.wait_for_function(f"{GROUPS} === 1")

        # void one copy of the flour delivery: the group is gone
        page.once("dialog", lambda d: d.accept())
        page.locator("tr[data-dup-entry] button:has-text('Void')").first.click()
        page.wait_for_function(f"{GROUPS} === 0")
        settle(page, handled)
        assert "No possible duplicates" in page.inner_text("#report-content")
    finally:
        page.close()
    assert keep["id"]


def test_a_read_only_sign_in_sees_no_write_buttons(
    browser, company, seed_accounts, db_session
):
    from app.models.users import ROLE_READONLY, User
    from app.services import auth as auth_service

    checking = seed_accounts["1000"].id
    category = seed_accounts["6000"].id
    _entry(company, checking, category, "Flour Mill Supply")
    _entry(company, checking, category, "Flour Mill Supply")
    pw = "reader-password-123"
    db_session.add(
        User(
            username="reader",
            display_name="Reader",
            password_hash=auth_service.hash_password(pw),
            role=ROLE_READONLY,
            is_active=True,
        )
    )
    db_session.commit()
    company.post("/api/auth/logout")
    assert (
        company.post(
            "/api/auth/login", json={"username": "reader", "password": pw}
        ).status_code
        == 200
    )
    page, handled = _open_at(
        browser, company, f"#/reports/possible-duplicates?account_id={checking}"
    )
    try:
        page.wait_for_selector("#report-content tr.dup-group")
        settle(page, handled)
        assert page.locator("tr[data-dup-entry] a:has-text('Open')").count() == 2
        for label in ("Void", "Not a duplicate"):
            assert (
                page.locator(
                    f"#report-content button:visible:has-text('{label}')"
                ).count()
                == 0
            ), label
    finally:
        page.close()

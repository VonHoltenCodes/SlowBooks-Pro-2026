"""Bank Feeds with no bank account in the chart (#718), in a real browser
against the real routes (the SimpleFIN bridge is never called: the accounts
it found are the cached ones).

A connected feed whose "Imports into" list held only "— not imported —"
looked broken, and an administrator spent a week unable to map it: the
checking account was in the chart as a plain asset, which is never offered.
The row now says why and offers + New bank account and Use an account I
already have…; the pick becomes a Bank account and the feed is mapped to it
in one step. A sign-in that may not change the mapping reads it as text."""

import json
from urllib.parse import urlsplit

import pytest

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.services import simplefin_service as sf
from app.services.settings_service import get_setting_raw, set_setting
from tests.test_simplefin import ACCESS_URL

sync_api = pytest.importorskip("playwright.sync_api")

NO_BANK = "No account in your chart is marked as a bank or credit-card account yet."
FOUND = {
    "errors": [],
    "accounts": [
        {
            "id": "ACT-1",
            "name": "Demo Checking",
            "org": {"name": "Demo Bank"},
            "currency": "USD",
            "balance": "1234.56",
            "transactions": [],
        },
        {
            "id": "ACT-2",
            "name": "Demo Savings",
            "org": {"name": "Demo Bank"},
            "currency": "USD",
            "balance": "50.00",
            "transactions": [],
        },
    ],
}


def _connected(db_session, account_map=None):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    set_setting(
        db_session, "simplefin_accounts_cache", json.dumps(sf.account_summaries(FOUND))
    )
    set_setting(db_session, "simplefin_account_map", json.dumps(account_map or {}))
    db_session.commit()


def _map(db_session):
    db_session.expire_all()
    return json.loads(get_setting_raw(db_session, "simplefin_account_map") or "{}")


def _feed_for(db_session, account_id):
    db_session.expire_all()
    return (
        db_session.query(BankAccount).filter(BankAccount.account_id == account_id).one()
    )


def _browse(client, test):
    """Open Banking with every request answered by the app, then run
    test(page, dialogs). `dialogs` collects each confirm()'s message, which
    is accepted."""
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors, dialogs = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)

        def serve(route):
            url = urlsplit(route.request.url)
            if url.hostname != "testserver":
                return route.abort()
            response = client.request(
                route.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=route.request.post_data_buffer,
                headers={
                    "Content-Type": route.request.headers.get(
                        "content-type", "application/json"
                    )
                },
            )
            route.fulfill(
                status=response.status_code,
                body=response.content,
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
            )

        page.route("**/*", serve)
        try:
            page.goto("http://testserver/#/banking")
            page.locator("#splash-dismiss").click()
            test(page, dialogs)
            assert errors == []
        finally:
            browser.close()


def _options(select):
    return select.evaluate("s => [...s.options].map(o => o.text.trim())")


def _row(page, name):
    return page.locator("tbody tr", has_text=name)


def test_a_chart_with_no_bank_account_says_so_and_the_pick_maps_the_feed(
    client, db_session
):
    checking = Account(name="Checking", account_type=AccountType.ASSET)
    db_session.add(checking)
    db_session.add(Account(name="Shop Loan", account_type=AccountType.LIABILITY))
    db_session.commit()
    assert checking.detail_type == "other_current_asset" and not checking.bank_kind
    _connected(db_session)

    def test(page, dialogs):
        row = _row(page, "Demo Checking")
        row.get_by_text(NO_BANK, exact=True).wait_for()
        # no dropdown with nothing in it
        assert (
            page.get_by_role("combobox", name="Import Demo Checking into").count() == 0
        )
        assert row.get_by_role("button", name="+ New bank account").is_visible()
        row.get_by_role("button", name="Use an account I already have…").click()
        dialog = page.locator("#modal-body")
        chooser = dialog.locator("#own-account")
        chooser.wait_for()
        # assets become bank accounts, liabilities credit-card accounts
        assert _options(chooser) == ["Checking", "Shop Loan"]
        chooser.select_option(label="Checking")
        dialog.get_by_role("button", name="Use this account").click()
        page.get_by_role("combobox", name="Import Demo Checking into").wait_for()
        assert dialogs == [
            "Checking will become a bank account and get a register. "
            "Demo Checking will import into it."
        ]
        mapping = page.get_by_role("combobox", name="Import Demo Checking into")
        assert mapping.locator("option:checked").inner_text() == "Checking"
        # the other row is still not imported, and now has the list too
        other = page.get_by_role("combobox", name="Import Demo Savings into")
        assert other.input_value() == ""
        assert page.get_by_text(NO_BANK).count() == 0

    _browse(client, test)
    db_session.expire_all()
    db_session.refresh(checking)
    assert (checking.detail_type, checking.bank_kind) == ("bank", "bank")
    assert _map(db_session) == {"ACT-1": _feed_for(db_session, checking.id).id}


def test_new_bank_account_from_the_row_is_where_that_row_imports(client, db_session):
    _connected(db_session)

    def test(page, dialogs):
        row = _row(page, "Demo Savings")
        row.get_by_text(NO_BANK, exact=True).wait_for()
        row.get_by_role("button", name="+ New bank account").click()
        dialog = page.locator("#modal-body")
        dialog.locator("input[name=new_name]").fill("Savings")
        dialog.locator("input[name=name]").fill("Demo Savings")
        dialog.get_by_role("button", name="Create").click()
        mapping = page.get_by_role("combobox", name="Import Demo Savings into")
        mapping.wait_for()
        # the chart account, its feed's own name after it
        assert (
            mapping.locator("option:checked").inner_text() == "Savings (Demo Savings)"
        )
        assert (
            page.get_by_role("combobox", name="Import Demo Checking into").input_value()
            == ""
        )

    _browse(client, test)
    savings = db_session.query(Account).filter(Account.name == "Savings").one()
    assert (savings.detail_type, savings.bank_kind) == ("bank", "bank")
    assert _map(db_session) == {"ACT-2": _feed_for(db_session, savings.id).id}


def test_a_list_with_accounts_still_offers_one_already_in_the_chart(client, db_session):
    bank = Account(name="Operating", account_type=AccountType.ASSET, bank_kind="bank")
    card = Account(name="Old Card", account_type=AccountType.LIABILITY)
    db_session.add_all([bank, card])
    db_session.commit()
    _connected(db_session)

    def test(page, dialogs):
        mapping = page.get_by_role("combobox", name="Import Demo Savings into")
        mapping.wait_for()
        assert _options(mapping) == [
            "— not imported —",
            "Operating",
        ]
        row = _row(page, "Demo Savings")
        row.get_by_role("button", name="Use an account I already have…").click()
        page.locator("#own-account").select_option(label="Old Card")
        page.locator("#modal-body").get_by_role(
            "button", name="Use this account"
        ).click()
        page.wait_for_function(
            """() => [...document.querySelectorAll('.simplefin-map')]
                .some(s => s.dataset.sfid === 'ACT-2' && s.value.startsWith('acct:'))"""
        )
        assert dialogs == [
            "Old Card will become a credit-card account and get a register. "
            "Demo Savings will import into it."
        ]

    _browse(client, test)
    db_session.refresh(card)
    assert (card.detail_type, card.bank_kind) == ("credit_card", "credit_card")
    assert _map(db_session) == {"ACT-2": _feed_for(db_session, card.id).id}


def test_a_bookkeeper_reads_the_mapping_as_text(client, db_session):
    from tests.test_rbac_users import _login_as, _mk_user

    checking = Account(
        name="Operating", account_type=AccountType.ASSET, bank_kind="bank"
    )
    db_session.add(checking)
    db_session.commit()
    feed = BankAccount(name="Operating", account_id=checking.id)
    db_session.add(feed)
    db_session.commit()
    _connected(db_session, {"ACT-1": feed.id})
    _mk_user(db_session, "keeper", "a-test-password-123", "bookkeeper")
    _login_as(client, "keeper", "a-test-password-123")

    def test(page, dialogs):
        page.get_by_text(
            "Only an administrator can change where a feed imports.", exact=False
        ).wait_for()
        assert page.locator("select.simplefin-map").count() == 0
        assert _row(page, "Demo Checking").locator(
            ".feed-imports-into"
        ).inner_text() == ("Operating")
        assert _row(page, "Demo Savings").locator(
            ".feed-imports-into"
        ).inner_text() == ("Not imported")
        assert (
            page.get_by_role("button", name="Use an account I already have…").count()
            == 0
        )
        assert page.get_by_text(NO_BANK).count() == 0

    _browse(client, test)

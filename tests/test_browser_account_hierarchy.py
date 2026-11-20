"""Sub-accounts on the Chart of Accounts page (#416, the chart half), in
playwright's Chromium on the bakery's books:

- a sub-account is listed under its parent, indented, with a
  screen-reader note naming the parent; a leaf name two accounts share
  shows its full path, and so does a sub-account the filter leaves
  showing without its parent;
- the form's "Sub-account of" picker opens on the parent the account has,
  offers only accounts of its type and never the account or one under it;
- clearing it and choosing one save; a type the parent cannot take lets
  the parent go, and a type it can brings it back; a control account is
  offered none; the server's refusal is written in the form.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def _new(client, name, atype="expense", **extra):
    r = client.post(
        "/api/accounts", json={"name": name, "account_type": atype, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _row(page, account_id):
    return page.locator(f'tr[data-account-id="{account_id}"]')


def _options(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('#account-parent option')]
            .map(o => [o.value, o.textContent])"""
    )


def _saved(page, handled):
    page.click("form button[type=submit]")
    page.wait_for_function(
        "() => document.getElementById('modal-overlay').classList.contains('hidden')"
    )
    settle(page, handled)


def test_the_chart_nests_sub_accounts_and_the_form_picks_a_parent(
    browser, company, books
):
    travel = _new(company, "Travel", account_number="8810")
    hotel = _new(company, "Hotel", account_number="8811", parent_id=travel["id"])
    meals = _new(company, "Meals out", account_number="8820")
    hotel2 = _new(company, "Hotel", account_number="8821", parent_id=meals["id"])
    fuel = _new(company, "Fuel")  # unnumbered, so listed after the numbered ones
    parking = _new(company, "Parking", account_number="8812", parent_id=travel["id"])
    page, handled = _open_at(browser, company, "#/accounts")
    try:
        page.wait_for_selector(f'tr[data-account-id="{hotel["id"]}"]')
        ids = page.evaluate(
            """() => [...document.querySelectorAll('tr[data-account-id]')]
                .map(r => Number(r.dataset.accountId))"""
        )
        assert ids.index(travel["id"]) + 1 == ids.index(hotel["id"])
        assert ids.index(meals["id"]) + 1 == ids.index(hotel2["id"])
        cell = _row(page, hotel["id"]).locator("td").nth(1)
        assert cell.locator(".visually-hidden").text_content() == (
            ", sub-account of Travel"
        )
        # two Hotels: each says its full path, and the link's title does too
        assert cell.locator(".account-path").text_content() == "Travel: Hotel"
        assert cell.locator("a").get_attribute("title") == (
            "Open the register: Travel: Hotel"
        )
        assert _row(page, travel["id"]).locator(".account-path").count() == 0
        # Parking, under Travel and its own name: no path while Travel shows
        path = _row(page, parking["id"]).locator(".account-path")
        assert not path.is_visible()
        # ...and its path when the filter hides Travel
        page.fill("#accounts-filter", "parking")
        assert not _row(page, travel["id"]).is_visible()
        assert path.is_visible() and path.text_content() == "Travel: Parking"
        # the filter finds a sub-account by its parent's name
        page.fill("#accounts-filter", "travel")
        assert _row(page, hotel["id"]).is_visible()
        assert not _row(page, fuel["id"]).is_visible()
        page.fill("#accounts-filter", "")
        page.evaluate("App.filterAccounts('')")

        # the edit form opens on Hotel's parent, offering expenses only
        page.evaluate(f"App.showAccountForm({hotel['id']})")
        page.wait_for_selector("#account-parent")
        assert page.input_value("#account-parent") == str(travel["id"])
        opts = dict(_options(page))
        assert opts[""] == "None (a top-level account)"
        assert opts[str(travel["id"])] == "8810 Travel"
        assert opts[str(hotel2["id"])] == "8821 Meals out: Hotel"
        assert str(hotel["id"]) not in opts  # never itself
        accounts = company.get("/api/accounts").json()
        assert all(
            a["account_type"] == "expense" for a in accounts if str(a["id"]) in opts
        )
        assert page.get_by_label("Sub-account of").count() == 1
        hint = page.get_attribute("#account-parent", "aria-describedby")
        assert hint == "account-parent-hint"
        # the note behind the tortoise (#977), not a live region; the field
        # is described by the note's text (NEW-263)
        assert page.get_attribute("#account-parent-hint-note", "role") == "note"
        assert page.get_attribute("#account-parent-hint", "class") == "hint-pop-body"
        assert page.get_attribute("#account-parent-status", "role") == "status"

        # a type round trip: Other Expense lets Travel go, Expense brings it
        # back, and an untouched save keeps it
        page.select_option("select[name=detail_type]", "other_expense")
        assert page.input_value("#account-parent") == ""
        assert "No longer under Travel" in page.text_content("#account-parent-status")
        page.select_option("select[name=detail_type]", "expense")
        assert page.input_value("#account-parent") == str(travel["id"])
        assert page.text_content("#account-parent-status") == ""
        _saved(page, handled)
        assert company.get(f"/api/accounts/{hotel['id']}").json()["parent_id"] == (
            travel["id"]
        )
        page.evaluate(f"App.showAccountForm({hotel['id']})")
        page.wait_for_selector("#account-parent")

        # clear it and save: top level
        page.select_option("#account-parent", "")
        _saved(page, handled)
        assert company.get(f"/api/accounts/{hotel['id']}").json()["parent_id"] is None

        # Travel's form never offers an account under Travel
        airfare = _new(company, "Airfare", parent_id=travel["id"])
        page.evaluate(f"App.showAccountForm({travel['id']})")
        page.wait_for_selector("#account-parent")
        values = [v for v, _t in _options(page)]
        assert str(airfare["id"]) not in values and str(travel["id"]) not in values
        assert str(meals["id"]) in values

        # a stale list: the server's refusal shows in the form, as a sentence
        page.evaluate(
            f"""() => {{ const s = document.getElementById('account-parent');
                s.add(new Option('Airfare', '{airfare['id']}')); s.value = '{airfare['id']}'; }}"""
        )
        page.click("form button[type=submit]")
        page.wait_for_function(
            "() => document.getElementById('account-form-error').textContent"
        )
        assert page.text_content("#account-form-error") == (
            "That would make Travel a sub-account of itself (through Airfare)."
        )
        assert page.get_attribute("#account-form-error", "role") == "alert"
        page.evaluate("closeModal()")

        # a new account under Travel, from the picker
        page.evaluate("App.showAccountForm()")
        page.wait_for_selector("#account-parent")
        page.fill("input[name=name]", "Tolls")
        page.select_option("#account-parent", str(travel["id"]))
        _saved(page, handled)
        tolls = next(
            a for a in company.get("/api/accounts").json() if a["name"] == "Tolls"
        )
        assert (tolls["parent_id"], tolls["detail_type"]) == (travel["id"], "expense")

        # Other Expense: Travel (Expense) cannot take it, so Tolls moves out
        # and changes type in the one save
        page.evaluate(f"App.showAccountForm({tolls['id']})")
        page.wait_for_selector("#account-parent")
        page.select_option("select[name=detail_type]", "other_expense")
        assert page.input_value("#account-parent") == ""
        assert "No longer under Travel" in page.text_content("#account-parent-status")
        _saved(page, handled)
        moved = company.get(f"/api/accounts/{tolls['id']}").json()
        assert (moved["parent_id"], moved["detail_type"]) == (None, "other_expense")
        # a control account is found by its number: no parent offered
        ar = next(
            a
            for a in company.get("/api/accounts").json()
            if a["account_number"] == "1100"
        )
        page.evaluate(f"App.showAccountForm({ar['id']})")
        page.wait_for_selector("#account-parent")
        assert _options(page) == [["", "None (a top-level account)"]]
        assert "stays a top-level account" in page.text_content("#account-parent-hint")
    finally:
        page.close()


def test_moving_a_subtree_says_how_far_the_retype_reaches(browser, company, books):
    """A move that hands a parent's detail type down (#428's cascade) says how
    many sub-accounts follow and that moving back out keeps the type; set to a
    top-level or pinned parent, the status says the type stays (#416)."""
    travel = _new(company, "Travel", account_number="8810")
    lodging = _new(company, "Lodging", account_number="8811", parent_id=travel["id"])
    _new(company, "Hotel", account_number="8812", parent_id=lodging["id"])
    interest = _new(
        company, "Interest", account_number="8000", detail_type="other_expense"
    )
    page, handled = _open_at(browser, company, "#/accounts")
    try:
        page.evaluate(f"App.showAccountForm({travel['id']})")
        page.wait_for_selector("#account-parent")
        # under Interest (Other Expense): both sub-accounts follow, and a
        # later move out keeps the type
        page.select_option("#account-parent", str(interest["id"]))
        status = page.text_content("#account-parent-status")
        assert "Type set to Other Expense, the type of Interest" in status
        assert "2 sub-accounts" in status and "follow" in status
        assert "keeps the type" in status
        assert page.input_value("select[name=detail_type]") == "other_expense"
        # back to None: the type and its sub-accounts stay
        page.select_option("#account-parent", "")
        status = page.text_content("#account-parent-status")
        assert "Travel keeps Other Expense" in status
        assert "2 sub-accounts" in status
        assert "above the Other lines" in status

        # the count is of the sub-accounts the cascade retypes — those sharing
        # the account's detail type. A sub-account of another type (a pinned
        # bank, or a divergent pair an import left behind) stops the walk and
        # is not counted, as _cascade_detail_type stops (#416). The account
        # API forbids making a divergent pair, so this exercises the tree
        # helper against the shape legacy/imported data can take.
        counts = page.evaluate("""() => {
                const accts = [
                    {id: 1, name: 'Reserves', parent_id: null,
                     detail_type: 'other_current_asset'},
                    {id: 2, name: 'Float', parent_id: 1,
                     detail_type: 'other_current_asset'},
                    {id: 3, name: 'Vault', parent_id: 1, detail_type: 'bank'},
                    {id: 4, name: 'Coins', parent_id: 3,
                     detail_type: 'other_current_asset'},
                ];
                const t = App._accountTree(accts);
                const a = accts[0];
                return {
                    sharing: t.sharing(a, a.detail_type).map(s => s.name),
                    descendants: t.descendants(a).map(s => s.name),
                };
            }""")
        assert counts["sharing"] == ["Float"]
        assert counts["descendants"] == ["Float", "Vault", "Coins"]
    finally:
        page.close()

"""The register's category pickers name a sub-account by its path.

A sub-account "8127" of "Phone Lines" was listed as "8127" at the top of
the register's Category pickers, while the Chart of Accounts shows it
nested. The quick-entry row, the To review lines and the bar above them,
the + Entry form and its split lines now read "Phone Lines: 8127", with
the sub-account under its parent, as the chart lists it; a parent that is
inactive still names its sub-account and is not offered itself. The
type-ahead box finds the sub-account by its own name and by its parent's.

Real books (the bakery's), real server, playwright's Chromium; skipped,
as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _account(company, **fields):
    return _ok(
        company.post("/api/accounts", json={"account_type": "expense", **fields})
    )


def _texts(page, selector):
    """The option texts of the first select the selector finds."""
    options = page.locator(selector).first.locator("option")
    return [t.strip() for t in options.all_inner_texts() if t.strip()]


def _check(texts):
    """The sub-account by its path, right under its parent; the inactive
    parent's sub-account by its path, the parent itself not offered."""
    assert "8127" not in texts
    assert "Stamps" not in texts
    parent = texts.index("6420 - Phone Lines")
    assert texts[parent + 1] == "Phone Lines: 8127"
    assert "Old Postage: Stamps" in texts
    assert not any(t.endswith("- Old Postage") for t in texts)


def test_every_register_category_picker_names_the_path(browser, company, books):
    phone = _account(company, name="Phone Lines", account_number="6420")
    _account(company, name="8127", parent_id=phone["id"])
    postage = _account(company, name="Old Postage", account_number="6430")
    _account(company, name="Stamps", parent_id=postage["id"])
    _ok(company.put(f"/api/accounts/{postage['id']}", json={"is_active": False}))

    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_function(
            "() => document.getElementById('bulk-cat').options.length > 1"
            " && document.getElementById('qe-category')",
            timeout=5000,
        )
        # the quick-entry row's Category…
        _check(_texts(page, "#qe-category"))
        # the narrow box's tooltip is the whole path once one is chosen
        child = page.evaluate(
            "() => [...document.getElementById('qe-category').options]"
            ".find(o => o.text === 'Phone Lines: 8127').value"
        )
        page.select_option("#qe-category", child)
        assert page.get_attribute("#qe-category", "title") == "Phone Lines: 8127"

        # To review: the bar above the list and a line's own picker
        _check(_texts(page, "#bulk-cat"))
        _check(_texts(page, "select.review-cat"))
        # the groups stay
        groups = page.locator("#bulk-cat optgroup").evaluate_all(
            "gs => gs.map(g => g.label)"
        )
        assert groups[0] == "Categories"

        # + Entry's Category
        page.click("button:text-is('+ Entry')")
        page.wait_for_selector("#entry-category", state="attached")
        _check(_texts(page, "#entry-category"))

        # the type-ahead box finds it by its own name and by its parent's
        for typed in ("8127", "phone lines"):
            box = page.locator("#entry-category-box")
            box.fill("")
            box.click()
            page.keyboard.type(typed)
            page.wait_for_function(
                "() => [...document.querySelectorAll('#cbx-listbox [role=option]')]"
                ".some(o => o.textContent.includes('Phone Lines: 8127'))",
                timeout=5000,
            )
            page.keyboard.press("Escape")

        # the split lines reuse the form's list
        page.click("#entry-split-toggle")
        page.wait_for_selector("select.split-cat", state="attached")
        _check(_texts(page, "select.split-cat"))
        settle(page, handled)
    finally:
        page.close()


def test_an_account_quick_added_under_a_quick_added_one_names_its_path(
    browser, company, books
):
    """Two accounts quick-added in a row from the quick-entry row, the second
    under the first: the second is named by its whole path too, not by its
    own name alone (the first was not in the chart the paths are read from
    until the page was drawn again)."""
    phone = _account(company, name="Phone Lines", account_number="6420")

    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_selector("#qe-category")

        def quick_add(name, parent_id):
            page.click("#qe-category-add")
            page.wait_for_selector("#qe-category-new", state="visible")
            page.fill("#qe-category-new-name", name)
            page.select_option("#qe-category-new-type", "expense")
            page.select_option("#qe-category-new-parent", str(parent_id))
            page.click("#qe-category-new >> text=Add account")
            page.wait_for_function(
                "n => { const s = document.getElementById('qe-category');"
                " const o = s.options[s.selectedIndex];"
                " return o && o.text.endsWith(n); }",
                arg=name,
            )
            settle(page, handled)
            return page.evaluate(
                "() => { const s = document.getElementById('qe-category');"
                " return [s.value, s.options[s.selectedIndex].text, s.title]; }"
            )

        first = quick_add("Long Distance", phone["id"])
        assert first[1:] == ["Phone Lines: Long Distance"] * 2
        second = quick_add("Night Rate", first[0])
        assert second[1:] == ["Phone Lines: Long Distance: Night Rate"] * 2
        settle(page, handled)
    finally:
        page.close()

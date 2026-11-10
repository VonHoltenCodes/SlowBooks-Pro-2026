"""The Turn on company file encryption dialog says what it asks
(Discussion #750, 2.31.11): the company's name exactly as Settings shows
it, with a line under the box that says when what was typed is not it
yet, and the password the administrator signed in with, not a new one.
On a server with no key source the page says which .env the secret goes
in. Skipped, as one module, where playwright or its Chromium is not
installed."""

import pytest

pytest.importorskip("playwright.sync_api")
from tests.test_browser_classes import classed as _classed  # noqa: E402,F401
from tests.test_dialog_contrast import OPEN  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)


@pytest.fixture(name="classed")
def classed_fixture(_classed):  # noqa: F811  (the fixture, by its name)
    return _classed


def _settings(browser, company):
    page, handled = _open(browser, company)
    _visit(page, handled, "#/settings")
    page.wait_for_selector("#settings-h-company-file", state="attached")
    settle(page, handled)
    return page


def test_the_dialog_names_the_company_and_says_when_the_name_is_not_it_yet(
    browser, company, books, classed, client
):
    name = client.get("/api/settings").json()["company_name"]
    assert name
    page = _settings(browser, company)
    try:
        # the desktop's own key: the dialog asks the sign-in password too
        page.evaluate(
            "() => { SettingsPage._companyFile = {supported: true, encrypted: false,"
            " can_turn_on: true, desktop_key: true, key_source: null}; }"
        )
        page.evaluate("async () => { await SettingsPage.confirmEncryption(true); }")
        page.wait_for_function(OPEN, timeout=5000)
        page.wait_for_selector("#encryption-company-name")
        submit = "#encryption-form button[type=submit]"
        help_ = "#encryption-name-help"
        assert name in page.text_content(help_)
        assert page.is_disabled(submit)
        # the label says which password, and that it is not a new one
        assert page.text_content("label[for=encryption-password]").strip() == (
            "Your sign-in password"
        )
        assert "not a new one" in page.text_content("#encryption-password-help")
        # not the name: the line says so, the button stays grey
        page.fill("#encryption-company-name", name + " Inc")
        page.fill("#encryption-password", "whatever")
        text = page.text_content(help_)
        assert "Not the same yet" in text and name in text, text
        assert page.is_disabled(submit)
        # the name, in any case: it matches and the button opens
        page.fill("#encryption-company-name", name.upper())
        assert page.text_content(help_).strip() == "Matches."
        assert not page.is_disabled(submit)
        # cleared again: the help line names the company again
        page.fill("#encryption-company-name", "")
        assert name in page.text_content(help_) and page.is_disabled(submit)
        page.evaluate("() => closeModal()")
    finally:
        page.close()


def test_a_server_with_no_key_source_says_where_the_secret_goes(
    browser, company, books, classed, client
):
    """This test server is no desktop window and has no secret set, so the
    page's Off line says which .env the secret goes in instead of a bare
    'set it where SlowBooks Pro starts'."""
    st = client.get("/api/company-file/encryption").json()
    if st.get("engine") == "postgresql" or not st.get("supported"):
        pytest.skip("the company file cannot be encrypted on this engine")
    if st.get("can_turn_on"):
        pytest.skip(
            "a key source is set here; the sentence under test is the no-key one"
        )
    page = _settings(browser, company)
    try:
        page.wait_for_function(
            "() => (document.querySelector('#company-file-status') || {}).textContent.includes('Off.')",
            timeout=5000,
        )
        text = page.text_content("#company-file-status")
        assert "COMPANY_FILE_ENCRYPTION_SECRET" in text
        assert ".env" in text and "ProgramData" in text and "--serve-lan" in text, text
        assert page.is_disabled("#company-file-actions button")
    finally:
        page.close()

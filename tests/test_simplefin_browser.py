"""Drive the SimpleFIN controls in a browser as a bookkeeper, against the
real routes with the bridge mocked."""

import json
from urllib.parse import urlsplit

import pytest

from app.models.banking import BankTransaction
from app.services import simplefin_service as sf
from app.services.settings_service import set_setting
from tests.test_simplefin import ACCESS_URL, SF_DATA, _mk_bank_account, _resp

sync_api = pytest.importorskip("playwright.sync_api")


def test_a_bookkeeper_syncs_with_the_saved_mapping(client, db_session, monkeypatch):
    """A bookkeeper sees the mapping locked with a sentence saying why, and
    Sync Now and Fetch older history import through the administrator's
    mapping without posting /map (an administrator's route since #384)."""
    from tests.test_rbac_users import _login_as, _mk_user

    ba = _mk_bank_account(db_session, name="Synced Checking")
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    set_setting(db_session, "simplefin_account_map", json.dumps({"ACT-1": ba.id}))
    set_setting(
        db_session,
        "simplefin_accounts_cache",
        json.dumps(sf.account_summaries(SF_DATA)),
    )
    db_session.commit()
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(json_body=SF_DATA))
    _mk_user(db_session, "keeper", "a-test-password-123", "bookkeeper")
    _login_as(client, "keeper", "a-test-password-123")
    sent = []
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            url = urlsplit(route.request.url)
            if url.hostname != "testserver":
                return route.abort()
            sent.append((route.request.method, url.path))
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
            mapping = page.get_by_role("combobox", name="Import Demo Checking into")
            mapping.wait_for()
            assert mapping.is_disabled()
            assert (
                mapping.locator("option:checked")
                .inner_text()
                .startswith("Synced Checking")
            )
            assert page.locator(".simplefin-reverse").is_disabled()
            page.get_by_text(
                "An administrator chooses which bank account each feed imports into",
                exact=False,
            ).wait_for()
            assert (
                page.get_by_role("button", name="Disconnect", exact=True).count() == 0
            )

            page.get_by_role("button", name="Sync Now", exact=True).click()
            page.get_by_text(
                "Synced: 2 new, 0 duplicates skipped", exact=True
            ).wait_for()
            assert db_session.query(BankTransaction).count() == 2

            page.get_by_role("button", name="Fetch older history…").click()
            page.get_by_role("button", name="Fetch", exact=True).click()
            page.get_by_text("2 duplicates skipped", exact=False).last.wait_for()
            assert db_session.query(BankTransaction).count() == 2

            assert ("POST", "/api/simplefin/map") not in sent
            assert sent.count(("POST", "/api/simplefin/sync")) == 2
            assert errors == []
        finally:
            browser.close()

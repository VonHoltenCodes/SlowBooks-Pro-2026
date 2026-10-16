"""Drive the Mercury controls against the real routes with a mocked bank API."""

from urllib.parse import urlsplit

import pytest

from app.models.banking import BankTransaction
from tests.test_mercury import TOKEN, bank as bank, transport as transport
from tests.test_mercury_webhooks import SECRET, deliver, event

sync_api = pytest.importorskip("playwright.sync_api")


def test_connect_map_sync_review_and_disconnect_in_browser(
    client, db_session, bank, transport, tmp_path
):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.clock.install()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

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
            page.get_by_label("Mercury read-only API token").fill(TOKEN)
            page.get_by_role("button", name="Connect Mercury", exact=True).click()
            mapping = page.get_by_role("combobox", name="Import My LLC into")
            mapping.select_option(label="Mercury LLC")
            page.get_by_label("Import transactions posted since").fill("2026-08-01")
            page.get_by_role("button", name="Sync Mercury", exact=True).click()
            page.get_by_text(
                "Mercury: 1 new, 0 already imported, 0 matched", exact=True
            ).wait_for()
            page.get_by_role("button", name="Sync Mercury", exact=True).click()
            page.get_by_text(
                "Mercury: 0 new, 1 already imported, 0 matched", exact=True
            ).wait_for()
            assert db_session.query(BankTransaction).count() == 1
            page.get_by_text("Balance webhooks", exact=True).click()
            registration = page.get_by_label("Mercury registration JSON").input_value()
            assert '"checkingAccount.balance.updated"' in registration
            page.get_by_label("Webhook signing secret").fill(SECRET)
            page.get_by_role("button", name="Save webhook secret", exact=True).click()
            page.get_by_text("Waiting for the first webhook.", exact=False).wait_for()
            page.get_by_label("Import transactions posted since").fill("2026-07-01")
            assert deliver(client, event()).json()["status"] == "updated"
            page.clock.fast_forward(15000)
            page.locator(".mercury-current").get_by_text(
                "7000.01 USD", exact=True
            ).wait_for()
            assert page.locator(".mercury-available").inner_text() == "6900.01 USD"
            assert page.locator(".mercury-inflight").inner_text() == "100 USD"
            assert (
                page.get_by_label("Import transactions posted since").input_value()
                == "2026-07-01"
            )
            assert page.locator(".mercury-map").input_value() == str(bank.id)
            page.screenshot(path=str(tmp_path / "mercury-banking.png"), full_page=True)
            assert TOKEN not in page.content()
            assert SECRET not in page.content()
            page.locator(f'a.card-link[href="#/banking/{bank.account_id}"]').click()
            page.get_by_role("cell").filter(has_text="Client LLC").wait_for()
            page.get_by_text(
                "ACH credit · Invoice 42 · August consulting", exact=True
            ).wait_for()
            assert db_session.query(BankTransaction).one().transaction_id is None
            page.goto("http://testserver/#/banking")
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Disconnect", exact=True).click()
            page.get_by_label("Mercury read-only API token").wait_for()
            assert db_session.query(BankTransaction).count() == 1
            assert not errors
        finally:
            browser.close()

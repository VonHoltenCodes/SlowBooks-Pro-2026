"""Voiding an estimate on the screens (#1128), in a real browser against
the real routes.

The view offers Void Estimate on an estimate nothing is built on; it asks
first in a dialog of its own that opens on the reason, never on Void
(NEW-32), and Cancel goes back to the view. Voided, the list shows it with
a void badge and no Edit or Convert, the view says who voided it, when and
why, and offers no Email, Convert or Void again. A converted estimate is
never offered Void."""

from datetime import date
from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_BROWSER = {}


@pytest.fixture(scope="module", autouse=True)
def _chromium():
    """One Chromium for the module's tests; each test opens its own page."""
    with sync_api.sync_playwright() as p:
        try:
            _BROWSER["browser"] = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        try:
            yield
        finally:
            _BROWSER.pop("browser").close()


def _estimate(client, customer_id, rate=250):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": date.today().isoformat(),
            "tax_rate": 0,
            "lines": [{"description": "Deck boards", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _page(client):
    page = _BROWSER["browser"].new_page(viewport={"width": 1280, "height": 900})
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
    page.goto("http://testserver/#/estimates")
    page.locator("#splash-dismiss").click()
    return page, errors


FOCUSED = """() => { const a = document.activeElement;
    return { id: a.id || '', text: a.textContent.trim().slice(0, 40),
             destructive: a.hasAttribute('data-destructive') }; }"""


def test_void_asks_first_lands_safely_and_marks_the_estimate(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    page, errors = _page(client)
    try:
        page.evaluate("async (id) => { await EstimatesPage.view(id); }", est["id"])
        void = page.locator("#modal-body button", has_text="Void Estimate")
        void.wait_for()
        void.click()
        page.locator("#estimate-void-form").wait_for()
        page.wait_for_timeout(50)
        at = page.evaluate(FOCUSED)
        assert at["id"] == "estimate-void-reason" and not at["destructive"], at
        text = page.locator("#estimate-void-form p").inner_text()
        assert "Estimate Closing Rate" in text and "can't be undone" in text

        # Cancel goes back to the estimate, which is still open
        page.locator("#estimate-void-form button", has_text="Cancel").click()
        page.locator("#modal-body button", has_text="Void Estimate").wait_for()
        assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "pending"

        page.locator("#modal-body button", has_text="Void Estimate").click()
        page.locator("#estimate-void-reason").fill("Entered twice")
        page.locator("#estimate-void-form button[type=submit]").click()
        page.wait_for_function(
            "() => document.querySelector('#modal-overlay').classList.contains('hidden')"
        )
        stored = client.get(f"/api/estimates/{est['id']}").json()
        assert (stored["status"], stored["void_reason"]) == ("void", "Entered twice")

        # the list: a void badge, no Edit or Convert on the row
        page.evaluate("() => App.navigate('#/estimates')")
        row = page.locator(f".est-row:has-text('{est['estimate_number']}')")
        row.wait_for()
        assert row.locator(".badge-void").inner_text().strip().lower() == "void"
        assert row.locator("button", has_text="Edit").count() == 0
        assert row.locator("button", has_text="Convert").count() == 0

        # the view: who, when and why in the open; nothing that changes it
        page.evaluate("async (id) => { await EstimatesPage.view(id); }", est["id"])
        note = page.locator(".estimate-voided")
        note.wait_for()
        assert "Entered twice" in note.inner_text()
        actions = page.locator("#modal-body .form-actions").inner_text()
        for gone in ("Void Estimate", "Email", "Convert"):
            assert gone not in actions, actions
        assert errors == []
    finally:
        page.close()


def test_a_converted_estimate_is_not_offered_void(client, seed_accounts, seed_customer):
    est = _estimate(client, seed_customer.id)
    assert client.post(f"/api/estimates/{est['id']}/convert").status_code == 200
    page, errors = _page(client)
    try:
        page.evaluate("async (id) => { await EstimatesPage.view(id); }", est["id"])
        page.locator("#modal-body .form-actions").wait_for()
        assert page.locator("#modal-body button", has_text="Void Estimate").count() == 0
        assert errors == []
    finally:
        page.close()

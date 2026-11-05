"""The Pay apps tab of a job with no schedule of values (#714), in a real
browser against the real routes, in construction mode.

New pay application on such a job made an application with no lines: the
form opened on an empty grid with nothing to type an amount into, and a
general contractor took it for a broken form. The tab now says what is
missing, in place of the New button, with a link to the Contract tab; a job
with a schedule of values keeps the button."""

from urllib.parse import urlsplit

import pytest

from app.models.pay_applications import PayApplication

sync_api = pytest.importorskip("playwright.sync_api")

HINT = (
    "This job has no schedule of values yet: set the contract on the job's "
    "Contract tab (Build from accepted estimate, or add the lines by hand), "
    "then bill against it here."
)


def _job(client, customer_id, name, sov=None):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    job = r.json()
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 1000},
    )
    assert r.status_code == 200, r.text
    if sov:
        r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": sov})
        assert r.status_code == 200, r.text
    return job


def _pay_apps_tab(client, job_id, test):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
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
            page.goto(f"http://testserver/#/jobs/{job_id}")
            page.locator("#splash-dismiss").click()
            page.locator('[data-jobtab="payapps"]').click()
            page.locator("#pa-log").wait_for()
            test(page)
            assert errors == []
        finally:
            browser.close()


def test_no_schedule_of_values_says_so_and_links_to_contract(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(client, seed_customer.id, "Bare Job")

    def test(page):
        hint = page.locator("#pa-no-sov")
        assert " ".join(hint.inner_text().split()) == HINT
        assert page.get_by_role("button", name="+ New pay application").count() == 0
        hint.get_by_role("link", name="Contract tab").click()
        page.locator('[data-jobtab="contract"].btn-primary').wait_for()
        assert page.locator("#pa-no-sov").count() == 0

    _pay_apps_tab(client, job["id"], test)
    assert db_session.query(PayApplication).count() == 0


def test_a_schedule_of_values_keeps_new_pay_application(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(
        client,
        seed_customer.id,
        "Tower",
        sov=[{"description": "Sitework", "kind": "work", "scheduled_value": 1000}],
    )

    def test(page):
        assert page.locator("#pa-no-sov").count() == 0
        page.get_by_role("button", name="+ New pay application").click()
        page.locator("#pa-form").wait_for()

    _pay_apps_tab(client, job["id"], test)
    db_session.expire_all()
    (app,) = db_session.query(PayApplication).all()
    assert [ln.description for ln in app.lines] == ["Sitework"]

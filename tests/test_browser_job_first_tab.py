"""The tab a job page opens on (#723), in a real browser against the real
routes, in construction mode.

The job page kept one tab for every job, so after working on one job's Pay
apps a newly created job opened on Pay apps too, which read as "make a pay
application next" before it had a schedule of values. Each job now keeps its
own tab: a new job (or one not opened yet) opens on Overview, and a job
visited before opens on the tab it was left on."""

from urllib.parse import urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def _job(client, customer_id, name):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _browser(client, test):
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
            page.goto("http://testserver/#/jobs")
            page.locator("#splash-dismiss").click()
            test(page)
            assert errors == []
        finally:
            browser.close()


def _open(page, job_id):
    page.evaluate(f"App.navigate('#/jobs/{job_id}')")
    page.locator("#job-tab-body").wait_for()


def _active(page):
    page.locator("[data-jobtab].btn-primary").wait_for()
    return page.locator("[data-jobtab].btn-primary").get_attribute("data-jobtab")


def test_a_new_job_opens_on_overview_after_another_jobs_pay_apps(
    client, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    first = _job(client, seed_customer.id, "Oak Court Garage")

    def test(page):
        _open(page, first["id"])
        page.locator('[data-jobtab="payapps"]').click()
        page.locator("#pa-log").wait_for()
        assert _active(page) == "payapps"
        # a new job with a contract value, from the Jobs list
        page.evaluate("App.navigate('#/jobs')")
        page.get_by_role("button", name="+ New Job").click()
        form = page.locator("#job-form")
        form.locator("select[name=customer_id]").select_option(str(seed_customer.id))
        form.locator("input[name=name]").fill("Cedar Hill Porch")
        form.locator("input[name=contract_amount]").fill("36000")
        form.locator("button[type=submit]").click()
        page.locator("h2", has_text="Cedar Hill Porch").wait_for()
        assert _active(page) == "overview"
        assert page.locator("#pa-log").count() == 0

    _browser(client, test)


def test_a_visited_job_keeps_its_tab(client, seed_accounts, seed_customer):
    client.put("/api/settings", json={"company_type": "construction"})
    first = _job(client, seed_customer.id, "Oak Court Garage")
    second = _job(client, seed_customer.id, "Cedar Hill Porch")

    def test(page):
        _open(page, first["id"])
        page.locator('[data-jobtab="payapps"]').click()
        page.locator("#pa-log").wait_for()
        _open(page, second["id"])
        assert _active(page) == "overview"
        page.locator('[data-jobtab="contract"]').click()
        page.locator("#contract-terms").wait_for()
        _open(page, first["id"])
        page.locator("#pa-log").wait_for()
        assert _active(page) == "payapps"
        _open(page, second["id"])
        page.locator("#contract-terms").wait_for()
        assert _active(page) == "contract"

    _browser(client, test)

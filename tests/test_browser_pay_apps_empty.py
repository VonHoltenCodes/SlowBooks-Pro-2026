"""A pay application with no lines on the Pay apps tab (#723), in a real
browser against the real routes, in construction mode.

One made before the job had a schedule of values (2.31.8's #714 stops new
ones) offered Edit on an empty grid, Submit and Approve (refused: nothing to
bill), and New answered that it was still open, so a contractor voided the
whole job to get out. Its row now offers Delete, Edit says why it can't be
billed, and New works once it is gone; an approved one with a zero total is
voided as before and New works after that too."""

from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from app.models.pay_applications import PayApplication

sync_api = pytest.importorskip("playwright.sync_api")


def _job(client, customer_id, name="Birch Lane Addition"):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    job = r.json()
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 36000},
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Framing", "kind": "work", "scheduled_value": 36000}
            ]
        },
    )
    assert r.status_code == 200, r.text
    return job


def _empty(db_session, job_id, status="draft"):
    pa = PayApplication(
        job_id=job_id,
        application_no=1,
        number=f"PA-{job_id}-01",
        date=date(2026, 6, 1),
        status=status,
        tax_rate=Decimal("0"),
    )
    if status == "approved":
        pa.approved_at = datetime.now(timezone.utc)
        pa.contract_sum_to_date = Decimal("36000.00")
    db_session.add(pa)
    db_session.commit()
    return pa.id


def _pay_apps_tab(client, job_id, test):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())

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


def _row(page, number):
    return page.locator("#pa-log tbody tr", has_text=number)


def test_an_empty_draft_is_deleted_and_new_works(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(client, seed_customer.id)
    _empty(db_session, job["id"])
    number = f"PA-{job['id']}-01"

    def test(page):
        row = _row(page, number)
        assert row.get_by_role("button", name="Submit").count() == 0
        assert row.get_by_role("button", name="Approve").count() == 0
        row.get_by_role("button", name="Edit").click()
        note = page.locator("#pa-empty [role=note]")
        note.wait_for()
        assert "made before the job had a schedule of values" in note.inner_text()
        assert page.locator("#pa-grid").count() == 0
        page.get_by_role("button", name="Delete this pay application").click()
        page.locator("#pa-log", has_text="No pay applications yet.").wait_for()
        page.get_by_role("button", name="+ New pay application").click()
        page.locator("#pa-form #pa-grid").wait_for()

    _pay_apps_tab(client, job["id"], test)
    db_session.expire_all()
    (app,) = db_session.query(PayApplication).all()
    assert app.number == number and app.status == "draft"
    assert [ln.description for ln in app.lines] == ["Framing"]


def test_the_row_delete_button_does_the_same(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(client, seed_customer.id)
    _empty(db_session, job["id"])

    def test(page):
        _row(page, f"PA-{job['id']}-01").get_by_role("button", name="Delete").click()
        page.locator("#pa-log", has_text="No pay applications yet.").wait_for()

    _pay_apps_tab(client, job["id"], test)
    db_session.expire_all()
    assert db_session.query(PayApplication).count() == 0


def test_an_application_with_lines_offers_no_delete(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(client, seed_customer.id)
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-06-01"}
    )
    assert r.status_code == 201

    def test(page):
        row = _row(page, f"PA-{job['id']}-01")
        assert row.get_by_role("button", name="Delete").count() == 0
        assert row.get_by_role("button", name="Approve").count() == 1

    _pay_apps_tab(client, job["id"], test)


def test_an_empty_approved_application_is_voided_and_new_works(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    job = _job(client, seed_customer.id)
    _empty(db_session, job["id"], status="approved")

    def test(page):
        row = _row(page, f"PA-{job['id']}-01")
        assert row.get_by_role("button", name="Delete").count() == 0
        row.get_by_role("button", name="Void").click()
        _row(page, f"PA-{job['id']}-01").get_by_text("Void", exact=True).wait_for()
        page.get_by_role("button", name="+ New pay application").click()
        page.locator("#pa-form #pa-grid").wait_for()

    _pay_apps_tab(client, job["id"], test)
    db_session.expire_all()
    apps = db_session.query(PayApplication).order_by(PayApplication.id).all()
    assert [a.status for a in apps] == ["void", "draft"]

"""Pay applications, the PM's and owner's second-round findings: the invoice
is due by the customer's terms (not the day it is issued), the application
date is a field of its own, the printed application foots, a draft can be
thrown away and picks up new schedule-of-values lines, and the list shows a
billed column that adds and Void only where it can be done."""

import io
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.pay_applications import PayApplication
from tests.test_pay_applications import (
    _approve,
    _edit,
    _new_app,
    _sov,
    _two_line_job,
)


def _bill(client, app_id, sov, amounts, **header):
    """Fill the application's lines (one amount per SOV line, in order)."""
    lines = [
        {"sov_line_id": s["id"], "this_period": a, "stored": 0}
        for s, a in zip(sov, amounts)
    ]
    r = _edit(client, app_id, lines=lines, **header)
    assert r.status_code == 200, r.text
    return r.json()


def _approved(client, job, sov, amounts, day="2026-07-31", **header):
    r = client.post("/api/pay-applications", json={"job_id": job["id"], "date": day})
    assert r.status_code == 201, r.text
    app = r.json()
    _bill(client, app["id"], sov, amounts, **header)
    r = _approve(client, app["id"])
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. Due by the customer's terms ───────────────────────────────────────


def _invoice(db_session, app):
    db_session.expire_all()
    return db_session.get(Invoice, app["invoice_id"])


def test_the_invoice_is_due_by_the_customers_terms(
    client, db_session, seed_accounts, seed_customer
):
    seed_customer.terms = "Net 45"
    db_session.commit()
    job, sov = _two_line_job(client, seed_customer.id)
    app = _approved(client, job, sov, [30000, 0])
    inv = _invoice(db_session, app)
    assert inv.terms == "Net 45"
    assert inv.date == date(2026, 7, 31)
    assert inv.due_date == date(2026, 7, 31) + timedelta(days=45)


def test_a_customer_due_on_receipt_is_due_the_day_it_is_issued(
    client, db_session, seed_accounts, seed_customer
):
    seed_customer.terms = "Due on Receipt"
    db_session.commit()
    job, sov = _two_line_job(client, seed_customer.id)
    inv = _invoice(db_session, _approved(client, job, sov, [30000, 0]))
    assert inv.terms == "Due on Receipt"
    assert inv.due_date == inv.date


def test_a_customer_with_no_terms_gets_the_company_default(
    client, db_session, seed_accounts, seed_customer
):
    seed_customer.terms = None
    db_session.commit()
    assert (
        client.put("/api/settings", json={"default_terms": "Net 15"}).status_code == 200
    )
    job, sov = _two_line_job(client, seed_customer.id)
    inv = _invoice(db_session, _approved(client, job, sov, [30000, 0]))
    assert inv.terms == "Net 15"
    assert inv.due_date == date(2026, 7, 31) + timedelta(days=15)


# ── 2. The application date ──────────────────────────────────────────────


def test_the_application_date_defaults_to_the_period_then_today(
    client, seed_accounts, seed_customer
):
    job, _sov_lines = _two_line_job(client, seed_customer.id)
    r = client.post(
        "/api/pay-applications",
        json={"job_id": job["id"], "period_to": "2026-05-31"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["date"] == "2026-05-31"
    assert client.delete(f"/api/pay-applications/{r.json()['id']}").status_code == 200
    r = client.post("/api/pay-applications", json={"job_id": job["id"]})
    assert r.json()["date"] == date.today().isoformat()


def test_the_date_set_on_the_form_is_the_invoice_date(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    r = client.post("/api/pay-applications", json={"job_id": job["id"]})
    app = r.json()
    r = _edit(client, app["id"], date="2026-07-31", period_to="2026-07-25")
    assert r.status_code == 200 and r.json()["date"] == "2026-07-31"
    _bill(client, app["id"], sov, [30000, 0])
    done = _approve(client, app["id"]).json()
    assert _invoice(db_session, done).date == date(2026, 7, 31)


def test_the_closing_date_still_guards_the_application_date(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    assert (
        client.put("/api/settings", json={"closing_date": "2026-06-30"}).status_code
        == 200
    )
    r = _edit(client, app["id"], date="2026-06-15")
    assert r.status_code in (400, 403, 409), r.text
    assert "closing date" in r.text
    # a date after it is fine, and approving on a closed date is still refused
    assert _edit(client, app["id"], date="2026-07-15").status_code == 200


# ── 3. The printed application foots ─────────────────────────────────────


def _summary(db_session, app_id):
    """The PDF's summary block (label → Decimal) and its HTML."""
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    db_session.expire_all()
    pa = db_session.get(PayApplication, app_id)
    job = db_session.get(Job, pa.job_id)
    html = render_pay_application_html(
        pa, _out(db_session, pa), job, get_all_settings(db_session)
    )
    rows = {}
    for label, amount in re.findall(
        r'<td class="label[^"]*">([^<]+)</td><td class="r[^"]*">\s*(-?\$?-?[\d,]+\.\d\d)\s*</td>',
        html,
    ):
        rows[label] = Decimal(amount.replace("$", "").replace(",", ""))
    return rows, html


def _assert_foots(rows):
    earned = rows["Total earned less retainage"]
    previous = rows["Less previous payment applications"]
    tax = rows.get("Sales tax", Decimal("0"))
    assert earned - previous + tax == rows["Current payment due"], rows
    # retainage: previous + this period - released = to date, a release being
    # inside the figure rather than an extra row
    assert (
        rows["Retainage held, previous applications"]
        + rows["Retainage this period"]
        - rows.get("Less retainage released", Decimal("0"))
        == rows["Retainage to date"]
    ), rows
    assert (
        rows["Completed and stored to date"] - rows["Retainage to date"] == earned
    ), rows


def test_the_pdf_prints_the_tax_and_foots(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    # the estimate-less job taxes at the application's own rate
    first = _approved(client, job, sov, [60000, 20000], tax_rate=0.07, day="2026-07-31")
    rows, _html = _summary(db_session, first["id"])
    assert rows["Sales tax"] == Decimal("5600.00")
    _assert_foots(rows)
    second = _approved(client, job, sov, [0, 10000], tax_rate=0.07, day="2026-08-31")
    rows, _html = _summary(db_session, second["id"])
    assert rows["Sales tax"] == Decimal("700.00")
    assert rows["Retainage this period"] == Decimal("1000.00")
    _assert_foots(rows)


def test_a_release_application_foots_with_the_release_inside_the_retainage(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    _approved(client, job, sov, [60000, 40000], tax_rate=0.07, day="2026-07-31")
    release = _approved(
        client, job, sov, [0, 0], release_retainage=10000, day="2026-08-31"
    )
    rows, html = _summary(db_session, release["id"])
    assert rows["Less retainage released"] == Decimal("10000.00")
    assert rows["Retainage to date"] == Decimal("0.00")
    assert rows["Current payment due"] == Decimal("10000.00")
    _assert_foots(rows)
    # the release is not also an additive "Retainage released" row
    assert "Retainage released<" not in html
    # the line table's retainage column is before releases, and says so
    assert "retainage column is before releases" in html


def test_a_negative_tax_application_foots(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    _approved(client, job, sov, [30000, 0], tax_rate=0.07, day="2026-07-31")
    credit = _approved(client, job, sov, [-2000, 0], tax_rate=0.07, day="2026-08-31")
    rows, _html = _summary(db_session, credit["id"])
    assert rows["Sales tax"] == Decimal("-140.00")
    _assert_foots(rows)


def test_the_totals_row_prints_once_not_in_a_repeating_footer(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _approved(client, job, sov, [30000, 0])
    _rows, html = _summary(db_session, app["id"])
    assert "<tfoot" not in html
    assert html.count(">Totals<") == 1


def test_a_long_application_prints_totals_once_at_the_end(
    client, db_session, seed_accounts, seed_customer
):
    """Needs WeasyPrint: a footer repeats on every printed page, so the
    totals showed twice with the first page's including a line that printed
    on the next."""
    pypdf = pytest.importorskip("pypdf")
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Long"}
    ).json()
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 60000,
            "retainage_pct": 10,
        },
    )
    sov = _sov(
        client,
        job["id"],
        [
            {"description": f"Scope {n:02d}", "kind": "work", "scheduled_value": 1000}
            for n in range(60)
        ],
    )
    app = _new_app(client, job["id"])
    _bill(client, app["id"], sov, [500] * 60)
    from app.routes.pay_applications import _out
    from app.services.pdf_service import generate_pay_application_pdf
    from app.services.settings_service import get_all_settings

    pa = db_session.get(PayApplication, app["id"])
    job_row = db_session.get(Job, pa.job_id)
    pdf = generate_pay_application_pdf(
        pa, _out(db_session, pa), job_row, get_all_settings(db_session)
    )
    pages = pypdf.PdfReader(io.BytesIO(pdf)).pages
    assert len(pages) >= 2
    text = [p.extract_text() for p in pages]
    assert sum(t.count("Totals") for t in text) == 1
    assert "Totals" in text[-1]


# ── 4. A draft can be thrown away and picks up new SOV lines ─────────────


def test_a_draft_with_lines_and_amounts_can_be_deleted(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _bill(client, app["id"], sov, [1000, 0])
    r = client.delete(f"/api/pay-applications/{app['id']}")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(PayApplication, app["id"]) is None
    # and New works again
    assert (
        client.post("/api/pay-applications", json={"job_id": job["id"]}).status_code
        == 201
    )


def test_a_submitted_application_can_be_deleted(client, seed_accounts, seed_customer):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _bill(client, app["id"], sov, [1000, 0])
    assert client.post(f"/api/pay-applications/{app['id']}/submit").status_code == 200
    assert client.delete(f"/api/pay-applications/{app['id']}").status_code == 200


def test_an_approved_application_is_voided_not_deleted(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _approved(client, job, sov, [30000, 0])
    r = client.delete(f"/api/pay-applications/{app['id']}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "application_not_open"


def _add_sov_line(client, job_id, description="Added later", value=5000):
    sov = client.get(f"/api/jobs/{job_id}/contract").json()["sov"]
    lines = [
        {
            "id": s["id"],
            "description": s["description"],
            "kind": s["kind"],
            "scheduled_value": s["scheduled_value"],
        }
        for s in sov
    ]
    lines.append({"description": description, "kind": "work", "scheduled_value": value})
    r = client.put(f"/api/jobs/{job_id}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    return client.get(f"/api/jobs/{job_id}/contract").json()["sov"]


def test_refresh_lines_adds_the_new_sov_line_and_keeps_typed_amounts(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _bill(client, app["id"], sov, [1000, 0])
    new_sov = _add_sov_line(client, job["id"])
    r = client.post(f"/api/pay-applications/{app['id']}/refresh-lines")
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    assert [ln["description"] for ln in lines] == [
        "Sitework",
        "Structure",
        "Added later",
    ]
    assert [Decimal(str(ln["this_period"])) for ln in lines] == [1000, 0, 0]
    assert Decimal(str(lines[2]["scheduled_value"])) == 5000
    assert lines[2]["sov_line_id"] == new_sov[2]["id"]
    # and a second refresh changes nothing
    again = client.post(f"/api/pay-applications/{app['id']}/refresh-lines").json()
    assert [ln["id"] for ln in again["lines"]] == [ln["id"] for ln in lines]


def test_saving_a_stale_draft_takes_the_new_line_instead_of_refusing_it(
    client, seed_accounts, seed_customer
):
    """This was `SOV line N is not on this pay application`."""
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    new_sov = _add_sov_line(client, job["id"])
    r = _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": new_sov[2]["id"], "this_period": 2000, "stored": 0}],
    )
    assert r.status_code == 200, r.text
    assert Decimal(str(r.json()["lines"][2]["this_period"])) == 2000


def test_refresh_leaves_a_submitted_application_as_it_was_sent(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _bill(client, app["id"], sov, [1000, 0])
    assert client.post(f"/api/pay-applications/{app['id']}/submit").status_code == 200
    _add_sov_line(client, job["id"])
    r = client.post(f"/api/pay-applications/{app['id']}/refresh-lines")
    assert r.status_code == 200 and len(r.json()["lines"]) == 2


# ── 5. The list ──────────────────────────────────────────────────────────


def test_the_billed_column_adds_up_to_the_current_due(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    _approved(client, job, sov, [30000, 5000], tax_rate=0.07, day="2026-07-31")
    # stored materials, a release and tax on one application
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-08-31"}
    )
    app = r.json()
    r = _edit(
        client,
        app["id"],
        tax_rate=0.07,
        release_retainage=1000,
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 5000, "stored": 4000},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert r.status_code == 200, r.text
    assert _approve(client, app["id"]).status_code == 200
    listed = client.get(f"/api/pay-applications?job_id={job['id']}").json()
    assert len(listed) == 2
    for a in listed:
        t = {
            k: Decimal(str(v))
            for k, v in a["totals"].items()
            if isinstance(v, (int, float))
        }
        assert (
            t["billed_this_period"]
            - t["retainage_this_period"]
            + t["release_retainage"]
            + t["tax_amount"]
            == t["current_due"]
        ), a["number"]
    # the second bills the stored materials, which "this period" leaves out
    t = listed[1]["totals"]
    assert t["billed_this_period"] > t["this_period_total"]


def test_only_the_latest_unpaid_approved_application_can_be_voided(
    client, seed_accounts, seed_customer
):
    job, sov = _two_line_job(client, seed_customer.id)
    first = _approved(client, job, sov, [10000, 0], day="2026-06-30")
    second = _approved(client, job, sov, [10000, 0], day="2026-07-31")
    draft = client.post("/api/pay-applications", json={"job_id": job["id"]}).json()

    def flags():
        listed = client.get(f"/api/pay-applications?job_id={job['id']}").json()
        return [a["can_void"] for a in listed]

    assert flags() == [False, True, False]
    # a real payment on the latest invoice takes its Void away too
    pay = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-08-05",
            "amount": 500,
            "method": "Check",
            "allocations": [{"invoice_id": second["invoice_id"], "amount": 500}],
        },
    )
    assert pay.status_code == 201, pay.text
    assert flags() == [False, False, False]
    # and Void on the earlier one is refused as it always was
    assert client.post(f"/api/pay-applications/{first['id']}/void").status_code == 409
    assert draft["status"] == "draft"

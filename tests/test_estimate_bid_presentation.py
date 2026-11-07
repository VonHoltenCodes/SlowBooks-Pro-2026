"""The bid an owner receives (construction estimate PDF and view).

Markup rows off spread the markups into each package's price pro rata; the
owner's PDF drops "Markup on allowances" (the GC's setting), prints the project,
the license number, the terms and an acceptance block, tags an accepted
alternate, and keeps the totals together on one page. The estimate can be
emailed with its PDF attached. A Business estimate is unchanged.
"""

import re
from decimal import Decimal

import pytest

import app.routes.estimates as estimates_routes
from app.models.email_log import EmailLog
from app.models.estimates import Estimate
from app.services.estimate_bid import spread_markup_prices
from app.services.estimating import estimate_totals
from app.services.pdf_service import _render


def _construction(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text


def _package(client, name):
    r = client.post("/api/trade-packages", json={"name": name, "section_ids": []})
    if r.status_code == 409:  # setup seeds some of the common names
        return next(
            p["id"]
            for p in client.get("/api/trade-packages").json()
            if p["name"] == name
        )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-03-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _html(db_session, est_id, company_type="construction", **company):
    est = db_session.get(Estimate, est_id)
    return _render(
        "estimate_pdf.html",
        {"company_name": "Keystone Builders", "company_type": company_type, **company},
        est=est,
    )


def _money(text):
    return Decimal(text.replace("$", "").replace(",", ""))


def _bid_lines(client, customer_id, **extra):
    concrete = _package(client, "Concrete")
    electric = _package(client, "Electrical")
    return _estimate(
        client,
        customer_id,
        [
            {
                "description": "Slab",
                "quantity": 1,
                "unit_cost": 40000,
                "trade_package_id": concrete,
            },
            {
                "description": "Rough-in",
                "quantity": 1,
                "unit_cost": 60000,
                "trade_package_id": electric,
            },
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 5000,
                "line_kind": "allowance",
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
        **extra,
    )


# --- show markup rows ----------------------------------------------------------


def test_markup_rows_show_by_default_and_the_pdf_omits_the_allowance_flag(
    client, db_session, seed_customer
):
    _construction(client)
    est = _bid_lines(client, seed_customer.id)
    assert est["show_markup_rows"] is True
    html = _html(db_session, est["id"])
    assert "General conditions (8%)" in html and "Bond (1%)" in html
    assert "Bid total" in html
    # the allowance flag is the GC's setting: not on the owner's document
    assert "Markup on allowances" not in html


def test_markup_rows_off_spread_the_markups_into_the_package_prices(
    client, db_session, seed_customer
):
    _construction(client)
    est = _bid_lines(client, seed_customer.id, show_markup_rows=False)
    assert est["show_markup_rows"] is False
    html = _html(db_session, est["id"])
    for label in ("General conditions", "Insurance", "Fee (", "Bond", "Bid total"):
        assert label not in html
    assert "Markup on allowances" not in html

    # math unchanged: the same stored totals as the rows-on estimate
    on = _bid_lines(client, seed_customer.id)
    assert est["total"] == on["total"] and est["subtotal"] == on["subtotal"]

    # the package prices and the (unmarked-up) allowance foot to the bid total
    obj = db_session.get(Estimate, est["id"])
    totals = estimate_totals(obj)
    prices = spread_markup_prices(obj, totals["markup_total"])
    assert sum(prices["packages"].values()) + sum(prices["allowances"].values()) == (
        totals["bid_total"]
    )
    # 40,000 and 60,000 of cost split the 16,252.01 of markup 40/60, the last
    # package taking the cent of rounding
    assert sorted(prices["packages"].values()) == [
        Decimal("46500.80"),
        Decimal("69751.21"),
    ]
    assert list(prices["allowances"].values()) == [Decimal("5000")]
    # one subtotal = the bid total, then Total
    assert re.search(
        r"Subtotal.*?\$%s" % re.escape(f"{totals['bid_total']:,.2f}"), html, re.S
    )


def test_spread_uses_the_budget_split_and_foots_to_the_cent():
    class L:
        def __init__(self, i, pkg, cost, kind="base", rate=None):
            self.id, self.trade_package_id, self.line_kind = i, pkg, kind
            self.quantity, self.unit_cost = 1, cost
            self.rate = rate if rate is not None else cost
            self.alternate_accepted = False

    class E:
        markup_allowances = False
        lines = [
            L(1, 1, Decimal("33.33")),
            L(2, 2, Decimal("33.33")),
            L(3, 3, Decimal("33.34")),
        ]

    e = E()
    prices = spread_markup_prices(e, Decimal("10.01"))
    assert sum(prices["packages"].values()) == Decimal("100.00") + Decimal("10.01")


def test_a_priced_direct_package_takes_no_markup_share():
    class L:
        id, trade_package_id, line_kind = 1, 9, "base"
        quantity, unit_cost, rate = 1, None, Decimal("500")
        alternate_accepted = False

    class E:
        markup_allowances = False
        lines = [L()]

    prices = spread_markup_prices(E(), Decimal("0"))
    assert prices["packages"] == {9: Decimal("500")}


def test_show_markup_rows_is_construction_only(client, db_session, seed_customer):
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Sign", "quantity": 1, "rate": 100}],
        show_markup_rows=False,
        terms="Net 30",
    )
    assert est["show_markup_rows"] is True and est["terms"] is None
    html = _html(db_session, est["id"], "business")
    assert "Acceptance" not in html and "Project" not in html


# --- the cover: project, license, terms, acceptance ---------------------------


def test_the_pdf_carries_project_license_terms_and_an_acceptance_block(
    client, db_session, seed_customer
):
    _construction(
        client,
        company_license="CGC1523456",
        estimate_terms="Includes permits.\nExcludes rock excavation.",
    )
    job = client.post(
        "/api/jobs",
        json={
            "customer_id": seed_customer.id,
            "name": "Lakeside Clinic Tenant Improvement",
            "site_address": "400 Lake Ave, Orlando, FL",
        },
    )
    assert job.status_code == 201, job.text
    est = _bid_lines(client, seed_customer.id, job_id=job.json()["id"])
    # a new construction estimate starts with the company's default terms
    assert est["terms"] == "Includes permits.\nExcludes rock excavation."
    assert est["job_name"] == "Lakeside Clinic Tenant Improvement"

    html = _html(db_session, est["id"], company_license="CGC1523456")
    assert "License # CGC1523456" in html
    assert "Lakeside Clinic Tenant Improvement" in html
    assert "400 Lake Ave, Orlando, FL" in html
    assert "Excludes rock excavation." in html
    for label in ("Acceptance", "Owner signature", "Printed name", "Date"):
        assert label in html


def test_terms_can_be_edited_or_cleared_per_estimate(client, seed_customer):
    _construction(client, estimate_terms="Default terms.")
    est = _bid_lines(client, seed_customer.id)
    assert est["terms"] == "Default terms."
    r = client.put(f"/api/estimates/{est['id']}", json={"terms": "Only this."})
    assert r.json()["terms"] == "Only this."
    # a blank sent by the form stays blank; the default applies to a missing one
    cleared = _bid_lines(client, seed_customer.id, terms="")
    assert cleared["terms"] == ""


def test_no_project_block_when_the_estimate_has_no_job(
    client, db_session, seed_customer
):
    _construction(client)
    est = _bid_lines(client, seed_customer.id)
    assert "Project" not in _html(db_session, est["id"])


# --- accepted alternates -------------------------------------------------------


def test_an_accepted_alternate_is_tagged_at_cost_in_its_package(
    client, db_session, seed_customer
):
    _construction(client)
    pkg = _package(client, "Framing")
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Framing",
                "quantity": 1,
                "unit_cost": 90000,
                "trade_package_id": pkg,
            },
            {
                "description": "Delete soffit",
                "quantity": 1,
                "unit_cost": -6000,
                "line_kind": "alternate",
                "alternate_accepted": True,
                "trade_package_id": pkg,
            },
        ],
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    html = _html(db_session, est["id"])
    assert re.search(r"Delete soffit.*?Deduct alternate, accepted", html, re.S)
    # at cost, the figure the subtotal holds; the markup rows carry the markup
    assert "-$6,000.00" in html or "($6,000.00)" in html
    assert "6,975" not in html


# --- keep the totals together ---------------------------------------------------


def test_the_totals_block_is_kept_together(client, db_session, seed_customer):
    _construction(client)
    est = _bid_lines(client, seed_customer.id)
    html = _html(db_session, est["id"])
    assert 'class="totals keep"' in html
    assert ".totals.keep" in html and "break-inside: avoid" in html


def test_total_is_never_alone_on_a_page(client, seed_customer):
    pytest.importorskip("weasyprint")
    pypdf = pytest.importorskip("pypdf")
    import io

    _construction(client)
    pkg = _package(client, "Everything")
    # enough lines that the totals block lands near a page boundary
    total_row = re.compile(r"^Total\s+\$", re.M)
    pages_with_total = set()
    for n in range(44, 56):
        lines = [
            {
                "description": f"Scope item {i}",
                "quantity": 1,
                "unit_cost": 100 + i,
                "trade_package_id": pkg,
            }
            for i in range(n)
        ]
        est = _estimate(client, seed_customer.id, lines, gc_pct=8, fee_pct=5)
        r = client.get(f"/api/estimates/{est['id']}/pdf")
        assert r.status_code == 200
        reader = pypdf.PdfReader(io.BytesIO(r.content))
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if total_row.search(text):
                pages_with_total.add(number)
                # the Total row travels with the Subtotal it follows
                assert "Subtotal" in text, f"{n} lines: Total alone on page {number}"
    # the sweep crossed the page boundary, so the check had something to test
    assert len(pages_with_total) >= 2


# --- email ----------------------------------------------------------------------


def _fake_send(monkeypatch, result=True):
    import app.services.email_service as email_service

    sent = {}

    def fake(*, db, to_email, subject, html_body, **kw):
        sent.update(to=to_email, subject=subject, body=html_body, **kw)
        return result

    monkeypatch.setattr(email_service, "send_email", fake)
    monkeypatch.setattr(
        estimates_routes, "generate_estimate_pdf", lambda *a, **k: b"%PDF-1.7 est"
    )
    return sent


def test_email_the_estimate_attaches_the_pdf(
    client, db_session, seed_customer, monkeypatch
):
    seed_customer.email = "owner@lakeside.test"
    db_session.commit()
    sent = _fake_send(monkeypatch)
    est = _bid_lines(client, seed_customer.id)
    r = client.post(
        f"/api/estimates/{est['id']}/email",
        json={"message": "Here is the bid <b>", "cc": "office@x.test"},
    )
    assert r.status_code == 200, r.text
    assert sent["to"] == "owner@lakeside.test"  # the customer's email by default
    assert sent["attachment_bytes"] == b"%PDF-1.7 est"
    assert sent["attachment_name"] == f"Estimate_{est['estimate_number']}.pdf"
    assert sent["entity_type"] == "estimate" and sent["entity_id"] == est["id"]
    assert sent["cc"] == "office@x.test"
    assert f"Estimate #{est['estimate_number']}" in sent["subject"]
    assert "&lt;b&gt;" in sent["body"]  # the message is escaped


def test_email_with_no_address_is_400_and_logs_nothing(
    client, db_session, seed_customer, monkeypatch
):
    _fake_send(monkeypatch)
    est = _bid_lines(client, seed_customer.id)
    r = client.post(f"/api/estimates/{est['id']}/email", json={})
    assert r.status_code == 400 and "email address" in r.json()["detail"]
    assert db_session.query(EmailLog).count() == 0


def test_email_reports_a_failed_send(client, seed_customer, monkeypatch):
    _fake_send(monkeypatch, result=False)
    est = _bid_lines(client, seed_customer.id)
    r = client.post(f"/api/estimates/{est['id']}/email", json={"recipient": "o@x.test"})
    assert r.status_code == 502 and "could not be sent" in r.json()["detail"]


def test_email_rejects_a_bad_copy_address(client, seed_customer, monkeypatch):
    _fake_send(monkeypatch)
    est = _bid_lines(client, seed_customer.id)
    r = client.post(
        f"/api/estimates/{est['id']}/email",
        json={"recipient": "o@x.test", "cc": "not-an-address"},
    )
    assert r.status_code == 400 and "Not an email address" in r.json()["detail"]
    assert client.post("/api/estimates/9999/email", json={}).status_code == 404

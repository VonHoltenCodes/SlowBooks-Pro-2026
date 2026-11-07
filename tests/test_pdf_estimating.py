"""The estimate PDF in construction mode (#551): lines AT COST grouped by
package with the section code, then the allowances, alternates and unit-price
blocks, and the totals footing Subtotal, a row per non-zero markup, Bid total,
Tax and Total.

These render the PDF template (app/templates/estimate_pdf.html) through the
same renderer the PDF endpoint uses. The HTML stage is deterministic and
needs no native graphics stack, so the template's logic is pinned here even
where WeasyPrint itself is not installed.
"""

import re
from decimal import Decimal

from app.models.estimates import Estimate
from app.services.estimating import included_lines, line_price
from app.services.pdf_service import _render


def _section(client, code, name, cost_type="material"):
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, lines, **extra):
    body = {"customer_id": customer_id, "date": "2026-03-01", "lines": lines}
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _html(db_session, est_id, company_type):
    est = db_session.get(Estimate, est_id)
    return _render(
        "estimate_pdf.html",
        {"company_name": "Keystone Builders", "company_type": company_type},
        est=est,
    )


def test_construction_pdf_groups_blocks_and_foots_with_tax(
    client, db_session, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    pkg = client.post(
        "/api/trade-packages", json={"name": "Concrete", "section_ids": [conc["id"]]}
    ).json()
    est = _estimate(
        client,
        seed_customer.id,
        [
            {
                "description": "Slab on grade",
                "quantity": 100,
                "rate": 60,
                "unit_cost": 50,
                "unit": "SF",
                "cost_code_id": conc["id"],
                "trade_package_id": pkg["id"],
            },
            {
                "description": "Flooring allowance",
                "quantity": 1,
                "rate": 4000,
                "line_kind": "allowance",
            },
            {
                "description": "Skylight alternate",
                "quantity": 1,
                "rate": 2500,
                "line_kind": "alternate",
            },
            {
                "description": "Rock excavation",
                "quantity": 1,
                "rate": 85,
                "unit": "CY",
                "line_kind": "unit_price",
            },
        ],
        tax_rate=0.07,
        gc_pct=10,
        fee_pct=5,
    )
    html = _html(db_session, est["id"], "construction")
    assert "Concrete" in html  # package heading row
    assert "03 30 00" in html  # section code in the description column
    assert "Allowances" in html and "Flooring allowance" in html
    assert "Alternates" in html and "Skylight alternate" in html
    assert "Unit prices" in html and "Rock excavation" in html
    # the totals foot Subtotal, the non-zero markups, Bid total, Tax, Total
    assert "Subtotal" in html and "Total" in html and "Bid total" in html
    assert "General conditions (10%)" in html  # percent without trailing zeros
    assert "Fee (5%)" in html
    assert re.search(r"Tax \(7", html)  # tax is charged on the bid and markups

    # the priced lines (base + allowance; unit price + pending alt excluded)
    # sum to the stored subtotal the PDF prints
    full = client.get(f"/api/estimates/{est['id']}").json()
    db_est = db_session.get(Estimate, est["id"])
    printed = sum((line_price(ln) for ln in included_lines(db_est)), Decimal("0"))
    assert printed == Decimal(str(full["subtotal"]))


def test_an_accepted_alternate_is_not_listed_again_as_an_alternate(
    client, db_session, seed_customer
):
    # an accepted alternate is in the scope table (at cost); the Alternates
    # block offers only the ones not yet taken, so it never double-prints
    client.put("/api/settings", json={"company_type": "construction"})
    est = _estimate(
        client,
        seed_customer.id,
        [
            {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 1000},
            {
                "description": "Upgrade alt",
                "quantity": 1,
                "rate": 0,
                "unit_cost": 500,
                "line_kind": "alternate",
                "alternate_accepted": True,
            },
        ],
        gc_pct=10,
    )
    html = _html(db_session, est["id"], "construction")
    assert "Upgrade alt" in html  # once, in the scope table
    assert "Alternates" not in html  # the only alternate is accepted → no block


def test_a_pre_551_estimate_has_no_construction_blocks(
    client, db_session, seed_customer
):
    # an estimate shaped as it was before #551 — all base lines, no package,
    # no markups, no allowances/alternates/unit prices — renders in
    # construction mode with the plain totals and none of the new blocks
    est = _estimate(
        client,
        seed_customer.id,
        [{"description": "Logo design", "quantity": 1, "rate": 500}],
        tax_rate=0,
    )
    html = _html(db_session, est["id"], "construction")
    assert "Allowances" not in html
    assert "Alternates" not in html
    assert "Unit prices" not in html
    # no markups, so no markup rows; Bid total equals the subtotal
    assert "General conditions" not in html
    assert "Subtotal" in html and "Bid total" in html and "Total" in html
    assert "$500.00" in html


def test_pdf_leaves_markup_on_allowances_off_the_owner_document(
    client, db_session, seed_customer
):
    client.put("/api/settings", json={"company_type": "construction"})
    lines = [
        {"description": "Shell", "quantity": 1, "rate": 0, "unit_cost": 1000},
        {
            "description": "Appliances",
            "quantity": 1,
            "rate": 500,
            "line_kind": "allowance",
        },
    ]
    off = _estimate(client, seed_customer.id, lines, gc_pct=10)
    on = _estimate(client, seed_customer.id, lines, gc_pct=10, markup_allowances=True)
    # the flag is the GC's setting: it moves the markup rows but is not printed
    # on the owner's PDF
    off_html = _html(db_session, off["id"], "construction")
    on_html = _html(db_session, on["id"], "construction")
    assert "Markup on allowances" not in off_html + on_html
    assert 'General conditions (10%)</td><td class="r">$100.00' in off_html
    assert 'General conditions (10%)</td><td class="r">$150.00' in on_html

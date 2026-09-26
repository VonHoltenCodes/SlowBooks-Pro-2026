"""Per-line sales tax (field report: labor on a customer-owned device is
not taxed, the part and its install on the same invoice are).

Items already carried is_taxable; the invoice math ignored it. Now the
document's tax rate applies only to the lines flagged taxable, the flag
defaults from the item (and a non-taxable customer), and it flows through
estimates → invoices, recurring invoices, the sales tax report and the PDF.
"""

from decimal import Decimal


def _item(client, name, rate, taxable):
    r = client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "service",
            "rate": rate,
            "is_taxable": taxable,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_tax_applies_only_to_taxable_lines(client, seed_accounts, seed_customer):
    labor = _item(client, "Repair labor", 100, False)
    part = _item(client, "SSD 1TB", 120, True)
    resp = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "tax_rate": 0.0825,
            "lines": [
                {
                    "item_id": labor["id"],
                    "description": "Diagnose & repair customer's laptop",
                    "quantity": 2,
                    "rate": 100,
                },
                {
                    "item_id": part["id"],
                    "description": "SSD",
                    "quantity": 1,
                    "rate": 120,
                },
                {
                    "description": "Install labor",
                    "quantity": 1,
                    "rate": 60,
                },  # no item → taxable
                {
                    "description": "Warranty consult",
                    "quantity": 1,
                    "rate": 40,
                    "is_taxable": False,
                },
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    inv = resp.json()
    assert Decimal(str(inv["subtotal"])) == Decimal("420.00")
    # taxable base = 120 + 60 = 180 → 14.85
    assert Decimal(str(inv["tax_amount"])) == Decimal("14.85")
    assert Decimal(str(inv["total"])) == Decimal("434.85")
    flags = [ln["is_taxable"] for ln in inv["lines"]]
    assert flags == [False, True, True, False]

    # editing the rate alone keeps the per-line flags
    upd = client.put(f"/api/invoices/{inv['id']}", json={"tax_rate": 0.10})
    assert upd.status_code == 200, upd.text
    assert Decimal(str(upd.json()["tax_amount"])) == Decimal("18.00")
    # editing lines re-resolves defaults from the items
    upd = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {"item_id": labor["id"], "quantity": 1, "rate": 100},
                {"item_id": part["id"], "quantity": 2, "rate": 120},
            ]
        },
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(str(upd.json()["tax_amount"])) == Decimal("24.00")


def test_explicit_flag_beats_item_default(client, seed_accounts, seed_customer):
    labor = _item(client, "Labor", 100, False)
    resp = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "tax_rate": 0.10,
            "lines": [
                {"item_id": labor["id"], "quantity": 1, "rate": 100, "is_taxable": True}
            ],
        },
    )
    assert Decimal(str(resp.json()["tax_amount"])) == Decimal("10.00")


def test_non_taxable_customer_defaults_every_line_off(
    client, db_session, seed_accounts
):
    cust = client.post(
        "/api/customers", json={"name": "Church of Exemptions", "is_taxable": False}
    ).json()
    part = _item(client, "Widget", 50, True)
    resp = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-07-01",
            "tax_rate": 0.10,
            "lines": [
                {"item_id": part["id"], "quantity": 1, "rate": 50},
                {"description": "misc", "quantity": 1, "rate": 10},
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    assert Decimal(str(resp.json()["tax_amount"])) == Decimal("0.00")
    assert all(ln["is_taxable"] is False for ln in resp.json()["lines"])


def test_estimate_carries_flags_into_the_invoice(client, seed_accounts, seed_customer):
    labor = _item(client, "Labor", 100, False)
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "tax_rate": 0.10,
            "lines": [
                {"item_id": labor["id"], "quantity": 3, "rate": 100},
                {"description": "Parts", "quantity": 1, "rate": 200},
            ],
        },
    )
    assert est.status_code in (200, 201), est.text
    assert Decimal(str(est.json()["tax_amount"])) == Decimal("20.00")
    assert [ln["is_taxable"] for ln in est.json()["lines"]] == [False, True]
    conv = client.post(f"/api/estimates/{est.json()['id']}/convert")
    assert conv.status_code in (200, 201), conv.text
    inv = conv.json()
    inv_id = inv.get("invoice_id") or inv.get("id")
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False, True]
    assert Decimal(str(inv["tax_amount"])) == Decimal("20.00")


def test_recurring_template_keeps_flags_on_generated_invoices(
    client, seed_accounts, seed_customer
):
    labor = _item(client, "Monthly support labor", 500, False)
    rec = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-07-01",
            "tax_rate": 0.10,
            "lines": [
                {"item_id": labor["id"], "quantity": 1, "rate": 500},
                {"description": "Hosted backup license", "quantity": 1, "rate": 30},
            ],
        },
    )
    assert rec.status_code in (200, 201), rec.text
    assert [ln["is_taxable"] for ln in rec.json()["lines"]] == [False, True]
    gen = client.post("/api/recurring/generate")  # runs everything due
    assert gen.status_code in (200, 201), gen.text
    invoices = client.get(f"/api/invoices?customer_id={seed_customer.id}").json()
    assert invoices, "the due template should have produced an invoice"
    inv = client.get(f"/api/invoices/{invoices[0]['id']}").json()
    assert Decimal(str(inv["tax_amount"])) == Decimal("3.00")
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False, True]


def test_sales_tax_report_uses_the_taxable_base(client, seed_accounts, seed_customer):
    labor = _item(client, "Labor", 100, False)
    client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-02",
            "tax_rate": 0.10,
            "lines": [
                {"item_id": labor["id"], "quantity": 1, "rate": 300},
                {"description": "Part", "quantity": 1, "rate": 100},
            ],
        },
    )
    rep = client.get(
        "/api/reports/sales-tax?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert rep["total_taxable"] == 100.0
    assert rep["total_tax"] == 10.0


def test_line_without_flag_attribute_is_taxable(db_session, seed_accounts):
    """Callers that never heard of is_taxable (imports, older code) keep
    today's numbers."""
    from types import SimpleNamespace

    from app.services.accounting import compute_line_totals

    lines = [
        SimpleNamespace(quantity=1, rate=100),
        SimpleNamespace(quantity=2, rate=50, is_taxable=None),
    ]
    assert compute_line_totals(lines, Decimal("0.10")) == (
        Decimal("200.00"),
        Decimal("20.00"),
        Decimal("220.00"),
    )


def test_a_non_taxable_customer_wins_over_a_ticked_line_box(
    client, db_session, seed_accounts
):
    """The page always sends each line's Tax box, defaulted from the item.
    The exemption used to fill only UNSET lines, so a reseller billed from the
    window was charged tax (2.16.2 gate, skytech). The customer wins now —
    on invoices, estimates and sales receipts — with a taxable control."""
    exempt = client.post(
        "/api/customers", json={"name": "Boise Reseller", "is_taxable": False}
    ).json()
    normal = client.post("/api/customers", json={"name": "Walk-in Shop"}).json()
    part = _item(client, "Neon Flex", 100, True)

    def payload(cust):
        return {
            "customer_id": cust["id"],
            "date": "2026-07-01",
            "tax_rate": 0.089,
            "lines": [
                {"item_id": part["id"], "quantity": 1, "rate": 100, "is_taxable": True}
            ],
        }

    for path in ("/api/invoices", "/api/estimates", "/api/sales-receipts"):
        body = payload(exempt)
        if path == "/api/sales-receipts":
            body["method"] = "cash"
        r = client.post(path, json=body)
        assert r.status_code in (200, 201), (path, r.text)
        doc = r.json()
        tax = doc.get("tax_amount", doc.get("invoice", {}).get("tax_amount"))
        assert Decimal(str(tax)) == Decimal("0.00"), (path, doc)

        body = payload(normal)
        if path == "/api/sales-receipts":
            body["method"] = "cash"
        r = client.post(path, json=body)
        doc = r.json()
        tax = doc.get("tax_amount", doc.get("invoice", {}).get("tax_amount"))
        assert Decimal(str(tax)) == Decimal("8.90"), (path, doc)


def test_the_sales_forms_show_the_exemption():
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
    assert "const TaxExempt = {" in (js / "utils.js").read_text(encoding="utf-8")
    for page, obj, sel in (
        ("invoices.js", "InvoicesPage", "inv-customer-select"),
        ("estimates.js", "EstimatesPage", "est-customer-select"),
        ("sales_receipts.js", "SalesReceiptsPage", "sr-customer-select"),
    ):
        src = (js / page).read_text(encoding="utf-8")
        assert f"TaxExempt.enforce({obj}._customers, $('#{sel}')?.value" in src, page


def test_the_estimate_preview_taxes_only_the_ticked_lines():
    """The estimate page built the taxable base and then taxed the subtotal,
    so its on-screen tax ignored every Tax box — $8.90 shown, 0.00 saved
    (macbase1, 2.16.2 gate). It reads the same as the invoice page now: every
    sales form adds up through SalesLines.totals (invoices.js), which the
    node probe in test_zero_total_documents runs with a ticked and an
    unticked line."""
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
    shared = (js / "invoices.js").read_text(encoding="utf-8")
    totals = shared[shared.index("    totals(tbody, taxPct") :]
    totals = totals[: totals.index("\n    },")]
    assert "SalesLines.cents(taxable) *" in totals
    assert "cents(subtotal) *" not in totals and "subtotal *" not in totals
    for page, prefix in (
        ("estimates.js", "est"),
        ("invoices.js", "inv"),
        ("sales_receipts.js", "sr"),
    ):
        src = (js / page).read_text(encoding="utf-8")
        assert f"SalesLines.totals($('#{prefix}-lines')" in src, page


def _taxed_while_taxable(client, db_session, path_create, extra=None):
    """Save a document to a customer while taxable, then mark the customer
    non-taxable — the field condition of every reseller estimate on a file
    upgraded to 2.16.2, and of any customer marked exempt later."""
    from app.models.contacts import Customer

    cust = client.post("/api/customers", json={"name": "Later Exempt"}).json()
    part = _item(client, "Sign", 100, True)
    body = {
        "customer_id": cust["id"],
        "tax_rate": 0.089,
        "lines": [
            {"item_id": part["id"], "quantity": 1, "rate": 100, "is_taxable": True}
        ],
    }
    body.update(extra or {"date": "2026-07-01"})
    doc = client.post(path_create, json=body).json()
    db_session.expire_all()
    c = db_session.get(Customer, cust["id"])
    c.is_taxable = False
    db_session.commit()
    return cust, doc


def test_converting_an_old_estimate_uses_the_customers_current_tax(
    client, db_session, seed_accounts
):
    """skytech, 2.16.2 gate: convert copied the estimate's stored tax, so a
    reseller estimate saved before the fix became a NEW invoice billing 8.90
    and crediting Sales Tax Payable."""
    from app.models.transactions import TransactionLine

    _, est = _taxed_while_taxable(client, db_session, "/api/estimates")
    assert Decimal(str(est["tax_amount"])) == Decimal("8.90")  # the stale figure
    inv = client.post(f"/api/estimates/{est['id']}/convert").json()
    assert Decimal(str(inv["tax_amount"])) == Decimal("0.00"), inv
    assert Decimal(str(inv["total"])) == Decimal("100.00")
    assert all(ln["is_taxable"] is False for ln in inv["lines"])
    tax_acct = seed_accounts["2200"].id
    db_session.expire_all()
    booked = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.account_id == tax_acct, TransactionLine.credit > 0)
        .count()
    )
    assert booked == 0


def test_converting_a_taxable_customers_estimate_still_carries_the_tax(
    client, seed_accounts
):
    cust = client.post("/api/customers", json={"name": "Taxable Buyer"}).json()
    part = _item(client, "Sign", 100, True)
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": cust["id"],
            "date": "2026-07-01",
            "tax_rate": 0.089,
            "lines": [
                {"item_id": part["id"], "quantity": 1, "rate": 100, "is_taxable": True}
            ],
        },
    ).json()
    inv = client.post(f"/api/estimates/{est['id']}/convert").json()
    assert Decimal(str(inv["tax_amount"])) == Decimal("8.90")


def test_duplicating_an_old_invoice_uses_the_customers_current_tax(
    client, db_session, seed_accounts
):
    _, inv = _taxed_while_taxable(client, db_session, "/api/invoices")
    assert Decimal(str(inv["tax_amount"])) == Decimal("8.90")
    dup = client.post(f"/api/invoices/{inv['id']}/duplicate").json()
    assert Decimal(str(dup["tax_amount"])) == Decimal("0.00"), dup
    assert all(ln["is_taxable"] is False for ln in dup["lines"])


def test_a_recurring_run_uses_the_customers_current_tax(
    client, db_session, seed_accounts
):
    from app.models.invoices import Invoice
    from app.services.recurring_service import generate_due_invoices
    from datetime import date

    cust, rec = _taxed_while_taxable(
        client,
        db_session,
        "/api/recurring",
        {"frequency": "monthly", "start_date": "2026-07-01"},
    )
    db_session.expire_all()
    ids = generate_due_invoices(db_session, as_of=date(2026, 7, 2))
    assert ids, rec
    inv = db_session.get(Invoice, ids[0])
    assert inv.customer_id == cust["id"]
    assert Decimal(str(inv.tax_amount)) == Decimal("0.00")

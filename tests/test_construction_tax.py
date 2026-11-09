"""Sales tax and use tax on a construction job (construction overhaul K).

The estimate taxes the taxable lines and their share of the markups, and the
schedule of values and the converted invoice carry the same split, so the
owner is billed the tax the bid quoted. A job's Sales tax setting says who
pays the tax: the owner (sales tax on the pay applications) or the
contractor (the lump-sum real-property rule: no sales tax to the owner, and
material bills accrue use tax to 2220 Use Tax Payable). The Sales Tax report
shows the use tax accrued, paid and owed.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.transactions import Transaction, TransactionLine

# ── Helpers ─────────────────────────────────────────────────────────────


def _construction(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text
    r = client.post("/api/construction/setup")
    assert r.status_code == 200, r.text


def _job(client, customer_id, name="Clinic", treatment=None):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    job = r.json()
    if treatment:
        r = client.put(
            f"/api/jobs/{job['id']}/contract",
            json={"sales_tax_treatment": treatment},
        )
        assert r.status_code == 200, r.text
    return job


def _line(desc, cost, **extra):
    return {
        "description": desc,
        "quantity": 1,
        "rate": cost,
        "unit_cost": cost,
        **extra,
    }


def _estimate(client, customer_id, lines, **est):
    body = {"customer_id": customer_id, "date": "2026-06-01", "lines": lines}
    body.update(est)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


B3 = dict(tax_rate="0.065", gc_pct=8, insurance_pct=1.5, fee_pct=5, bond_pct=1)


def _acct(db, number=None, name=None):
    q = db.query(Account)
    q = (
        q.filter(Account.name == name)
        if name
        else q.filter(Account.account_number == number)
    )
    return q.first()


def _vendor(client, name="Metro Supply"):
    return client.post("/api/vendors", json={"name": name}).json()


def _bill(client, vendor_id, job_id, lines, number="MS-1", **extra):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": "2026-07-04",
            "job_id": job_id,
            "lines": lines,
            **extra,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _lines_on(db, account_id, txn_id=None):
    q = db.query(TransactionLine).filter(TransactionLine.account_id == account_id)
    if txn_id is not None:
        q = q.filter(TransactionLine.transaction_id == txn_id)
    return q.all()


def _balance(db, account_id) -> Decimal:
    return sum(
        (
            Decimal(str(tl.credit or 0)) - Decimal(str(tl.debit or 0))
            for tl in _lines_on(db, account_id)
        ),
        Decimal("0"),
    )


# ── Estimate tax: only the taxable lines' share of the markups ───────────


def test_the_estimate_taxes_only_the_markups_on_taxable_lines(
    client, seed_accounts, seed_customer
):
    """est B3: A 60,000 exempt, B 40,000 taxable, 6.5%, 8 / 1.5 / 5 / 1:
    40,000 × 1.16252 × 6.5% = 3,022.55, not 3,656.38 (tax on every markup)."""
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [_line("A", 60000, is_taxable=False), _line("B", 40000, is_taxable=True)],
        **B3,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["tax_amount"]) == 3022.55
    assert float(full["total"]) == 116252.01 + 3022.55


def test_an_exempt_job_with_a_taxable_allowance_taxes_the_allowance_alone(
    client, seed_accounts, seed_customer
):
    """est B3's worse case: 100,000 exempt plus a 25,000 taxable allowance
    (allowance markup off) taxed 2,681.38; 6.5% of the allowance is 1,625."""
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [
            _line("Shell", 100000, is_taxable=False),
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 25000,
                "line_kind": "allowance",
                "is_taxable": True,
            },
        ],
        **B3,
    )
    full = client.get(f"/api/estimates/{est['id']}").json()
    assert float(full["tax_amount"]) == 1625.0


def test_the_converted_invoice_charges_the_bids_tax(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    est = _estimate(
        client,
        seed_customer.id,
        [_line("A", 60000, is_taxable=False), _line("B", 40000, is_taxable=True)],
        **B3,
    )
    r = client.post(f"/api/estimates/{est['id']}/convert")
    assert r.status_code in (200, 201), r.text
    inv = db_session.get(Invoice, r.json()["id"])
    assert inv.tax_amount == Decimal("3022.55")
    assert inv.subtotal == Decimal("116252.01")
    taxed = {ln.description: ln.is_taxable for ln in inv.lines}
    assert taxed["Fee (5%)"] is True
    assert taxed["Fee (5%) (not taxable)"] is False


def test_a_wholly_taxable_bid_keeps_one_line_per_markup(
    client, seed_accounts, seed_customer
):
    _construction(client)
    est = _estimate(client, seed_customer.id, [_line("Shell", 100000)], **B3)
    job = _job(client, seed_customer.id)
    r = client.post(f"/api/estimates/{est['id']}/accept", json={"job_id": job["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est['id']}")
    assert r.status_code == 200, r.text
    marks = [s for s in r.json()["sov"] if s["kind"] != "work"]
    assert [m["description"] for m in marks] == [
        "General conditions (8%)",
        "Insurance (1.5%)",
        "Fee (5%)",
        "Bond (1%)",
    ]
    assert all(m["is_taxable"] for m in marks)


# ── The job's Sales tax setting ──────────────────────────────────────────


def test_the_contract_carries_the_sales_tax_setting(
    client, seed_accounts, seed_customer
):
    _construction(client)
    job = _job(client, seed_customer.id)
    c = client.get(f"/api/jobs/{job['id']}/contract").json()["contract"]
    assert c["sales_tax_treatment"] == "charge_owner"
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contract"]["sales_tax_treatment"] == "contractor_pays"
    r = client.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": "owner"}
    )
    assert r.status_code == 422
    # null clears it back to charging the owner
    r = client.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": None}
    )
    assert r.json()["contract"]["sales_tax_treatment"] == "charge_owner"


def _sov_job(client, customer_id, treatment):
    job = _job(client, customer_id, treatment=treatment)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 100000},
    )
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Shell", "kind": "work", "scheduled_value": 100000}
            ]
        },
    )
    assert r.status_code == 200, r.text
    return job, client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]


def _pay_app(client, job_id, sov_line_id, this_period, stored, rate="0.065"):
    r = client.post(
        "/api/pay-applications", json={"job_id": job_id, "date": "2026-07-31"}
    )
    assert r.status_code == 201, r.text
    app = r.json()
    r = client.put(
        f"/api/pay-applications/{app['id']}",
        json={
            "tax_rate": rate,
            "charge_tax": True,
            "lines": [
                {
                    "sov_line_id": sov_line_id,
                    "this_period": this_period,
                    "stored": stored,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_contractor_pays_job_bills_the_owner_no_sales_tax(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job, sov = _sov_job(client, seed_customer.id, "contractor_pays")
    app = _pay_app(client, job["id"], sov[0]["id"], 30000, 5000)
    # stored materials included, whatever the SOV's Tax box says
    assert sov[0]["is_taxable"] is True
    assert app["contractor_pays_tax"] is True
    assert float(app["totals"]["tax_amount"]) == 0.0
    r = client.post(f"/api/pay-applications/{app['id']}/approve")
    assert r.status_code == 200, r.text
    inv = db_session.get(Invoice, r.json()["invoice_id"])
    assert inv.tax_amount == Decimal("0.00")
    assert not _lines_on(db_session, _acct(db_session, "2200").id, inv.transaction_id)


def test_a_charge_owner_job_taxes_work_and_stored_materials_as_before(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job, sov = _sov_job(client, seed_customer.id, "charge_owner")
    app = _pay_app(client, job["id"], sov[0]["id"], 30000, 5000)
    assert app["contractor_pays_tax"] is False
    # 6.5% of the 35,000 billed (work and stored)
    assert float(app["totals"]["tax_amount"]) == 2275.0


# ── Use tax on a contractor-pays job's material bills ────────────────────


def _use_tax_setup(client, db_session, seed_customer, treatment="contractor_pays"):
    _construction(client, default_tax_rate="6.5")
    job = _job(client, seed_customer.id, treatment=treatment)
    return job, _vendor(client), _acct(db_session, name="Use Tax Payable")


def test_a_material_bill_on_a_contractor_pays_job_accrues_use_tax(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)
    code = next(c for c in client.get("/api/cost-codes").json() if c["code"])
    bill = _bill(
        client,
        vendor["id"],
        job["id"],
        [
            {
                "description": "Studs",
                "rate": 1000,
                "cost_type": "material",
                "cost_code_id": code["id"],
                "job_id": job["id"],
            },
            # labor is not a material purchase: no use tax
            {"description": "Crew", "rate": 500, "cost_type": "labor"},
        ],
    )
    txn_id = db_session.get(Bill, bill["id"]).transaction_id
    credit = _lines_on(db_session, payable.id, txn_id)
    assert [(c.credit, c.job_id) for c in credit] == [(Decimal("65.00"), job["id"])]
    # the debit is the line's job-cost account with its job, code and type
    materials = _acct(db_session, "5100")
    debits = [
        tl
        for tl in _lines_on(db_session, materials.id, txn_id)
        if tl.description.startswith("Use tax")
    ]
    assert len(debits) == 1
    d = debits[0]
    assert (d.debit, d.job_id, d.cost_code_id, d.cost_type) == (
        Decimal("65.00"),
        job["id"],
        code["id"],
        "material",
    )
    # A/P is still the bill's total: the use tax is owed to the state
    ap = _acct(db_session, "2000")
    assert sum(tl.credit for tl in _lines_on(db_session, ap.id, txn_id)) == Decimal(
        "1500.00"
    )
    # voiding the bill reverses it
    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200
    assert _balance(db_session, payable.id) == Decimal("0")


def test_the_use_tax_rate_setting_wins_over_the_default_rate(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)
    r = client.put("/api/settings", json={"use_tax_rate": "7"})
    assert r.status_code == 200, r.text
    _bill(
        client,
        vendor["id"],
        job["id"],
        [{"rate": 1000, "cost_type": "material", "job_id": job["id"]}],
    )
    assert _balance(db_session, payable.id) == Decimal("70.00")
    # blank goes back to the default tax rate; a bad percent is refused
    r = client.put("/api/settings", json={"use_tax_rate": ""})
    assert r.status_code == 200 and r.json()["use_tax_rate"] == ""
    assert client.put("/api/settings", json={"use_tax_rate": "150"}).status_code == 422


def test_no_use_tax_when_the_vendor_charged_tax_or_the_owner_is_charged(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)
    # the vendor charged sales tax on the bill: nothing more is owed
    _bill(
        client,
        vendor["id"],
        job["id"],
        [{"rate": 1000, "cost_type": "material", "job_id": job["id"]}],
        tax_rate="0.065",
    )
    # a job that charges the owner sales tax accrues none
    other = _job(client, seed_customer.id, name="Retail", treatment="charge_owner")
    _bill(
        client,
        vendor["id"],
        other["id"],
        [{"rate": 1000, "cost_type": "material", "job_id": other["id"]}],
        number="MS-2",
    )
    # and a line told never to accrue does not
    _bill(
        client,
        vendor["id"],
        job["id"],
        [
            {
                "rate": 1000,
                "cost_type": "material",
                "job_id": job["id"],
                "use_tax": False,
            }
        ],
        number="MS-3",
    )
    assert _balance(db_session, payable.id) == Decimal("0")


def test_the_use_tax_box_accrues_on_any_line(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(
        client, db_session, seed_customer, treatment="charge_owner"
    )
    bill = _bill(
        client,
        vendor["id"],
        job["id"],
        [
            {
                "rate": 400,
                "cost_type": "equipment",
                "job_id": job["id"],
                "use_tax": True,
            }
        ],
    )
    assert bill["lines"][0]["use_tax"] is True
    assert _balance(db_session, payable.id) == Decimal("26.00")
    # an edit that re-posts keeps the box and re-figures the tax
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                {
                    "rate": 600,
                    "cost_type": "equipment",
                    "job_id": job["id"],
                    "use_tax": True,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _balance(db_session, payable.id) == Decimal("39.00")
    # a header-only re-post carries the stored box through
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "MS-1A"})
    assert r.status_code == 200, r.text
    assert _balance(db_session, payable.id) == Decimal("39.00")


def test_a_material_po_billed_on_a_contractor_pays_job_accrues_use_tax(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Studs",
                    "quantity": 1,
                    "rate": 2000,
                    "cost_type": "material",
                    "job_id": job["id"],
                }
            ],
        },
    )
    assert po.status_code in (200, 201), po.text
    r = client.post(f"/api/purchase-orders/{po.json()['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    assert _balance(db_session, payable.id) == Decimal("130.00")


def test_a_business_company_accrues_no_use_tax(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _vendor(client)
    expense = _acct(db_session, "6300") or db_session.query(Account).first()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-1",
            "date": "2026-07-04",
            "lines": [{"rate": 100, "account_id": expense.id, "use_tax": True}],
        },
    )
    assert r.status_code in (200, 201), r.text
    txn = db_session.get(
        Transaction, db_session.get(Bill, r.json()["id"]).transaction_id
    )
    assert len(txn.lines) == 2  # the expense and A/P, nothing else
    assert client.get("/api/reports/sales-tax").json()["use_tax"] is None


# ── The Sales Tax report's Use tax section ───────────────────────────────


def test_the_sales_tax_report_shows_use_tax_accrued_paid_and_owed(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)
    bill = _bill(
        client,
        vendor["id"],
        job["id"],
        [{"description": "Studs", "rate": 1000, "cost_type": "material"}],
    )
    # paying it: a payment to the tax agency debiting 2220
    bank = _acct(db_session, "1000")
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-07-20",
            "description": "Use tax paid",
            "lines": [
                {"account_id": payable.id, "debit": "40.00", "credit": "0"},
                {"account_id": bank.id, "debit": "0", "credit": "40.00"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    q = "start_date=2026-07-01&end_date=2026-07-31"
    for basis in ("accrual", "cash"):
        u = client.get(f"/api/reports/sales-tax?{q}&basis={basis}").json()["use_tax"]
        assert u["account_name"] == "Use Tax Payable"
        assert u["rate"] == 0.065
        assert u["paid"] == 40.0
        if basis == "accrual":
            assert u["accrued"] == 65.0 and u["balance"] == 25.0
            assert [
                (i["bill_number"], i["job_name"], i["amount"]) for i in u["items"]
            ] == [("MS-1", "Clinic", 65.0)]
        else:
            # nothing paid on the bill yet, so the cash basis has accrued none
            assert u["accrued"] == 0.0
    # paying the bill recognizes its use tax on the cash basis
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-25",
            "amount": 1000,
            "pay_from_account_id": bank.id,
            "allocations": [{"bill_id": bill["id"], "amount": 1000}],
        },
    )
    assert r.status_code in (200, 201), r.text
    u = client.get(f"/api/reports/sales-tax?{q}&basis=cash").json()["use_tax"]
    assert u["accrued"] == 65.0 and u["balance"] == 25.0


# ── A bill edit keeps each line's booked use-tax rate by line id ────────────


def _rated_bill(client, db_session, seed_customer):
    """Two $1,000 material lines booked at 6.5% (65.00 each); the use tax
    rate setting then goes to 8%."""
    job, vendor, payable = _use_tax_setup(client, db_session, seed_customer)

    def mat(desc):
        return {
            "description": desc,
            "rate": 1000,
            "cost_type": "material",
            "job_id": job["id"],
        }

    bill = _bill(client, vendor["id"], job["id"], [mat("Studs"), mat("Plates")])
    assert client.put("/api/settings", json={"use_tax_rate": "8"}).status_code == 200
    return job, bill, payable, mat


def _accrued(db_session, payable, bill):
    txn_id = db_session.get(Bill, bill["id"]).transaction_id
    db_session.expire_all()
    return sorted(c.credit for c in _lines_on(db_session, payable.id, txn_id))


def _sent(line):
    keep = ("id", "description", "quantity", "rate", "cost_type", "job_id")
    return {k: v for k, v in line.items() if k in keep}


def test_deleting_the_first_line_keeps_the_other_lines_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    """With two lines booked at 6.5% and the setting now 8%, deleting the first
    line re-posts the second at 65.00, not 80.00."""
    job, bill, payable, mat = _rated_bill(client, db_session, seed_customer)
    plates = _sent(bill["lines"][1])
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": [plates]})
    assert r.status_code == 200, r.text
    assert _accrued(db_session, payable, bill) == [Decimal("65.00")]


def test_inserting_a_line_on_top_does_not_shift_the_booked_rates(
    client, db_session, seed_accounts, seed_customer
):
    """A new line on top accrues at today's 8% (80.00); the two existing lines
    keep 65.00 each."""
    job, bill, payable, mat = _rated_bill(client, db_session, seed_customer)
    lines = [mat("New")] + [_sent(ln) for ln in bill["lines"]]
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    assert _accrued(db_session, payable, bill) == [
        Decimal("65.00"),
        Decimal("65.00"),
        Decimal("80.00"),
    ]


def test_a_bill_edit_refuses_a_line_id_from_another_bill(
    client, db_session, seed_accounts, seed_customer
):
    """A line id that is not one of the bill's own is a 422 that says so."""
    job, bill, payable, mat = _rated_bill(client, db_session, seed_customer)
    other = _bill(
        client,
        bill["vendor_id"],
        job["id"],
        [mat("Other")],
        number="MS-2",
    )
    foreign = {**mat("Studs"), "id": other["lines"][0]["id"]}
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": [foreign]})
    assert r.status_code == 422, r.text
    assert "not a line of this bill" in r.text


def _accruals(db_session, payable_id, bill_id):
    from app.models.bills import Bill
    from app.models.transactions import TransactionLine

    db_session.expire_all()
    txn_id = db_session.get(Bill, bill_id).transaction_id
    return [
        (tl.credit, tl.description)
        for tl in db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=payable_id)
        .order_by(TransactionLine.id)
    ]


def _brick(job_id, desc="Brick", rate=4000, **extra):
    return {
        "description": desc,
        "rate": rate,
        "cost_type": "material",
        "job_id": job_id,
        **extra,
    }


def _job_2(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _use_tax_job(client, db_session, seed_customer):
    from app.models.accounts import Account

    r = client.put(
        "/api/settings",
        json={"company_type": "construction", "default_tax_rate": "6.5"},
    )
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200
    job = _job_2(client, seed_customer.id, name="Clinic")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.status_code == 200, r.text
    vendor = client.post("/api/vendors", json={"name": "Brick Co"}).json()
    payable = db_session.query(Account).filter_by(name="Use Tax Payable").one()
    return job, vendor, payable


def _ut4(client, db_session, seed_customer):
    """The bill: UT-4 dated 2026-08-20, $4,000 of brick on a
    contractor-pays job, 260.00 of use tax at 6.5%; then the Use tax rate
    setting goes to 7."""
    job, vendor, payable = _use_tax_job(client, db_session, seed_customer)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "UT-4",
            "date": "2026-08-20",
            "job_id": job["id"],
            "lines": [_brick(job["id"])],
        },
    )
    assert r.status_code in (200, 201), r.text
    bill = r.json()
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (6.5%)")
    ]
    assert client.put("/api/settings", json={"use_tax_rate": "7"}).status_code == 200
    return job, bill, payable


def test_a_description_edit_re_posts_use_tax_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    """PUT /api/bills/{id} with only the line's description changed
    re-booked the 8/20 accrual at today's 7% (280.00), and the Sales Tax
    report row read rate 0.07."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                _brick(
                    job["id"], desc="Brick (corrected desc)", id=bill["lines"][0]["id"]
                )
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (corrected desc) (6.5%)")
    ]
    u = client.get(
        "/api/reports/sales-tax?start_date=2026-08-01&end_date=2026-08-31"
    ).json()["use_tax"]
    assert [(i["rate"], i["amount"]) for i in u["items"]] == [(0.065, 260.0)]


def test_a_bill_number_edit_re_posts_use_tax_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    """PUT with only bill_number changed re-posted at 7%."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4A"})
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("260.00"), "Use tax - Brick (6.5%)")
    ]


def test_an_amount_change_keeps_the_rate_and_a_new_use_tax_line_takes_todays(
    client, db_session, seed_accounts, seed_customer
):
    """A line whose amount changes keeps its stored rate; only a line newly
    marked for use tax accrues at today's setting."""
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={
            "lines": [
                _brick(job["id"], rate=5000, id=bill["lines"][0]["id"]),
                _brick(
                    job["id"], desc="Sand", rate=1000, cost_type="other", use_tax=True
                ),
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill["id"]) == [
        (Decimal("325.00"), "Use tax - Brick (6.5%)"),
        (Decimal("70.00"), "Use tax - Sand (7%)"),
    ]
    # and both keep their own rate on the next edit, after the setting moves
    assert client.put("/api/settings", json={"use_tax_rate": "8"}).status_code == 200
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4B"})
    assert r.status_code == 200, r.text
    assert [a for a, _ in _accruals(db_session, payable.id, bill["id"])] == [
        Decimal("325.00"),
        Decimal("70.00"),
    ]


def test_a_void_after_an_edit_reverses_at_the_booked_rate(
    client, db_session, seed_accounts, seed_customer
):
    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "UT-4A"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/bills/{bill['id']}/void").status_code == 200
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(account_id=payable.id).all()
    assert sum((tl.credit - tl.debit for tl in lines), Decimal("0")) == 0
    assert sorted(tl.debit for tl in lines if tl.debit) == [Decimal("260.00")]


def _construction_2(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


@pytest.fixture
def con(client):
    _construction_2(client)
    return client


@pytest.fixture
def po(con, seed_customer, seed_accounts):
    """A sent PO on a job: 100 sticks of conduit at $3."""
    job = _job_2(con, seed_customer.id)
    v = con.post(
        "/api/vendors",
        json={
            "name": "City Supply",
            "default_expense_account_id": seed_accounts["5100"].id,
        },
    ).json()
    r = con.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-06-01",
            "job_id": job["id"],
            "lines": [{"description": "Conduit", "quantity": 100, "rate": 3}],
        },
    )
    assert r.status_code == 201, r.text
    up = con.put(f"/api/purchase-orders/{r.json()['id']}", json={"status": "sent"})
    assert up.status_code == 200, up.text
    return job, up.json()


def test_a_bill_from_a_po_keeps_its_use_tax_rate_on_an_edit(
    client, db_session, seed_accounts, seed_customer
):
    job, vendor, payable = _use_tax_job(client, db_session, seed_customer)
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "job_id": job["id"],
            "lines": [_brick(job["id"], quantity=1, rate=2000)],
        },
    )
    assert po.status_code == 201, po.text
    r = client.post(f"/api/purchase-orders/{po.json()['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    bill_id = r.json()["bill_id"] if "bill_id" in r.json() else r.json()["id"]
    assert client.put("/api/settings", json={"use_tax_rate": "7"}).status_code == 200
    r = client.put(f"/api/bills/{bill_id}", json={"bill_number": "PO-B"})
    assert r.status_code == 200, r.text
    assert _accruals(db_session, payable.id, bill_id) == [
        (Decimal("130.00"), "Use tax - Brick (6.5%)")
    ]


def test_the_migration_backfills_the_booked_rate_from_the_entry(
    client, db_session, seed_accounts, seed_customer
):
    """Bills booked before the column: each line named by a 2220 credit
    takes the rate its memo ends with, else credit / amount."""
    import importlib.util
    from pathlib import Path

    from app.models.bills import Bill, BillLine
    from app.models.transactions import TransactionLine

    job, bill, payable = _ut4(client, db_session, seed_customer)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": bill["vendor_id"],
            "bill_number": "UT-5",
            "date": "2026-08-21",
            "job_id": job["id"],
            "lines": [
                {"description": "Labor", "rate": 300, "cost_type": "labor"},
                _brick(job["id"], desc="Block", rate=1000),
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    second = r.json()
    # the older memo had no rate on it: credit / amount
    txn_id = db_session.get(Bill, second["id"]).transaction_id
    for tl in db_session.query(TransactionLine).filter_by(
        transaction_id=txn_id, account_id=payable.id
    ):
        tl.description = "Use tax - Block"
    db_session.query(BillLine).update({BillLine.use_tax_rate: None})
    db_session.commit()

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/c7e4a9b2d1f5_bill_line_use_tax_rate.py"
    )
    spec = importlib.util.spec_from_file_location("ut_rate_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._backfill(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    rates = {
        (ln.bill_id, ln.description): ln.use_tax_rate
        for ln in db_session.query(BillLine)
    }
    assert rates == {
        (bill["id"], "Brick"): Decimal("0.065"),
        (second["id"], "Labor"): None,
        (second["id"], "Block"): Decimal("0.07"),
    }


def _construction_company(client, **settings):
    r = client.put("/api/settings", json={"company_type": "construction", **settings})
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200


def _job_3(client, customer_id, name="Tower", **extra):
    r = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _contractor_pays_job(client, customer_id):
    job = _job_3(client, customer_id, name="Clinic")
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.status_code == 200, r.text
    return job, r.json()


def test_saving_contractor_pays_with_no_use_tax_rate_warns(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="0")
    job, saved = _contractor_pays_job(client, seed_customer.id)
    assert len(saved["warnings"]) == 1
    assert "Use tax rate" in saved["warnings"][0]
    # a charge-owner job has nothing to warn about
    r = client.put(
        f"/api/jobs/{job['id']}/contract", json={"sales_tax_treatment": "charge_owner"}
    )
    assert r.json()["warnings"] == []
    # with a rate set, the contractor-pays save is quiet
    client.put("/api/settings", json={"use_tax_rate": "6.5"})
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"sales_tax_treatment": "contractor_pays"},
    )
    assert r.json()["warnings"] == []


def _material_bill(client, job_id, number="MS-1", rate=1000):
    vendor = client.post(
        "/api/vendors?force=true", json={"name": f"Supply {number}"}
    ).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": number,
            "date": "2026-07-04",
            "job_id": job_id,
            "lines": [
                {
                    "description": "Studs",
                    "rate": rate,
                    "cost_type": "material",
                    "job_id": job_id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bill_that_accrues_use_tax_at_zero_percent_says_so(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="0")
    job, _ = _contractor_pays_job(client, seed_customer.id)
    bill = _material_bill(client, job["id"])
    assert len(bill["warnings"]) == 1
    assert "Use tax rate" in bill["warnings"][0]
    client.put("/api/settings", json={"use_tax_rate": "6.5"})
    assert _material_bill(client, job["id"], number="MS-2")["warnings"] == []


def test_the_sales_tax_report_shows_the_rate_each_accrual_was_booked_at(
    client, seed_accounts, seed_customer
):
    _construction_company(client, default_tax_rate="6.5")
    job, _ = _contractor_pays_job(client, seed_customer.id)
    _material_bill(client, job["id"], number="MS-1")
    client.put("/api/settings", json={"use_tax_rate": "7"})
    _material_bill(client, job["id"], number="MS-2")
    q = "start_date=2026-01-01&end_date=2026-12-31"
    u = client.get(f"/api/reports/sales-tax?{q}").json()["use_tax"]
    by_bill = {i["bill_number"]: i for i in u["items"]}
    assert by_bill["MS-1"]["rate"] == 0.065 and by_bill["MS-1"]["amount"] == 65.0
    assert by_bill["MS-2"]["rate"] == 0.07 and by_bill["MS-2"]["amount"] == 70.0
    assert by_bill["MS-1"]["description"] == "Use tax - Studs"
    assert u["rate"] == 0.07  # the setting today

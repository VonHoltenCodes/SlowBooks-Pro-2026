"""How the schedule of values maps to the books (construction overhaul G).

Each SOV line carries a cost code (section), a revenue account and a taxable
flag. Build from accepted estimate sets them (the package's section, 4050
Contract Revenue, the estimate lines' tax), the Contract tab grid edits
them, and a pay application posts each line's billing to its revenue
account with the job and cost code, taxing only the taxable lines. Change
orders' SOV lines take the change order's code. Construction setup points
each starting cost type at its job-cost account, and a construction bill
line with a cost type and no account posts there.
"""

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.invoices import Invoice
from app.models.job_costing import CostType
from app.models.transactions import TransactionLine

# ── Helpers ─────────────────────────────────────────────────────────────


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    r = client.post("/api/construction/setup")
    assert r.status_code == 200, r.text


def _job(client, customer_id, name="Clinic"):
    r = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _section(client, code, name, cost_type="subcontract"):
    """The section `code`: construction setup may already have seeded it."""
    for c in client.get("/api/cost-codes").json():
        if c["code"] == code:
            r = client.put(f"/api/cost-codes/{c['id']}", json={"cost_type": cost_type})
            assert r.status_code == 200, r.text
            return r.json()
    r = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _package(client, name, section_ids):
    r = client.post(
        "/api/trade-packages", json={"name": name, "section_ids": section_ids}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _line(desc, cost, **extra):
    return {
        "description": desc,
        "quantity": 1,
        "rate": cost,
        "unit_cost": cost,
        **extra,
    }


def _accepted_build(client, customer_id, lines, **est):
    """Accept an estimate on a new job and build the contract from it."""
    body = {"customer_id": customer_id, "date": "2026-06-01", "lines": lines}
    body.update(est)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    est_id = r.json()["id"]
    job = _job(client, customer_id)
    r = client.post(f"/api/estimates/{est_id}/accept", json={"job_id": job["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/jobs/{job['id']}/contract/from-estimate/{est_id}")
    assert r.status_code == 200, r.text
    return job, r.json()["sov"], est_id


def _bill_all(client, job_id, sov, day="2026-07-31"):
    """One pay application billing every SOV line in full, approved."""
    r = client.post("/api/pay-applications", json={"job_id": job_id, "date": day})
    assert r.status_code == 201, r.text
    app = r.json()
    lines = [
        {"sov_line_id": s["id"], "this_period": s["scheduled_value"], "stored": 0}
        for s in sov
    ]
    r = client.put(f"/api/pay-applications/{app['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app['id']}/approve")
    assert r.status_code == 200, r.text
    return r.json()


def _acct(db, number):
    return db.query(Account).filter(Account.account_number == number).first()


def _income_lines(db, invoice_id):
    inv = db.get(Invoice, invoice_id)
    rows = (
        db.query(TransactionLine, Account)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            TransactionLine.transaction_id == inv.transaction_id,
            Account.account_type == AccountType.INCOME,
        )
        .all()
    )
    return [(acct.account_number, tl) for tl, acct in rows]


# ── Build from accepted estimate ─────────────────────────────────────────


def test_build_carries_section_revenue_account_and_tax(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    demo = _section(client, "02 41 19", "Selective Demolition")
    gyp = _section(client, "09 21 16", "Gypsum Board Assemblies")
    acoust = _section(client, "09 51 00", "Acoustical Ceilings")
    elec = _section(client, "26 05 00", "Electrical")
    pkg_demo = _package(client, "Demo (bid)", [demo["id"]])
    # two sections mapped: the lowest (09 21 16) is the line's section
    pkg_dry = _package(client, "Drywall (bid)", [acoust["id"], gyp["id"]])
    job, sov, _ = _accepted_build(
        client,
        seed_customer.id,
        [
            _line("Demo", 18000, trade_package_id=pkg_demo["id"], is_taxable=False),
            _line("Board", 96000, trade_package_id=pkg_dry["id"], is_taxable=False),
            # no package: its own line, on its own code
            _line("Panels", 8000, cost_code_id=elec["id"]),
            {
                "description": "Finishes allowance",
                "quantity": 1,
                "rate": 25000,
                "line_kind": "allowance",
                "cost_code_id": gyp["id"],
                "is_taxable": False,
            },
        ],
        tax_rate="0.065",
        gc_pct=8,
    )
    by = {line["description"]: line for line in sov}
    assert by["Demo (bid)"]["cost_code_id"] == demo["id"]
    assert by["Drywall (bid)"]["cost_code_id"] == gyp["id"]
    assert by["Panels"]["cost_code_id"] == elec["id"]
    assert by["Finishes allowance"]["cost_code_id"] == gyp["id"]
    # taxable per the estimate's lines; the markup splits by the taxable
    # share of its base: 8,000 of 122,000, so 640 of the 9,760 is taxed
    assert by["Demo (bid)"]["is_taxable"] is False
    assert by["Drywall (bid)"]["is_taxable"] is False
    assert by["Panels"]["is_taxable"] is True
    assert by["Finishes allowance"]["is_taxable"] is False
    gc_taxed = by["General conditions (8%)"]
    gc_exempt = by["General conditions (8%) (not taxable)"]
    assert gc_taxed["is_taxable"] is True and gc_exempt["is_taxable"] is False
    assert float(gc_taxed["scheduled_value"]) == 640.0
    assert float(gc_exempt["scheduled_value"]) == 9120.0
    assert gc_taxed["cost_code_id"] is None
    # every built line credits 4050 Contract Revenue
    rev = _acct(db_session, "4050")
    assert rev is not None and rev.name == "Contract Revenue"
    assert {line["revenue_account_id"] for line in sov} == {rev.id}
    assert {line["revenue_account_name"] for line in sov} == {"Contract Revenue"}


def test_a_partly_taxable_package_is_two_lines_and_the_tax_matches_the_bid(
    client, db_session, seed_accounts, seed_customer
):
    """est B2/B3: Labor 60,000 not taxable and Materials 40,000 taxable at
    6.5%, markups 8 / 1.5 / 5 / 1. The estimate taxes Materials and its share
    of the markups, 40,000 × 1.16252 × 6.5% = 3,022.55 (it used to tax every
    markup, 3,656.38); billed at 100%, the pay application charges the same
    (it once charged 7,556.38, tax on the whole billing)."""
    _construction(client)
    job, sov, est_id = _accepted_build(
        client,
        seed_customer.id,
        [
            _line("Labor", 60000, is_taxable=False),
            _line("Materials", 40000, is_taxable=True),
        ],
        tax_rate="0.065",
        gc_pct=8,
        insurance_pct=1.5,
        fee_pct=5,
        bond_pct=1,
    )
    est = client.get(f"/api/estimates/{est_id}").json()
    assert float(est["tax_amount"]) == 3022.55
    work = [line for line in sov if line["kind"] == "work"]
    # no package: each line is its own SOV line under its own description
    assert [(w["description"], w["is_taxable"]) for w in work] == [
        ("Labor", False),
        ("Materials", True),
    ]
    assert [float(w["scheduled_value"]) for w in work] == [60000.0, 40000.0]

    # each markup is a taxable part (40% of it) and a part that is not; the
    # taxable parts sum to the estimate's taxable share, 6,500.80
    marks = [line for line in sov if line["kind"] != "work"]
    assert [m["description"] for m in marks] == [
        "General conditions (8%)",
        "General conditions (8%) (not taxable)",
        "Insurance (1.5%)",
        "Insurance (1.5%) (not taxable)",
        "Fee (5%)",
        "Fee (5%) (not taxable)",
        "Bond (1%)",
        "Bond (1%) (not taxable)",
    ]
    taxed_marks = sum(Decimal(m["scheduled_value"]) for m in marks if m["is_taxable"])
    assert taxed_marks == Decimal("6500.80")
    assert sum(Decimal(m["scheduled_value"]) for m in marks) == Decimal("16252.01")

    approved = _bill_all(client, job["id"], sov)
    assert float(approved["totals"]["tax_amount"]) == 3022.55
    # each application line tells the form whether it is taxed, so the form's
    # live tax preview skips the same lines the server does
    flags = {ln["description"]: ln["is_taxable"] for ln in approved["lines"]}
    assert flags["Materials"] is True
    assert flags["Labor"] is False
    inv = db_session.get(Invoice, approved["invoice_id"])
    assert inv.tax_amount == Decimal("3022.55")
    # the invoice lines say which were taxed
    taxed = {ln.description: ln.is_taxable for ln in inv.lines}
    assert taxed["01 Labor"] is False
    assert taxed["02 Materials"] is True
    assert taxed["09 Bond (1%)"] is True
    assert taxed["10 Bond (1%) (not taxable)"] is False


def test_a_job_whose_estimate_taxed_nothing_bills_no_tax(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job, sov, _ = _accepted_build(
        client,
        seed_customer.id,
        [_line("Shell", 50000, is_taxable=False)],
        tax_rate="0.065",
        gc_pct=8,
    )
    assert all(line["is_taxable"] is False for line in sov)
    approved = _bill_all(client, job["id"], sov)
    assert float(approved["totals"]["tax_amount"]) == 0.0
    assert db_session.get(Invoice, approved["invoice_id"]).tax_amount == Decimal("0.00")


# ── Posting: revenue account, job and cost code per line ─────────────────


def test_each_line_credits_its_revenue_account_with_job_and_code(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    conc = _section(client, "03 30 00", "Cast-in-Place Concrete")
    product = _acct(db_session, "4100")
    job = _job(client, seed_customer.id)
    client.put(
        f"/api/jobs/{job['id']}/contract",
        json={"contract_type": "lump_sum", "contract_amount": 30000},
    )
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "description": "Slab",
                    "kind": "work",
                    "scheduled_value": 20000,
                    "cost_code_id": conc["id"],
                    "revenue_account_id": product.id,
                },
                # no account named: 4050 Contract Revenue
                {"description": "Site", "kind": "work", "scheduled_value": 10000},
            ]
        },
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    approved = _bill_all(client, job["id"], sov)

    lines = _income_lines(db_session, approved["invoice_id"])
    by_number = {n: tl for n, tl in lines}
    assert set(by_number) == {"4100", "4050"}
    assert by_number["4100"].credit == Decimal("20000.00")
    assert by_number["4100"].cost_code_id == conc["id"]
    assert by_number["4100"].job_id == job["id"]
    assert by_number["4050"].credit == Decimal("10000.00")
    assert by_number["4050"].job_id == job["id"]
    # nothing to 4000 Service Income any more
    assert "4000" not in by_number

    # revenue by section reaches the job cost tree (by section, not under a
    # cost type: a pay application's revenue has none)
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    by_section = {r["cost_code_id"]: r for r in tree["revenue_sections"]}
    assert by_section[conc["id"]]["act_revenue"] == 20000.0
    assert by_section[None]["act_revenue"] == 10000.0  # the line with no section
    assert tree["totals"]["act_revenue"] == 30000.0


def test_put_sov_refuses_a_revenue_account_that_is_not_income(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job = _job(client, seed_customer.id)
    cash = _acct(db_session, "1000")
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {
                    "description": "A",
                    "kind": "work",
                    "scheduled_value": 100,
                    "revenue_account_id": cash.id,
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "not an income account" in r.json()["detail"]


def test_a_line_resent_without_the_fields_keeps_them(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job, sov, _ = _accepted_build(
        client,
        seed_customer.id,
        [_line("Shell", 50000, is_taxable=False)],
    )
    # an API caller that knows nothing of the new fields resends the lines
    lines = [
        {
            "id": s["id"],
            "description": s["description"],
            "kind": s["kind"],
            "scheduled_value": s["scheduled_value"],
        }
        for s in sov
    ]
    r = client.put(f"/api/jobs/{job['id']}/sov", json={"lines": lines})
    assert r.status_code == 200, r.text
    again = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert [s["is_taxable"] for s in again] == [False]
    assert [s["revenue_account_id"] for s in again] == [sov[0]["revenue_account_id"]]
    # the grid can turn tax on and clear the account (4050 at posting)
    lines[0].update(is_taxable=True, revenue_account_id=None)
    client.put(f"/api/jobs/{job['id']}/sov", json={"lines": lines})
    again = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    assert again[0]["is_taxable"] is True
    assert again[0]["revenue_account_id"] is None


def test_change_order_line_takes_the_change_orders_code(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    elec = _section(client, "26 05 00", "Electrical")
    job, sov, _ = _accepted_build(
        client,
        seed_customer.id,
        [_line("Shell", 50000, is_taxable=False)],
    )
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "date": "2026-08-03",
            "title": "Added circuits",
            "lines": [
                {
                    "description": "Circuits",
                    "cost_type": "subcontract",
                    "quantity": 1,
                    "unit_cost": 1000,
                    "cost_code_id": elec["id"],
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    co = r.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(f"/api/change-orders/{co['id']}/approve")
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    co_line = next(s for s in sov if s["kind"] == "change_order")
    assert co_line["cost_code_id"] == elec["id"]
    # the bid taxed nothing, so the change is not taxed either
    assert co_line["is_taxable"] is False

    # dated after the change order takes effect (2026-08-03): before it, the
    # application carries no line for it
    approved = _bill_all(client, job["id"], sov, day="2026-08-31")
    by_number = {}
    for n, tl in _income_lines(db_session, approved["invoice_id"]):
        by_number.setdefault(n, []).append(tl)
    assert any(tl.cost_code_id == elec["id"] for tl in by_number["4050"])
    assert float(approved["totals"]["tax_amount"]) == 0.0


# ── Cost types map to accounts ────────────────────────────────────────────


def test_setup_points_each_cost_type_at_its_job_cost_account(
    client, db_session, seed_accounts
):
    from app.services.job_costing import ensure_default_cost_types

    ensure_default_cost_types(db_session)
    # a type the user already mapped is left alone
    other = db_session.query(CostType).filter_by(code="other").one()
    other.default_account_id = _acct(db_session, "5100").id
    db_session.commit()
    _construction(client)
    db_session.expire_all()
    got = {
        t.code: (db_session.get(Account, t.default_account_id).account_number)
        for t in db_session.query(CostType).all()
        if t.default_account_id
    }
    assert got == {
        "material": "5100",
        "labor": "5200",
        "subcontract": "5300",
        "equipment": "5400",
        "other": "5100",
    }


def test_a_construction_bill_line_with_a_cost_type_and_no_account_posts_there(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    code = _section(client, "26 05 00", "Electrical", cost_type="subcontract")
    job = _job(client, seed_customer.id)
    vendor = client.post("/api/vendors", json={"name": "Sparks Electric"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "SE-1",
            "date": "2026-07-04",
            "job_id": job["id"],
            "lines": [
                # the code's type (subcontract) → 5300
                {"rate": 1000, "cost_code_id": code["id"], "job_id": job["id"]},
                # the line's own type → 5400
                {"rate": 200, "cost_type": "equipment", "job_id": job["id"]},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    accounts = sorted(
        db_session.get(Account, ln["account_id"]).account_number
        for ln in r.json()["lines"]
    )
    assert accounts == ["5300", "5400"]


def test_a_business_bill_line_with_no_account_is_still_refused(
    client, db_session, seed_accounts
):
    from app.services.job_costing import map_construction_cost_types

    # even with the cost types mapped, a business company resolves as before
    map_construction_cost_types(db_session)
    db_session.commit()
    vendor = client.post("/api/vendors", json={"name": "Yard"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "Y-1",
            "date": "2026-07-04",
            "lines": [{"rate": 10, "cost_type": "material"}],
        },
    )
    assert r.status_code == 400
    assert "has no account to post to" in r.json()["detail"]


def test_a_po_line_with_a_cost_type_bills_to_its_account(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job = _job(client, seed_customer.id)
    vendor = client.post("/api/vendors", json={"name": "Metro Supply"}).json()
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
                    "rate": 500,
                    "cost_type": "material",
                    "job_id": job["id"],
                }
            ],
        },
    )
    assert po.status_code in (200, 201), po.text
    r = client.post(f"/api/purchase-orders/{po.json()['id']}/convert-to-bill", json={})
    assert r.status_code in (200, 201), r.text
    from app.models.bills import Bill

    bill = db_session.query(Bill).filter(Bill.vendor_id == vendor["id"]).one()
    assert [
        db_session.get(Account, ln.account_id).account_number for ln in bill.lines
    ] == ["5100"]


def test_the_tax_skips_a_line_that_is_not_taxable_including_its_stored_change():
    from app.services.pay_applications import _Row, application_totals

    rows = [
        # taxable: 10,000 this period
        _Row(50000, 0, 10000, 0),
        # not taxable: 5,000 this period, stored 2,000 → 3,000 (−1,000)
        _Row(50000, 0, 5000, 3000, is_taxable=False, prior_stored=4000),
    ]
    t = application_totals(
        rows,
        retainage_pct=0,
        previous_retainage=0,
        previous_applications=0,
        previous_billed=4000,
        tax_rate=Decimal("0.1"),
    )
    assert t["billed_this_period"] == Decimal("14000.00")
    assert t["taxable_billed"] == Decimal("10000.00")
    assert t["tax_amount"] == Decimal("1000.00")


def test_a_construction_vendor_credit_line_takes_the_same_account(
    client, db_session, seed_accounts
):
    _construction(client)
    vendor = client.post("/api/vendors", json={"name": "Rental Yard"}).json()
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "lines": [{"rate": 40, "cost_type": "equipment"}],
        },
    )
    assert r.status_code in (200, 201), r.text
    acct_id = r.json()["lines"][0]["account_id"]
    assert db_session.get(Account, acct_id).account_number == "5400"

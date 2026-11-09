"""Construction on the cash basis (docs/contractor-mode.md, *Retainage on the
cash basis*): retainage held on a sub's bill or an owner's pay application
is not expense or income until the release is paid, and then lands on the
held document's own accounts, jobs and cost codes, not on 6990 / 4950; an
owner deposit is income when applied, not when received; and the start-up
backfill gives an existing book the same cash lines as fresh postings.

The owner figures: pay
applications billing 157,711.89 / 140,002.49 / 103,631.89 at 10% retainage
and 6.5% tax, a 20,000 deposit applied on the first, a release of 40,134.63.
Cash net income at 2026-09-30 read 29,771.44 too high (the retainage held on
the first two) and 1150 showed as a cash asset.
"""

from decimal import Decimal

from app.models.transactions import (
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ALL,
    TransactionLine,
)
from app.services import cash_basis
from tests.test_cash_basis import (
    _amount,
    _assert_cash_invariant,
    _bill_payment,
    _bs,
    _bs_amount,
    _each_entry_balances_on_every_basis,
    _payment,
    _pl,
    _strip_cash_lines,
)

D = Decimal


# ---------------------------------------------------------------------------
# the owner side: a job, its pay applications, a deposit, payments
# ---------------------------------------------------------------------------


def _job(client, customer_id):
    job = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": "Lakeside Clinic"}
    ).json()
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "lump_sum",
            "contract_amount": 401346.27,
            "retainage_pct": 10,
        },
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/jobs/{job['id']}/sov",
        json={
            "lines": [
                {"description": "Building", "kind": "work", "scheduled_value": 250000},
                {
                    "description": "Systems",
                    "kind": "work",
                    "scheduled_value": 151346.27,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    sov = client.get(f"/api/jobs/{job['id']}/contract").json()["sov"]
    return job, sov


def _approved_app(client, job_id, day, sov, amounts=(0, 0), **extra):
    """Approve one pay application dated `day`; returns its invoice id."""
    r = client.post("/api/pay-applications", json={"job_id": job_id, "date": day})
    assert r.status_code == 201, r.text
    app_id = r.json()["id"]
    body = {
        "tax_rate": "0.065",
        "lines": [
            {"sov_line_id": ln["id"], "this_period": amt, "stored": 0}
            for ln, amt in zip(sov, amounts)
        ],
        **extra,
    }
    r = client.put(f"/api/pay-applications/{app_id}", json=body)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app_id}/approve")
    assert r.status_code == 200, r.text
    return r.json()["invoice_id"]


def _owner_side(client, db_session, customer_id):
    """The owner side through 2026-11-05; returns (job, invoices)."""
    job, sov = _job(client, customer_id)
    bank = client.get("/api/accounts").json()
    bank_id = next(a["id"] for a in bank if a["account_number"] == "1000")
    r = client.post(
        f"/api/jobs/{job['id']}/deposits",
        json={"date": "2026-06-01", "amount": 20000, "deposit_to_account_id": bank_id},
    )
    assert r.status_code == 201, r.text
    inv1 = _approved_app(
        client,
        job["id"],
        "2026-07-31",
        sov,
        (100000, 57711.89),
        deposit_applied=20000,
    )
    inv2 = _approved_app(client, job["id"], "2026-08-31", sov, (90000, 50002.49))
    inv3 = _approved_app(client, job["id"], "2026-09-30", sov, (60000, 43631.89))
    inv4 = _approved_app(
        client, job["id"], "2026-10-31", sov, release_retainage=40134.63
    )
    _payment(client, customer_id, "2026-08-20", "132191.97", [(inv1, "132191.97")])
    _payment(client, customer_id, "2026-09-25", "135102.40", [(inv2, "135102.40")])
    _payment(client, customer_id, "2026-11-05", "100004.77", [(inv3, "100004.77")])
    _payment(client, customer_id, "2026-11-05", "40134.63", [(inv4, "40134.63")])
    return job, (inv1, inv2, inv3, inv4)


# ---------------------------------------------------------------------------
# the sub side: two commitments holding 10%, paid net, released, paid
# ---------------------------------------------------------------------------


def _sub(client, name):
    r = client.post("/api/vendors", json={"name": name, "vendor_type": "sub"})
    assert r.status_code == 201, r.text
    return r.json()


def _commitment(client, vendor_id, job_id, amount, cost_code_id):
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor_id,
            "date": "2026-06-15",
            "job_id": job_id,
            "commitment_type": "subcontract",
            "retainage_pct": 10,
            "lines": [
                {
                    "description": "Scope",
                    "quantity": 1,
                    "rate": amount,
                    "job_id": job_id,
                    "cost_code_id": cost_code_id,
                    "cost_type": "subcontract",
                }
            ],
        },
    )
    assert po.status_code in (200, 201), po.text
    po = po.json()
    r = client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    assert r.status_code == 200, r.text
    return r.json()


def _sub_bill(client, po, day, amount, account_id):
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={
            "date": day,
            "lines": [
                {
                    "line_id": po["lines"][0]["id"],
                    "account_id": account_id,
                    "amount": amount,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["bill_id"]


def _release(client, po, day):
    r = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage", json={"date": day}
    )
    assert r.status_code == 200, r.text
    return r.json()["bill_id"], D(str(r.json()["amount"]))


def _sub_side(client, seed_accounts, job_id):
    """Sparks (97,400 in two bills, 5100, code 26) and Cool Breeze (74,000,
    6000, code 23), each holding 10%, paid net, released 11-02, paid 11-06."""
    elec = client.post(
        "/api/cost-codes", json={"code": "26 05 00", "name": "Electrical"}
    ).json()
    hvac = client.post("/api/cost-codes", json={"code": "23 00 00", "name": "HVAC"})
    hvac = hvac.json()
    sparks, breeze = _sub(client, "Sparks Electric"), _sub(client, "Cool Breeze")
    po_s = _commitment(client, sparks["id"], job_id, 97400, elec["id"])
    po_b = _commitment(client, breeze["id"], job_id, 74000, hvac["id"])
    s1 = _sub_bill(client, po_s, "2026-07-31", 50000, seed_accounts["5100"].id)
    s2 = _sub_bill(client, po_s, "2026-09-15", 47400, seed_accounts["5100"].id)
    b1 = _sub_bill(client, po_b, "2026-08-15", 74000, seed_accounts["6000"].id)
    _bill_payment(client, sparks["id"], "2026-08-25", 45000, [(s1, 45000)])
    _bill_payment(client, sparks["id"], "2026-09-20", 42660, [(s2, 42660)])
    _bill_payment(client, breeze["id"], "2026-08-25", 66600, [(b1, 66600)])
    rel_s, amt_s = _release(client, po_s, "2026-11-02")
    rel_b, amt_b = _release(client, po_b, "2026-11-02")
    assert (amt_s, amt_b) == (D("9740.00"), D("7400.00"))
    _bill_payment(client, sparks["id"], "2026-11-06", amt_s, [(rel_s, amt_s)])
    _bill_payment(client, breeze["id"], "2026-11-06", amt_b, [(rel_b, amt_b)])
    return elec["id"], hvac["id"]


def _job_row(client, start, end, basis, job_id):
    r = client.get(
        f"/api/reports/job-profitability?start_date={start}&end_date={end}"
        f"&basis={basis}"
    )
    assert r.status_code == 200, r.text
    rows = {row["job_id"]: row for row in r.json()["jobs"]}
    return rows.get(job_id), rows.get(None)


def _income(report):
    """Total income on a P&L, whichever income accounts the lines carry."""
    return D(str(report["total_income"])).quantize(D("0.01"))


def _cash_lines(db_session):
    """Every cash line as a comparable tuple (ids aside)."""
    rows = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.basis == BASIS_CASH)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )
    return sorted(
        (
            r.transaction_id,
            r.account_id,
            r.job_id,
            r.cost_code_id,
            r.cost_type,
            r.class_id,
            D(str(r.debit)),
            D(str(r.credit)),
            r.recognizes_invoice_id,
            r.recognizes_bill_id,
        )
        for r in rows
    )


# ---------------------------------------------------------------------------
# acct B1: a paid retainage release is the job's expense, not 6990
# ---------------------------------------------------------------------------


def test_paying_a_sub_retainage_release_recognizes_the_held_expense_on_the_job(
    client, db_session, seed_accounts, seed_customer
):
    job, _sov = _job(client, seed_customer.id)
    elec, hvac = _sub_side(client, seed_accounts, job["id"])

    # before the releases are paid: the nets only, 2050 zero on cash
    oct_ = _pl(client, "2026-01-01", "2026-10-31", "cash")
    assert _amount(oct_, "5100") == D("87660.00")
    assert _amount(oct_, "6000") == D("66600.00")
    assert _bs_amount(_bs(client, "2026-10-31", "cash"), "2050") == 0

    # November: the held 17,140 lands on 5100 / 6000, nothing on 6990
    nov, bs_nov, _ = _assert_cash_invariant(client, "2026-11-01", "2026-11-30")
    assert _amount(nov, "5100") == D("9740.00")
    assert _amount(nov, "6000") == D("7400.00")
    assert _amount(nov, "6990") == 0
    assert _bs_amount(bs_nov, "2050") == 0

    # the year: cash cost equals accrual cost, line for line
    cash = _pl(client, "2026-01-01", "2026-12-31", "cash")
    acc = _pl(client, "2026-01-01", "2026-12-31")
    for number in ("5100", "6000", "6990"):
        assert _amount(cash, number) == _amount(acc, number), number
    assert D(str(cash["net_income"])) == D(str(acc["net_income"])) == D("-171400.00")

    # every cash expense line, the releases' too, carries the job and its
    # bill's cost code; none is on 6990
    bill_lines = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.basis == BASIS_CASH,
            TransactionLine.recognizes_bill_id.isnot(None),
        )
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )
    by_code = {}
    for ln in bill_lines:
        assert ln.job_id == job["id"], ln.description
        by_code[ln.cost_code_id] = (
            by_code.get(ln.cost_code_id, D("0")) + D(str(ln.debit)) - D(str(ln.credit))
        )
    assert by_code == {elec: D("97400.00"), hvac: D("74000.00")}
    unapplied = cash_basis.unapplied_expense_id(db_session)
    assert all(ln.account_id != unapplied for ln in bill_lines)

    # cash Job Profitability: the job's cost is the accrual's, no "No job" row
    row, no_job = _job_row(client, "2026-01-01", "2026-12-31", "cash", job["id"])
    acc_row, _ = _job_row(client, "2026-01-01", "2026-12-31", "accrual", job["id"])
    assert D(str(row["total_costs"])) == D(str(acc_row["total_costs"]))
    assert D(str(row["total_costs"])) == D("171400.00")
    assert no_job is None or D(str(no_job["total_costs"])) == 0
    _each_entry_balances_on_every_basis(db_session)


def test_a_partial_release_draws_on_each_bill_in_proportion(
    client, db_session, seed_accounts, seed_customer
):
    """Two bills on one commitment hold 500 and 300. A release of 400 then a
    release of the 400 left recognize each bill's held part once: 5100's
    500 and 6000's 300, whatever the split between the two releases."""
    sub = _sub(client, "Framer")
    job, _sov = _job(client, seed_customer.id)
    po = _commitment(client, sub["id"], job["id"], 8000, None)
    b1 = _sub_bill(client, po, "2026-07-01", 5000, seed_accounts["5100"].id)
    b2 = _sub_bill(client, po, "2026-07-15", 3000, seed_accounts["6000"].id)
    _bill_payment(client, sub["id"], "2026-07-20", 7200, [(b1, 4500), (b2, 2700)])
    r = client.post(
        f"/api/purchase-orders/{po['id']}/release-retainage",
        json={"date": "2026-08-01", "amount": 400},
    )
    assert r.status_code == 200, r.text
    rel1 = r.json()["bill_id"]
    _bill_payment(client, sub["id"], "2026-08-05", 400, [(rel1, 400)])
    aug = _pl(client, "2026-08-01", "2026-08-31", "cash")
    assert _amount(aug, "5100") == D("250.00")
    assert _amount(aug, "6000") == D("150.00")
    rel2, left = _release(client, po, "2026-09-01")
    assert left == D("400.00")
    _bill_payment(client, sub["id"], "2026-09-05", 400, [(rel2, 400)])
    whole = _pl(client, "2026-07-01", "2026-09-30", "cash")
    assert _amount(whole, "5100") == D("5000.00")
    assert _amount(whole, "6000") == D("3000.00")
    assert _amount(whole, "6990") == 0
    _each_entry_balances_on_every_basis(db_session)


def test_editing_a_released_bill_rewrites_the_release_payment(
    client, db_session, seed_accounts, seed_customer
):
    """The release recognized the held bill's expense as its lines stood; an
    edit moving that line to another account moves the release's too."""
    sub = _sub(client, "Mason")
    job, _sov = _job(client, seed_customer.id)
    po = _commitment(client, sub["id"], job["id"], 1000, None)
    bill_id = _sub_bill(client, po, "2026-07-01", 1000, seed_accounts["5100"].id)
    _bill_payment(client, sub["id"], "2026-07-10", 900, [(bill_id, 900)])
    rel, amount = _release(client, po, "2026-08-01")
    _bill_payment(client, sub["id"], "2026-08-05", amount, [(rel, amount)])
    bill = client.get(f"/api/bills/{bill_id}").json()
    line = dict(bill["lines"][0])
    r = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "description": line["description"],
                    "quantity": 1,
                    "rate": 1000,
                    "account_id": seed_accounts["6000"].id,
                    "job_id": job["id"],
                    "purchase_order_line_id": line["purchase_order_line_id"],
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    aug = _pl(client, "2026-08-01", "2026-08-31", "cash")
    assert _amount(aug, "6000") == D("100.00")
    assert _amount(aug, "5100") == 0
    _each_entry_balances_on_every_basis(db_session)


# ---------------------------------------------------------------------------
# acct B2: held owner retainage is not cash income, and 1150 reads zero
# ---------------------------------------------------------------------------


def test_held_owner_retainage_is_income_only_when_the_release_is_paid(
    client, db_session, seed_accounts, seed_customer
):
    job, invoices = _owner_side(client, db_session, seed_customer.id)

    # 2026-09-30: two applications paid (287,294.37 with the deposit), the
    # third billed and unpaid. Income is the cash less the tax: 267,942.94,
    # not the 297,714.38 that counted the 29,771.44 held as income.
    sept, bs_sept, _ = _assert_cash_invariant(client, "2026-01-01", "2026-09-30")
    assert _income(sept) == D("267942.94")
    assert D(str(sept["net_income"])) == D("267942.94")
    assert _bs_amount(bs_sept, "1150") == 0
    assert _bs_amount(bs_sept, "2200") == D("19351.43")
    assert _bs_amount(bs_sept, "2210") == 0
    # the accrual view still holds the retainage on 1150
    assert _bs_amount(_bs(client, "2026-09-30"), "1150") == D("40134.63")

    # November: application 3's net and the release are paid; the release
    # recognizes the held 40,134.63 as income, still nothing on 1150 or 4950
    nov, bs_nov, _ = _assert_cash_invariant(client, "2026-10-01", "2026-11-30")
    assert _amount(nov, "4950") == 0
    assert _bs_amount(bs_nov, "1150") == 0

    # all paid: cash income and tax are the accrual's
    cash = _pl(client, "2026-01-01", "2026-12-31", "cash")
    acc = _pl(client, "2026-01-01", "2026-12-31")
    assert _income(cash) == _income(acc) == D("401346.27")
    assert _bs_amount(_bs(client, "2026-12-31", "cash"), "2200") == _bs_amount(
        _bs(client, "2026-12-31"), "2200"
    )
    row, _ = _job_row(client, "2026-01-01", "2026-12-31", "cash", job["id"])
    assert D(str(row["income"])) == D("401346.27")
    _each_entry_balances_on_every_basis(db_session)


def test_the_cash_sales_tax_report_still_matches_its_ledger_on_pay_apps(
    client, db_session, seed_accounts, seed_customer
):
    """The Sales Tax report prorates the tax on the amount due; the cash
    lines now collect the tax whole with the net, which is the same figure."""
    _owner_side(client, db_session, seed_customer.id)
    r = client.get(
        "/api/reports/sales-tax?start_date=2026-01-01&end_date=2026-09-30&basis=cash"
    )
    assert r.status_code == 200, r.text
    tax = r.json()
    assert tax["total_tax"] == 19351.43
    assert tax["ledger"]["difference"] == 0.0


# ---------------------------------------------------------------------------
# acct gap 10: a deposit is income when applied, not when received
# ---------------------------------------------------------------------------


def test_a_deposit_is_a_liability_on_the_cash_basis_until_applied(
    client, db_session, seed_accounts, seed_customer
):
    _owner_side(client, db_session, seed_customer.id)
    june, bs_june, _ = _assert_cash_invariant(client, "2026-06-01", "2026-06-30")
    assert D(str(june["net_income"])) == 0
    assert _bs_amount(bs_june, "2210") == D("20000.00")
    # applied on application 1 (07-31): its share of the income and tax
    july, bs_july, _ = _assert_cash_invariant(client, "2026-07-01", "2026-07-31")
    assert _bs_amount(bs_july, "2210") == 0
    assert D("0") < _income(july) < D("20000.00")


# ---------------------------------------------------------------------------
# the backfill writes what fresh postings write
# ---------------------------------------------------------------------------


def test_the_backfill_gives_the_construction_book_the_same_cash_lines(
    client, db_session, seed_accounts, seed_customer
):
    job, _invoices = _owner_side(client, db_session, seed_customer.id)
    _sub_side(client, seed_accounts, job["id"])
    fresh = _cash_lines(db_session)
    reports = [
        _pl(client, "2026-01-01", "2026-09-30", "cash"),
        _pl(client, "2026-10-01", "2026-12-31", "cash"),
        _bs(client, "2026-09-30", "cash"),
        _bs(client, "2026-12-31", "cash"),
    ]

    _strip_cash_lines(db_session)
    assert _cash_lines(db_session) == []
    done = cash_basis.backfill(db_session)
    assert done["failed"] == 0
    assert done["payments"] == 5 and done["bill_payments"] == 5

    assert _cash_lines(db_session) == fresh
    assert [
        _pl(client, "2026-01-01", "2026-09-30", "cash"),
        _pl(client, "2026-10-01", "2026-12-31", "cash"),
        _bs(client, "2026-09-30", "cash"),
        _bs(client, "2026-12-31", "cash"),
    ] == reports
    assert not cash_basis.pending_backfill(db_session)
    _each_entry_balances_on_every_basis(db_session)

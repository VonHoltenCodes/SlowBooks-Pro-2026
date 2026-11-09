"""The GC's view of the business: the
contractor's dashboard tiles, the Retainage report that ties to 1150 and 2050,
the job Overview's projected final cost, the vendor list's COI cell and the
Sub Compliance report. The scenario: pay
applications at 10% retainage with a release, and two subs held at 10%,
released and paid (tests.test_cash_basis_construction)."""

import json
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.dashboard_widgets import (
    CONSTRUCTION_ONLY,
    DEFAULT_LAYOUT,
    WIDGETS,
    default_layout,
)
from tests.test_cash_basis import _bill_payment, _bs, _bs_amount
from tests.test_cash_basis_construction import (
    _commitment,
    _owner_side,
    _sub,
    _sub_side,
)

D = Decimal
ROOT = Path(__file__).resolve().parents[1]
requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _construction(client):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _retainage(client, as_of, **q):
    qs = "".join(f"&{k}={v}" for k, v in q.items())
    r = client.get(f"/api/reports/retainage?as_of={as_of}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _books(client, db_session, seed_accounts, seed_customer):
    _construction(client)
    job, _invoices = _owner_side(client, db_session, seed_customer.id)
    _sub_side(client, seed_accounts, job["id"])
    return job


# ── the Retainage report ─────────────────────────────────────────────────


def test_retainage_totals_equal_the_balance_sheet_on_every_date(
    client, db_session, seed_accounts, seed_customer
):
    """The grand totals are 1150 and 2050 on the Balance Sheet for the date:
    before the first application, while retainage builds, after the owner
    release (10-31), between the sub bills and their release (11-02) and when
    everything is paid."""
    job = _books(client, db_session, seed_accounts, seed_customer)
    seen = set()
    for day in (
        "2026-06-30",
        "2026-07-31",
        "2026-08-31",
        "2026-09-30",
        "2026-10-31",
        "2026-11-01",
        "2026-11-02",
        "2026-11-07",
    ):
        data = _retainage(client, day)
        bs = _bs(client, day)
        assert D(str(data["totals"]["receivable"])) == _bs_amount(bs, "1150"), day
        assert D(str(data["totals"]["payable"])) == _bs_amount(bs, "2050"), day
        assert data["books"] == {
            "receivable": data["totals"]["receivable"],
            "payable": data["totals"]["payable"],
        }, day
        # every figure is a document's: nothing falls to the ledger-only row
        kinds = {
            r["kind"] for r in data["receivable"]["rows"] + data["payable"]["rows"]
        }
        assert "other" not in kinds, (day, kinds)
        seen.add((data["totals"]["receivable"], data["totals"]["payable"]))
    assert len(seen) >= 5  # the dates really did read different balances

    # mid-build: three applications at 10%, nothing released
    data = _retainage(client, "2026-09-30")
    assert data["receivable"]["totals"] == {
        "held": 40134.63,
        "released": 0.0,
        "outstanding": 40134.63,
    }
    [row] = data["receivable"]["rows"]
    assert row["job_id"] == job["id"] and row["customer_name"]
    # between the sub bills and their release: 9,740 + 7,400 held, two commitments
    data = _retainage(client, "2026-11-01")
    assert data["payable"]["totals"]["outstanding"] == 17140.0
    assert [r["kind"] for r in data["payable"]["rows"]] == ["commitment"] * 2
    assert {r["vendor_name"] for r in data["payable"]["rows"]} == {
        "Sparks Electric",
        "Cool Breeze",
    }
    # all released by 11-07: held and released stand, outstanding is zero
    data = _retainage(client, "2026-11-07")
    assert data["totals"] == {"receivable": 0.0, "payable": 0.0}
    assert data["receivable"]["totals"]["held"] == 40134.63
    assert data["receivable"]["totals"]["released"] == 40134.63
    assert data["payable"]["totals"]["released"] == 17140.0


def test_retainage_lists_a_hand_entered_bill_and_a_closed_job(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Old job"}
    ).json()
    vendor = _sub(client, "Hand Entry Framing")
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "HE-1",
            "date": "2026-05-01",
            "job_id": job["id"],
            "retainage_held": 500,
            "lines": [
                {
                    "account_id": seed_accounts["5100"].id,
                    "quantity": 1,
                    "rate": 5000,
                    "job_id": job["id"],
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    client.put(f"/api/jobs/{job['id']}", json={"status": "closed"})

    data = _retainage(client, "2026-06-01")
    [row] = data["payable"]["rows"]
    assert (row["kind"], row["document"], row["vendor_name"]) == (
        "bill",
        "HE-1",
        "Hand Entry Framing",
    )
    assert row["outstanding"] == 500.0
    assert D(str(data["totals"]["payable"])) == _bs_amount(
        _bs(client, "2026-06-01"), "2050"
    )
    # a closed job still holds its retainage; the job filter narrows to it
    assert (
        _retainage(client, "2026-06-01", job_id=job["id"])["totals"]["payable"] == 500.0
    )
    # before the bill there is nothing
    assert _retainage(client, "2026-04-30")["totals"]["payable"] == 0.0


def test_retainage_journal_entry_with_no_document_is_its_own_row(
    client, db_session, seed_accounts, seed_customer
):
    """A journal posted straight to 1150 has no pay application behind it: the
    report shows it as its own row and still ties to the Balance Sheet."""
    from app.models.accounts import Account
    from app.services.accounting import (
        create_journal_entry,
        ensure_construction_accounts,
    )

    _construction(client)
    accounts = ensure_construction_accounts(db_session)
    create_journal_entry(
        db_session,
        date(2026, 3, 1),
        "Opening retainage",
        [
            {"account_id": accounts["1150"].id, "debit": D("1200")},
            (
                {"account_id": seed_accounts["3000"].id, "credit": D("1200")}
                if "3000" in seed_accounts
                else {
                    "account_id": db_session.query(Account)
                    .filter(Account.account_type == "equity")
                    .first()
                    .id,
                    "credit": D("1200"),
                }
            ),
        ],
    )
    db_session.commit()
    data = _retainage(client, "2026-03-31")
    assert data["totals"]["receivable"] == 1200.0
    [row] = data["receivable"]["rows"]
    assert row["kind"] == "other" and row["job_name"] == "No job"
    assert D("1200") == _bs_amount(_bs(client, "2026-03-31"), "1150")
    # a run for one customer's jobs leaves the jobless ledger out, and says so
    cust = _retainage(client, "2026-03-31", customer_id=seed_customer.id)
    assert cust["filtered"] is True and cust["totals"]["receivable"] == 0.0


def test_retainage_csv_and_job_filter(client, db_session, seed_accounts, seed_customer):
    job = _books(client, db_session, seed_accounts, seed_customer)
    r = client.get("/api/reports/retainage/csv?as_of=2026-11-01")
    assert r.status_code == 200, r.text
    assert "Held by owner" in r.text and "Held from sub" in r.text
    assert "Total owed to me" in r.text and "Total I owe" in r.text
    assert "Sparks Electric" in r.text
    other = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Quiet"}
    ).json()
    quiet = _retainage(client, "2026-11-01", job_id=other["id"])
    assert quiet["jobs"] == [] and quiet["totals"] == {
        "receivable": 0.0,
        "payable": 0.0,
    }
    mine = _retainage(client, "2026-11-01", job_id=job["id"])
    assert [j["job_id"] for j in mine["jobs"]] == [job["id"]]


def test_retainage_and_compliance_pdf_and_print(
    client, db_session, seed_accounts, seed_customer
):
    """Needs WeasyPrint's native stack (skips where it is missing, which
    counts against the Windows MAX_SKIPS budget)."""
    _books(client, db_session, seed_accounts, seed_customer)
    pdf = client.get("/api/reports/retainage/pdf?as_of=2026-11-01")
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    page = client.get("/api/reports/retainage/print?as_of=2026-11-01")
    assert page.status_code == 200, page.text
    assert "Retainage owed to me (1150)" in page.text
    assert "Retainage I owe (2050)" in page.text
    assert "Sparks Electric" in page.text
    pdf = client.get("/api/reports/compliance/pdf?as_of=2026-11-01")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    page = client.get("/api/reports/compliance/print?as_of=2026-11-01")
    assert page.status_code == 200 and "Sub Compliance" in page.text


# ── the dashboard ────────────────────────────────────────────────────────


def test_business_dashboard_is_unchanged(client, seed_accounts):
    body = client.get("/api/dashboard/widgets").json()
    ids = {w["id"] for w in body["widgets"]}
    assert ids == set(WIDGETS) - set(CONSTRUCTION_ONLY)
    assert body["default_order"] == list(DEFAULT_LAYOUT)
    titles = {w["id"]: w["title"] for w in body["widgets"]}
    assert titles["receivables"] == "Total Receivables"
    assert titles["payables"] == "Total Payables"
    # a construction card asked for by id is not served to a business
    data = client.get("/api/dashboard/data?ids=receivables,retainage_payable").json()
    assert set(data) == {"receivables"}


def test_construction_dashboard_offers_the_tiles_and_says_excl_retainage(
    client, seed_accounts
):
    _construction(client)
    body = client.get("/api/dashboard/widgets").json()
    ids = {w["id"] for w in body["widgets"]}
    assert set(CONSTRUCTION_ONLY) <= ids
    titles = {w["id"]: w["title"] for w in body["widgets"]}
    assert titles["receivables"] == "Total Receivables (excl. retainage)"
    assert titles["payables"] == "Total Payables (excl. retainage)"
    assert body["default_order"] == default_layout(_terms_construction())
    assert set(CONSTRUCTION_ONLY) <= set(body["default_order"])
    # every card builds on an empty company
    data = client.get("/api/dashboard/data").json()
    for wid, payload in data.items():
        assert "error" not in payload, (wid, payload)
    assert data["retainage_receivable"]["total"] == 0.0
    assert data["billing_position"]["over"] == 0.0


def _terms_construction():
    from app.services.terminology import Terms

    return Terms("construction")


def test_construction_tiles_read_the_books_and_the_reports_behind_them(
    client, db_session, seed_accounts, seed_customer
):
    job = _books(client, db_session, seed_accounts, seed_customer)
    # an open commitment with nothing billed yet: committed, not yet billed
    vendor = _sub(client, "Open Sub")
    cc = client.post("/api/cost-codes", json={"code": "31 00 00", "name": "Earthwork"})
    _commitment(client, vendor["id"], job["id"], 5000, cc.json()["id"])
    # a second job whose pay application still holds retainage, so the tiles
    # are not all zero
    from tests.test_pay_applications import _approve, _edit, _new_app, _two_line_job

    annex, sov2 = _two_line_job(client, seed_customer.id, name="Annex")
    app = _new_app(client, annex["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov2[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve(client, app["id"]).status_code == 200

    data = client.get("/api/dashboard/data").json()
    today = date.today().isoformat()
    bs = _bs(client, today)
    assert D(str(data["retainage_receivable"]["total"])) == _bs_amount(bs, "1150")
    assert D(str(data["retainage_payable"]["total"])) == _bs_amount(bs, "2050")
    assert data["retainage_receivable"]["total"] == 3000.0
    assert data["retainage_receivable"]["jobs"][0]["job_id"] == annex["id"]

    wip = client.get(f"/api/reports/wip-schedule?as_of={today}").json()
    bp = data["billing_position"]
    assert bp["over"] == wip["totals"]["over_billing"]
    assert bp["under"] == wip["totals"]["under_billing"]
    assert data["backlog"]["total"] == wip["totals"]["backlog"]
    cs = client.get(f"/api/reports/commitment-status?as_of={today}").json()
    assert data["committed_unbilled"]["total"] == cs["totals"]["remaining"] == 5000.0
    assert data["committed_unbilled"]["jobs"][0]["amount"] == 5000.0


# ── Projected final cost (est B5) ────────────────────────────────────────


def _budget_job(client, customer_id):
    job = client.post("/api/jobs", json={"customer_id": customer_id, "name": "Bid"})
    return job.json()


def test_projected_counts_the_budget_nobody_has_bought(
    client, db_session, seed_accounts, seed_customer
):
    """Est B5: $150,000 of trades not yet bought were projected at $0 and the
    variance read as $189k saved. Per code, the budget no cost or commitment
    has taken up is cost to come; cost types net within a code."""
    from tests.test_job_costing import _income_expense  # noqa: F401

    _construction(client)
    job = _budget_job(client, seed_customer.id)
    conc = client.post("/api/cost-codes", json={"code": "03 30 00", "name": "Concrete"})
    elec = client.post(
        "/api/cost-codes", json={"code": "26 05 00", "name": "Electrical"}
    )
    frame = client.post("/api/cost-codes", json={"code": "06 10 00", "name": "Framing"})
    conc, elec, frame = conc.json(), elec.json(), frame.json()
    r = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={
            "rows": [
                {
                    "cost_code_id": conc["id"],
                    "amount": 50000,
                },
                {
                    "cost_code_id": elec["id"],
                    "amount": 80000,
                },
                {
                    "cost_code_id": frame["id"],
                    "amount": 20000,
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    # electrical is bought for 84,000 (over its 80,000); concrete is half spent
    # as MATERIAL (it was bid as subcontract); framing is not bought
    vendor = _sub(client, "Sparks")
    _commitment(client, vendor["id"], job["id"], 84000, elec["id"])
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "C-1",
            "date": "2026-05-01",
            "job_id": job["id"],
            "lines": [
                {
                    "account_id": seed_accounts["5100"].id,
                    "quantity": 1,
                    "rate": 20000,
                    "job_id": job["id"],
                    "cost_code_id": conc["id"],
                    "cost_type": "material",
                }
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text

    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    t = tree["totals"]
    assert t["revised"] == 150000.0
    assert t["actual"] == 20000.0 and t["committed"] == 84000.0
    # concrete: 50,000 - 20,000 = 30,000 to come; electrical: over, floored at
    # 0 (not netted against the others); framing: 20,000 not bought
    assert t["uncommitted_budget"] == 50000.0
    assert t["projected"] == 20000 + 84000 + 50000
    assert t["variance"] == 150000 - 154000
    assert t["pct_used"] == pytest.approx(154000 / 150000 * 100)

    # the list and the Job Budget vs Actual report read the same figure
    [row] = [
        r
        for r in client.get("/api/jobs/budget-vs-actual?include_inactive=true").json()
        if r["job_id"] == job["id"]
    ]
    assert row["projected"] == t["projected"] and row["variance"] == t["variance"]
    # the tree's PDF/CSV rows carry it as a row of its own so Projected foots
    csv = client.get(f"/api/jobs/{job['id']}/cost-tree/csv").text
    assert "Budget not yet committed" in csv

    # a closed job is finished: nothing left to spend
    client.put(f"/api/jobs/{job['id']}", json={"status": "closed"})
    closed = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert closed["uncommitted_budget"] == 0.0
    assert closed["projected"] == 104000.0


def test_projected_is_actual_plus_committed_for_a_business(
    client, seed_accounts, seed_customer
):
    job = _budget_job(client, seed_customer.id)
    code = client.post("/api/cost-codes", json={"code": "9000", "name": "Misc"}).json()
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": code["id"], "amount": 5000}]},
    )
    t = client.get(f"/api/jobs/{job['id']}/cost-tree").json()["totals"]
    assert t["projected"] == 0.0 and t["variance"] == 5000.0
    assert "uncommitted_budget" not in t


# ── subs at a glance ─────────────────────────────────────────────────────


def test_compliance_report_lists_the_subs_to_chase(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    today = date.today()

    def sub(name, coi, w9):
        v = _sub(client, name)
        r = client.put(
            f"/api/vendors/{v['id']}",
            json={"coi_expires": coi.isoformat() if coi else None, "w9_on_file": w9},
        )
        assert r.status_code == 200, r.text
        return v

    sub("Fine Electric", today + timedelta(days=200), True)
    sub("Lapsed Roofing", today - timedelta(days=10), True)
    sub("Soon Plumbing", today + timedelta(days=20), True)
    sub("Edge Paving", today + timedelta(days=30), True)  # the 30th day counts
    sub("Far Paving", today + timedelta(days=31), True)  # one day past the window
    sub("No Paper Drywall", today + timedelta(days=90), False)
    sub("Blank Tile", None, True)
    # a vendor that is not a sub is never listed
    client.post("/api/vendors", json={"name": "Lumber Yard", "w9_on_file": False})

    data = client.get(f"/api/reports/compliance?as_of={today.isoformat()}").json()
    got = {(r["vendor_name"], r["issue_key"]) for r in data["rows"]}
    assert got == {
        ("Lapsed Roofing", "coi_expired"),
        ("Soon Plumbing", "coi_expiring"),
        ("Edge Paving", "coi_expiring"),
        ("No Paper Drywall", "no_w9"),
        ("Blank Tile", "coi_missing"),
    }
    assert data["totals"]["coi_expired"] == 1 and data["totals"]["subs"] == 5
    [exp] = [r for r in data["rows"] if r["issue_key"] == "coi_expired"]
    assert "10 days ago" in exp["detail"]


def test_compliance_report_lists_a_paid_bill_with_no_lien_waiver(
    client, db_session, seed_accounts, seed_customer
):
    _construction(client)
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Lien job"}
    ).json()
    sub = _sub(client, "Waiver Mechanical")
    client.put(
        f"/api/vendors/{sub['id']}",
        json={"coi_expires": "2099-01-01", "w9_on_file": True},
    )
    ids = {}
    for number, status in (("W-1", None), ("W-2", "pending"), ("W-3", "unconditional")):
        r = client.post(
            "/api/bills",
            json={
                "vendor_id": sub["id"],
                "bill_number": number,
                "date": "2026-05-01",
                "job_id": job["id"],
                "lien_waiver_status": status,
                "lines": [
                    {
                        "account_id": seed_accounts["5100"].id,
                        "quantity": 1,
                        "rate": 1000,
                        "job_id": job["id"],
                    }
                ],
            },
        )
        assert r.status_code in (200, 201), r.text
        ids[number] = r.json()["id"]
    # W-1 and W-3 are paid; W-2 (pending) is not, so it is not yet an exposure
    _bill_payment(client, sub["id"], "2026-05-20", 1000, [(ids["W-1"], 1000)])
    _bill_payment(client, sub["id"], "2026-05-20", 1000, [(ids["W-3"], 1000)])

    data = client.get("/api/reports/compliance?as_of=2026-06-01").json()
    [row] = data["rows"]
    assert row["issue_key"] == "lien_waiver" and "W-1" in row["detail"]
    assert row["amount"] == 1000.0 and row["date"] == "2026-05-20"
    # once W-2 is paid its pending waiver joins the list
    _bill_payment(client, sub["id"], "2026-05-25", 1000, [(ids["W-2"], 1000)])
    data = client.get("/api/reports/compliance?as_of=2026-06-01").json()
    assert sorted(r["detail"][:8] for r in data["rows"]) == ["Bill W-1", "Bill W-2"]
    # as of a day before the payments, none had been paid
    early = client.get("/api/reports/compliance?as_of=2026-05-10").json()
    assert early["rows"] == []
    assert client.get("/api/reports/compliance/csv?as_of=2026-06-01").status_code == 200
    assert "Bill W-1" in client.get("/api/reports/compliance/csv?as_of=2026-06-01").text


# ── the screens (node probe) ─────────────────────────────────────────────


def _probe(payload: dict, mode="construction"):
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "co_dashboard_probe.js"),
            json.dumps(payload),
            mode,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@requires_node
def test_dashboard_tiles_show_the_figure_link_to_the_report_and_list_jobs():
    got = _probe(
        {
            "tiles": {
                "retainage_receivable": {
                    "total": 38694.14,
                    "jobs": [
                        {"job_id": 1, "job": "Lakeside", "amount": 30000},
                        {"job_id": 2, "job": "Annex", "amount": 8694.14},
                    ],
                },
                "retainage_payable": {"total": 15570, "jobs": []},
                "billing_position": {
                    "over": 73688.99,
                    "under": 0,
                    "over_jobs": [{"job_id": 1, "job": "Lakeside", "amount": 73688.99}],
                    "under_jobs": [],
                },
                "backlog": {"total": 90000, "jobs": []},
                "committed_unbilled": {"total": 5000, "jobs": []},
            }
        }
    )["tiles"]
    recv = got["retainage_receivable"]
    assert "$38,694.14" in recv["text"] and "held by owners (1150)" in recv["text"]
    assert recv["href"] == "#/reports/retainage?as_of_date=2026-11-07"
    assert (
        "Lakeside: $30,000.00" in recv["title"] and "Annex: $8,694.14" in recv["title"]
    )
    assert got["retainage_payable"]["href"] == recv["href"]
    assert "held from subs (2050)" in got["retainage_payable"]["text"]
    bp = got["billing_position"]
    assert "Over-billed" in bp["text"] and "$73,688.99" in bp["text"]
    assert "Under-billed" in bp["text"] and "$0.00" in bp["text"]
    assert bp["href"] == "#/reports/wip-schedule?as_of_date=2026-11-07"
    assert "Lakeside" in bp["title"]
    assert got["backlog"]["href"] == "#/reports/wip-schedule?as_of_date=2026-11-07"
    assert got["committed_unbilled"]["href"].startswith("#/reports/commitment-status")


@requires_node
def test_vendor_list_flags_subs_and_colours_the_coi_date():
    def iso(days):
        return (date.today() + timedelta(days=days)).isoformat()

    got = _probe(
        {
            "vendors": {
                "expired": {"vendor_type": "sub", "coi_expires": iso(-3)},
                "soon": {"vendor_type": "sub", "coi_expires": iso(12)},
                "fine": {"vendor_type": "sub", "coi_expires": iso(120)},
                "blank": {"vendor_type": "sub", "coi_expires": None},
                "supplier": {"vendor_type": None, "coi_expires": iso(-3)},
            }
        }
    )["coi"]
    assert got["expired"]["color"] == "var(--text-danger)"
    assert "expired 3 days ago" in got["expired"]["text"]
    assert got["soon"]["color"] == "#b26a00" and "(12 days)" in got["soon"]["text"]
    assert got["fine"]["color"] == "inherit" and "(" not in got["fine"]["text"]
    assert got["blank"]["color"] == "var(--text-danger)"
    assert got["supplier"]["text"] == ""


_TREE = {
    "totals": {
        "revised": 150000,
        "committed": 84000,
        "actual": 20000,
        "projected": 154000,
        "variance": -4000,
        "pct_used": 102.7,
        "uncommitted_budget": 50000,
        "act_revenue": 60000,
        "est_revenue": 0,
        "committed_total": 84000,
        "billed_to_date": 0,
    },
    "types": [],
}
_JOB = {"id": 7, "contract_amount": "200000", "job_number": "", "job_type": ""}
_RETAINAGE = {
    "jobs": [
        {
            "job_id": 7,
            "receivable": {"outstanding": 6000},
            "payable": {"outstanding": 2500},
        }
    ]
}


@requires_node
def test_job_overview_labels_and_retainage_tiles_for_a_contractor():
    got = _probe({"tree": _TREE, "job": _JOB, "retainage": _RETAINAGE})
    text = got["overview"]
    assert "Billed margin" in text and "Revenue billed (gross of retainage)" in text
    assert "Retainage owed to me $6,000.00" in text
    assert "Retainage I owe $2,500.00" in text
    assert "Budget not yet committed $50,000.00" in text
    assert "Actual + committed + budget not yet committed" in got["overviewHtml"]
    assert got["calls"] == ["/reports/retainage?as_of=2026-11-07&job_id=7"]
    # the retainage tiles open the report
    assert "#/reports/retainage?as_of_date=2026-11-07" in got["overviewHtml"]


@requires_node
def test_job_overview_is_as_it_was_for_a_business():
    got = _probe({"tree": _TREE, "job": _JOB, "retainage": _RETAINAGE}, "business")
    text = got["overview"]
    assert "Billed margin" not in text and "gross of retainage" not in text
    assert "Retainage" not in text and "not yet committed" not in text
    assert got["calls"] == []
    assert "Margin" in text and "Revenue" in text

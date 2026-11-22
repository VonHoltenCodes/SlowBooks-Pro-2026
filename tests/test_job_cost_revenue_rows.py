"""A contractor's Cost Detail foots its revenue (#1066): the Total row's
estimated and actual revenue are the sums of the rows a reader sees, on
screen, in the PDF and in the CSV. Budgeted revenue sits on the code and
type it was budgeted on, posted revenue on its line's code, and revenue
with no cost code and no cost type on a row of its own."""

import csv
import io
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.contacts import Vendor


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


@pytest.fixture
def job(client, db_session, seed_accounts, seed_customer):
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))
    jid = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Clinic"}
        ),
        201,
    )["id"]
    conc = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "93-100", "name": "Slab", "cost_type": "material"},
        ),
        201,
    )
    dry = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "99-100", "name": "Board", "cost_type": "subcontract"},
        ),
        201,
    )
    _ok(
        client.put(
            f"/api/jobs/{jid}/budgets",
            json={
                "rows": [
                    {
                        "cost_code_id": conc["id"],
                        "amount": 2000,
                        "revenue_amount": 3000,
                    },
                    {"cost_code_id": dry["id"], "amount": 1000, "revenue_amount": 1500},
                ]
            },
        )
    )
    vendor = Vendor(name="Supply Co")
    db_session.add(vendor)
    db_session.commit()
    cost = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.COGS)
        .order_by(Account.id)
        .first()
    )
    _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": vendor.id,
                "bill_number": "B-1",
                "date": "2026-07-01",
                "job_id": jid,
                "lines": [
                    {
                        "account_id": cost.id,
                        "quantity": 1,
                        "rate": 500,
                        "cost_code_id": conc["id"],
                    }
                ],
            },
        ),
        201,
    )
    # one invoice line on a code, one with none
    _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-07-02",
                "job_id": jid,
                "lines": [
                    {
                        "description": "Slab work",
                        "quantity": 1,
                        "rate": 1200,
                        "cost_code_id": conc["id"],
                    },
                    {"description": "Project revenue", "quantity": 1, "rate": 2500},
                ],
            },
        ),
        201,
    )
    return {"id": jid, "conc": conc, "dry": dry}


def test_the_tree_carries_the_revenue_its_total_adds_up(client, job):
    tree = _ok(client.get(f"/api/jobs/{job['id']}/cost-tree"))
    t = tree["totals"]
    assert (t["est_revenue"], t["act_revenue"]) == (4500.0, 3700.0)
    by_type = {ty["cost_type"]: ty["figures"] for ty in tree["types"]}
    assert by_type["material"]["est_revenue"] == 3000.0
    assert by_type["material"]["act_revenue"] == 1200.0
    assert by_type["subcontract"]["est_revenue"] == 1500.0
    un = tree["unassigned_revenue"]
    assert un["figures"]["act_revenue"] == 2500.0
    assert [ln["description"] for ln in un["lines"]] == ["Project revenue"]
    rows_est = sum(ty["figures"]["est_revenue"] for ty in tree["types"])
    rows_act = sum(ty["figures"]["act_revenue"] for ty in tree["types"])
    assert rows_est + un["figures"]["est_revenue"] == t["est_revenue"]
    assert rows_act + un["figures"]["act_revenue"] == t["act_revenue"]
    # the comparison by section is still there, with the same totals
    sections = tree["revenue_sections"]
    assert sum(r["act_revenue"] for r in sections) == 3700.0
    assert sum(r["est_revenue"] for r in sections) == 4500.0


def _money(v):
    return Decimal(v or "0")


def test_the_csv_revenue_columns_foot_to_its_total(client, job):
    text = client.get(f"/api/jobs/{job['id']}/cost-tree/csv").text
    rows = list(csv.reader(io.StringIO(text)))
    head = next(r for r in rows if r and r[0] == "Cost type")
    est, act = head.index("Est. Rev"), head.index("Act. Rev")
    top = [r for r in rows[rows.index(head) + 1 :] if len(r) == len(head)]
    total = next(r for r in top if r[0] == "Total")
    level0 = [r for r in top if r[0] != "Total" and r[3] == "0"]
    assert sum(_money(r[est]) for r in level0) == _money(total[est]) == 4500
    assert sum(_money(r[act]) for r in level0) == _money(total[act]) == 3700
    assert any(r[2] == "Revenue with no cost code" for r in level0)


def test_the_pdf_prints_the_revenue_row(client, job):
    pypdf = pytest.importorskip("pypdf")
    for qs in ("", "?hide_zero=1"):
        r = client.get(f"/api/jobs/{job['id']}/cost-tree/pdf{qs}")
        assert r.status_code == 200
        text = "".join(
            p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages
        )
        assert "Revenue with no cost code" in text
        assert "$2,500.00" in text and "$1,200.00" in text


def test_what_has_no_cost_code_is_called_that_in_both_tables():
    """The cost tree's row reads "Revenue with no cost code"; the section
    table's row, which carries cost too, read "Unassigned" for the same
    thing. It is "No cost code" now."""
    from types import SimpleNamespace
    from decimal import Decimal

    from app.services import job_costing

    figures = {
        k: Decimal("10") for k in ("budget", "actual", "est_revenue", "act_revenue")
    }
    code = SimpleNamespace(code="03", name="Concrete", label="03 Concrete")
    rows = job_costing._revenue_sections(
        {None: dict(figures), 7: dict(figures)}, {7: code}
    )
    assert [r["label"] for r in rows] == ["03 Concrete", "No cost code"]
    assert rows[-1]["name"] == job_costing.NO_COST_CODE == "No cost code"

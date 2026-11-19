"""Hide zero balances on the job's Cost Detail (#1052): the financial
reports' choice (#625) on the job cost report. With hide_zero=1 the JSON,
the PDF and the CSV leave out a cost code with no budget, no committed
cost, no actual cost and no revenue, and a cost type left with nothing;
the totals are the same either way. The tab's checkbox keeps the choice on
the address and on the Save PDF / Save CSV links."""

import csv
import io
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.contacts import Vendor

ROOT = Path(__file__).resolve().parent.parent


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _construction(client):
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))


@pytest.fixture
def job(client, db_session, seed_accounts, seed_customer):
    """A construction job on the seeded chart of codes (dozens of them),
    two of which carry a budget and one a bill."""
    _construction(client)
    jid = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Clinic"}
        ),
        201,
    )["id"]
    codes = {c["code"]: c for c in _ok(client.get("/api/cost-codes"))}
    conc = codes.get("03 30 00") or next(iter(codes.values()))
    dry = next(c for k, c in codes.items() if c["id"] != conc["id"] and c["parent_id"])
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
                    {"cost_code_id": dry["id"], "amount": 1000},
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
    return {"id": jid, "conc": conc, "dry": dry, "count": len(codes)}


def _code_ids(tree):
    out = []

    def walk(n):
        out.append(n["id"])
        for ch in n["children"]:
            walk(ch)

    for ty in tree["types"]:
        for n in ty["codes"]:
            walk(n)
    return out


def test_the_tree_leaves_out_the_codes_with_nothing_on_them(client, job):
    whole = _ok(client.get(f"/api/jobs/{job['id']}/cost-tree"))
    shown = _ok(client.get(f"/api/jobs/{job['id']}/cost-tree?hide_zero=1"))
    assert len(_code_ids(whole)) > 10  # the seeded chart, used or not
    ids = _code_ids(shown)
    assert job["conc"]["id"] in ids and job["dry"]["id"] in ids
    # what is left is a used code or the parent that rolls one up
    assert len(ids) < len(_code_ids(whole))

    def walk(n):
        f = n["figures"]
        assert any(f[k] for k in ("original", "changes", "committed", "actual"))
        for ch in n["children"]:
            walk(ch)

    for ty in shown["types"]:
        for n in ty["codes"]:
            walk(n)
    assert shown["totals"] == whole["totals"]
    assert shown["hide_zero"] is True and "hide_zero" not in whole


def test_the_csv_and_pdf_take_the_choice(client, job):
    whole = client.get(f"/api/jobs/{job['id']}/cost-tree/csv").text
    shown = client.get(f"/api/jobs/{job['id']}/cost-tree/csv?hide_zero=1").text
    rows = list(csv.reader(io.StringIO(shown)))
    assert len(rows) < len(list(csv.reader(io.StringIO(whole))))
    assert any("zero balances hidden" in " ".join(r) for r in rows[:4])
    total_whole = next(
        r for r in csv.reader(io.StringIO(whole)) if r and r[0] == "Total"
    )
    total_shown = next(r for r in rows if r and r[0] == "Total")
    assert total_whole == total_shown
    names = {r[2] for r in rows if len(r) > 2}
    assert job["conc"]["name"] in names

    pypdf = pytest.importorskip("pypdf")
    r = client.get(f"/api/jobs/{job['id']}/cost-tree/pdf?hide_zero=1")
    assert r.status_code == 200
    text = "".join(
        p.extract_text() for p in pypdf.PdfReader(io.BytesIO(r.content)).pages
    )
    assert "zero balances hidden" in text
    assert job["conc"]["name"] in text
    full = client.get(f"/api/jobs/{job['id']}/cost-tree/pdf")
    assert len(pypdf.PdfReader(io.BytesIO(r.content)).pages) <= len(
        pypdf.PdfReader(io.BytesIO(full.content)).pages
    )


def test_a_business_tree_takes_it_too(client, db_session, seed_accounts, seed_customer):
    jid = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Shed"}
        ),
        201,
    )["id"]
    used = _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "10", "name": "Used", "cost_type": "material"},
        ),
        201,
    )
    _ok(
        client.post(
            "/api/cost-codes",
            json={"code": "20", "name": "Unused", "cost_type": "material"},
        ),
        201,
    )
    _ok(
        client.put(
            f"/api/jobs/{jid}/budgets",
            json={"rows": [{"cost_code_id": used["id"], "amount": 100}]},
        )
    )
    whole = _ok(client.get(f"/api/jobs/{jid}/cost-tree"))
    shown = _ok(client.get(f"/api/jobs/{jid}/cost-tree?hide_zero=1"))
    assert {n["name"] for t in whole["types"] for n in t["codes"]} >= {"Used", "Unused"}
    assert {n["name"] for t in shown["types"] for n in t["codes"]} == {"Used"}
    assert shown["totals"] == whole["totals"]


def test_an_unknown_value_is_refused_as_any_filter_is(client, job):
    r = client.get(f"/api/jobs/{job['id']}/cost-tree?hide_zero=maybe")
    assert r.status_code == 422


def test_the_cost_detail_tab_offers_the_box_and_keeps_it():
    js = (ROOT / "app" / "static" / "js" / "jobs.js").read_text(encoding="utf-8")
    assert 'id="job-hide-zero"' in js and "Hide zero balances" in js
    # on the address, and on the tree and its exports
    assert "qs.set('hide_zero', '1')" in js
    assert "q.hide_zero === '1'" in js
    assert "cost-tree${JobsPage._treeQs()}" in js
    assert "JobsPage._treeQs().replace" in js

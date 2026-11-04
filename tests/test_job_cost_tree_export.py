"""Save PDF and CSV of the job's Cost Detail tab (#691): the figures are the
/cost-tree JSON's for the same dates, the period applies to the actuals, the
CSV carries a row per tree node, the PDF prints the rows the tab has open,
and the read-only and accountant sign-ins get them like the other job
reports."""

import csv
import io
from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.routes.reports.construction import cost_tree_sections
from app.services import auth as auth_service


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def _code(client, code, name, cost_type="material", parent_id=None):
    return _ok(
        client.post(
            "/api/cost-codes",
            json={
                "code": code,
                "name": name,
                "cost_type": cost_type,
                "parent_id": parent_id,
            },
        ),
        201,
    )


def _bill(client, vendor_id, job_id, account_id, number, day, amount, code_id):
    _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": vendor_id,
                "bill_number": number,
                "date": day,
                "job_id": job_id,
                "lines": [
                    {
                        "account_id": account_id,
                        "quantity": 1,
                        "rate": amount,
                        "cost_code_id": code_id,
                    }
                ],
            },
        ),
        201,
    )


@pytest.fixture
def job(client, db_session, seed_accounts, seed_customer):
    """A job with a budget, an approved change order, an open commitment and
    costs in July and September."""
    _ok(client.post("/api/cost-types/setup-offsets"))
    job = _ok(
        client.post(
            "/api/jobs",
            json={
                "customer_id": seed_customer.id,
                "name": "Barn",
                "contract_amount": 0,
            },
        ),
        201,
    )
    jid = job["id"]
    _ok(
        client.put(
            f"/api/jobs/{jid}/contract",
            json={"contract_type": "lump_sum", "contract_amount": 50000},
        )
    )
    div = _code(client, "06", "Wood")
    fram = _code(client, "06-100", "Framing", parent_id=div["id"])
    trim = _code(client, "06-200", "Trim", parent_id=div["id"])
    _ok(
        client.put(
            f"/api/jobs/{jid}/budgets",
            json={
                "rows": [
                    {
                        "cost_code_id": fram["id"],
                        "amount": 2000,
                        "revenue_amount": 3000,
                    },
                    {"cost_code_id": trim["id"], "amount": 1200},
                    {"cost_type": "labor", "amount": 4000},
                ]
            },
        )
    )
    co = _ok(
        client.post(
            "/api/change-orders",
            json={
                "job_id": jid,
                "kind": "owner",
                "funding": "contract",
                "lines": [
                    {
                        "description": "Crew",
                        "cost_type": "labor",
                        "quantity": 10,
                        "unit_cost": 50,
                        "markup_pct": 15,
                    }
                ],
            },
        ),
        201,
    )
    _ok(client.post(f"/api/change-orders/{co['id']}/submit"))
    _ok(client.post(f"/api/change-orders/{co['id']}/approve"))

    vendor = _ok(client.post("/api/vendors", json={"name": "Lumber"}), 201)
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    _bill(client, vendor["id"], jid, expense.id, "B1", "2026-07-05", 800, fram["id"])
    _bill(client, vendor["id"], jid, expense.id, "B2", "2026-09-10", 300, trim["id"])
    po = _ok(
        client.post(
            "/api/purchase-orders",
            json={
                "vendor_id": vendor["id"],
                "date": "2026-07-06",
                "job_id": jid,
                "lines": [
                    {
                        "description": "studs",
                        "quantity": 1,
                        "rate": 600,
                        "cost_code_id": fram["id"],
                    }
                ],
            },
        ),
        201,
    )
    _ok(client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"}))
    return {"id": jid, "div": div, "fram": fram, "trim": trim}


_COLS = [
    ("Original", "original"),
    ("Changes", "changes"),
    ("Budget", "revised"),
    ("Committed", "committed"),
    ("Actual", "actual"),
    ("Projected", "projected"),
    ("Variance", "variance"),
    ("% Used", "pct_used"),
    ("Est. Rev", "est_revenue"),
    ("Act. Rev", "act_revenue"),
]


def _csv(client, jid, qs=""):
    r = client.get(f"/api/jobs/{jid}/cost-tree/csv{qs}")
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    head = rows.index(["Cost type", "Code", "Name", "Level"] + [c for c, _ in _COLS])
    return rows[:head], rows[head + 1 :]


def _nodes(tree):
    """(cost type name, code, figures) for every node, the CSV's order."""
    out = []

    def walk(n, ty):
        out.append((ty["name"], n["code"], n["name"], n["figures"]))
        for ch in n["children"]:
            walk(ch, ty)

    for ty in tree["types"]:
        out.append((ty["name"], "", ty["name"], ty["figures"]))
        for n in ty["codes"]:
            walk(n, ty)
        un = ty["uncoded"]
        if un["lines"] or any(un["figures"][k] for _, k in _COLS):
            out.append((ty["name"], "", "No cost code", un["figures"]))
    jl = tree["job_level_budget"]
    if jl["original"] or jl["changes"] or jl["est_revenue"]:
        out.append(("", "", "Whole-job budget (not by code)", jl))
    return out


def _num(cell):
    return None if cell == "" else Decimal(cell.rstrip("%"))


def _assert_matches(row, figures):
    for i, (_, key) in enumerate(_COLS):
        want = figures[key]
        got = _num(row[4 + i])
        if want is None:
            assert got is None, (row, key)
        elif key == "pct_used":
            assert abs(got - Decimal(str(want))) < Decimal("0.1"), (row, key)
        else:
            assert got == Decimal(str(want)).quantize(Decimal("0.01")), (row, key)


def test_csv_has_a_row_per_node_with_the_json_figures(client, job):
    tree = _ok(client.get(f"/api/jobs/{job['id']}/cost-tree"))
    pre, rows = _csv(client, job["id"])
    assert pre[0][0] == "Company"
    assert pre[1][0] == "Report" and pre[1][1].startswith("Job Cost Detail — ")
    assert "Barn" in pre[1][1]
    assert pre[2] == ["Period", "Job to date"]
    nodes = _nodes(tree)
    body, total = rows[:-1], rows[-1]
    assert len(body) == len(nodes)
    for row, (ty, code, name, figures) in zip(body, nodes):
        assert row[:3] == [ty, code, name]
        _assert_matches(row, figures)
    assert total[0] == "Total"
    _assert_matches(total, tree["totals"])
    # the data really has every kind of figure in it
    t = tree["totals"]
    assert t["original"] == 7200.0 and t["changes"] == 500.0
    assert t["committed"] == 600.0 and t["actual"] == 1100.0
    # the nested code is there, at its level
    framing = next(r for r in body if r[1] == "06-100")
    assert framing[3] == "2"
    assert _num(framing[8]) == Decimal("800.00")


def test_period_filter_applies(client, job):
    jid = job["id"]
    qs = "?start_date=2026-07-01&end_date=2026-07-31"
    tree = _ok(client.get(f"/api/jobs/{jid}/cost-tree{qs}"))
    pre, rows = _csv(client, jid, qs)
    assert pre[2] == ["Period", "2026-07-01 — 2026-07-31"]
    total = next(r for r in rows if r and r[0] == "Total")
    assert rows[-1][0].startswith("Budgets and committed cost are job to date")
    _assert_matches(total, tree["totals"])
    assert _num(total[8]) == Decimal("800.00")  # September's 300 left out
    trim = next(r for r in rows if r[1] == "06-200")
    assert _num(trim[8]) == Decimal("0.00")
    # budgets are job to date either way; the note says so
    assert _num(total[4]) == Decimal("7200.00")
    # Job to date includes it
    _, jtd = _csv(client, jid)
    assert _num(jtd[-1][8]) == Decimal("1100.00")
    trim_jtd = next(r for r in jtd if r[1] == "06-200")
    assert _num(trim_jtd[8]) == Decimal("300.00")


def test_pdf_opens_by_get_and_carries_the_json_figures(client, job):
    pypdf = pytest.importorskip("pypdf")
    jid = job["id"]
    qs = "?start_date=2026-07-01&end_date=2026-07-31"
    r = client.get(f"/api/jobs/{jid}/cost-tree/pdf{qs}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline;")
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    page = reader.pages[0]
    assert float(page.mediabox.width) > float(page.mediabox.height)  # landscape
    text = " ".join(p.extract_text() for p in reader.pages)
    assert "Job Cost Detail" in text and "2026-07-01" in text
    tree = _ok(client.get(f"/api/jobs/{jid}/cost-tree{qs}"))
    t = tree["totals"]
    for key in ("original", "changes", "revised", "committed", "actual", "variance"):
        assert f"${t[key]:,.2f}" in text, key
    assert "$1,100.00" not in text  # September's cost is outside the period
    assert "06-100 Framing" in text


def test_pdf_prints_the_rows_the_tab_has_open(client, job):
    tree = _ok(client.get(f"/api/jobs/{job['id']}/cost-tree"))
    labels = lambda opened: [  # noqa: E731
        row["cells"][0] for row in cost_tree_sections(tree, "Barn", opened)[0]["rows"]
    ]
    every = labels(None)
    assert "06-100 Framing" in every and every[-1] == "Total"
    collapsed = labels(set())
    assert "06 Wood" not in collapsed and "Material" in collapsed
    one_level = labels({"type:material"})
    assert "06 Wood" in one_level and "06-100 Framing" not in one_level
    both = labels({"type:material", f"code:{job['div']['id']}"})
    assert "06-100 Framing" in both
    # the route reads the same choice
    r = client.get(f"/api/jobs/{job['id']}/cost-tree/pdf?expanded=type:material")
    assert r.status_code == 200, r.text


def test_unknown_job_is_404(client, job):
    assert client.get("/api/jobs/99999/cost-tree/csv").status_code == 404
    assert client.get("/api/jobs/99999/cost-tree/pdf").status_code == 404


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_ACCOUNTANT])
def test_readonly_and_accountant_get_them(client, db_session, job, role):
    db_session.add(
        User(
            username="viewer",
            display_name="Viewer",
            password_hash=auth_service.hash_password("viewer-password-123"),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password-123"},
    )
    assert r.status_code == 200, r.text
    for kind in ("csv", "pdf"):
        r = client.get(f"/api/jobs/{job['id']}/cost-tree/{kind}")
        assert r.status_code == 200, (role, kind, r.text)

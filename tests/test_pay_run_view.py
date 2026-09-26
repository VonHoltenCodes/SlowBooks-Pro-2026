"""The pay-run view adds up (2.17.3 exploratory test, macbase1 F15).

Lena's $2,166.67 less the listed Fed, State, SS and Medicare is $1,764.78,
but Net showed $1,762.61: Oregon's 0.1% statewide transit tax was withheld
and posted but had no column. Garnishments and reimbursements had none
either. The view now has an "Other" column (state taxes besides income tax,
and garnishments) and a reimbursements column when a run has any, so every
row reconciles to Net.
"""

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _money(cell):
    return Decimal(cell.replace("$", "").replace(",", ""))


def _reconciles(row):
    total = _money(row["Gross"])
    for col in ("Fed", "State", "SS", "Med", "Other", "Deductions"):
        total -= _money(row[col])
    if "+ Reimb." in row:
        total += _money(row["+ Reimb."])
    return total == _money(row["Net"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_row_adds_up_to_net():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "pay_run_view_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    rows = [json.loads(line) for line in out.stdout.splitlines() if line]
    assert [r["Other"] for r in rows] == ["$2.17", "$101.00"]
    assert all(_reconciles(r) for r in rows), rows


def test_the_view_has_an_other_column():
    js = (JS / "payroll.js").read_text(encoding="utf-8")
    view = js[js.index("async view(id)") : js.index("async process(id)")]
    assert (
        "formatCurrency((s.state_other_employee || 0) + (s.garnishments || 0))" in view
    )
    assert '<th scope="col" class="amount">Other</th>' in view
    assert "formatCurrency(s.reimbursements || 0)" in view


def test_an_oregon_stub_reconciles_with_those_columns(client, seed_accounts):
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Lena",
            "last_name": "Hart",
            "pay_type": "salary",
            "pay_rate": 56333.42,
            "work_state": "OR",
            "residence_state": "OR",
        },
    ).json()
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-09-13",
            "period_end": "2026-09-26",
            "pay_date": "2026-10-01",
            "stubs": [{"employee_id": emp["id"], "reimbursements": 12.5}],
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()["stubs"][0]
    assert s["state_other_employee"] > 0  # the statewide transit tax
    row = {
        "Gross": f"${s['gross_pay']}",
        "Fed": f"${s['federal_tax']}",
        "State": f"${s['state_tax']}",
        "SS": f"${s['ss_tax']}",
        "Med": f"${s['medicare_tax']}",
        "Other": f"${Decimal(str(s['state_other_employee'])) + Decimal(str(s['garnishments']))}",
        "Deductions": f"${Decimal(str(s['pretax_deductions'])) + Decimal(str(s['posttax_deductions']))}",
        "+ Reimb.": f"${s['reimbursements']}",
        "Net": f"${s['net_pay']}",
    }
    assert _reconciles(row), row

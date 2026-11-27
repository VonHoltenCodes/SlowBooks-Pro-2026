"""Return unspent allowance with cost posted on the allowance's code.

"Used" counts only approved allowance-funded change orders, so an allowance
billed in full with its cost posted on its code (no change order recording
the selections) still reads as all unspent. The allowances summary reports
the cost posted on the line's cost code, the Return dialog asks for the
amount (starting at the remaining, never more) and warns, naming both
figures, when more cost has posted there than the change orders record."""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from tests.test_co_effective_date_allowance import (
    _approve,
    _bill,
    _contract,
    _return,
    _setup,
)
from tests.test_wip_schedule_post import _cost

ROOT = Path(__file__).resolve().parents[1]

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _allowance_job(client, db_session, customer_id):
    code = CostCode(code="09 72 00", name="Fixtures", cost_type="material")
    other = CostCode(code="03 30 00", name="Concrete", cost_type="material")
    db_session.add_all([code, other])
    db_session.commit()
    job, sov = _setup(
        client,
        customer_id,
        [
            {"description": "Base", "kind": "work", "scheduled_value": 88000},
            {
                "description": "Fixture allowance",
                "kind": "allowance",
                "scheduled_value": 12000,
                "cost_code_id": code.id,
            },
        ],
        100000,
    )
    db_session.add(
        JobBudget(
            job_id=job["id"],
            cost_code_id=code.id,
            cost_type="material",
            amount=Decimal("12000"),
            revenue_amount=Decimal("12000"),
            source="estimate",
            locked=True,
        )
    )
    db_session.commit()
    allowance = next(s for s in sov if s["kind"] == "allowance")["id"]
    base = next(s for s in sov if s["kind"] == "work")["id"]
    return job, base, allowance, code, other


def _billed_and_spent(client, db_session, customer_id):
    job, base, allowance, code, other = _allowance_job(client, db_session, customer_id)
    _bill(
        client,
        job["id"],
        "2026-09-30",
        [
            {"sov_line_id": base, "this_period": 88000, "stored": 0},
            {"sov_line_id": allowance, "this_period": 12000, "stored": 0},
        ],
    )
    the_job = db_session.get(Job, job["id"])
    # 11,650 of fixture cost on the allowance's own code, none recorded by
    # an allowance-funded change order; 4,000 of concrete on another code
    _cost(db_session, the_job, code, "material", "11650", when=date(2026, 9, 15))
    _cost(db_session, the_job, other, "material", "4000", when=date(2026, 9, 15))
    db_session.commit()
    return job, allowance


def _row(client, job_id, allowance):
    return next(
        a
        for a in _contract(client, job_id)["contract"]["allowances"]
        if a["sov_line_id"] == allowance
    )


def test_the_summary_reports_cost_posted_on_the_allowance_code(
    client, db_session, seed_accounts, seed_customer
):
    job, allowance = _billed_and_spent(client, db_session, seed_customer.id)
    row = _row(client, job["id"], allowance)
    # "used" still means allowance-funded change orders only
    assert row["used"] == 0.0
    assert row["remaining"] == 12000.0
    # the other code's 4,000 is not the allowance's
    assert row["cost_posted"] == 11650.0


def test_an_allowance_line_with_no_cost_code_reports_no_cost_posted(
    client, db_session, seed_accounts, seed_customer
):
    job, sov = _setup(
        client,
        seed_customer.id,
        [
            {"description": "Base", "kind": "work", "scheduled_value": 90000},
            {
                "description": "Tile allowance",
                "kind": "allowance",
                "scheduled_value": 10000,
            },
        ],
        100000,
        name="Annex",
    )
    allowance = next(s for s in sov if s["kind"] == "allowance")["id"]
    assert _row(client, job["id"], allowance)["cost_posted"] is None


def test_a_partial_return_credits_only_the_amount_given(
    client, db_session, seed_accounts, seed_customer
):
    job, allowance = _billed_and_spent(client, db_session, seed_customer.id)
    r = _return(client, allowance, date="2026-09-30", amount="350")
    assert r.status_code == 201, r.text
    co = r.json()
    assert co["sell_total"] == -350.0
    assert _approve(client, co["id"]).status_code == 200
    after = _contract(client, job["id"])
    assert after["contract"]["contract_amount"] == 99650.0
    credit = [s for s in after["sov"] if s["kind"] == "change_order"]
    assert [Decimal(str(s["scheduled_value"])) for s in credit] == [Decimal("-350.00")]
    row = _row(client, job["id"], allowance)
    assert row["remaining"] == 11650.0
    assert row["cost_posted"] == 11650.0


def test_a_return_above_the_remaining_is_still_refused(
    client, db_session, seed_accounts, seed_customer
):
    _job, allowance = _billed_and_spent(client, db_session, seed_customer.id)
    r = _return(client, allowance, date="2026-09-30", amount="12000.01")
    assert r.status_code == 422, r.text


def _dialog(remaining, used, cost_posted, amount_typed="350"):
    """Run the Return dialog and its submit under node with the page's
    helpers stubbed: the dialog's HTML and the body it posts."""
    script = f"""
const fs = require('fs'), vm = require('vm');
const out = {{}};
const ctx = {{
  console, window: {{}},
  Terms: {{ isConstruction: () => true }},
  formatCurrency: n => '$' + Number(n).toFixed(2),
  escapeHtml: s => String(s),
  hint: () => '',
  openModal: (title, html) => {{ out.title = title; out.html = html; }},
  closeModal: () => {{}},
  toast: () => {{}},
  $: sel => ({{ '#co-return-date': {{ value: '2026-09-30' }},
               '#co-return-amount': {{ value: {json.dumps(amount_typed)} }} }})[sel],
  API: {{ post: async (url, body) => {{ out.url = url; out.body = body; return {{}}; }} }},
}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/jobs.js', 'utf8') + '\\nthis.J = JobsPage;', ctx);
ctx.J.setTab = async () => {{}};
ctx.J.coReturnAllowance(7, {remaining}, {used}, {json.dumps(cost_posted)});
ctx.J.coReturnAllowanceSubmit(null, 7).then(() => console.log(JSON.stringify(out)));
"""
    res = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@requires_node
def test_the_return_dialog_asks_for_the_amount_and_warns_of_cost_posted():
    out = _dialog(12000, 0, 11650)
    html = out["html"]
    assert 'id="co-return-amount"' in html
    assert 'value="12000"' in html and 'max="12000"' in html
    assert 'id="co-return-cost-warning"' in html
    assert "$11650.00 of cost has posted" in html
    assert "record only $0.00 as used" in html
    assert "Returning $12000.00" in html
    # the amount typed is what is posted
    assert out["url"] == "/change-orders/allowance-return"
    assert out["body"] == {
        "allowance_sov_line_id": 7,
        "date": "2026-09-30",
        "amount": "350",
    }


@requires_node
def test_the_return_dialog_has_no_warning_when_change_orders_record_the_cost():
    assert 'id="co-return-cost-warning"' not in _dialog(350, 11650, 11650)["html"]
    # a line with no cost code reports none posted: no warning either
    assert 'id="co-return-cost-warning"' not in _dialog(12000, 0, None)["html"]


def test_the_contract_tab_shows_cost_posted_beside_used():
    src = (ROOT / "app/static/js/jobs.js").read_text(encoding="utf-8")
    assert "Cost posted ${formatCurrency(usage.cost_posted)}" in src
    assert (
        "JobsPage.coReturnAllowance(${line.id}, ${usage.remaining}, ${usage.used},"
        in src
    )

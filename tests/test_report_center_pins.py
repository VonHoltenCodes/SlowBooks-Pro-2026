"""The Report Center's Reconciliation Reports card and its pinned cards
(#395, from a bookkeeper's notes).

- GET /api/banking/reconciliations?status=completed lists the finished
  reconciliations only, each with its account's name, for the card's list;
- the pins are the preference report_center_pins, {"pinned": [view, …]},
  through the preferences routes as they are;
- the page: the view is registered, its card on the grid, each card's star
  a toggle button (aria-pressed, named for the card, data-write), outside
  the link, and the list's rows reach the on-screen report and the PDF.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_reconciliation_report import (  # noqa: F401  (the fixture)
    _start,
    completed,
)

JS = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text(
    encoding="utf-8"
)
CSS = (Path(__file__).resolve().parents[1] / "app/static/css/style.css").read_text(
    encoding="utf-8"
)


def test_status_completed_lists_the_finished_reconciliations_with_their_account(
    client, seed_accounts, completed  # noqa: F811  (the fixture)
):
    # a second one, still open on the account
    started = _start(client, seed_accounts, 30, "670").json()
    everything = client.get("/api/banking/reconciliations").json()
    assert {r["id"] for r in everything} == {completed["id"], started["id"]}
    done = client.get("/api/banking/reconciliations?status=completed").json()
    assert [r["id"] for r in done] == [completed["id"]]
    assert done[0]["account_name"] == "Checking"
    assert done[0]["status"] == "completed" and done[0]["completed_at"]
    assert done[0]["cleared_total"] is not None
    open_ones = client.get("/api/banking/reconciliations?status=in_progress").json()
    assert [r["id"] for r in open_ones] == [started["id"]]
    assert client.get("/api/banking/reconciliations?status=done").status_code == 422


def test_an_account_less_reconciliation_is_left_out_of_the_cross_account_list(
    client, seed_accounts, db_session, completed  # noqa: F811  (the fixture)
):
    """A completed reconciliation whose ledger account is gone (a pre-2.10
    row) has a blank account and a raw-400 PDF. The accountant's package
    skips it; the Report Center's cross-account list does too, the same way
    (the account is gone), so only the account-bearing one is listed."""
    import datetime

    from app.models.banking import Reconciliation, ReconciliationStatus

    orphan = Reconciliation(
        account_id=None,
        statement_date=datetime.date(2026, 8, 31),
        statement_balance=100,
        beginning_balance=0,
        cleared_total=0,
        status=ReconciliationStatus.COMPLETED,
        completed_at=datetime.datetime(2026, 9, 1),
    )
    db_session.add(orphan)
    db_session.commit()
    assert orphan.account_name is None
    # the cross-account card list skips the orphan
    done = client.get("/api/banking/reconciliations?status=completed").json()
    assert [r["id"] for r in done] == [completed["id"]]
    # unfiltered too (still a cross-account query)
    everything = client.get("/api/banking/reconciliations").json()
    assert orphan.id not in {r["id"] for r in everything}
    # but a query scoped to the orphan's (null) account is left alone — it was
    # asked for an account, and the orphan can't match a real account_id
    scoped = client.get(
        f"/api/banking/reconciliations?account_id={seed_accounts['1000'].id}"
    ).json()
    assert orphan.id not in {r["id"] for r in scoped}


def test_the_pins_preference_round_trips(client):
    assert client.get("/api/preferences/report_center_pins").json() == {
        "key": "report_center_pins",
        "value": None,
    }
    saved = client.put(
        "/api/preferences/report_center_pins",
        json={"value": {"pinned": ["ap-aging", "reconciliation-reports"]}},
    )
    assert saved.status_code == 200, saved.text
    assert client.get("/api/preferences/report_center_pins").json()["value"] == {
        "pinned": ["ap-aging", "reconciliation-reports"]
    }
    # unpinned: the list shrinks, in place
    client.put(
        "/api/preferences/report_center_pins",
        json={"value": {"pinned": ["reconciliation-reports"]}},
    )
    assert client.get("/api/preferences/report_center_pins").json()["value"][
        "pinned"
    ] == ["reconciliation-reports"]
    assert client.delete("/api/preferences/report_center_pins").status_code == 200
    assert client.get("/api/preferences/report_center_pins").json()["value"] is None


def test_the_card_and_its_view_are_on_the_page():
    assert re.search(
        r"'reconciliation-reports':\s*\{ label: 'Reconciliation Reports', "
        r"open: \(\) => ReportsPage\.reconciliationReports\(\) \}",
        JS,
    )
    assert "card('reconciliation-reports', 'Reconciliation Reports', " in JS
    view = JS[JS.index("ReportsPage.reconciliationReports = async function") :]
    view = view[: view.index("\n};")]
    assert "ReportsPage.setAddress('reconciliation-reports', {})" in view
    assert "API.get('/banking/reconciliations?status=completed')" in view
    for col in (
        "Account",
        "Statement date",
        "Statement balance",
        "Cleared",
        "Completed",
    ):
        assert f">{col}</th>" in view, col
    # View opens the on-screen report and tells it to come back to this list
    assert (
        "BankingPage.showReconReport(${r.id}, ReportsPage.reconciliationReports)"
        in view
    )
    assert (
        """window.open('/api/banking/reconciliations/${r.id}/pdf', '_blank')""" in view
    )
    assert "No completed reconciliations yet" in view


def test_each_card_has_a_pin_outside_its_link():
    card = JS[JS.index("    _card(view, title, text) {") :]
    card = card[: card.index("\n    },")]
    link_end = card.index("</a>")
    pin = card[link_end:]
    assert '<button type="button" class="card-pin" data-write' in pin
    assert 'aria-pressed="${pinned}"' in pin
    assert 'aria-label="Pin ${escapeHtml(title)}"' in pin
    assert """onclick="ReportsPage.togglePin('${view}')\"""" in pin
    # pinned first, in the order pinned; the rest in their fixed order
    grid = JS[JS.index("    _cardGrid() {") :]
    grid = grid[: grid.index("\n    },")]
    assert "ReportsPage._pins.map(v => cards.find(c => c.view === v))" in grid
    assert "[...pinned, ...cards.filter(c => !pinned.includes(c))]" in grid
    assert (
        "API.put('/preferences/report_center_pins', { value: { pinned: ReportsPage._pins } })"
        in JS
    )
    assert "API.get('/preferences/report_center_pins')" in JS
    # the nonprofit's cards are arranged with the rest
    assert "...(Terms.isNonprofit() ? ReportsPage._nonprofitCards() : [" in JS
    assert '.card-pin[aria-pressed="true"]' in CSS


ROOT = Path(__file__).resolve().parents[1]


_CONSTRUCTION_REPORTS = (
    "wip-schedule",
    "retainage",
    "compliance",
    "field-ticket-log",
)


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


@needs_node
def test_a_construction_report_by_address_opens_the_report_center_elsewhere(tmp_path):
    probe = tmp_path / "deeplink.cjs"
    probe.write_text(
        """
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
async function run(isCon, name){
  const log={opened:[],toasts:[],center:0};
  const ctx={console,URLSearchParams,JSON,Object,
    T:s=>s, Terms:{text:s=>s,isNonprofit:()=>false,isConstruction:()=>isCon},
    escapeHtml:s=>String(s==null?'':s), document:{}, window:{}, location:{hash:'#/'},
    toast:(m,k)=>log.toasts.push([m,k]), $:()=>({classList:{contains:()=>false}}),
    App:{}};
  vm.createContext(ctx);
  vm.runInContext(src+'\\nthis.ReportsPage=ReportsPage;',ctx);
  const R=ctx.ReportsPage;
  R._addressReportCenter=()=>{log.center++;};
  for (const fn of ['wipSchedule','retainage','subCompliance','fieldTicketLog'])
    R[fn]=async()=>{log.opened.push(fn);};
  await R.openView(name,{});
  return log;
}
(async()=>{
  const out={};
  for (const name of ['wip-schedule','retainage','compliance','field-ticket-log']) {
    out[name]={con:await run(true,name), biz:await run(false,name)};
  }
  console.log(JSON.stringify(out));
})();
""",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(probe), str(ROOT / "app/static/js/reports.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert set(res) == set(_CONSTRUCTION_REPORTS)
    for name, got in res.items():
        assert len(got["con"]["opened"]) == 1, name
        assert got["con"]["toasts"] == [], name
        assert got["biz"]["opened"] == [], name
        assert got["biz"]["center"] == 1, name
        assert "construction-mode report" in got["biz"]["toasts"][0][0], name


_GATED = (
    "cost-to-complete",
    "cost-to-complete-forecast",
    "commitment-status",
    "change-order-log",
    "buyout",
)


def test_the_report_center_routes_the_gated_reports_by_mode():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app/static/js/reports.js"
    js = path.read_text(encoding="utf-8")
    for name in _GATED:
        line = next(ln for ln in js.splitlines() if f"'{name}':" in ln)
        assert "construction: true" in line, name
    line = next(ln for ln in js.splitlines() if "'job-cost-by-vendor':" in ln)
    assert "construction: true" not in line

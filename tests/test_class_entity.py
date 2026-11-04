"""A class is an entity of its own (#234, R4): the API behind the Classes
list and a class's page, and what they must reconcile to.

- GET /api/classes/activity: every class with its income, cost of goods,
  expenses and net for the period; the rows together are the Profit & Loss
  for the dates, and each row is that class's P&L by Class column; a class
  with no activity is a row of zeros; archived classes on request.
- GET /api/classes/{id}: one class. /summary: its P&L figures by account,
  the same query as /reports/profit-loss?class_id=, with the company's net
  beside it. /transactions: every posted line attributed to the class
  across every account (line class, else header, else Uncategorized), each
  with its document's link; the P&L lines net to the class's net.
- The global search finds classes, archived ones too, and the SPA opens
  the page from a hit by id alone, the name escaped.
- BillPaymentCreate no longer takes class_id or job_id (R12's substitute):
  nothing sent them and the route dropped them; now they are refused.
- The router: #/classes and #/classes/:id hand their query to the page.

What the pages do in a browser is tests/test_browser_classes.py.
"""

import json
import re
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.accounting import create_journal_entry

# the by-class module's ledger fixture, registered by its name
from tests.test_pl_by_class_detail import ledger_fixture  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
PERIOD = {"start_date": "2026-07-01", "end_date": "2026-07-31"}
D = Decimal


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, (path, r.status_code, r.text)
    return r.json()


# ── the list ──────────────────────────────────────────────────────────────


def test_activity_lists_every_class_and_ties_to_the_profit_and_loss(client, ledger):
    data = _get(client, "/api/classes/activity", **PERIOD)
    rows = {c["name"]: c for c in data["classes"]}
    assert list(rows) == ["Uncategorized", "Retail", "Side Gig"], list(rows)
    assert rows["Uncategorized"]["is_system_default"] is True
    assert rows["Side Gig"] == {
        **rows["Side Gig"],
        "income": 300.0,
        "cogs": 100.0,
        "gross_profit": 200.0,
        "expenses": 300.0,
        "net_income": -100.0,
    }
    assert rows["Retail"]["net_income"] == 100.0
    assert rows["Uncategorized"]["net_income"] == 0.0

    # each row is the by-class column; the rows together are the P&L
    by_class = _get(client, "/api/reports/profit-loss-by-class", **PERIOD)
    for col in by_class["classes"]:
        row = rows[col["class_name"]]
        for k in ("income", "cogs", "gross_profit", "expenses", "net_income"):
            assert row[k] == col[k], (col["class_name"], k)
    pl = _get(client, "/api/reports/profit-loss", **PERIOD)
    assert data["totals"]["net_income"] == pl["net_income"] == 0.0
    assert data["totals"]["income"] == pl["total_income"] == 450.0
    assert data["company"]["net_income"] == pl["net_income"]
    assert data["totals"]["count"] == 3
    assert (data["start_date"], data["end_date"]) == (
        PERIOD["start_date"],
        PERIOD["end_date"],
    )


def test_a_class_with_no_activity_is_a_row_of_zeros_and_archived_on_request(
    client, ledger
):
    quiet = client.post("/api/classes", json={"name": "Quiet"}).json()
    gone = client.post("/api/classes", json={"name": "Gone"}).json()
    assert (
        client.put(f"/api/classes/{gone['id']}", json={"is_archived": True}).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/classes/{ledger['retail']}", json={"is_archived": True}
        ).status_code
        == 200
    )

    data = _get(client, "/api/classes/activity", **PERIOD)
    names = [c["name"] for c in data["classes"]]
    assert "Quiet" in names and "Gone" not in names and "Retail" not in names
    q = next(c for c in data["classes"] if c["id"] == quiet["id"])
    assert (q["income"], q["expenses"], q["net_income"]) == (0.0, 0.0, 0.0)
    # Retail is archived with activity: left out of the rows, so the shown
    # total is not the company's, and the company figure says so
    assert data["totals"]["net_income"] == -100.0
    assert data["company"]["net_income"] == 0.0

    both = _get(client, "/api/classes/activity", include_archived=True, **PERIOD)
    names = [c["name"] for c in both["classes"]]
    assert "Gone" in names and "Retail" in names
    assert (
        next(c for c in both["classes"] if c["name"] == "Retail")["is_archived"] is True
    )
    assert both["totals"]["net_income"] == both["company"]["net_income"] == 0.0


def test_activity_defaults_to_this_year_to_date(client, ledger):
    data = _get(client, "/api/classes/activity")
    assert data["start_date"] == f"{date.today().year}-01-01"
    assert data["end_date"] == date.today().isoformat()


# ── the page ──────────────────────────────────────────────────────────────


def test_one_class_and_its_summary(client, ledger):
    one = _get(client, f"/api/classes/{ledger['side']}")
    assert one["name"] == "Side Gig" and one["is_archived"] is False
    assert client.get("/api/classes/999999").status_code == 404
    assert client.get("/api/classes/999999/summary").status_code == 404
    assert client.get("/api/classes/999999/transactions").status_code == 404

    s = _get(client, f"/api/classes/{ledger['side']}/summary", **PERIOD)
    assert s["class"]["id"] == ledger["side"] and s["class"]["name"] == "Side Gig"
    assert (s["total_income"], s["total_cogs"], s["total_expenses"]) == (
        300.0,
        100.0,
        300.0,
    )
    assert s["gross_profit"] == 200.0 and s["net_income"] == -100.0
    assert [i["account_id"] for i in s["income"]] == [ledger["income"]]
    assert [i["account_id"] for i in s["cogs"]] == [ledger["cogs"]]
    # the same query as the class's own P&L
    pl = _get(client, "/api/reports/profit-loss", class_id=ledger["side"], **PERIOD)
    for k in ("income", "cogs", "expenses", "net_income", "total_income"):
        assert s[k] == pl[k], k
    # and the company's net beside it (every class together: 0 here)
    assert s["company_net_income"] == 0.0
    assert s["company_total_income"] == 450.0


def test_transactions_are_every_line_across_every_account(
    client, db_session, seed_accounts, ledger
):
    # a balance-sheet line too: a classed entry moving cash
    create_journal_entry(
        db_session,
        date(2026, 7, 20),
        "owner draw, side gig",
        [
            {
                "account_id": seed_accounts["3000"].id,
                "debit": D("25"),
                "credit": D("0"),
            },
            {
                "account_id": seed_accounts["1000"].id,
                "debit": D("0"),
                "credit": D("25"),
            },
        ],
        class_id=ledger["side"],
    )
    db_session.commit()
    t = _get(client, f"/api/classes/{ledger['side']}/transactions", **PERIOD)
    assert t["class"]["name"] == "Side Gig"
    assert [
        (e["date"], e["account_id"], e["debit"], e["credit"]) for e in t["entries"]
    ] == [
        ("2026-07-10", ledger["expense"], 300.0, 0.0),
        ("2026-07-10", ledger["income"], 0.0, 300.0),
        ("2026-07-12", ledger["cogs"], 100.0, 0.0),  # the line's own class
        ("2026-07-20", seed_accounts["3000"].id, 25.0, 0.0),
        ("2026-07-20", seed_accounts["1000"].id, 0.0, 25.0),
    ]
    # the P&L lines net to the class's net; balance-sheet lines carry no pl_amount
    assert t["net_income"] == -100.0
    assert [e["pl_amount"] for e in t["entries"]] == [300.0, 300.0, 100.0, None, None]
    assert t["total_debit"] == 425.0 and t["total_credit"] == 325.0
    by_class = _get(client, "/api/reports/profit-loss-by-class", **PERIOD)
    col = next(c for c in by_class["classes"] if c["class_id"] == ledger["side"])
    assert col["net_income"] == t["net_income"]
    for e in t["entries"]:
        assert e["account_name"] and e["account_type"] in (
            "income",
            "cogs",
            "expense",
            "asset",
            "equity",
        )
        assert e["voided"] is False
        assert e["source_type"] == "journal"  # posted straight to the ledger
    # the one before the period is left out; with no dates, everything
    everything = _get(client, f"/api/classes/{ledger['side']}/transactions")
    assert len(everything["entries"]) == 7
    assert everything["start_date"] is None and everything["end_date"] is None


def test_a_document_line_links_to_its_document(client, seed_accounts, ledger):
    vendor = client.post("/api/vendors", json={"name": "Gravel Co"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-15",
            "terms": "Net 30",
            "bill_number": "G-1",
            "class_id": ledger["retail"],
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "gravel",
                    "quantity": 1,
                    "rate": 60,
                    "class_id": ledger["side"],
                },
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "delivery",
                    "quantity": 1,
                    "rate": 15,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    side = _get(client, f"/api/classes/{ledger['side']}/transactions", **PERIOD)
    line = next(e for e in side["entries"] if e["description"] == "gravel")
    assert line["source_type"] == "bill" and line["source_id"] == bill["id"]
    assert line["source_link"] == f"/#/bills/{bill['id']}"
    assert line["account_type"] == "expense" and line["pl_amount"] == 60.0
    # the header's class takes the rest: the delivery line and the A/P credit
    retail = _get(client, f"/api/classes/{ledger['retail']}/transactions", **PERIOD)
    kinds = {
        (e["description"], e["account_type"])
        for e in retail["entries"]
        if e["source_id"] == bill["id"]
    }
    assert kinds == {("delivery", "expense"), ("Bill G-1 - Gravel Co", "liability")}
    assert retail["net_income"] == 100.0 - 15.0


def test_the_uncategorized_page_is_the_cleanup_list(client, ledger):
    t = _get(client, f"/api/classes/{ledger['uncat']}/transactions", **PERIOD)
    assert [e["description"] for e in t["entries"]] == ["no class", "no class"]
    assert t["net_income"] == 0.0
    s = _get(client, f"/api/classes/{ledger['uncat']}/summary", **PERIOD)
    assert s["class"]["is_system_default"] is True
    assert s["total_income"] == 50.0 and s["total_expenses"] == 50.0


def test_an_archived_class_still_opens(client, ledger):
    assert (
        client.put(
            f"/api/classes/{ledger['side']}", json={"is_archived": True}
        ).status_code
        == 200
    )
    s = _get(client, f"/api/classes/{ledger['side']}/summary", **PERIOD)
    assert s["class"]["is_archived"] is True and s["net_income"] == -100.0
    t = _get(client, f"/api/classes/{ledger['side']}/transactions", **PERIOD)
    assert len(t["entries"]) == 3


# ── search ────────────────────────────────────────────────────────────────


def test_search_finds_classes_archived_too(client, ledger):
    assert (
        client.put(
            f"/api/classes/{ledger['retail']}", json={"is_archived": True}
        ).status_code
        == 200
    )
    found = _get(client, "/api/search", q="tail")
    assert found["classes"] == [
        {"id": ledger["retail"], "name": "Retail", "is_archived": True}
    ]
    found = _get(client, "/api/search", q="side")
    assert [c["name"] for c in found["classes"]] == ["Side Gig"]
    assert "classes" not in _get(client, "/api/search", q="zzzz")


def test_the_dropdown_opens_the_class_page_by_id_with_the_name_escaped():
    js = (JS / "app.js").read_text(encoding="utf-8")
    assert "key: 'classes'" in js
    assert "App.navigate('#/classes/${Number(item.id)}')" in js
    # every result's text goes through escapeHtml, and a hit takes the
    # keyboard (tabindex, Enter/Space/arrows through App.searchItemKey)
    assert (
        'html += `<div class="search-item" tabindex="0" onclick="${sec.onClick(item)}" '
        'onkeydown="App.searchItemKey(event)">${escapeHtml(label)}</div>`;' in js
    )
    keys = js[js.index("    searchItemKey(e) {") :]
    keys = keys[: keys.index("\n    },")]
    assert (
        "if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }"
        in keys
    )
    assert "else if (e.key === 'ArrowDown' && items[i + 1])" in keys
    assert "else if (e.key === 'ArrowUp')" in keys


# ── R12's substitute: BillPaymentCreate.class_id ──────────────────────────


def test_a_bill_payment_takes_a_class_but_no_job(client, seed_accounts):
    """v2.22.0 (#234) refused both; 2.31.5 (#686) gives the payment a class
    of its own, carried on its lines. A job is still refused."""
    from app.schemas.bills import BillPaymentCreate, BillPaymentResponse

    assert "class_id" in BillPaymentCreate.model_fields
    assert "job_id" not in BillPaymentCreate.model_fields
    assert "class_id" in BillPaymentResponse.model_fields
    vendor = client.post("/api/vendors", json={"name": "Ridge Supply"}).json()
    body = {
        "vendor_id": vendor["id"],
        "date": "2026-07-03",
        "amount": 10,
        "allocations": [],
    }
    r = client.post("/api/bill-payments", json={**body, "job_id": 1})
    assert r.status_code == 422, r.text
    r = client.post("/api/bill-payments", json={**body, "class_id": 999999})
    assert r.status_code == 404, r.text
    r = client.post("/api/bill-payments", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["class_id"] is None


# ── the router ────────────────────────────────────────────────────────────

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


@needs_node
def test_the_class_routes_hand_their_query_to_the_page():
    hashes = [
        "#/classes?period=last_month&show=all",
        "#/classes/5?tab=transactions&start_date=2026-07-01&end_date=2026-07-31",
    ]
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "report_views_probe.js"),
            json.dumps(hashes),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    shown = json.loads(out.stdout)
    assert shown[hashes[0]]["calls"] == [
        ["ClassesPage.render", {"period": "last_month", "show": "all"}]
    ]
    assert shown[hashes[1]]["calls"] == [
        [
            "ClassesPage.renderDetail",
            "5",
            {
                "tab": "transactions",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            },
        ]
    ]


def test_the_page_is_wired_in():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/classes.js"></script>' in index
    assert 'href="#/classes" class="nav-link" data-page="classes"' in index
    src = (JS / "classes.js").read_text(encoding="utf-8")
    # a change on the page replaces the address; the pages open by address
    assert "history.replaceState(history.state, '', url)" in src
    assert "ReportsPage.viewUrl('profit-loss-class'" in src
    assert "ReportsPage.viewUrl('profit-loss-by-class'" in src
    # an account's drill-down names the page as its way back, through a
    # push that records the page as it was left
    assert re.search(r"from: 'classes'", src)
    assert "App.navigate(this.getAttribute('href'))" in src
    reports = (JS / "reports.js").read_text(encoding="utf-8")
    assert "from === 'classes' && classId" in reports
    assert "backToPage(path, params)" in reports
    # the settings link says where rename and archive live
    assert "settings_focus', 'settings-h-classes'" in src

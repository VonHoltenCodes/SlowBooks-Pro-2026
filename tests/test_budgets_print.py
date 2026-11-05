"""Budgets and Budget vs Actual: a Print page of their own (NEW-208, #702).

The desktop app has no print menu and no Ctrl+P / Cmd+P of its own, so the
Budgets page's print layout was reachable only from a browser's Ctrl+P. A
report's Print button (#683) opens a page that brings up the print dialog
on load; /api/budgets/print and /api/budgets/variance/print are that page
for the budget sheet and for Budget vs Actual: the report PDF's template and
stylesheet, landscape, the month row at the top of every page, rows kept
whole, the company, the title and the year in the header."""

import html
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routes._print import PRINT_SCRIPT

ROOT = Path(__file__).resolve().parents[1]
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
BUDGETED = ("4000", "5000", "6000")


def _text(fragment: str) -> str:
    """A cell's words: tags stripped until none are left, entities read."""
    while True:
        stripped = re.sub(r"<[^<>]*>", "", fragment)
        if stripped == fragment:
            break
        fragment = stripped
    return html.unescape(fragment).strip()


def _rows(page: str) -> list[list[str]]:
    body = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return [
        [_text(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    ]


def _heads(page: str) -> list[str]:
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    return [_text(th) for th in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]


def _money(value) -> str:
    value = float(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _seed(client, accounts, year=2026):
    items = [
        {
            "account_id": accounts[n].id,
            "year": year,
            "month": m,
            "amount": 1000 + i * 100 + m,
        }
        for i, n in enumerate(BUDGETED)
        for m in range(1, 13)
    ]
    r = client.post("/api/budgets/bulk", json=items)
    assert r.status_code == 200, r.text
    # an actual in March against the income account
    r = client.post(
        "/api/journal",
        json={
            "date": f"{year}-03-15",
            "description": "March sales",
            "lines": [
                {"account_id": accounts["1100"].id, "debit": "750.00", "credit": "0"},
                {"account_id": accounts["4000"].id, "debit": "0", "credit": "750.00"},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text


def _as(client, role):
    r = client.post("/api/tokens", json={"label": f"as-{role}", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _is_print_page(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert PRINT_SCRIPT in r.text
    # landscape, from the report theme's @page
    assert re.search(r"@page\s*{\s*size:\s*letter landscape;", r.text), r.text[:800]
    # whole rows, the month row again on every page
    assert "table.report tr { break-inside: avoid;" in r.text
    assert "table.report thead { display: table-header-group; }" in r.text


def test_the_budgets_sheet_prints_every_account_and_month(client, seed_accounts):
    r = client.put("/api/settings", json={"company_name": "Explore Signs & Co"})
    assert r.status_code == 200, r.text
    _seed(client, seed_accounts)
    r = client.get("/api/budgets/print?year=2026")
    _is_print_page(r)
    page = r.text
    assert "<h1>Budgets</h1>" in page
    assert "Explore Signs &amp; Co" in page
    assert re.search(r'class="report-meta">\s*2026<br>', page)
    assert _heads(page) == ["Account"] + MONTHS

    # every account the page has a row for, in the page's order
    shown = [
        a
        for a in client.get("/api/accounts").json()
        if a["account_type"] in ("expense", "income", "cogs")
    ]
    rows = _rows(page)
    assert [row[0] for row in rows] == [
        f"{a['account_number']} {a['name']}" for a in shown
    ]
    by_id = {a["id"]: row for a, row in zip(shown, rows)}
    for row in rows:
        assert len(row) == 13, row

    # the figures are GET /api/budgets's for the same year
    budgets = client.get("/api/budgets?year=2026").json()
    assert len(budgets) == 36
    for b in budgets:
        assert by_id[b["account_id"]][b["month"]] == _money(b["amount"]), b
    # an account with no budget prints blank months
    unbudgeted = next(a for a in shown if a["account_number"] not in BUDGETED)
    assert by_id[unbudgeted["id"]][1:] == [""] * 12

    # one account, as GET /api/budgets takes account_id
    one = seed_accounts["5000"].id
    r = client.get(f"/api/budgets/print?year=2026&account_id={one}")
    _is_print_page(r)
    rows = _rows(r.text)
    assert [row[0] for row in rows] == [f"5000 {seed_accounts['5000'].name}"], rows
    for b in client.get(f"/api/budgets?year=2026&account_id={one}").json():
        assert rows[0][b["month"]] == _money(b["amount"])


def test_a_year_with_no_budgets_prints_an_empty_sheet_with_its_header(
    client, seed_accounts
):
    _seed(client, seed_accounts)
    r = client.get("/api/budgets/print?year=1999")
    _is_print_page(r)
    assert "<h1>Budgets</h1>" in r.text
    assert re.search(r'class="report-meta">\s*1999<br>', r.text)
    assert _heads(r.text) == ["Account"] + MONTHS
    assert all(row[1:] == [""] * 12 for row in _rows(r.text))

    r = client.get("/api/budgets/variance/print?year=1999")
    _is_print_page(r)
    assert "<h1>Budget vs Actual</h1>" in r.text
    assert re.search(r'class="report-meta">\s*1999<br>', r.text)
    assert _heads(r.text) == [""] + MONTHS + ["Total"]
    assert _rows(r.text) == []
    assert "No budgets set for this year." in r.text


def test_budget_vs_actual_prints_as_its_dialog_shows_it(client, seed_accounts):
    _seed(client, seed_accounts)
    r = client.get("/api/budgets/variance/print?year=2026")
    _is_print_page(r)
    page = r.text
    assert "<h1>Budget vs Actual</h1>" in page
    assert re.search(r'class="report-meta">\s*2026<br>', page)
    assert _heads(page) == [""] + MONTHS + ["Total"]

    data = client.get("/api/budgets/variance?year=2026").json()
    assert len(data["accounts"]) == len(BUDGETED)
    rows = _rows(page)
    assert len(rows) == 4 * len(data["accounts"])
    for i, acct in enumerate(data["accounts"]):
        head, *lines = rows[4 * i : 4 * i + 4]
        assert head[0] == f"{acct['account_number']} — {acct['account_name']}"
        for line, key in zip(lines, ("budget", "actual", "variance")):
            assert line[0] == key.capitalize()
            assert line[1:13] == [_money(m[key]) for m in acct["months"]], key
            assert line[13] == _money(acct["total_" + key]), key
    # the March actual is there
    income = next(a for a in data["accounts"] if a["account_number"] == "4000")
    assert income["months"][2]["actual"] == 750.0
    assert _money(750) in page


def test_the_print_pages_take_the_list_routes_parameters(client, seed_accounts):
    # year is required on Budget vs Actual, as on /variance
    assert client.get("/api/budgets/variance").status_code == 422
    assert client.get("/api/budgets/variance/print").status_code == 422
    # no year on the sheet is this year's
    from datetime import date

    r = client.get("/api/budgets/print")
    _is_print_page(r)
    assert re.search(rf'class="report-meta">\s*{date.today().year}<br>', r.text)


def test_every_role_that_reads_budgets_can_print_them(client, seed_accounts):
    """The role rule of the reports' Print: a print page answers where its
    data route does. Printing reads only, so read-only and accountant
    sign-ins print too; signed out is refused."""
    _seed(client, seed_accounts)
    who = {role: _as(client, role) for role in ("readonly", "bookkeeper", "accountant")}
    who["signed out"] = TestClient(app)
    pairs = (
        ("/api/budgets?year=2026", "/api/budgets/print?year=2026"),
        ("/api/budgets/variance?year=2026", "/api/budgets/variance/print?year=2026"),
    )
    for role, c in who.items():
        for data_url, print_url in pairs:
            data, page = c.get(data_url), c.get(print_url)
            assert page.status_code == data.status_code, (role, print_url)
            if role == "signed out":
                assert page.status_code == 401, print_url
                assert PRINT_SCRIPT not in page.text
            else:
                assert page.status_code == 200, (role, print_url)
                assert PRINT_SCRIPT in page.text


def test_budgets_js_opens_the_print_pages():
    js = (ROOT / "app/static/js/budgets.js").read_text(encoding="utf-8")
    assert "/api/budgets/print?year=" in js
    assert "/api/budgets/variance/print?year=" in js
    assert "window.open(BudgetsPage.printUrl(variance), '_blank')" in js

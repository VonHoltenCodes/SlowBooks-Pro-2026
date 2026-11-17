"""Enter Expense's Paid From (#1005): active bank and card accounts only,
opening on the default deposit account, else the first active checking;
an expense being edited keeps its own account even if inactive."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_list_and_the_start_rules_are_in_the_page():
    js = (ROOT / "app/static/js/expenses.js").read_text(encoding="utf-8")
    body = js.split("paidFromAccounts(accounts, keepId) {")[1].split("},")[0]
    assert "a.is_active !== false || String(a.id) === String(keepId)" in body
    start = js.split("startPaidFrom(paidFrom, settings) {")[1].split("},")[0]
    assert "DepositAccounts.startId(paidFrom, settings)" in start
    assert "/checking/i.test(a.name || '')" in start
    assert "ExpensesPage.startPaidFrom(paidFrom, App.settings)" in js
    assert "ExpensesPage.showForm(x.paid_from_account_id)" in js
    assert "' (inactive)'" in js

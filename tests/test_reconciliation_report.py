"""Reconciliation: statements go forward, an out-of-balance Finish says why,
and a completed reconciliation leaves a report to keep.

Exploratory 2.17.3 (skytech, W-M20):
- a new reconciliation could start with a statement date earlier than the
  last completed one (Sep 1 after Sep 26);
- an out-of-balance Finish Reconciliation did nothing and said nothing;
- there was no reconciliation report or PDF."""

import re
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)


def _entry(client, seed_accounts, day, amount, category, check=None, payee=None):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "category_account_id": seed_accounts[category].id,
            "payee": payee,
            "check_number": check,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _lines(client, recon_id):
    return client.get(f"/api/banking/reconciliations/{recon_id}/transactions").json()


def _start(client, seed_accounts, day, balance):
    return client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": f"2026-09-{day:02d}",
            "statement_balance": balance,
        },
    )


@pytest.fixture
def completed(client, seed_accounts):
    """Sep: a $1,000 deposit and check 1050 for $250 clear; check 1051 for
    $80 is still out on the statement date."""
    _entry(client, seed_accounts, 2, "1000", "4000", payee="Acme Diner")
    _entry(client, seed_accounts, 5, "-250", "6000", check="1050", payee="Sign Supply")
    _entry(client, seed_accounts, 20, "-80", "6000", check="1051", payee="Print Shop")
    recon = _start(client, seed_accounts, 26, "750").json()
    for row in _lines(client, recon["id"])["transactions"]:
        if row["reference"] != "1051":
            r = client.post(
                f"/api/banking/reconciliations/{recon['id']}/toggle/{row['id']}"
            )
            assert r.status_code == 200, r.text
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    return recon


def test_a_statement_on_or_before_the_last_reconciled_one_is_refused(
    client, seed_accounts, completed
):
    for day in (1, 26):
        r = _start(client, seed_accounts, day, "750")
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == (
            "Checking is reconciled through Sep 26, 2026. Start the next "
            "reconciliation with a later statement date."
        )
    r = _start(client, seed_accounts, 30, "670")
    assert r.status_code == 201, r.text
    assert float(r.json()["beginning_balance"]) == 750.0


def test_an_out_of_balance_finish_says_why(client, seed_accounts):
    _entry(client, seed_accounts, 2, "1000", "4000")
    recon = _start(client, seed_accounts, 26, "1012.34").json()
    [row] = _lines(client, recon["id"])["transactions"]
    client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{row['id']}")
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Not finished: the difference is $12.34, and it must be $0.00. Tick the "
        "lines that are on your statement, or check the statement's ending balance."
    )


def test_a_completed_reconciliation_has_a_report(client, seed_accounts, completed):
    r = client.get(f"/api/banking/reconciliations/{completed['id']}/report")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["account_name"] == "Checking" and rep["statement_date"] == "2026-09-26"
    assert rep["beginning_balance"] == 0.0 and rep["ending_balance"] == 750.0
    assert rep["cleared_balance"] == 750.0 and rep["difference"] == 0.0
    assert rep["labels"] == {
        "increase": "Deposits and other credits",
        "decrease": "Checks and payments",
    }
    deposits = rep["cleared"]["increase"]
    checks = rep["cleared"]["decrease"]
    assert (deposits["count"], deposits["total"]) == (1, 1000.0)
    assert (checks["count"], checks["total"]) == (1, -250.0)
    assert [(i["reference"], i["payee"], i["amount"]) for i in checks["items"]] == [
        ("1050", "Sign Supply", -250.0)
    ]
    outstanding = rep["uncleared"]["decrease"]
    assert [(i["reference"], i["amount"]) for i in outstanding["items"]] == [
        ("1051", -80.0)
    ]
    assert rep["register_balance"] == 670.0


def test_the_report_prints(client, seed_accounts, completed):
    r = client.get(f"/api/banking/reconciliations/{completed['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert "reconciliation_1000_2026-09-26.pdf" in r.headers["content-disposition"]


def test_an_open_reconciliation_has_no_report_yet(client, seed_accounts):
    recon = _start(client, seed_accounts, 26, "0").json()
    r = client.get(f"/api/banking/reconciliations/{recon['id']}/report")
    assert r.status_code == 400 and "isn't finished" in r.json()["detail"]


def test_the_next_reports_leave_out_what_an_earlier_one_closed(
    client, seed_accounts, completed
):
    recon = _start(client, seed_accounts, 30, "670").json()
    [row] = _lines(client, recon["id"])["transactions"]  # only check 1051 is left
    client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{row['id']}")
    assert (
        client.post(f"/api/banking/reconciliations/{recon['id']}/complete").status_code
        == 200
    )
    rep = client.get(f"/api/banking/reconciliations/{recon['id']}/report").json()
    assert rep["beginning_balance"] == 750.0
    assert rep["cleared"]["increase"]["items"] == []
    assert [i["reference"] for i in rep["cleared"]["decrease"]["items"]] == ["1051"]
    # the first report still shows 1051 as outstanding on its own date
    first = client.get(f"/api/banking/reconciliations/{completed['id']}/report").json()
    assert [i["reference"] for i in first["uncleared"]["decrease"]["items"]] == ["1051"]


def test_finish_is_offered_and_says_why_it_cannot_finish():
    view = re.search(r"async showReconcileView\(reconId\).*?\n    },\n", JS, re.S)
    body = view.group(0)
    assert 'id="recon-finish-btn"' in body
    finish_btn = re.search(r'<button[^>]*id="recon-finish-btn"[^>]*>', body).group(0)
    assert "disabled" not in finish_btn
    finish = re.search(
        r"async finishReconcile\(reconId, accountId\).*?\n    },\n", JS, re.S
    ).group(0)
    assert "the difference is ${formatCurrency(data.difference)}" in finish
    assert finish.index("data.difference") < finish.index("confirm(")
    assert "showReconReport(reconId)" in finish
    report = re.search(r"async showReconReport\(reconId\).*?\n    },\n", JS, re.S)
    assert "/report`" in report.group(0) and "/pdf'" in report.group(0)
    assert "BankingPage.showReconciliations(${id})" in JS

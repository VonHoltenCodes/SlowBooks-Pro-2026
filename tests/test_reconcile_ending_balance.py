"""The statement's ending balance can be corrected mid-reconciliation (#389).

The ending balance was typed once, at the start; a transposed digit meant
starting over. `PUT /api/banking/reconciliations/{id}` now takes a new
statement_balance and/or statement_date while the reconciliation is in
progress. The ticks stay; the next session's difference is refigured from
them. A date moved later brings more lines onto the session; one moved
earlier leaves the lines after it out, their ticks kept. Refused once
completed, and refused on or before the last completed statement.
"""

import pytest


@pytest.fixture
def vendor(client):
    return client.post("/api/vendors", json={"name": "Grid Power"}).json()


def _expense(client, seed_accounts, vendor, amount, day):
    r = client.post(
        "/api/expenses",
        json={
            "date": f"2026-09-{day:02d}",
            "vendor_id": vendor["id"],
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": amount,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deposit(client, seed_accounts, amount, day):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "category_account_id": seed_accounts["4000"].id,
            "payee": "walk-in",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _start(client, seed_accounts, balance, day):
    r = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": f"2026-09-{day:02d}",
            "statement_balance": balance,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _session(client, recon_id):
    return client.get(f"/api/banking/reconciliations/{recon_id}/transactions").json()


def _put(client, recon_id, **body):
    return client.put(f"/api/banking/reconciliations/{recon_id}", json=body)


def _tick(client, recon_id, line_id):
    r = client.post(f"/api/banking/reconciliations/{recon_id}/toggle/{line_id}")
    assert r.status_code == 200, r.text


@pytest.fixture
def statement(client, seed_accounts, vendor):
    """Three lines through the 12th, a fourth on the 25th; the statement
    is dated the 20th with the balance typed as 801 instead of 810."""
    _deposit(client, seed_accounts, "1000", 1)
    _expense(client, seed_accounts, vendor, "150", 5)
    _expense(client, seed_accounts, vendor, "40", 12)
    _deposit(client, seed_accounts, "500", 25)
    return _start(client, seed_accounts, "801.00", day=20)


def test_a_corrected_balance_refigures_the_difference_from_the_ticks(client, statement):
    s = _session(client, statement["id"])
    for t in s["transactions"]:
        _tick(client, statement["id"], t["id"])
    s = _session(client, statement["id"])
    assert s["cleared_total"] == 810.0 and s["difference"] == -9.0

    r = _put(client, statement["id"], statement_balance="810.00")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == statement["id"]
    assert float(body["statement_balance"]) == 810.0
    assert body["statement_date"] == "2026-09-20"
    assert body["status"] == "in_progress"

    s = _session(client, statement["id"])
    assert s["statement_balance"] == 810.0
    assert s["cleared_total"] == 810.0  # the ticks stayed
    assert s["difference"] == 0.0
    r = client.post(f"/api/banking/reconciliations/{statement['id']}/complete")
    assert r.status_code == 200, r.text


def test_a_completed_reconciliation_refuses_the_change(client, statement):
    _put(client, statement["id"], statement_balance="810.00")
    client.post(
        f"/api/banking/reconciliations/{statement['id']}/mark-all",
        json={"cleared": True},
    )
    r = client.post(f"/api/banking/reconciliations/{statement['id']}/complete")
    assert r.status_code == 200, r.text
    r = _put(client, statement["id"], statement_balance="999.00")
    assert r.status_code == 400 and "already completed" in r.json()["detail"]
    assert float(_session(client, statement["id"])["statement_balance"]) == 810.0


def test_a_later_date_brings_lines_in_and_an_earlier_one_keeps_the_ticks(
    client, statement
):
    s = _session(client, statement["id"])
    assert [t["date"] for t in s["transactions"]] == [
        "2026-09-01",
        "2026-09-05",
        "2026-09-12",
    ]
    for t in s["transactions"]:
        _tick(client, statement["id"], t["id"])

    # Later: the 25th joins the session, unticked; the ticks stay.
    r = _put(client, statement["id"], statement_date="2026-09-30")
    assert r.status_code == 200, r.text
    assert r.json()["statement_date"] == "2026-09-30"
    s = _session(client, statement["id"])
    assert s["statement_date"] == "2026-09-30"
    by_date = {t["date"]: t["reconciled"] for t in s["transactions"]}
    assert by_date == {
        "2026-09-01": True,
        "2026-09-05": True,
        "2026-09-12": True,
        "2026-09-25": False,
    }
    assert s["cleared_total"] == 810.0 and s["difference"] == -9.0

    # Earlier, past a ticked line: the line leaves the session, its tick
    # kept (as Abandon keeps them), and the figures count what is left.
    _tick(
        client, statement["id"], [t for t in s["transactions"]][2]["id"]
    )  # untick 12th
    r = _put(client, statement["id"], statement_date="2026-09-04")
    assert r.status_code == 200, r.text
    s = _session(client, statement["id"])
    assert [t["date"] for t in s["transactions"]] == ["2026-09-01"]
    assert s["cleared_total"] == 1000.0 and s["difference"] == -199.0
    # The register still shows the 5th's line cleared.
    reg = client.get(
        f"/api/banking/check-register?account_id={statement['account_id']}"
    ).json()
    entries = reg["entries"]
    fifth = [e for e in entries if e["date"] == "2026-09-05"]
    assert fifth and fifth[0]["cleared"] is True and not fifth[0]["reconciliation_id"]


def test_an_earlier_date_reports_the_ticks_that_carry_to_the_next_session(
    client, seed_accounts, vendor, statement
):
    """A date moved earlier past ticked lines leaves them ticked but off the
    session; completing won't stamp them, so they carry to the next
    reconciliation already ticked (#400 review). The change reports how
    many, and the session carries the same count for the screen."""
    s = _session(client, statement["id"])
    # With the fourth line (the 25th) in reach, tick all four.
    r = _put(client, statement["id"], statement_date="2026-09-30")
    assert r.status_code == 200 and r.json()["ticked_after_date"] == 0
    s = _session(client, statement["id"])
    for t in s["transactions"]:
        _tick(client, statement["id"], t["id"])

    # Move the date back to the 20th (and correct the balance to the 810
    # that the lines through it clear to): the 25th's tick now falls after.
    r = _put(
        client, statement["id"], statement_date="2026-09-20", statement_balance="810.00"
    )
    assert r.status_code == 200, r.text
    assert r.json()["ticked_after_date"] == 1
    s = _session(client, statement["id"])
    assert s["ticked_after_date"] == 1
    # The 25th is not on the session, but still cleared in the register.
    assert "2026-09-25" not in [t["date"] for t in s["transactions"]]
    reg = client.get(
        f"/api/banking/check-register?account_id={statement['account_id']}"
    ).json()
    twenty_fifth = [e for e in reg["entries"] if e["date"] == "2026-09-25"]
    assert twenty_fifth and twenty_fifth[0]["cleared"] is True

    # Finish this one at the 20th, then the next session (ending later)
    # shows the 25th already ticked — it carried, unstamped.
    assert (
        client.post(
            f"/api/banking/reconciliations/{statement['id']}/complete"
        ).status_code
        == 200
    )
    nxt = _start(client, seed_accounts, "500.00", day=30)
    ns = _session(client, nxt["id"])
    carried = [t for t in ns["transactions"] if t["date"] == "2026-09-25"]
    assert carried and carried[0]["reconciled"] is True


def test_a_fresh_reconcile_ignores_a_feed_cleared_line_after_the_date(
    client, db_session, seed_accounts, vendor, statement
):
    """A deposit dated after the statement date, cleared by a bank-feed match
    or a register clear (both just set cleared=True), is not a tick this
    session left behind: the date never moved, so the line was never in reach
    (#400 review). The old count, which took every cleared, unreconciled line
    after the date, reported 1 here and showed the note on nearly every
    reconcile of a feed-linked account."""
    from datetime import date

    from app.models.transactions import Transaction, TransactionLine

    # The 25th's deposit line is after the 20th statement date. Tick it the
    # way the feed does, outside any reconciliation.
    line = (
        db_session.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == seed_accounts["1000"].id)
        .filter(Transaction.date == date(2026, 9, 25))
        .first()
    )
    line.cleared = True
    db_session.commit()

    s = _session(client, statement["id"])
    assert s["ticked_after_date"] == 0
    # It is genuinely cleared in the register, just not attributed to this
    # fresh session.
    assert line.cleared is True


def test_the_reconcile_screen_warns_about_ticks_past_the_date():
    src = open("app/static/js/banking.js", encoding="utf-8").read()
    # The Change… button is named for a screen reader (#400 review).
    assert 'aria-label="Change the statement date or ending balance"' in src
    # The screen and the change response say the carried ticks count.
    assert "data.ticked_after_date > 0" in src
    assert "to the next reconciliation, still ticked" in src
    assert "res.ticked_after_date" in src


def test_a_date_on_or_before_the_last_completed_statement_is_refused(
    client, seed_accounts, vendor
):
    _deposit(client, seed_accounts, "1000", 1)
    first = _start(client, seed_accounts, "1000.00", day=10)
    client.post(
        f"/api/banking/reconciliations/{first['id']}/mark-all", json={"cleared": True}
    )
    assert (
        client.post(f"/api/banking/reconciliations/{first['id']}/complete").status_code
        == 200
    )
    second = _start(client, seed_accounts, "1000.00", day=20)
    r = _put(client, second["id"], statement_date="2026-09-10")
    assert r.status_code == 400 and "reconciled through" in r.json()["detail"]
    r = _put(client, second["id"], statement_date="2026-09-11")
    assert r.status_code == 200, r.text


def test_an_invalid_body_is_422(client, statement):
    assert _put(client, statement["id"], statement_balance="ten").status_code == 422
    assert _put(client, statement["id"], ending_balance="810").status_code == 422
    assert _put(client, statement["id"], statement_balance=None).status_code == 422
    assert _put(client, statement["id"], statement_date="2026-13-01").status_code == 422
    # Nothing sent: nothing changes.
    r = _put(client, statement["id"])
    assert r.status_code == 200 and float(r.json()["statement_balance"]) == 801.0
    assert _put(client, 999999, statement_balance="1").status_code == 404


def test_the_change_leaves_an_update_row_in_the_audit_trail(client, statement):
    _put(client, statement["id"], statement_balance="810.00")
    rows = client.get(
        "/api/audit",
        params={"table_name": "reconciliations", "record_id": statement["id"]},
    ).json()
    updates = [r for r in rows if r["action"] == "UPDATE"]
    assert updates, rows
    assert "statement_balance" in updates[-1]["changed_fields"]


def test_the_reconcile_screen_offers_the_change():
    src = open("app/static/js/banking.js", encoding="utf-8").read()
    assert "BankingPage.changeStatement(${reconId}" in src
    assert "API.put(`/banking/reconciliations/${reconId}`" in src
    assert 'name="statement_balance" type="number" step="0.01" required value=' in src


def test_starting_over_an_open_session_puts_the_typed_values(client, statement):
    """Starting a reconciliation while one is open (409) offers the typed date
    and balance to the open session via PUT, rather than dropping them (#497)."""
    src = open("app/static/js/banking.js", encoding="utf-8").read()
    # The 409 handler PUTs the just-typed values to the existing session.
    assert "await API.put(`/banking/reconciliations/${existing}`, {" in src
    assert "statement_date: typedDate" in src
    assert "statement_balance: typedBalance" in src

    # The server accepts that PUT on the open session (both fields at once).
    r = _put(
        client, statement["id"], statement_date="2026-09-22", statement_balance="810.00"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["statement_date"] == "2026-09-22"
    assert float(body["statement_balance"]) == 810.0

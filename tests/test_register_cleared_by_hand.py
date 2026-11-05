"""The register's ✓ set by hand (#715): someone who ticks each entry off
against online banking, and never runs a reconciliation, marks a line
cleared in the register, as in QuickBooks Desktop. It is the flag a feed
match or a reconciliation tick sets, so the next reconciliation opens with
the line ticked; a line a completed reconciliation holds, and a voided
entry, are not marked; a read-only or accountant's sign-in is refused; every
change is in the audit log."""

from pathlib import Path

import pytest

from app.models.audit import AuditLog
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY
from tests.test_accountant_role import _login_as, _mk_user

ROOT = Path(__file__).resolve().parents[1]


def _entry(client, seed_accounts, amount, day, reference="", payee="Grid Power"):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": f"2026-09-{day:02d}",
            "amount": amount,
            "category_account_id": seed_accounts[
                "4000" if float(amount) > 0 else "6000"
            ].id,
            "payee": payee,
            "check_number": reference or None,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _register(client, seed_accounts):
    r = client.get(f"/api/banking/check-register?account_id={seed_accounts['1000'].id}")
    assert r.status_code == 200, r.text
    return {e["transaction_id"]: e for e in r.json()["entries"]}


def _line(client, seed_accounts, txn_id):
    return _register(client, seed_accounts)[txn_id]["line_id"]


def _mark(client, seed_accounts, line_id, cleared, account=None):
    acct = account if account is not None else seed_accounts["1000"].id
    return client.post(
        f"/api/banking/registers/{acct}/lines/{line_id}/cleared",
        json={"cleared": cleared},
    )


@pytest.fixture
def books(client, seed_accounts):
    deposit = _entry(client, seed_accounts, "1000", 1, payee="walk-in")
    check = _entry(client, seed_accounts, "-150", 5, reference="1042")
    late = _entry(client, seed_accounts, "-40", 25, reference="1043")
    return {"deposit": deposit, "check": check, "late": late}


def test_the_route_marks_and_unmarks_and_the_register_says_so(
    client, seed_accounts, books, db_session
):
    line = _line(client, seed_accounts, books["check"]["id"])
    r = _mark(client, seed_accounts, line, True)
    assert r.status_code == 200, r.text
    assert r.json() == {"line_id": line, "cleared": True, "reconciliation_id": None}
    row = _register(client, seed_accounts)[books["check"]["id"]]
    assert row["cleared"] is True and row["reconciliation_id"] is None
    # in the audit log: an UPDATE of the line naming `cleared`
    logged = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "transaction_lines", AuditLog.record_id == line)
        .filter(AuditLog.action == "UPDATE")
        .all()
    )
    assert logged and "cleared" in (logged[-1].changed_fields or [])
    assert logged[-1].new_values["cleared"] is True

    # the same again changes nothing; off takes the mark away
    assert _mark(client, seed_accounts, line, True).json()["cleared"] is True
    r = _mark(client, seed_accounts, line, False)
    assert r.status_code == 200 and r.json()["cleared"] is False
    assert _register(client, seed_accounts)[books["check"]["id"]]["cleared"] is False
    db_session.expire_all()
    updates = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "transaction_lines", AuditLog.record_id == line)
        .filter(AuditLog.action == "UPDATE")
        .count()
    )
    assert updates == 2


def test_a_line_of_another_account_or_none_at_all_is_not_found(
    client, seed_accounts, books
):
    line = _line(client, seed_accounts, books["check"]["id"])
    r = _mark(client, seed_accounts, line, True, account=seed_accounts["2100"].id)
    assert r.status_code == 404
    assert "register" in r.json()["detail"]
    assert _mark(client, seed_accounts, 999999, True).status_code == 404
    # an account that is not a bank or card account is not a register
    r = _mark(client, seed_accounts, line, True, account=seed_accounts["4000"].id)
    assert r.status_code == 400


def test_a_reconciled_line_is_refused(client, seed_accounts, books):
    line = _line(client, seed_accounts, books["deposit"]["id"])
    recon = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": "2026-09-02",
            "statement_balance": "1000.00",
        },
    ).json()
    assert (
        client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{line}")
    ).status_code == 200
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    for want in (False, True):
        r = _mark(client, seed_accounts, line, want)
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail.startswith(
            "This line is in the completed reconciliation of the statement dated"
        ) and detail.endswith("which holds its cleared mark."), detail
    row = _register(client, seed_accounts)[books["deposit"]["id"]]
    assert row["cleared"] is True and row["reconciliation_id"] == recon["id"]


def test_a_voided_entry_and_its_reversal_are_refused(client, seed_accounts, books):
    txn = books["check"]["id"]
    r = client.post(f"/api/banking/entries/{txn}/void")
    assert r.status_code == 200, r.text
    rows = _register(client, seed_accounts)
    voided = [e for e in rows.values() if e["voided"]]
    assert len(voided) == 2  # the entry and the reversal that voided it
    for e in voided:
        r = _mark(client, seed_accounts, e["line_id"], True)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "A voided entry cannot be marked cleared."
        assert _register(client, seed_accounts)[e["transaction_id"]]["cleared"] is (
            False
        )


def test_the_next_reconciliation_opens_with_a_hand_cleared_line_ticked(
    client, seed_accounts, books
):
    check = _line(client, seed_accounts, books["check"]["id"])
    late = _line(client, seed_accounts, books["late"]["id"])
    assert _mark(client, seed_accounts, check, True).status_code == 200
    assert _mark(client, seed_accounts, late, True).status_code == 200
    recon = client.post(
        "/api/banking/reconciliations",
        json={
            "account_id": seed_accounts["1000"].id,
            "statement_date": "2026-09-20",
            "statement_balance": "850.00",
        },
    ).json()
    s = client.get(f"/api/banking/reconciliations/{recon['id']}/transactions").json()
    ticked = {t["id"]: t["reconciled"] for t in s["transactions"]}
    # on the statement: ticked, and counted in the cleared figure
    assert ticked[check] is True
    assert s["cleared_total"] == -150.0
    # after the statement date: not on the session, and not "left behind"
    assert late not in ticked and s["ticked_after_date"] == 0

    # unticked there, it is unticked in the register too
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{check}")
    assert r.status_code == 200 and r.json()["reconciled"] is False
    assert _register(client, seed_accounts)[books["check"]["id"]]["cleared"] is False

    # ticked again and finished: the R holds it, the late one stays ✓
    client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{check}")
    deposit = _line(client, seed_accounts, books["deposit"]["id"])
    client.post(f"/api/banking/reconciliations/{recon['id']}/toggle/{deposit}")
    r = client.post(f"/api/banking/reconciliations/{recon['id']}/complete")
    assert r.status_code == 200, r.text
    rows = _register(client, seed_accounts)
    assert rows[books["check"]["id"]]["reconciliation_id"] == recon["id"]
    assert rows[books["late"]["id"]]["cleared"] is True
    assert rows[books["late"]["id"]]["reconciliation_id"] is None


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_ACCOUNTANT])
def test_a_read_only_or_accountant_sign_in_is_refused(
    client, seed_accounts, books, db_session, role
):
    line = _line(client, seed_accounts, books["check"]["id"])
    _mk_user(db_session, "viewer", "viewer-password-123", role)
    _login_as(client, "viewer", "viewer-password-123")
    # the mark is read
    assert _register(client, seed_accounts)[books["check"]["id"]]["cleared"] is False
    r = _mark(client, seed_accounts, line, True)
    assert r.status_code == 403, r.text
    _login_as(client, "admin", "test-password-123")
    assert _register(client, seed_accounts)[books["check"]["id"]]["cleared"] is False


def test_the_page_offers_the_control_to_writers_only():
    js = (ROOT / "app" / "static" / "js" / "banking.js").read_text(encoding="utf-8")
    cell = js[js.index("    _clearedCell(e, accountId) {") :]
    cell = cell[: cell.index("    _clearedName(e) {")]
    # a reconciled line is no control; a locked sign-in or a void gets text
    assert "if (e.reconciliation_id)" in cell
    assert "App.isLocked()" in cell and "e.voided" in cell
    assert "data-write" in cell
    app_js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "'BankingPage.toggleRegCleared'" in app_js

"""Reclassify (#337): the lines on an account, each saying whether it can
move and why not; a dry run that changes nothing; the move itself, as one
audited change naming every line, the bill's own line following; the
closing date respected; the accountant role allowed, read-only not."""

from urllib.parse import quote

from app.models.bills import BillLine
from app.models.transactions import TransactionLine
from app.models.users import ROLE_ACCOUNTANT, ROLE_READONLY, User
from app.services import auth as auth_service

CPA_PW = "ledger-and-lamplight"
OVERRIDE_PW = "period-is-closed"


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _journal(client, accounts, debit_no, credit_no, amount, text, day="2026-03-10"):
    if {debit_no, credit_no} & {"1100", "2000"}:
        # the journal form refuses a line on A/R or A/P since #897; an entry
        # saved before then (or imported) still has one, written as it was
        from datetime import date
        from decimal import Decimal

        from sqlalchemy.orm import object_session

        from app.services.accounting import create_journal_entry

        db = object_session(accounts[debit_no])
        txn = create_journal_entry(
            db,
            date.fromisoformat(day),
            text,
            [
                {
                    "account_id": accounts[debit_no].id,
                    "debit": Decimal(amount),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": accounts[credit_no].id,
                    "debit": Decimal("0"),
                    "credit": Decimal(amount),
                },
            ],
            source_type="manual",
        )
        db.commit()
        return {"id": txn.id}
    return _ok(
        client.post(
            "/api/journal",
            json={
                "date": day,
                "description": text,
                "lines": [
                    {
                        "account_id": accounts[debit_no].id,
                        "debit": amount,
                        "credit": "0",
                    },
                    {
                        "account_id": accounts[credit_no].id,
                        "debit": "0",
                        "credit": amount,
                    },
                ],
            },
        )
    )


def _bank_entry(client, accounts, category_no, amount, text, day="2026-03-12"):
    return _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": accounts["1000"].id,
                "date": day,
                "amount": amount,
                "category_account_id": accounts[category_no].id,
                "payee": "Corner Hardware",
                "description": text,
            },
        )
    )


def _bill(
    client, accounts, account_no, amount, number="B-1", day="2026-03-14", vendor_id=None
):
    if vendor_id is None:
        vendor_id = _ok(client.post("/api/vendors", json={"name": f"Vendor {number}"}))[
            "id"
        ]
    return _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": vendor_id,
                "bill_number": number,
                "date": day,
                "lines": [
                    {
                        "description": "Parked",
                        "quantity": 1,
                        "rate": amount,
                        "account_id": accounts[account_no].id,
                    }
                ],
            },
        )
    )


def _lines(client, accounts, account_no, start="2026-01-01", end="2026-12-31"):
    return _ok(
        client.get(
            f"/api/reclassify/lines?account_id={accounts[account_no].id}"
            f"&start_date={start}&end_date={end}"
        )
    )


def _line_ids(client, db, account_no, accounts):
    return [
        ln.id
        for ln in db.query(TransactionLine)
        .filter(TransactionLine.account_id == accounts[account_no].id)
        .order_by(TransactionLine.id)
        .all()
    ]


def test_the_list_says_which_lines_can_move_and_why_not(
    client, db_session, seed_accounts, seed_customer
):
    accounts = seed_accounts
    # parked in Other Expenses (6900): a journal, a bank entry, a bill line
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    _bank_entry(client, accounts, "6900", "-75.00", "Card swipe, unknown")
    _bill(client, accounts, "6900", 120)
    # an invoice posts to income through its item: that line stays put
    _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": seed_customer.id,
                "date": "2026-03-15",
                "lines": [{"description": "Service", "quantity": 1, "rate": 100}],
            },
        )
    )
    parked = _lines(client, accounts, "6900")
    assert parked["count"] == 3 and parked["movable"] == 3
    assert parked["movable_total"] == "235.00"
    labels = sorted(ln["source_label"] for ln in parked["lines"])
    assert labels == ["Bank entry", "Bill", "Journal entry"]
    bill_line = next(ln for ln in parked["lines"] if ln["source_label"] == "Bill")
    assert bill_line["document"] == "Bill B-1"

    # the bank side of the register entry stays, and says so
    bank = _lines(client, accounts, "1000")
    assert bank["count"] == 1 and bank["movable"] == 0
    assert "bank side" in bank["lines"][0]["reason"]
    # the invoice's income line comes from the document
    income = _lines(client, accounts, "4000")
    assert income["movable"] == 0
    assert "comes from the document" in income["lines"][0]["reason"]
    # Accounts Payable is the documents' side
    ap = _lines(client, accounts, "2000")
    assert ap["movable"] == 0
    assert all("Accounts Payable" in ln["reason"] for ln in ap["lines"])


def test_a_dry_run_plans_and_changes_nothing(client, db_session, seed_accounts):
    accounts = seed_accounts
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    ids = _line_ids(client, db_session, "6900", accounts)
    plan = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "account_id": accounts["6300"].id},
        )
    )
    assert plan["dry_run"] is True and plan["count"] == 1
    assert plan["moved"][0]["from_account"]["name"] == accounts["6900"].name
    assert plan["moved"][0]["to_account"]["name"] == accounts["6300"].name
    db_session.expire_all()
    assert _line_ids(client, db_session, "6300", accounts) == []
    assert _line_ids(client, db_session, "6900", accounts) == ids


def test_the_move_is_one_audited_change_and_the_bill_follows(
    client, db_session, seed_accounts
):
    accounts = seed_accounts
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    _bank_entry(client, accounts, "6900", "-75.00", "Card swipe, unknown")
    bill = _bill(client, accounts, "6900", 120)
    klass = _ok(client.post("/api/classes", json={"name": "Shop"}))
    ids = _line_ids(client, db_session, "6900", accounts)
    assert len(ids) == 3

    done = _ok(
        client.post(
            "/api/reclassify",
            json={
                "line_ids": ids,
                "account_id": accounts["6300"].id,
                "class_id": klass["id"],
                "dry_run": False,
            },
        )
    )
    assert done["dry_run"] is False and done["count"] == 3 and done["skipped"] == []
    assert {m["line_id"] for m in done["moved"]} == set(ids)
    assert all(m["to_class"]["name"] == "Shop" for m in done["moved"])
    followed = [m for m in done["moved"] if m["document"] == "Bill B-1"]
    assert followed and followed[0]["document_line_follows"] is True

    db_session.expire_all()
    assert _line_ids(client, db_session, "6900", accounts) == []
    moved = db_session.query(TransactionLine).filter(TransactionLine.id.in_(ids)).all()
    assert all(ln.account_id == accounts["6300"].id for ln in moved)
    assert all(ln.class_id == klass["id"] for ln in moved)
    # the bill's own line agrees with its posting
    bl = db_session.query(BillLine).filter(BillLine.bill_id == bill["id"]).one()
    assert bl.account_id == accounts["6300"].id and bl.class_id == klass["id"]
    # the bill's page shows the new account too
    shown = _ok(client.get(f"/api/bills/{bill['id']}"))
    assert shown["lines"][0]["account_id"] == accounts["6300"].id

    # one event for the move, every line named, beside each line's own
    events = _ok(client.get("/api/audit?table_name=reclassify"))
    rows = events["entries"] if isinstance(events, dict) else events
    assert len(rows) == 1
    event = rows[0]
    assert event["source"] == "reclassify"
    assert sorted(event["new_values"]["lines"]) == sorted(ids)
    assert event["new_values"]["account"]["name"] == accounts["6300"].name
    per_line = _ok(client.get("/api/audit?table_name=transaction_lines"))
    per_line = per_line["entries"] if isinstance(per_line, dict) else per_line
    changed = [
        e
        for e in per_line
        if e["action"] == "UPDATE" and "account_id" in (e["changed_fields"] or [])
    ]
    assert {e["record_id"] for e in changed} >= set(ids)

    # the moved lines now list on the account they went to, with the class
    there = _lines(client, accounts, "6300")
    assert there["count"] == 3
    assert all(ln["class_name"] == "Shop" for ln in there["lines"])
    # the list on the old account is empty
    assert _lines(client, accounts, "6900")["count"] == 0


def test_a_line_already_there_and_a_bad_target_are_refused(
    client, db_session, seed_accounts
):
    accounts = seed_accounts
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    ids = _line_ids(client, db_session, "6900", accounts)
    # the same account: nothing to do
    r = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "account_id": accounts["6900"].id, "dry_run": False},
        )
    )
    assert r["count"] == 0 and r["skipped"][0]["reason"] == "already there"
    # a bank account is not a target
    r = client.post(
        "/api/reclassify",
        json={"line_ids": ids, "account_id": accounts["1000"].id, "dry_run": False},
    )
    assert r.status_code == 400 and "register" in r.json()["detail"]
    # nor Accounts Payable
    r = client.post(
        "/api/reclassify",
        json={"line_ids": ids, "account_id": accounts["2000"].id, "dry_run": False},
    )
    assert r.status_code == 400 and "Accounts Payable" in r.json()["detail"]
    # neither an account nor a class: nothing asked
    r = client.post("/api/reclassify", json={"line_ids": ids, "dry_run": False})
    assert r.status_code == 400
    # the bank side cannot be sent
    bank_ids = _line_ids(client, db_session, "2000", accounts)
    r = _ok(
        client.post(
            "/api/reclassify",
            json={
                "line_ids": bank_ids,
                "account_id": accounts["6300"].id,
                "dry_run": False,
            },
        )
    )
    assert r["count"] == 0 and "Accounts Payable" in r["skipped"][0]["reason"]


def test_a_voided_entrys_lines_stay(client, db_session, seed_accounts):
    accounts = seed_accounts
    entry = _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    _ok(client.post(f"/api/journal/{entry['id']}/void"))
    listed = _lines(client, accounts, "6900")
    assert listed["count"] == 2 and listed["movable"] == 0
    assert all(ln["reason"] == "voided" for ln in listed["lines"])


def test_the_closing_date_holds_and_the_password_opens_it(
    client, db_session, seed_accounts
):
    accounts = seed_accounts
    _journal(client, accounts, "6900", "2000", "40.00", "Old charge", day="2026-01-20")
    _ok(
        client.put(
            "/api/settings",
            json={"closing_date": "2026-02-28", "closing_date_password": OVERRIDE_PW},
        )
    )
    ids = _line_ids(client, db_session, "6900", accounts)
    body = {"line_ids": ids, "account_id": accounts["6300"].id, "dry_run": False}
    r = client.post("/api/reclassify", json=body)
    assert r.status_code == 403, r.text
    db_session.expire_all()
    assert _line_ids(client, db_session, "6900", accounts) == ids
    r = client.post(
        "/api/reclassify",
        json=body,
        headers={"X-Closing-Date-Password": quote(OVERRIDE_PW)},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _line_ids(client, db_session, "6300", accounts) == ids
    # the Closing Date Exceptions report lists the lines the move changed
    report = _ok(
        client.get(
            "/api/reports/closing-date-exceptions?start_date=2026-01-01&end_date=2026-12-31"
        )
    )
    assert report["count"] == 1
    records = [c["record"] for c in report["events"][0]["changes"]]
    assert "journal line" in records


def _mk_user(db, username, password, role):
    u = User(
        username=username,
        display_name=username.title(),
        password_hash=auth_service.hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _login_as(client, username, password):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text


def test_the_accountant_may_move_lines_and_read_only_may_not(
    client, db_session, seed_accounts
):
    accounts = seed_accounts
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    ids = _line_ids(client, db_session, "6900", accounts)
    _mk_user(db_session, "cpa", CPA_PW, ROLE_ACCOUNTANT)
    _mk_user(db_session, "viewer", CPA_PW, ROLE_READONLY)

    _login_as(client, "viewer", CPA_PW)
    assert _lines(client, accounts, "6900")["count"] == 1  # the list is a read
    r = client.post(
        "/api/reclassify",
        json={"line_ids": ids, "account_id": accounts["6300"].id, "dry_run": False},
    )
    assert r.status_code == 403

    _login_as(client, "cpa", CPA_PW)
    r = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "account_id": accounts["6300"].id, "dry_run": False},
        )
    )
    assert r["count"] == 1
    db_session.expire_all()
    assert _line_ids(client, db_session, "6300", accounts) == ids
    # the move is in the accountant's name
    events = _ok(client.get("/api/audit?table_name=reclassify"))
    rows = events["entries"] if isinstance(events, dict) else events
    assert rows[0]["username"] == "cpa"


def test_one_payees_lines_are_listed_and_moved_alone(client, db_session, seed_accounts):
    """#394: a vendor's lines picked out of everything else parked on the
    account, by vendor or by the payee's name, and moved on their own."""
    accounts = seed_accounts
    acme_vendor = _ok(client.post("/api/vendors", json={"name": "Acme Supply"}))["id"]
    acme = _bill(client, accounts, "6900", 120, number="ACME-1", vendor_id=acme_vendor)
    _bill(client, accounts, "6900", 80, number="ACME-2", vendor_id=acme_vendor)
    _bill(client, accounts, "6900", 55, number="OTHER-1")
    _bank_entry(client, accounts, "6900", "-75.00", "Card swipe, unknown")
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")

    # every row names its payee as its document records it
    parked = _lines(client, accounts, "6900")
    assert parked["count"] == 5 and parked["vendor"] is None
    by_doc = {ln["document"] or ln["source_label"]: ln for ln in parked["lines"]}
    assert by_doc["Bill ACME-1"]["payee"] == "Acme Supply"
    assert by_doc["Bill OTHER-1"]["payee"] == "Vendor OTHER-1"
    assert by_doc["Bill ACME-1"]["vendor_id"] == acme_vendor
    assert by_doc["Bank entry"]["payee"] == "Corner Hardware"
    assert by_doc["Bank entry"]["vendor_id"] is None
    assert by_doc["Journal entry"]["payee"] == ""

    # the vendor filter: that vendor's lines, nobody else's
    mine = _ok(
        client.get(
            f"/api/reclassify/lines?account_id={accounts['6900'].id}"
            f"&start_date=2026-01-01&end_date=2026-12-31&vendor_id={acme_vendor}"
        )
    )
    assert mine["vendor"] == {"id": acme_vendor, "name": "Acme Supply"}
    assert sorted(ln["document"] for ln in mine["lines"]) == [
        "Bill ACME-1",
        "Bill ACME-2",
    ]
    assert mine["count"] == 2 and mine["movable_total"] == "200.00"
    # ...and a payee name, case aside, finds the typed payee on a register entry
    corner = _ok(
        client.get(
            f"/api/reclassify/lines?account_id={accounts['6900'].id}"
            f"&start_date=2026-01-01&end_date=2026-12-31&payee=corner%20hard"
        )
    )
    assert [ln["source_label"] for ln in corner["lines"]] == ["Bank entry"]
    assert corner["payee"] == "corner hard"
    # a vendor nobody has is refused, not an empty list
    assert (
        client.get(
            f"/api/reclassify/lines?account_id={accounts['6900'].id}"
            f"&start_date=2026-01-01&end_date=2026-12-31&vendor_id=99999"
        ).status_code
        == 404
    )

    # moving the listed lines moves only them
    moved = _ok(
        client.post(
            "/api/reclassify",
            json={
                "line_ids": [ln["id"] for ln in mine["lines"]],
                "account_id": accounts["6400"].id,
                "dry_run": False,
            },
        )
    )
    assert moved["count"] == 2 and moved["skipped"] == []
    left = _lines(client, accounts, "6900")
    assert left["count"] == 3
    assert {ln["document"] or ln["source_label"] for ln in left["lines"]} == {
        "Bill OTHER-1",
        "Bank entry",
        "Journal entry",
    }
    gone = _lines(client, accounts, "6400")
    assert gone["count"] == 2 and {ln["payee"] for ln in gone["lines"]} == {
        "Acme Supply"
    }
    bill_line = db_session.query(BillLine).filter(BillLine.bill_id == acme["id"]).one()
    assert bill_line.account_id == accounts["6400"].id


def _bank_entry_payee(client, accounts, category_no, amount, payee, day="2026-03-12"):
    """A register entry whose payee is `payee` (the register stores it as the
    transaction's description); used to stand in for the varied spellings a
    vendor's name takes on real lines."""
    return _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": accounts["1000"].id,
                "date": day,
                "amount": amount,
                "category_account_id": accounts[category_no].id,
                "payee": payee,
                "description": "memo",
            },
        )
    )


def test_a_vendors_lines_come_along_in_every_spelling(
    client, db_session, seed_accounts
):
    """#394 review: the filter matched the exact name only, so bank-feed and
    check spellings of a vendor stayed behind. A contains match on the
    resolved payee brings them along. Six Home Depot lines on 6900 — three
    off-spelling, three exact — all six list under vendor "Home Depot"."""
    accounts = seed_accounts
    hd = _ok(client.post("/api/vendors", json={"name": "Home Depot"}))["id"]
    # the three spellings the review names: a bank-feed payee, an IIF check
    # memo, a QB report check — none equal to "Home Depot"
    _bank_entry_payee(client, accounts, "6900", "-11.00", "HOME DEPOT #4521 JOLIET IL")
    _bank_entry_payee(client, accounts, "6900", "-12.00", "Home Depot — lumber")
    _bank_entry_payee(client, accounts, "6900", "-13.00", "Check 1042 - Home Depot")
    # and three that match exactly
    _bank_entry_payee(client, accounts, "6900", "-14.00", "Home Depot")
    _bank_entry_payee(client, accounts, "6900", "-15.00", "Home Depot")
    _bank_entry_payee(client, accounts, "6900", "-16.00", "Home Depot")

    found = _ok(
        client.get(
            f"/api/reclassify/lines?account_id={accounts['6900'].id}"
            f"&start_date=2026-01-01&end_date=2026-12-31&vendor_id={hd}"
        )
    )
    assert found["count"] == 6  # exact-name matching would list only the 3
    payees = sorted(ln["payee"] for ln in found["lines"])
    assert payees == [
        "Check 1042 - Home Depot",
        "HOME DEPOT #4521 JOLIET IL",
        "Home Depot",
        "Home Depot",
        "Home Depot",
        "Home Depot — lumber",
    ]


def test_an_iif_card_charge_reads_the_payee_the_register_does(
    client, db_session, seed_accounts
):
    """#394 review: an IIF card charge has no "CC Charge: " prefix, so the
    Payee column went blank where the register showed the name. Reusing
    payees_for keeps the two the same."""
    from datetime import date

    from app.models.transactions import Transaction, TransactionLine
    from app.services.bank_register import payees_for

    accounts = seed_accounts
    # a card charge imported without the API's "CC Charge: " prefix
    txn = Transaction(
        date=date(2026, 3, 12),
        description="Home Depot — lumber",
        source_type="cc_charge",
    )
    txn.lines = [
        TransactionLine(account_id=accounts["6900"].id, debit=20, credit=0),
        TransactionLine(account_id=accounts["2000"].id, debit=0, credit=20),
    ]
    db_session.add(txn)
    db_session.commit()

    listed = _lines(client, accounts, "6900")
    row = next(ln for ln in listed["lines"] if ln["source_label"] == "Card charge")
    assert row["payee"] == "Home Depot — lumber"  # not "" as before
    # exactly what the register reads for the same transaction
    assert payees_for(db_session, [txn])[txn.id] == row["payee"]


def test_the_report_center_offers_the_tool_and_the_page_keeps_it_open_to_the_accountant():
    from pathlib import Path

    src = Path("app/static/js/reports.js").read_text(encoding="utf-8")
    assert "card('reclassify'" in src
    assert "'reclassify':" in src and "ReportsPage.reclassify(p)" in src
    # the move controls stay usable for the accountant and hidden from
    # read-only: the container carries data-accountant-ok, the buttons data-write
    assert 'id="rc-move" data-accountant-ok' in src
    assert 'data-write onclick="ReportsPage.rcPreview()"' in src
    assert 'data-write onclick="ReportsPage.rcMove()"' in src
    # the payee filter (#394) beside the account, riding on the address,
    # and a Payee column on the rows
    assert 'id="rc-vendor"' in src and "All payees" in src
    assert "ReportsPage.setParam('vendor_id', this.value)" in src
    assert "params.vendor_id = p.vendor_id" in src
    assert "&vendor_id=${vendorId}" in src
    assert '<th scope="col">Payee</th>' in src
    # the free-text Payee box drives the same contains filter, also riding
    # on the address
    assert 'id="rc-payee"' in src
    assert "ReportsPage.setParam('payee', this.value.trim())" in src
    assert "params.payee = p.payee" in src
    assert "&payee=${encodeURIComponent(payeeText)}" in src


def test_a_vendor_name_inside_another_word_does_not_match(
    client, db_session, seed_accounts
):
    """The spelling match is whole words: vendor "Ace" must not take the
    lines of "Space Coast Supply" or "Acer" into a move."""
    accounts = seed_accounts
    ace = _ok(client.post("/api/vendors", json={"name": "Ace"}))["id"]
    _bank_entry_payee(client, accounts, "6900", "-21.00", "ACE HARDWARE #12")
    _bank_entry_payee(client, accounts, "6900", "-22.00", "Space Coast Supply")
    _bank_entry_payee(client, accounts, "6900", "-23.00", "Acer Store")
    found = _ok(
        client.get(
            f"/api/reclassify/lines?account_id={accounts['6900'].id}"
            f"&start_date=2026-01-01&end_date=2026-12-31&vendor_id={ace}"
        )
    )
    assert [ln["payee"] for ln in found["lines"]] == ["ACE HARDWARE #12"]


# ---------------------------------------------------------------------------
# A job moves too (#674): the dry run says it, the move sets the line's job
# (and the bill line's), the job reports follow, the job comes off, and the
# guards the account and class moves have hold for a job move.
# ---------------------------------------------------------------------------


def _job(client, customer_id, name):
    return _ok(
        client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    )


def _job_figures(client):
    rows = _ok(client.get("/api/jobs/profitability"))
    return {r["job_id"]: r for r in rows}


def test_a_dry_run_lists_the_job_move_and_changes_nothing(
    client, db_session, seed_accounts, seed_customer
):
    accounts = seed_accounts
    kitchen = _job(client, seed_customer.id, "Kitchen")
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    ids = _line_ids(client, db_session, "6900", accounts)
    plan = _ok(
        client.post("/api/reclassify", json={"line_ids": ids, "job_id": kitchen["id"]})
    )
    assert plan["dry_run"] is True and plan["count"] == 1
    assert plan["to_job"]["id"] == kitchen["id"]
    m = plan["moved"][0]
    assert m["from_job"] is None
    assert m["to_job"]["name"].endswith("Kitchen")
    # the account stays where it was
    assert m["from_account"]["id"] == m["to_account"]["id"] == accounts["6900"].id
    db_session.expire_all()
    line = db_session.get(TransactionLine, ids[0])
    assert line.job_id is None


def test_the_job_move_changes_the_line_the_bill_and_the_job_reports(
    client, db_session, seed_accounts, seed_customer
):
    accounts = seed_accounts
    kitchen = _job(client, seed_customer.id, "Kitchen")
    bath = _job(client, seed_customer.id, "Bath")
    _journal(client, accounts, "6900", "2000", "40.00", "Unknown charge")
    bill = _bill(client, accounts, "6900", 120)
    ids = _line_ids(client, db_session, "6900", accounts)
    assert len(ids) == 2
    done = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "job_id": kitchen["id"], "dry_run": False},
        )
    )
    assert done["count"] == 2 and done["skipped"] == []
    db_session.expire_all()
    assert {
        ln.job_id
        for ln in db_session.query(TransactionLine).filter(TransactionLine.id.in_(ids))
    } == {kitchen["id"]}
    bl = db_session.query(BillLine).filter(BillLine.bill_id == bill["id"]).one()
    assert bl.job_id == kitchen["id"]
    figures = _job_figures(client)
    assert figures[kitchen["id"]]["expenses"] == 160.0
    # the list shows the job on each line
    listed = _lines(client, accounts, "6900")
    assert {ln["job_id"] for ln in listed["lines"]} == {kitchen["id"]}

    # on to another job: the first job's costs go with it
    _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "job_id": bath["id"], "dry_run": False},
        )
    )
    figures = _job_figures(client)
    assert figures[bath["id"]]["expenses"] == 160.0
    assert kitchen["id"] not in figures or figures[kitchen["id"]]["expenses"] == 0

    # and off its job: the No job bucket takes it back
    done = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "clear_job": True, "dry_run": False},
        )
    )
    assert done["count"] == 2 and done["clear_job"] is True
    assert all(m["to_job"] is None for m in done["moved"])
    db_session.expire_all()
    assert all(
        ln.job_id is None
        for ln in db_session.query(TransactionLine).filter(TransactionLine.id.in_(ids))
    )
    bl = db_session.query(BillLine).filter(BillLine.bill_id == bill["id"]).one()
    assert bl.job_id is None
    figures = _job_figures(client)
    assert bath["id"] not in figures or figures[bath["id"]]["expenses"] == 0
    # nothing left to clear: already there
    again = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": ids, "clear_job": True, "dry_run": False},
        )
    )
    assert again["count"] == 0
    assert {s["reason"] for s in again["skipped"]} == {"already there"}
    # the audit event names the job
    events = _ok(client.get("/api/audit?table_name=reclassify"))
    rows = events["entries"] if isinstance(events, dict) else events
    assert any(e["new_values"].get("job") == "cleared" for e in rows)


def test_the_job_move_guards_hold(client, db_session, seed_accounts, seed_customer):
    accounts = seed_accounts
    kitchen = _job(client, seed_customer.id, "Kitchen")
    _journal(client, accounts, "6900", "2000", "40.00", "Old charge", day="2026-01-20")
    ids = _line_ids(client, db_session, "6900", accounts)
    # a job and no job together, a missing job, an inactive job
    r = client.post(
        "/api/reclassify",
        json={"line_ids": ids, "job_id": kitchen["id"], "clear_job": True},
    )
    assert r.status_code == 400 and "not both" in r.json()["detail"]
    r = client.post("/api/reclassify", json={"line_ids": ids, "job_id": 999999})
    assert r.status_code == 404
    closed = _job(client, seed_customer.id, "Old porch")
    _ok(client.put(f"/api/jobs/{closed['id']}", json={"is_active": False}))
    r = client.post("/api/reclassify", json={"line_ids": ids, "job_id": closed["id"]})
    assert r.status_code == 400 and "inactive" in r.json()["detail"]

    # the closing date holds a job move as it does an account move
    _ok(
        client.put(
            "/api/settings",
            json={"closing_date": "2026-02-28", "closing_date_password": OVERRIDE_PW},
        )
    )
    body = {"line_ids": ids, "job_id": kitchen["id"], "dry_run": False}
    r = client.post("/api/reclassify", json=body)
    assert r.status_code == 403, r.text
    db_session.expire_all()
    assert db_session.get(TransactionLine, ids[0]).job_id is None
    r = client.post(
        "/api/reclassify",
        json=body,
        headers={"X-Closing-Date-Password": quote(OVERRIDE_PW)},
    )
    assert r.status_code == 200, r.text

    # a voided entry's lines stay
    entry = _journal(
        client, accounts, "6900", "2000", "15.00", "Later", day="2026-04-02"
    )
    _ok(client.post(f"/api/journal/{entry['id']}/void"))
    later = [
        ln["id"] for ln in _lines(client, accounts, "6900", start="2026-04-01")["lines"]
    ]
    r = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": later, "job_id": kitchen["id"], "dry_run": False},
        )
    )
    assert r["count"] == 0 and {s["reason"] for s in r["skipped"]} == {"voided"}


def test_a_line_whose_entry_is_on_the_job_is_not_taken_off_it(
    client, db_session, seed_accounts, seed_customer
):
    accounts = seed_accounts
    kitchen = _job(client, seed_customer.id, "Kitchen")
    bath = _job(client, seed_customer.id, "Bath")
    entry = _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-03-10",
                "description": "Tile",
                "job_id": kitchen["id"],
                "lines": [
                    {"account_id": accounts["6900"].id, "debit": "50", "credit": "0"},
                    {"account_id": accounts["2500"].id, "debit": "0", "credit": "50"},
                ],
            },
        )
    )
    line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == entry["id"],
            TransactionLine.account_id == accounts["6900"].id,
        )
        .one()
    )
    # taking it off would leave it on the entry's job: refused, with a sentence
    r = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": [line.id], "clear_job": True, "dry_run": False},
        )
    )
    assert r["count"] == 0
    assert "take the job off the entry" in r["skipped"][0]["reason"]
    # onto another job is the line's own: the reports follow it
    _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": [line.id], "job_id": bath["id"], "dry_run": False},
        )
    )
    assert _job_figures(client)[bath["id"]]["expenses"] == 50.0


def test_a_job_cost_entrys_lines_do_not_move(client, db_session, seed_accounts):
    """A job cost entry posts its own lines from the stored entry; moving one
    line would leave the stored entry on the old job, so they stay."""
    from datetime import date

    from app.services.accounting import create_journal_entry

    accounts = seed_accounts
    create_journal_entry(
        db_session,
        date(2026, 3, 10),
        "Job cost JC-1",
        [
            {"account_id": accounts["6900"].id, "debit": 30, "credit": 0},
            {"account_id": accounts["6300"].id, "debit": 0, "credit": 30},
        ],
        source_type="job_cost",
        source_id=1,
    )
    db_session.commit()
    listed = _lines(client, accounts, "6900")
    assert listed["count"] == 1 and listed["movable"] == 0
    assert "edit the document" in listed["lines"][0]["reason"]


def test_the_page_offers_a_job_beside_the_class():
    from pathlib import Path

    src = Path("app/static/js/reports.js").read_text(encoding="utf-8")
    assert "API.get('/jobs')" in src
    assert 'id="rc-to-job"' in src
    assert "body.clear_job = true" in src and "body.job_id = parseInt(toJob, 10)" in src
    # shown once the company has jobs
    assert "const hasJobs = rc.lists.jobs.length > 0;" in src
    assert '${hasJobs ? `<div class="form-group"' in src


def test_a_line_whose_entry_is_on_the_class_is_not_taken_off_it(
    client, db_session, seed_accounts
):
    # the same shape as the job: clearing the line's own class would leave
    # it reporting under the entry's, so it is refused with the sentence
    accounts = seed_accounts
    cls = _ok(client.post("/api/classes", json={"name": "North Crew"}))
    entry = _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-03-10",
                "description": "Tile",
                "class_id": cls["id"],
                "lines": [
                    {"account_id": accounts["6900"].id, "debit": "50", "credit": "0"},
                    {"account_id": accounts["2500"].id, "debit": "0", "credit": "50"},
                ],
            },
        )
    )
    line = (
        db_session.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == entry["id"],
            TransactionLine.account_id == accounts["6900"].id,
        )
        .one()
    )
    r = _ok(
        client.post(
            "/api/reclassify",
            json={"line_ids": [line.id], "clear_class": True, "dry_run": False},
        )
    )
    assert r["moved"] == []
    assert r["skipped"][0]["line_id"] == line.id
    assert "North Crew" in r["skipped"][0]["reason"]
    assert "take the class off the entry" in r["skipped"][0]["reason"]

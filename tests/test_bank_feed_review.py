"""Statement lines are a review queue: matched to the posting the ledger
already has, added as a new posting, or excluded (issue #114)."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.bank_rules import BankRule
from app.models.banking import BankAccount, BankTransaction
from app.models.transactions import Transaction, TransactionLine
from app.services.ofx_import import import_transactions


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def vendor(client):
    return client.post("/api/vendors", json={"name": "City Water"}).json()


def _expense(
    client, seed_accounts, vendor, amount, day, paid_from="1000", reference=None
):
    r = client.post(
        "/api/expenses",
        json={
            "date": f"2026-09-{day:02d}",
            "vendor_id": vendor["id"],
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts[paid_from].id,
            "amount": amount,
            "reference": reference or "",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _lines(*rows):
    return [
        {
            "fitid": f"F{i}",
            "date": date(2026, 9, d),
            "amount": Decimal(a),
            "payee": p,
            "memo": "",
        }
        for i, (d, a, p) in enumerate(rows)
    ]


def _bank_line(db, txn_id, account_id):
    return (
        db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == txn_id,
            TransactionLine.account_id == account_id,
        )
        .one()
    )


def test_import_auto_matches_a_unique_amount_within_five_days_and_clears_it(
    client, db_session, seed_accounts, feed, vendor
):
    e = _expense(client, seed_accounts, vendor, "125.00", 2)
    out = import_transactions(
        db_session, feed["id"], _lines((5, "-125.00", "CITY WATER"))
    )
    assert out["imported"] == 1 and out["matched"] == 1
    [bt] = db_session.query(BankTransaction).all()
    assert bt.match_status == "auto" and bt.transaction_id == e["id"]
    assert _bank_line(db_session, e["id"], seed_accounts["1000"].id).cleared is True
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert reg["entries"][0]["cleared"] is True
    rows = client.get(f"/api/banking/transactions?bank_account_id={feed['id']}").json()
    assert rows[0]["match_status"] == "auto" and rows[0]["transaction_line_id"]


def test_two_identical_lines_take_two_identical_entries_once_each(
    client, db_session, seed_accounts, feed, vendor
):
    a = _expense(client, seed_accounts, vendor, "20", 3)
    b = _expense(client, seed_accounts, vendor, "20", 4)
    out = import_transactions(
        db_session, feed["id"], _lines((3, "-20", "x"), (4, "-20", "y"))
    )
    assert out["matched"] == 2
    linked = {bt.transaction_id for bt in db_session.query(BankTransaction).all()}
    assert linked == {a["id"], b["id"]}


def test_ambiguity_stays_unmatched_and_a_check_number_breaks_the_tie(
    client, db_session, seed_accounts, feed, vendor
):
    a = _expense(client, seed_accounts, vendor, "50", 10, reference="1041")
    b = _expense(client, seed_accounts, vendor, "50", 10, reference="1042")
    out = import_transactions(db_session, feed["id"], _lines((10, "-50", "check")))
    assert out["matched"] == 0
    bt = db_session.query(BankTransaction).one()
    assert bt.match_status == "unmatched"
    bt.check_number = "1042"
    db_session.commit()
    r = client.post(f"/api/banking/accounts/{feed['id']}/feed/auto-match")
    assert r.json()["matched"] == 1
    db_session.refresh(bt)
    assert bt.transaction_id == b["id"] and bt.transaction_id != a["id"]


def test_opposite_sign_and_voided_postings_never_match(
    client, db_session, seed_accounts, feed, vendor
):
    e = _expense(client, seed_accounts, vendor, "75", 6)
    client.post(f"/api/expenses/{e['id']}/void")
    out = import_transactions(
        db_session, feed["id"], _lines((6, "-75", "v"), (6, "75", "w"))
    )
    assert out["matched"] == 0
    assert (
        client.get(
            f"/api/banking/transactions/{db_session.query(BankTransaction).first().id}/candidates"
        ).json()
        == []
    )


def test_manual_match_unmatch_and_the_guards(
    client, db_session, seed_accounts, feed, vendor
):
    e = _expense(client, seed_accounts, vendor, "33", 1)
    other = _expense(client, seed_accounts, vendor, "44", 1)
    import_transactions(
        db_session, feed["id"], _lines((20, "-33", "late"))
    )  # 19 days off: not auto
    bt = db_session.query(BankTransaction).one()
    assert bt.match_status == "unmatched"
    cands = client.get(f"/api/banking/transactions/{bt.id}/candidates").json()
    assert [c["transaction_id"] for c in cands] == [e["id"]] and cands[0][
        "payee"
    ] == "City Water"
    wrong = _bank_line(db_session, other["id"], seed_accounts["1000"].id).id
    assert (
        client.post(
            f"/api/banking/transactions/{bt.id}/match", json={"line_id": wrong}
        ).status_code
        == 400
    )
    ok = _bank_line(db_session, e["id"], seed_accounts["1000"].id).id
    r = client.post(f"/api/banking/transactions/{bt.id}/match", json={"line_id": ok})
    assert r.status_code == 200 and r.json()["match_status"] == "manual", r.text
    assert (
        client.post(
            f"/api/banking/transactions/{bt.id}/match", json={"line_id": ok}
        ).status_code
        == 400
    )
    r = client.post(f"/api/banking/transactions/{bt.id}/unmatch")
    assert r.status_code == 200 and r.json()["match_status"] == "unmatched"
    db_session.expire_all()
    assert _bank_line(db_session, e["id"], seed_accounts["1000"].id).cleared is False


def test_add_posts_and_links_a_withdrawal_a_card_charge_and_a_card_payment(
    client, db_session, seed_accounts, feed
):
    card = client.post(
        "/api/banking/accounts",
        json={"name": "Visa feed", "account_id": seed_accounts["2100"].id},
    ).json()
    import_transactions(db_session, feed["id"], _lines((8, "-60", "HARDWARE STORE")))
    import_transactions(
        db_session,
        card["id"],
        _lines((9, "-15", "COFFEE"), (10, "300", "PAYMENT THANK YOU")),
    )
    w, c, p = db_session.query(BankTransaction).order_by(BankTransaction.id).all()
    assert (
        client.post(f"/api/banking/transactions/{w.id}/add").status_code == 400
    )  # no category yet
    r = client.post(
        f"/api/banking/transactions/{w.id}/add",
        json={"category_account_id": seed_accounts["6000"].id},
    )
    assert r.status_code == 200 and r.json()["match_status"] == "added", r.text
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert (
        txn.source_type == "bank_entry"
        and txn.source_id == w.id
        and txn.description == "HARDWARE STORE"
    )
    assert {ln.account_id: ln.credit for ln in txn.lines if ln.credit > 0} == {
        seed_accounts["1000"].id: Decimal("60")
    }
    r = client.post(
        f"/api/banking/transactions/{c.id}/add",
        json={"category_account_id": seed_accounts["6000"].id},
    )
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert {ln.account_id: ln.credit for ln in txn.lines if ln.credit > 0} == {
        seed_accounts["2100"].id: Decimal("15")
    }
    r = client.post(
        f"/api/banking/transactions/{p.id}/add",
        json={"category_account_id": seed_accounts["1000"].id},
    )
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert txn.source_type == "transfer"
    assert {ln.account_id: ln.debit for ln in txn.lines if ln.debit > 0} == {
        seed_accounts["2100"].id: Decimal("300")
    }
    assert (
        client.post(f"/api/banking/transactions/{w.id}/unmatch").status_code == 400
    )  # void the entry instead
    # voiding the added entry sends the statement line back to the queue
    db_session.refresh(w)
    r = client.post(f"/api/banking/entries/{w.transaction_id}/void")
    assert r.status_code == 200
    db_session.refresh(w)
    assert w.match_status == "unmatched" and w.transaction_id is None
    card_reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['2100'].id}"
    ).json()
    assert card_reg["balance"] == -285.0
    bs = client.get("/api/reports/balance-sheet").json()
    assert (
        abs(bs["total_assets"] - (bs["total_liabilities"] + bs["total_equity"])) < 0.01
    )


def test_rules_categorise_add_all_posts_and_skips_closed_dates(
    client, db_session, seed_accounts, feed
):
    db_session.add(
        BankRule(
            name="water",
            pattern="city water",
            account_id=seed_accounts["6000"].id,
            rule_type="contains",
            priority=10,
            is_active=True,
        )
    )
    db_session.commit()
    import_transactions(
        db_session,
        feed["id"],
        _lines(
            (2, "-10", "CITY WATER"), (3, "-11", "CITY WATER"), (4, "-12", "unknown")
        ),
    )
    rows = client.get(
        f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
    ).json()
    cats = {r["payee"]: r["category_account_id"] for r in rows}
    assert cats["CITY WATER"] == seed_accounts["6000"].id and cats["unknown"] is None
    assert all(r["match_status"] == "unmatched" for r in rows)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-02"}).status_code
        == 200
    )
    out = client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all").json()
    assert (
        out["added"] == 1
        and len(out["skipped"]) == 1
        and "closing" in out["skipped"][0]["reason"].lower()
    )
    statuses = sorted(
        r["match_status"]
        for r in client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}"
        ).json()
    )
    assert statuses == ["added", "unmatched", "unmatched"]


def test_exclude_restore_and_unlinked_feed(client, db_session, seed_accounts):
    ba = BankAccount(name="Unlinked", bank_name="x")
    db_session.add(ba)
    db_session.commit()
    out = import_transactions(db_session, ba.id, _lines((1, "-5", "a")))
    assert out["imported"] == 1 and out["matched"] == 0
    bt = db_session.query(BankTransaction).one()
    assert (
        client.post(
            f"/api/banking/transactions/{bt.id}/add",
            json={"category_account_id": seed_accounts["6000"].id},
        ).status_code
        == 400
    )
    assert (
        client.get(f"/api/banking/transactions/{bt.id}/candidates").status_code == 400
    )
    r = client.post(f"/api/banking/transactions/{bt.id}/exclude")
    assert r.status_code == 200 and r.json()["match_status"] == "excluded"
    assert (
        client.post(f"/api/banking/transactions/{bt.id}/restore").json()["match_status"]
        == "unmatched"
    )
    assert client.post(f"/api/banking/transactions/{bt.id}/restore").status_code == 400


def _class_and_job(client):
    cls = client.post("/api/classes", json={"name": "North Crew"}).json()
    cust = client.post("/api/customers", json={"name": "Harbor Co"}).json()
    job = client.post(
        "/api/jobs", json={"customer_id": cust["id"], "name": "Dock Repair"}
    ).json()
    return cls, cust, job


def test_add_carries_the_class_job_and_vendor_the_review_row_chose(
    client, db_session, seed_accounts, feed, vendor
):
    """#671: the To review row sets the class, the job and the vendor (or
    the customer, for money in) before the line is added; every line of
    the posted entry carries the class and job, and P&L by Class sees it."""
    cls, cust, job = _class_and_job(client)
    import_transactions(
        db_session,
        feed["id"],
        _lines((8, "-60", "HARDWARE STORE"), (9, "400", "DEPOSIT")),
    )
    out, inn = db_session.query(BankTransaction).order_by(BankTransaction.id).all()
    r = client.post(
        f"/api/banking/transactions/{out.id}/add",
        json={
            "category_account_id": seed_accounts["6000"].id,
            "class_id": cls["id"],
            "job_id": job["id"],
            "vendor_id": vendor["id"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["vendor_name"] == "City Water"
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert txn.class_id == cls["id"] and txn.job_id == job["id"]
    assert txn.vendor_id == vendor["id"]
    assert {ln.class_id for ln in txn.lines} == {cls["id"]}
    assert {ln.job_id for ln in txn.lines} == {job["id"]}

    r = client.post(
        f"/api/banking/transactions/{inn.id}/add",
        json={
            "category_account_id": seed_accounts["4000"].id,
            "class_id": cls["id"],
            "customer_id": cust["id"],
        },
    )
    assert r.status_code == 200, r.text
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    income = next(ln for ln in txn.lines if ln.account_id == seed_accounts["4000"].id)
    assert income.customer_id == cust["id"] and income.class_id == cls["id"]

    g = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-09-01&end_date=2026-09-30"
    ).json()
    by_class = {c["class_id"]: c for c in g["classes"]}
    assert by_class[cls["id"]]["income"] == 400.0, g["classes"]
    assert by_class[cls["id"]]["expenses"] == 60.0


def test_add_all_keeps_each_rows_class_job_and_vendor(
    client, db_session, seed_accounts, feed, vendor
):
    """#671: Add all categorised posts each line with the picks of its own
    row; a line with none posts as before, with no class or job."""
    cls, _cust, job = _class_and_job(client)
    import_transactions(
        db_session,
        feed["id"],
        _lines((8, "-60", "HARDWARE STORE"), (9, "-20", "PAPER CO")),
    )
    a, b = db_session.query(BankTransaction).order_by(BankTransaction.id).all()
    for bt in (a, b):
        r = client.patch(
            f"/api/banking/transactions/{bt.id}",
            json={"category_account_id": seed_accounts["6000"].id},
        )
        assert r.status_code == 200, r.text
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/add-all",
        json={
            "lines": [
                {
                    "id": a.id,
                    "class_id": cls["id"],
                    "job_id": job["id"],
                    "vendor_id": vendor["id"],
                },
                {"id": b.id, "payee": "Paper Company"},
            ]
        },
    )
    assert r.status_code == 200 and r.json()["added"] == 2, r.text
    db_session.refresh(a)
    db_session.refresh(b)
    ta = db_session.query(Transaction).get(a.transaction_id)
    tb = db_session.query(Transaction).get(b.transaction_id)
    assert {ln.class_id for ln in ta.lines} == {cls["id"]}
    assert {ln.job_id for ln in ta.lines} == {job["id"]}
    assert ta.vendor_id == vendor["id"]
    assert {ln.class_id for ln in tb.lines} == {None} and tb.job_id is None
    assert tb.description == "Paper Company"

    # a customer who is not there refuses the whole call, nothing posted
    [dep] = _lines((10, "75", "DEPOSIT"))
    import_transactions(db_session, feed["id"], [{**dep, "fitid": "F-DEP"}])
    c = db_session.query(BankTransaction).filter_by(payee="DEPOSIT").one()
    client.patch(
        f"/api/banking/transactions/{c.id}",
        json={"category_account_id": seed_accounts["4000"].id},
    )
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/add-all",
        json={"lines": [{"id": c.id, "customer_id": 99999}]},
    )
    assert r.status_code == 404
    db_session.refresh(c)
    assert c.match_status == "unmatched"


def test_a_review_line_added_as_a_transfer_keeps_its_class(
    client, db_session, seed_accounts, feed
):
    """#676: a statement line categorised to another bank or card account
    posts as a transfer, and the class chosen is on both of its lines."""
    cls = client.post("/api/classes", json={"name": "North Crew"}).json()
    import_transactions(db_session, feed["id"], _lines((8, "-300", "CARD PAYMENT")))
    [bt] = db_session.query(BankTransaction).all()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={"category_account_id": seed_accounts["2100"].id, "class_id": cls["id"]},
    )
    assert r.status_code == 200, r.text
    txn = db_session.query(Transaction).get(r.json()["transaction_id"])
    assert txn.source_type == "transfer" and txn.class_id == cls["id"]
    assert [ln.class_id for ln in txn.lines] == [cls["id"], cls["id"]]


def test_add_all_refuses_a_class_or_job_that_does_not_exist_before_posting(
    client, db_session, seed_accounts, feed, vendor
):
    """2.31.4 gate, W-95: Add all posted a row's job 99999 where one Add
    answered 404, and P&L by Job then failed on the unknown job. The picks
    are checked before anything posts, like one Add's."""
    cls, _cust, _job = _class_and_job(client)
    import_transactions(
        db_session,
        feed["id"],
        _lines((8, "-60", "HARDWARE STORE"), (9, "-20", "PAPER CO")),
    )
    a, b = db_session.query(BankTransaction).order_by(BankTransaction.id).all()
    for bt in (a, b):
        r = client.patch(
            f"/api/banking/transactions/{bt.id}",
            json={"category_account_id": seed_accounts["6000"].id},
        )
        assert r.status_code == 200, r.text
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/add-all",
        json={"lines": [{"id": a.id, "job_id": 99999}, {"id": b.id}]},
    )
    assert r.status_code == 404 and "Job 99999" in r.json()["detail"]
    db_session.expire_all()
    assert all(
        bt.transaction_id is None for bt in db_session.query(BankTransaction).all()
    ), "nothing posts when a pick is refused"
    r = client.put(f"/api/classes/{cls['id']}", json={"is_archived": True})
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/add-all",
        json={"lines": [{"id": a.id, "class_id": cls["id"]}]},
    )
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    assert client.get("/api/reports/profit-loss-by-job").status_code == 200


def test_one_add_refuses_an_inactive_job(client, db_session, seed_accounts, feed):
    """2.31.4 gate, W-96: rules and Reclassify refused an inactive job; one
    Add took it. The same sentence everywhere."""
    _cls, _cust, job = _class_and_job(client)
    r = client.put(f"/api/jobs/{job['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text
    import_transactions(db_session, feed["id"], _lines((8, "-60", "HARDWARE STORE")))
    bt = db_session.query(BankTransaction).first()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={"category_account_id": seed_accounts["6000"].id, "job_id": job["id"]},
    )
    assert r.status_code == 400 and "inactive" in r.json()["detail"], r.text


def test_a_money_in_add_refuses_another_customers_job(
    client, db_session, seed_accounts, feed
):
    """2.31.4 gate, NEW-198: the review row's Job list narrows to the
    customer picked, but macOS's native menu still shows (and let through)
    another customer's job; the server refuses it now, and Add all and the
    register entry go the same way."""
    _cls, cust, job = _class_and_job(client)
    other = client.post("/api/customers", json={"name": "Someone Else"}).json()
    import_transactions(db_session, feed["id"], _lines((8, "400", "DEPOSIT")))
    bt = db_session.query(BankTransaction).first()
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "category_account_id": seed_accounts["4000"].id,
            "customer_id": other["id"],
            "job_id": job["id"],
        },
    )
    assert r.status_code == 400 and "another customer" in r.json()["detail"], r.text
    r = client.post(
        f"/api/banking/transactions/{bt.id}/add",
        json={
            "category_account_id": seed_accounts["4000"].id,
            "customer_id": cust["id"],
            "job_id": job["id"],
        },
    )
    assert r.status_code == 200, r.text

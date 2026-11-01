"""Transfers between bank and card accounts (issue #114)."""

from decimal import Decimal

from app.models.transactions import Transaction


def _transfer(client, seed_accounts, **over):
    body = {
        "date": "2026-09-05",
        "from_account_id": seed_accounts["1000"].id,
        "to_account_id": seed_accounts["2100"].id,
        "amount": "250.00",
        "memo": "September card payment",
        "reference": "web",
    }
    body.update(over)
    return client.post("/api/transfers", json=body)


def test_paying_the_card_debits_the_card_and_credits_the_bank(
    client, db_session, seed_accounts
):
    r = _transfer(client, seed_accounts)
    assert r.status_code == 201, r.text
    body = r.json()
    txn = db_session.query(Transaction).filter(Transaction.id == body["id"]).one()
    assert txn.source_type == "transfer"
    assert {ln.account_id: ln.debit for ln in txn.lines if ln.debit > 0} == {
        seed_accounts["2100"].id: Decimal("250.00")
    }
    assert {ln.account_id: ln.credit for ln in txn.lines if ln.credit > 0} == {
        seed_accounts["1000"].id: Decimal("250.00")
    }
    assert (
        body["from_account_name"] == "Checking"
        and body["to_account_name"] == "Credit Card"
    )
    assert body["memo"] == "September card payment" and body["status"] == "recorded"
    card = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['2100'].id}"
    ).json()
    assert card["balance"] == -250.0  # nothing owed, overpaid by 250
    bs = client.get("/api/reports/balance-sheet").json()
    assert (
        abs(bs["total_assets"] - (bs["total_liabilities"] + bs["total_equity"])) < 0.01
    )


def test_transfer_validation(client, seed_accounts):
    assert (
        _transfer(
            client, seed_accounts, to_account_id=seed_accounts["1000"].id
        ).status_code
        == 400
    )
    assert (
        _transfer(
            client, seed_accounts, to_account_id=seed_accounts["1100"].id
        ).status_code
        == 400
    )
    assert _transfer(client, seed_accounts, amount="0").status_code == 400
    assert _transfer(client, seed_accounts, amount="-5").status_code == 400
    assert (
        client.put("/api/settings", json={"closing_date": "2026-12-31"}).status_code
        == 200
    )
    assert _transfer(client, seed_accounts).status_code == 403


def test_list_and_void(client, seed_accounts):
    tid = _transfer(client, seed_accounts).json()["id"]
    _transfer(client, seed_accounts, amount="10", memo="")
    rows = client.get("/api/transfers").json()
    assert len(rows) == 2 and all(r["status"] == "recorded" for r in rows)
    r = client.post(f"/api/transfers/{tid}/void")
    assert r.status_code == 200 and r.json()["status"] == "void"
    assert client.post(f"/api/transfers/{tid}/void").status_code == 400
    assert {r["id"]: r["status"] for r in client.get("/api/transfers").json()}[
        tid
    ] == "void"
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert reg["balance"] == -10.0


def test_a_transfer_with_a_class_posts_both_lines_in_it(
    client, db_session, seed_accounts
):
    """#676: the Transfer dialog's class goes on the entry and on both of
    its lines, and the register's class filter finds the transfer."""
    cls = client.post("/api/classes", json={"name": "North Crew"}).json()
    r = _transfer(client, seed_accounts, class_id=cls["id"])
    assert r.status_code == 201, r.text
    txn = db_session.query(Transaction).filter(Transaction.id == r.json()["id"]).one()
    assert txn.class_id == cls["id"]
    assert [ln.class_id for ln in txn.lines] == [cls["id"], cls["id"]]
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
        f"&class_id={cls['id']}"
    ).json()
    assert [e["transaction_id"] for e in reg["entries"]] == [txn.id]
    # without one, nothing changes: no class on either line
    r = _transfer(client, seed_accounts)
    txn = db_session.query(Transaction).filter(Transaction.id == r.json()["id"]).one()
    assert txn.class_id is None and {ln.class_id for ln in txn.lines} == {None}


def test_a_register_entry_to_a_bank_account_keeps_its_class(
    client, db_session, seed_accounts
):
    """#676: + Entry with a bank or card account as the category is a
    transfer, and it used to drop the class its picker showed. Money out
    and money in both keep it on both lines."""
    cls = client.post("/api/classes", json={"name": "North Crew"}).json()
    for amount in ("-125.00", "40.00"):
        r = client.post(
            "/api/banking/transactions",
            json={
                "account_id": seed_accounts["1000"].id,
                "date": "2026-09-06",
                "amount": amount,
                "category_account_id": seed_accounts["2100"].id,
                "class_id": cls["id"],
            },
        )
        assert r.status_code == 201, r.text
        txn = (
            db_session.query(Transaction)
            .filter(Transaction.source_type == "transfer")
            .order_by(Transaction.id.desc())
            .first()
        )
        assert txn.class_id == cls["id"], amount
        assert [ln.class_id for ln in txn.lines] == [cls["id"], cls["id"]]

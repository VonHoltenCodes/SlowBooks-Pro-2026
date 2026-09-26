"""Tax Reports → Pay Sales Tax (2.17.3 exploratory test, skytech W-H1/W-M13).

The payment could never be recorded: the request model declared
``date: Optional[date]``, and inside the class body the field name shadowed
the type, so every real date was refused with "date: Input should be None".
Its "Pay From" list also offered every asset account — Accounts Receivable,
Inventory, Accumulated Depreciation, Undeposited Funds — and the server took
any of them.
"""

from decimal import Decimal
from pathlib import Path

from app.models.transactions import Transaction

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _lines(db_session, txn_id):
    txn = db_session.query(Transaction).filter(Transaction.id == txn_id).one()
    return {
        ln.account.account_number: (Decimal(ln.debit), Decimal(ln.credit))
        for ln in txn.lines
    }, txn


def test_a_sales_tax_payment_with_a_date_posts(client, db_session, seed_accounts):
    checking = seed_accounts["1000"]
    r = client.post(
        "/api/reports/sales-tax/pay",
        json={
            "date": "2026-07-15",
            "amount": 25.50,
            "pay_from_account_id": checking.id,
            "check_number": "1050",
        },
    )
    assert r.status_code == 200, r.text
    lines, txn = _lines(db_session, r.json()["transaction_id"])
    assert str(txn.date) == "2026-07-15"
    assert txn.reference == "1050"
    assert lines == {
        "2200": (Decimal("25.50"), Decimal("0")),
        "1000": (Decimal("0"), Decimal("25.50")),
    }


def test_a_card_can_pay_the_tax(client, db_session, seed_accounts):
    card = seed_accounts["2100"]
    r = client.post(
        "/api/reports/sales-tax/pay",
        json={"date": "2026-07-15", "amount": 10, "pay_from_account_id": card.id},
    )
    assert r.status_code == 200, r.text
    lines, _ = _lines(db_session, r.json()["transaction_id"])
    assert lines["2100"] == (Decimal("0"), Decimal("10.00"))


def test_only_a_bank_or_card_account_can_pay_it(client, db_session, seed_accounts):
    before = db_session.query(Transaction).count()
    for number in ("1100", "1200", "1300", "1510", "2200", "6000"):
        r = client.post(
            "/api/reports/sales-tax/pay",
            json={
                "date": "2026-07-15",
                "amount": 25,
                "pay_from_account_id": seed_accounts[number].id,
            },
        )
        assert r.status_code == 400, (number, r.text)
        detail = r.json()["detail"]
        assert seed_accounts[number].name in detail
        assert "bank or credit card account" in detail
    assert db_session.query(Transaction).count() == before


def test_the_pay_from_list_offers_only_bank_and_card_accounts():
    js = (JS / "tax.js").read_text(encoding="utf-8")
    dialog = js[js.index("async showPaySalesTax()") :]
    dialog = dialog[: dialog.index("openModal(")]
    assert "API.get('/accounts?bank=1&active_only=true')" in dialog
    assert "account_type === 'asset'" not in dialog

"""The IIF bill export carries a bill's tax the way the ledger booked it.

Tax a supplier charges is part of what a purchase cost: the posting spreads
it over the bill's lines and debits each line's account with its amount
plus its share, and nothing goes to Sales Tax Payable (explore 2.17.3,
macbase1 F9 — Pay Sales Tax offered $0.33 where $59.57 was owed). The IIF
export still wrote the tax as its own split to Sales Tax Payable, so a file
taken into QuickBooks put the old error back. Each split now carries its
line's share, and every bill's block matches its journal entry. A bill
posted before that change still debits Sales Tax Payable in the ledger and
goes out as it was booked.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.bills import Bill
from app.models.contacts import Vendor
from app.models.transactions import TransactionLine


@pytest.fixture
def mill(db_session, seed_accounts):
    v = Vendor(
        name="Cascade Flour Mill",
        is_active=True,
        default_expense_account_id=seed_accounts["5000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v


def _rows(content: bytes):
    return [line.split("\t") for line in content.decode("cp1252").splitlines()]


def _block(client, docnum):
    rows = _rows(client.get("/api/iif/export/bills").content)
    for i, r in enumerate(rows):
        if r[:2] == ["TRNS", "BILL"] and r[6] == docnum:
            spls = []
            for s in rows[i + 1 :]:
                if s[0] != "SPL":
                    break
                spls.append(s)
            return r, spls
    raise AssertionError(f"no BILL {docnum}")


def _ledger(db_session, docnum):
    """The bill's journal: {account name: debit} and the A/P credit."""
    bill = db_session.query(Bill).filter_by(bill_number=docnum).one()
    lines = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=bill.transaction_id)
        .all()
    )
    debits = {}
    for ln in lines:
        if ln.debit > 0:
            name = db_session.get(Account, ln.account_id).name
            debits[name] = debits.get(name, Decimal("0")) + Decimal(str(ln.debit))
    credit = sum((Decimal(str(ln.credit)) for ln in lines), Decimal("0"))
    return debits, credit


def _file(trns, spls):
    debits = {}
    for s in spls:
        debits[s[3]] = debits.get(s[3], Decimal("0")) + Decimal(s[5])
    return debits, -Decimal(trns[5])


def _bill(client, vendor_id, number, lines, tax_rate="0.0825", **extra):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": "2026-09-03",
            "tax_rate": tax_rate,
            "lines": lines,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_taxed_bill_goes_out_with_the_tax_in_its_lines(
    client, db_session, seed_accounts, mill
):
    _bill(
        client,
        mill.id,
        "CFM-2201",
        [
            {"description": "Bread flour 50 lb", "quantity": 20, "rate": 31.50},
            {
                "description": "Cake boxes",
                "quantity": 1000,
                "rate": 0.088,
                "account_id": seed_accounts["6400"].id,
            },
        ],
    )
    trns, spls = _block(client, "CFM-2201")
    assert "Sales Tax Payable" not in [s[3] for s in spls]
    assert len(spls) == 2
    got = _file(trns, spls)
    assert got == _ledger(db_session, "CFM-2201")
    # 630.00 + 88.00 of goods and 59.24 of tax, all of it cost
    assert got[1] == Decimal("777.24")
    assert sum(got[0].values()) == Decimal("777.24")


def test_a_foreign_bill_goes_out_at_the_home_amounts_it_booked(
    client, db_session, seed_accounts, mill
):
    _bill(
        client,
        mill.id,
        "EUR-7",
        [
            {"description": "Rye", "quantity": 3, "rate": 33.33},
            {"description": "Spelt", "quantity": 7, "rate": 11.11},
        ],
        tax_rate="0.19",
        currency="EUR",
        exchange_rate="1.0843",
    )
    trns, spls = _block(client, "EUR-7")
    assert "Sales Tax Payable" not in [s[3] for s in spls]
    assert _file(trns, spls) == _ledger(db_session, "EUR-7")


def test_a_bill_from_a_taxed_po_matches_its_journal(
    client, db_session, seed_accounts, mill
):
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": mill.id,
            "date": "2026-09-01",
            "tax_rate": 0.0825,
            "lines": [
                {"description": "Bread flour 50 lb", "quantity": 20, "rate": 31.50},
                {"description": "Rye flour 25 lb", "quantity": 4, "rate": 22.00},
            ],
        },
    )
    assert po.status_code == 201, po.text
    po = po.json()
    r = client.post(f"/api/purchase-orders/{po['id']}/convert-to-bill", json={})
    assert r.status_code == 200, r.text
    number = f"BILL-{po['po_number']}"
    trns, spls = _block(client, number)
    assert "Sales Tax Payable" not in [s[3] for s in spls]
    assert _file(trns, spls) == _ledger(db_session, number)


def test_a_bill_booked_before_the_change_goes_out_as_booked(
    client, db_session, seed_accounts, mill
):
    """Books from before this release: the bill's journal debited 2200
    with the tax. The file matches that journal, tax split and all."""
    from app.models.bills import BillLine, BillStatus
    from app.services.accounting import create_journal_entry

    bill = Bill(
        bill_number="OLD-1",
        vendor_id=mill.id,
        status=BillStatus.UNPAID,
        date=date(2026, 3, 2),
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("0.0825"),
        tax_amount=Decimal("8.25"),
        total=Decimal("108.25"),
        balance_due=Decimal("108.25"),
    )
    db_session.add(bill)
    db_session.flush()
    db_session.add(
        BillLine(
            bill_id=bill.id,
            account_id=seed_accounts["5000"].id,
            description="flour",
            quantity=Decimal("1"),
            rate=Decimal("100"),
            amount=Decimal("100.00"),
            line_order=0,
        )
    )
    txn = create_journal_entry(
        db_session,
        date(2026, 3, 2),
        "Bill OLD-1",
        [
            {"account_id": seed_accounts["5000"].id, "debit": Decimal("100.00")},
            {"account_id": seed_accounts["2200"].id, "debit": Decimal("8.25")},
            {"account_id": seed_accounts["2000"].id, "credit": Decimal("108.25")},
        ],
        source_type="bill",
        source_id=bill.id,
    )
    bill.transaction_id = txn.id
    db_session.commit()

    trns, spls = _block(client, "OLD-1")
    assert [(s[3], s[5]) for s in spls] == [
        (seed_accounts["5000"].name, "100.00"),
        ("Sales Tax Payable", "8.25"),
    ]
    assert _file(trns, spls) == _ledger(db_session, "OLD-1")

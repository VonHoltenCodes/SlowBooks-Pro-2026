"""What of a sale is income (#1038, W-195).

An item may post to a balance-sheet account: a security deposit invoiced
through an item credits a liability, not income. Such a line is billed and
owed, but it is not a sale, so the sales figures leave it out: the Profit
and Loss (which reads the income accounts), Sales by Item, Income by
Customer, Past Customers' lifetime sales and the analytics' revenue by
customer. A line counts as income when it posts to an income account: its
item's income account, else the default income account (4000), the account
the invoice and credit memo journals credit. The account is the item's as
it is now, as Sales by Item reads it."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.items import Item


def posts_to_income(db: Session):
    """A test of whether a sales line posts to an income account, by its
    item id (None: a line with no item). Looked up once."""
    from app.services.accounting import get_default_income_account_id
    from app.services.control_accounts import MissingControlAccount

    income = {
        a.id
        for a in db.query(Account.id).filter(Account.account_type == AccountType.INCOME)
    }
    try:
        default_ok = get_default_income_account_id(db) in income
    except MissingControlAccount:
        default_ok = False
    item_ok = {
        it.id: (it.income_account_id in income if it.income_account_id else default_ok)
        for it in db.query(Item.id, Item.income_account_id)
    }
    return lambda item_id: item_ok.get(item_id, default_ok) if item_id else default_ok


def non_income_item_ids(db: Session) -> set[int]:
    """The items whose lines post to no income account: an item on a
    balance-sheet (or any other non-income) account, or one with none of
    its own when the default income account is missing or not income.
    Small, so a sales figure can leave their lines out in SQL."""
    to_income = posts_to_income(db)
    return {iid for (iid,) in db.query(Item.id) if not to_income(iid)}


def non_income_by_invoice(db: Session, invoice_ids) -> dict[int, Decimal]:
    """{invoice id: home amount} of each invoice's lines that post to no
    income account (a security deposit's), each line converted at its
    invoice's booked rate and rounded per line, as the invoice journal
    converts them. An invoice with none is not in the answer. (A line with
    no item posts to the default income account and is income.)"""
    from app.models.invoices import Invoice, InvoiceLine
    from app.services.contact_balances import home_amount

    bad = non_income_item_ids(db)
    ids = list({i for i in invoice_ids if i is not None})
    if not ids or not bad:
        return {}
    out: dict[int, Decimal] = {}
    for start in range(0, len(ids), 500):
        rows = (
            db.query(InvoiceLine.invoice_id, InvoiceLine.amount, Invoice.exchange_rate)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .filter(InvoiceLine.invoice_id.in_(ids[start : start + 500]))
            .filter(InvoiceLine.item_id.in_(bad))
        )
        for invoice_id, amount, rate in rows:
            out[invoice_id] = out.get(invoice_id, Decimal("0")) + home_amount(
                amount or 0, rate
            )
    return {k: v for k, v in out.items() if v != 0}

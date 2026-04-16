from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount, BankTransaction, Reconciliation
from app.models.bills import Bill, BillPayment
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditMemo
from app.models.invoices import Invoice
from app.models.items import Item
from app.models.payments import Payment
from app.models.payroll import Employee, PayRun
from app.models.purchase_orders import PurchaseOrder
from app.models.recurring import RecurringInvoice
from app.models.settings import Settings
from app.models.transactions import Transaction, TransactionLine
from app.seed.chart_templates import CHART_TEMPLATES

SYSTEM_ACCOUNT_KEYS = (
    "system_account_default_bank_id",
    "system_account_accounts_receivable_id",
    "system_account_undeposited_funds_id",
    "system_account_default_sales_income_id",
    "system_account_default_expense_id",
    "system_account_accounts_payable_id",
    "system_account_payroll_clearing_id",
    "system_account_gst_control_id",
    "system_account_paye_payable_id",
    "system_account_kiwisaver_payable_id",
    "system_account_esct_payable_id",
    "system_account_child_support_payable_id",
    "system_account_wages_expense_id",
    "system_account_employer_kiwisaver_expense_id",
)


def _set(db: Session, key: str, value: str):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))


def _require_clean_ledger(db: Session) -> None:
    blockers = (
        (Transaction, "transactions"),
        (TransactionLine, "transaction lines"),
        (Invoice, "invoices"),
        (Bill, "bills"),
        (Payment, "payments"),
        (BillPayment, "bill payments"),
        (PurchaseOrder, "purchase orders"),
        (CreditMemo, "credit memos"),
        (RecurringInvoice, "recurring invoices"),
        (BankAccount, "bank accounts"),
        (BankTransaction, "bank transactions"),
        (Reconciliation, "reconciliations"),
        (Item, "items"),
        (Customer, "customers"),
        (Vendor, "vendors"),
        (Employee, "employees"),
        (PayRun, "pay runs"),
    )
    active = [label for model, label in blockers if db.query(model).count() > 0]
    if active:
        raise ValueError("Chart templates can only be loaded into a clean ledger")


def load_chart_template(db: Session, template_key: str) -> dict:
    template = CHART_TEMPLATES.get(template_key)
    if not template:
        raise ValueError("Unknown chart template")

    _require_clean_ledger(db)

    db.query(Account).delete()
    db.query(Settings).filter(Settings.key.in_(SYSTEM_ACCOUNT_KEYS)).delete(synchronize_session=False)
    db.flush()

    for entry in template["accounts"]:
        db.add(Account(
            name=entry["name"],
            account_number=entry["account_number"],
            account_type=AccountType(entry["account_type"]),
            is_system=True,
            is_active=True,
        ))
    db.flush()

    for key, number in template["system_account_numbers"].items():
        account = db.query(Account).filter(Account.account_number == number).first()
        if account:
            _set(db, key, str(account.id))

    db.commit()
    return {
        "status": "loaded",
        "template_key": template_key,
        "template_label": template["label"],
        "accounts_seeded": len(template["accounts"]),
    }

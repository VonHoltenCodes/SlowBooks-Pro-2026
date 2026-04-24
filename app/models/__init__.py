from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.transactions import Transaction, TransactionLine
from app.models.invoices import Invoice, InvoiceLine
from app.models.estimates import Estimate, EstimateLine
from app.models.payments import Payment, PaymentAllocation
from app.models.banking import BankAccount, BankTransaction, Reconciliation
from app.models.settings import Settings

# Phase 1: Foundation
from app.models.audit import AuditLog

# Phase 2: Accounts Payable
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.models.bills import Bill, BillLine, BillPayment, BillPaymentAllocation
from app.models.credit_memos import CreditMemo, CreditMemoLine, CreditApplication

# Phase 3: Productivity
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine

# Phase 4: Communication & Export
from app.models.email_log import EmailLog

# Phase 5: Advanced Integration
from app.models.tax import TaxCategoryMapping
from app.models.backups import Backup

# Phase 6: Ambitious
from app.models.companies import Company
from app.models.payroll import Employee, PayRun, PayStub

# Phase 8: QuickBooks Online
from app.models.qbo_mapping import QBOMapping

# Phase 10: Quick Wins + Medium Effort Features
from app.models.bank_rules import BankRule
from app.models.budgets import Budget
from app.models.attachments import Attachment
from app.models.email_templates import EmailTemplate

__all__ = [
    "Account", "Customer", "Vendor", "Item",
    "Transaction", "TransactionLine",
    "Invoice", "InvoiceLine",
    "Estimate", "EstimateLine",
    "Payment", "PaymentAllocation",
    "BankAccount", "BankTransaction", "Reconciliation",
    "Settings",
    # Phase 1
    "AuditLog",
    # Phase 2
    "PurchaseOrder", "PurchaseOrderLine",
    "Bill", "BillLine", "BillPayment", "BillPaymentAllocation",
    "CreditMemo", "CreditMemoLine", "CreditApplication",
    # Phase 3
    "RecurringInvoice", "RecurringInvoiceLine",
    # Phase 4
    "EmailLog",
    # Phase 5
    "TaxCategoryMapping", "Backup",
    # Phase 6
    "Company", "Employee", "PayRun", "PayStub",
    # Phase 8
    "QBOMapping",
    # Phase 10
    "BankRule", "Budget", "Attachment", "EmailTemplate",
]

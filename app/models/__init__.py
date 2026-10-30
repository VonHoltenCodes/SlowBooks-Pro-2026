from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.transactions import Transaction, TransactionLine
from app.models.invoices import Invoice, InvoiceLine
from app.models.estimates import Estimate, EstimateLine
from app.models.payments import Payment, PaymentAllocation
from app.models.banking import BankAccount, BankTransaction, Reconciliation
from app.models.settings import Settings
from app.models.classes import TxnClass  # noqa: F401 — registers the table
from app.models.jobs import Job, SovLine  # noqa: F401 — registers the tables
from app.models.change_orders import (  # noqa: F401 — registers the tables
    ChangeOrder,
    ChangeOrderLine,
)
from app.models.preferences import UserPreference  # noqa: F401 — registers the table
from app.models.cost_codes import CostCode  # noqa: F401 — registers the table
from app.models.job_costing import (  # noqa: F401 — registers the tables
    CostType,
    Equipment,
    JobBudget,
    JobCost,
    JobCostLine,
    JobCostToComplete,
)
from app.models.trade_packages import (  # noqa: F401 — registers the tables
    TradePackage,
    TradePackageSection,
)
from app.models.fixed_assets import FixedAsset, FixedAssetType  # noqa: F401
from app.models.in_kind import InKindGift, InKindGiftLine  # noqa: F401
from app.models.nonprofit import (  # noqa: F401 — registers the tables
    AllocationRule,
    AllocationRuleTarget,
    FunctionalAllocation,
    FunctionalAllocationLine,
    RestrictionRelease,
)

# Phase 1: Foundation
from app.models.audit import AuditLog

# Server Edition: user principals
from app.models.users import User  # noqa: F401 — registers the table
from app.models.api_tokens import ApiToken  # noqa: F401 — registers the table

# Phase 2: Accounts Payable
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.models.bills import Bill, BillLine, BillPayment, BillPaymentAllocation
from app.models.credit_memos import CreditMemo, CreditMemoLine, CreditApplication
from app.models.vendor_credits import (
    VendorCredit,
    VendorCreditLine,
    VendorCreditApplication,
)

# Phase 3: Productivity
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.memorized import (  # noqa: F401 — registers the tables
    MemorizedTransaction,
    MemorizedTransactionLine,
)

# Phase 4: Communication & Export
from app.models.email_log import EmailLog

# Phase 5: Advanced Integration
from app.models.tax import TaxCategoryMapping
from app.models.backups import Backup

# Phase 6: Ambitious
from app.models.auth import LoginAttempt
from app.models.companies import Company
from app.models.document_audit import DocumentAudit
from app.models.portal_access import PortalAccess
from app.models.reseller_permit import ResellerPermit
from app.models.payroll import Employee, PayRun, PayStub

# Tier 1: Full payroll / HR system
from app.models.time_entries import TimeEntry
from app.models.pto import PTOPolicy, PTOAccrual, PTORequest
from app.models.bank_accounts import EmployeeBankAccount

# Tier 2: deductions and garnishments
from app.models.deductions import GarnishmentOrder
from app.models.benefits import (
    BenefitCode,
    BenefitRate,
    EmployeeGroup,
    EmployeeGroupBenefit,
    EmployeeBenefit,
    BenefitYTD,
    PayStubBenefit,
)

# Opening YTD balances for a mid-year switch (#433)
from app.models.payroll_opening import (  # noqa: F401 — registers the tables
    PayrollOpeningBalance,
    PayrollOpeningBenefit,
    PayrollOpeningQuarter,
    PayrollOpeningStateBalance,
)

# Tier 3: HR onboarding
from app.models.hr import OnboardingTask

# Phase 8: QuickBooks Online
from app.models.qbo_mapping import QBOMapping

# Phase 10: Quick Wins + Medium Effort Features
from app.models.bank_rules import BankRule
from app.models.budgets import Budget
from app.models.attachments import Attachment
from app.models.stored_files import StoredFile
from app.models.email_templates import EmailTemplate
from app.models.invoice_messages import InvoiceMessage

# Phase 11: Inventory + Saved Reports
from app.models.items import InventoryMovement
from app.models.saved_reports import SavedReport

# Possible Duplicates report (#414): groups kept as "not a duplicate"
from app.models.duplicate_dismissals import DuplicateDismissal  # noqa: F401

__all__ = [
    "Account",
    "Customer",
    "Vendor",
    "Item",
    "Transaction",
    "TransactionLine",
    "Invoice",
    "InvoiceLine",
    "Estimate",
    "EstimateLine",
    "Payment",
    "PaymentAllocation",
    "BankAccount",
    "BankTransaction",
    "Reconciliation",
    "Settings",
    # Phase 1
    "AuditLog",
    # Phase 2
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Bill",
    "BillLine",
    "BillPayment",
    "BillPaymentAllocation",
    "CreditMemo",
    "CreditMemoLine",
    "CreditApplication",
    "VendorCredit",
    "VendorCreditLine",
    "VendorCreditApplication",
    # Phase 3
    "RecurringInvoice",
    "RecurringInvoiceLine",
    "MemorizedTransaction",
    "MemorizedTransactionLine",
    # Phase 4
    "EmailLog",
    # Phase 5
    "TaxCategoryMapping",
    "Backup",
    # Phase 6
    "Company",
    "Employee",
    "PayRun",
    "PayStub",
    # Tier 1: Full payroll / HR
    "TimeEntry",
    "PTOPolicy",
    "PTOAccrual",
    "PTORequest",
    "EmployeeBankAccount",
    # Tier 2: deductions and garnishments
    "BenefitCode",
    "BenefitRate",
    "EmployeeGroup",
    "EmployeeGroupBenefit",
    "EmployeeBenefit",
    "BenefitYTD",
    "PayStubBenefit",
    "GarnishmentOrder",
    # Tier 3: HR onboarding
    "OnboardingTask",
    # Phase 8
    "QBOMapping",
    # Phase 10
    "BankRule",
    "Budget",
    "Attachment",
    "StoredFile",
    "EmailTemplate",
    "InvoiceMessage",
    # Phase 11
    "InventoryMovement",
    "SavedReport",
    # Auth audit
    "LoginAttempt",
    # Document audit (tax forms, regulated PDFs)
    "DocumentAudit",
    # Portal access audit
    "PortalAccess",
    # Reseller permits — expiry tracking + manual verification trail
    "ResellerPermit",
]
from app.models.ocr_templates import OcrTemplate  # noqa: F401
from app.models.request_keys import RequestKey  # noqa: F401
from app.models.accountants_copies import AccountantsCopy  # noqa: E402,F401

# Construction billing (#555): registered here too, not only through their
# routes, so every process that imports the models knows every table. The
# cron script (scripts/run_recurring.py) imports the services without the
# web app, and its first invoice could not be flushed: invoices.
# pay_application_id names pay_applications, a table only the routes had
# registered (2.31.1 gate, W-83).
from app.models.pay_applications import PayApplication  # noqa: E402,F401
from app.models.pay_applications import PayApplicationLine  # noqa: E402,F401
from app.models.lien_waivers import LienWaiver  # noqa: E402,F401
from app.models.job_deposits import JobDeposit  # noqa: E402,F401
from app.models.collections import (  # noqa: F401 — registers the tables
    CustomerStatement,
    InvoiceReminder,
    InvoiceReminderRule,
)

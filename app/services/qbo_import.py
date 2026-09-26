# ============================================================================
# QBO Import Service — Pull data from QuickBooks Online into Slowbooks
#
# Import order follows the same dependency chain as IIF import:
#   accounts -> customers -> vendors -> items -> invoices -> payments
#
# Each entity type: query QBO -> check qbo_mappings for existing ->
# check by name/docnum for duplicates -> create or skip -> record mapping.
#
# QBO REST API returns Python objects via python-quickbooks SDK.
# All QBO object field access uses getattr(obj, field, None) for safety.
# ============================================================================

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.jobs import Job
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.qbo_mapping import QBOMapping
from app.models.transactions import Transaction
from app.services.qbo_common import (
    QBO_TO_ACCOUNT_TYPE,
    QBO_TO_ITEM_TYPE,
    create_mapping,
    apply_rollup_repair,
    get_mapping_by_qbo_id,
    is_journal_entry_type,
    journal_posting_matches,
    legacy_rollup_repair,
    posting_mismatch,
    rebase_account_balances,
)
from app.services.qbo_service import get_qbo_client
from app.services.safe_errors import DataProblem
from app.services import qbo_progress


def _safe(obj, attr, default=None):
    """Safe attribute access for QBO objects."""
    return getattr(obj, attr, default) or default


def _safe_decimal(obj, attr) -> Decimal:
    """Safe decimal extraction from QBO object."""
    val = getattr(obj, attr, None)
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0")


def _parse_qbo_date(s) -> date:
    """Parse QBO date string (YYYY-MM-DD) to date object."""
    if not s:
        return date.today()
    try:
        from datetime import datetime

        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


class CustomerNotFound(DataProblem):
    error_code = "IMPORT_CUSTOMER_NOT_FOUND"


def _resolve_customer(db, ref):
    """QBO CustomerRef may refer to a subcustomer imported as a local job."""
    qbo_id = str(_safe(ref, "value", ""))
    name = _safe(ref, "name", "")
    customer_map = get_mapping_by_qbo_id(db, "customer", qbo_id) if qbo_id else None
    if customer_map:
        customer = db.get(Customer, customer_map.slowbooks_id)
        if customer:
            return customer.id, None
    job_map = get_mapping_by_qbo_id(db, "job", qbo_id) if qbo_id else None
    if job_map:
        job = db.get(Job, job_map.slowbooks_id)
        if job and db.get(Customer, job.customer_id):
            qbo_progress.emit(
                "resolve",
                f"CustomerRef QBO #{qbo_id} ({name}) resolved through local project #{job.id} to customer #{job.customer_id}",
            )
            return job.customer_id, job.id
    if name:
        customer = db.query(Customer).filter(Customer.name == name).first()
        if customer:
            return customer.id, None
    mapped = []
    if customer_map:
        mapped.append(
            f"mapped local customer #{customer_map.slowbooks_id} does not exist"
        )
    if job_map:
        job = db.get(Job, job_map.slowbooks_id)
        mapped.append(
            f"mapped local project #{job_map.slowbooks_id}"
            + (
                f" references missing customer #{job.customer_id}"
                if job
                else " does not exist"
            )
        )
    raise CustomerNotFound(
        f"Customer not found: CustomerRef QBO #{qbo_id or '(missing ID)'} ({name or '(missing name)'})"
        + (
            f"; {'; '.join(mapped)}"
            if mapped
            else "; no local customer or project mapping and no matching customer name"
        )
        + ". Import Customers before this document."
    )


def _all_qbo_objects(qbo_class, client):
    """The SDK's .all() returns one page of 100 unless positioned explicitly."""
    page_size = 100
    start_position = 1
    objects = []
    while True:
        qbo_progress.emit(
            "query",
            f"Fetching {getattr(qbo_class, 'qbo_object_name', qbo_class.__name__)} page at position {start_position}",
            item_id="",
        )
        page = qbo_class.all(
            qb=client, start_position=start_position, max_results=page_size
        )
        qbo_progress.emit(
            "fetch", f"Fetched {len(page)} source items", fetched=len(page), item_id=""
        )
        objects.extend(page)
        if len(page) < page_size:
            return objects
        start_position += len(page)


def _all_inactive_qbo_accounts(qbo_class, client):
    """QBO omits inactive accounts from its default account query."""
    page_size = 100
    start_position = 1
    objects = []
    while True:
        qbo_progress.emit(
            "query",
            f"Fetching inactive Accounts at position {start_position}",
            item_id="",
        )
        page = qbo_class.query(
            "SELECT * FROM Account WHERE Active = false "
            f"STARTPOSITION {start_position} MAXRESULTS {page_size}",
            qb=client,
        )
        qbo_progress.emit(
            "fetch",
            f"Fetched {len(page)} inactive accounts",
            fetched=len(page),
            item_id="",
        )
        objects.extend(page)
        if len(page) < page_size:
            return objects
        start_position += len(page)


def _sync_bank_identity(db: Session, account: Account, qbo_type: str) -> None:
    """Make QBO bank/card chart accounts visible and usable in Banking."""
    bank_kind = {"Bank": "bank", "Credit Card": "credit_card"}.get(qbo_type)
    if bank_kind is None:
        return
    expected_type = AccountType.ASSET if bank_kind == "bank" else AccountType.LIABILITY
    if account.account_type != expected_type:
        raise ValueError(
            f"QBO {qbo_type} account {account.name!r} matches a local account "
            "with an incompatible type"
        )
    if account.bank_kind is None:
        account.bank_kind = bank_kind
    elif account.bank_kind != bank_kind:
        raise ValueError(
            f"QBO {qbo_type} account {account.name!r} matches a different "
            "local banking kind"
        )
    if not db.query(BankAccount).filter(BankAccount.account_id == account.id).first():
        db.add(BankAccount(name=account.name, account_id=account.id))
        db.flush()


# ============================================================================

# Import functions
# ============================================================================


@qbo_progress.stage("accounts")
def import_accounts(db: Session) -> dict:
    """Import accounts from QBO into Slowbooks."""
    from quickbooks.objects.account import Account as QBOAccount

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        # Historical report postings can reference inactive/deleted accounts.
        qbo_accounts = list(
            {
                str(a.Id): a
                for a in (
                    _all_qbo_objects(QBOAccount, client)
                    + _all_inactive_qbo_accounts(QBOAccount, client)
                )
            }.values()
        )
        # Sort by FullyQualifiedName depth so parents come first
        qbo_accounts.sort(
            key=lambda a: (_safe(a, "FullyQualifiedName", "") or "").count(":")
        )
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "accounts",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_acct in qbo_accounts:
        try:
            qbo_id = _safe(qbo_acct, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_acct, "DocNumber")
                or _safe(qbo_acct, "DisplayName")
                or _safe(qbo_acct, "Name"),
            )
            if not qbo_id:
                continue

            qbo_type = _safe(qbo_acct, "AccountType", "Expense")

            active = getattr(qbo_acct, "Active", True) is not False

            # Earlier imports mapped bank/card accounts without their Banking
            # identity. Repair those mappings on the next import.
            mapping = get_mapping_by_qbo_id(db, "account", qbo_id)
            if mapping:
                existing = db.get(Account, mapping.slowbooks_id)
                if existing is not None:
                    existing.is_active = active
                    if active:
                        _sync_bank_identity(db, existing, qbo_type)
                continue

            name = _safe(qbo_acct, "Name", "")
            if not name:
                continue

            # Check if name already exists in Slowbooks
            existing = db.query(Account).filter(Account.name == name).first()
            if existing:
                existing.is_active = active
                if active:
                    _sync_bank_identity(db, existing, qbo_type)
                create_mapping(
                    db, "account", existing.id, qbo_id, _safe(qbo_acct, "SyncToken")
                )
                db.flush()
                continue

            # Map QBO account type to Slowbooks
            acct_type = QBO_TO_ACCOUNT_TYPE.get(qbo_type, AccountType.EXPENSE)

            # Resolve parent account
            parent_id = None
            parent_ref = _safe(qbo_acct, "ParentRef")
            if parent_ref:
                parent_qbo_id = _safe(parent_ref, "value", "")
                parent_map = get_mapping_by_qbo_id(db, "account", parent_qbo_id)
                if parent_map:
                    parent_id = parent_map.slowbooks_id

            acct = Account(
                name=name,
                account_type=acct_type,
                account_number=_safe(qbo_acct, "AcctNum") or None,
                description=_safe(qbo_acct, "Description") or None,
                parent_id=parent_id,
                is_active=active,
                balance=_safe_decimal(qbo_acct, "CurrentBalance"),
            )
            db.add(acct)
            db.flush()
            if active:
                _sync_bank_identity(db, acct, qbo_type)

            create_mapping(db, "account", acct.id, qbo_id, _safe(qbo_acct, "SyncToken"))
            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "account",
                    "qbo_id": str(qbo_id),
                    "message": qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("customers")
def import_customers(db: Session) -> dict:
    """Import customers from QBO into Slowbooks."""
    from quickbooks.objects.customer import Customer as QBOCustomer

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_customers = _all_qbo_objects(QBOCustomer, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "customers",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    # Sub-customers (QBO "Job": true, the Online "Projects" flavour) become
    # Jobs under their parent, so parents must land first.
    qbo_customers = sorted(qbo_customers, key=lambda c: bool(_safe(c, "Job", False)))

    for qbo_cust in qbo_customers:
        try:
            qbo_id = _safe(qbo_cust, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_cust, "DocNumber")
                or _safe(qbo_cust, "DisplayName")
                or _safe(qbo_cust, "Name"),
            )
            if not qbo_id:
                continue

            display_name = _safe(qbo_cust, "DisplayName", "")
            if not display_name:
                continue

            parent_ref = _safe(qbo_cust, "ParentRef")
            if _safe(qbo_cust, "Job", False) and parent_ref:
                if get_mapping_by_qbo_id(db, "job", qbo_id):
                    continue
                parent_qbo_id = (
                    _safe(parent_ref, "value", "")
                    if not isinstance(parent_ref, str)
                    else parent_ref
                )
                parent_map = get_mapping_by_qbo_id(db, "customer", str(parent_qbo_id))
                parent = (
                    db.get(Customer, parent_map.slowbooks_id) if parent_map else None
                )
                if parent is None:
                    # Fall back to the qualified name ("Parent:Child")
                    from app.services.jobs_service import resolve_customer_and_job

                    fq = _safe(qbo_cust, "FullyQualifiedName", "") or display_name
                    parent, job = resolve_customer_and_job(db, fq)
                else:
                    from app.services.jobs_service import get_or_create_job

                    job = get_or_create_job(db, parent.id, display_name)
                if job is not None:
                    create_mapping(
                        db, "job", job.id, qbo_id, _safe(qbo_cust, "SyncToken")
                    )
                    db.flush()
                    imported += 1
                    qbo_progress.created(qbo_id)
                continue

            if get_mapping_by_qbo_id(db, "customer", qbo_id):
                continue

            # Check by name
            existing = db.query(Customer).filter(Customer.name == display_name).first()
            if existing:
                create_mapping(
                    db, "customer", existing.id, qbo_id, _safe(qbo_cust, "SyncToken")
                )
                db.flush()
                continue

            # Extract billing address
            bill_addr = _safe(qbo_cust, "BillAddr")
            bill_address1 = _safe(bill_addr, "Line1") if bill_addr else None
            bill_address2 = _safe(bill_addr, "Line2") if bill_addr else None
            bill_city = _safe(bill_addr, "City") if bill_addr else None
            bill_state = (
                _safe(bill_addr, "CountrySubDivisionCode") if bill_addr else None
            )
            bill_zip = _safe(bill_addr, "PostalCode") if bill_addr else None

            # Extract shipping address
            ship_addr = _safe(qbo_cust, "ShipAddr")
            ship_address1 = _safe(ship_addr, "Line1") if ship_addr else None
            ship_address2 = _safe(ship_addr, "Line2") if ship_addr else None
            ship_city = _safe(ship_addr, "City") if ship_addr else None
            ship_state = (
                _safe(ship_addr, "CountrySubDivisionCode") if ship_addr else None
            )
            ship_zip = _safe(ship_addr, "PostalCode") if ship_addr else None

            # Extract primary email/phone
            email_addr = _safe(qbo_cust, "PrimaryEmailAddr")
            email = _safe(email_addr, "Address") if email_addr else None

            phone_obj = _safe(qbo_cust, "PrimaryPhone")
            phone = _safe(phone_obj, "FreeFormNumber") if phone_obj else None

            mobile_obj = _safe(qbo_cust, "Mobile")
            mobile = _safe(mobile_obj, "FreeFormNumber") if mobile_obj else None

            fax_obj = _safe(qbo_cust, "Fax")
            fax = _safe(fax_obj, "FreeFormNumber") if fax_obj else None

            # Resolve payment terms
            terms = "Net 30"
            terms_ref = _safe(qbo_cust, "SalesTermRef")
            if terms_ref:
                terms = _safe(terms_ref, "name", "Net 30") or "Net 30"

            cust = Customer(
                name=display_name,
                company=_safe(qbo_cust, "CompanyName") or None,
                email=email,
                phone=phone,
                mobile=mobile,
                fax=fax,
                website=(
                    _safe(qbo_cust, "WebAddr", {}).get("URI")
                    if isinstance(_safe(qbo_cust, "WebAddr"), dict)
                    else None
                ),
                bill_address1=bill_address1,
                bill_address2=bill_address2,
                bill_city=bill_city,
                bill_state=bill_state,
                bill_zip=bill_zip,
                ship_address1=ship_address1,
                ship_address2=ship_address2,
                ship_city=ship_city,
                ship_state=ship_state,
                ship_zip=ship_zip,
                terms=terms,
                tax_id=_safe(qbo_cust, "PrimaryTaxIdentifier") or None,
                is_taxable=_safe(qbo_cust, "Taxable", True),
                is_active=_safe(qbo_cust, "Active", True),
                balance=_safe_decimal(qbo_cust, "Balance"),
                notes=_safe(qbo_cust, "Notes") or None,
            )
            db.add(cust)
            db.flush()

            create_mapping(
                db, "customer", cust.id, qbo_id, _safe(qbo_cust, "SyncToken")
            )
            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "customer",
                    "qbo_id": str(qbo_id),
                    "message": qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("vendors")
def import_vendors(db: Session) -> dict:
    """Import vendors from QBO into Slowbooks."""
    from quickbooks.objects.vendor import Vendor as QBOVendor

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_vendors = _all_qbo_objects(QBOVendor, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "vendors",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_vend in qbo_vendors:
        try:
            qbo_id = _safe(qbo_vend, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_vend, "DocNumber")
                or _safe(qbo_vend, "DisplayName")
                or _safe(qbo_vend, "Name"),
            )
            if not qbo_id:
                continue

            if get_mapping_by_qbo_id(db, "vendor", qbo_id):
                continue

            display_name = _safe(qbo_vend, "DisplayName", "")
            if not display_name:
                continue

            existing = db.query(Vendor).filter(Vendor.name == display_name).first()
            if existing:
                create_mapping(
                    db, "vendor", existing.id, qbo_id, _safe(qbo_vend, "SyncToken")
                )
                db.flush()
                continue

            # Extract address
            addr = _safe(qbo_vend, "BillAddr")
            address1 = _safe(addr, "Line1") if addr else None
            address2 = _safe(addr, "Line2") if addr else None
            city = _safe(addr, "City") if addr else None
            state = _safe(addr, "CountrySubDivisionCode") if addr else None
            zipcode = _safe(addr, "PostalCode") if addr else None

            email_addr = _safe(qbo_vend, "PrimaryEmailAddr")
            email = _safe(email_addr, "Address") if email_addr else None

            phone_obj = _safe(qbo_vend, "PrimaryPhone")
            phone = _safe(phone_obj, "FreeFormNumber") if phone_obj else None

            fax_obj = _safe(qbo_vend, "Fax")
            fax = _safe(fax_obj, "FreeFormNumber") if fax_obj else None

            terms = "Net 30"
            terms_ref = _safe(qbo_vend, "TermRef")
            if terms_ref:
                terms = _safe(terms_ref, "name", "Net 30") or "Net 30"

            vend = Vendor(
                name=display_name,
                company=_safe(qbo_vend, "CompanyName") or None,
                email=email,
                phone=phone,
                fax=fax,
                address1=address1,
                address2=address2,
                city=city,
                state=state,
                zip=zipcode,
                terms=terms,
                tax_id=_safe(qbo_vend, "TaxIdentifier") or None,
                account_number=_safe(qbo_vend, "AcctNum") or None,
                is_active=_safe(qbo_vend, "Active", True),
                balance=_safe_decimal(qbo_vend, "Balance"),
                notes=_safe(qbo_vend, "Notes") or None,
            )
            db.add(vend)
            db.flush()

            create_mapping(db, "vendor", vend.id, qbo_id, _safe(qbo_vend, "SyncToken"))
            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "vendor",
                    "qbo_id": str(qbo_id),
                    "message": qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("items")
def import_items(db: Session) -> dict:
    """Import items from QBO into Slowbooks."""
    from quickbooks.objects.item import Item as QBOItem

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_items = _all_qbo_objects(QBOItem, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "items",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_item in qbo_items:
        try:
            qbo_id = _safe(qbo_item, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_item, "DocNumber")
                or _safe(qbo_item, "DisplayName")
                or _safe(qbo_item, "Name"),
            )
            if not qbo_id:
                continue

            if get_mapping_by_qbo_id(db, "item", qbo_id):
                continue

            name = _safe(qbo_item, "Name", "")
            if not name:
                continue

            existing = db.query(Item).filter(Item.name == name).first()
            if existing:
                create_mapping(
                    db, "item", existing.id, qbo_id, _safe(qbo_item, "SyncToken")
                )
                db.flush()
                continue

            qbo_type = _safe(qbo_item, "Type", "Service")
            item_type = QBO_TO_ITEM_TYPE.get(qbo_type, ItemType.SERVICE)

            # Resolve income account
            income_account_id = None
            income_ref = _safe(qbo_item, "IncomeAccountRef")
            if income_ref:
                income_qbo_id = _safe(income_ref, "value", "")
                income_map = get_mapping_by_qbo_id(db, "account", income_qbo_id)
                if income_map:
                    income_account_id = income_map.slowbooks_id

            # Resolve expense account
            expense_account_id = None
            expense_ref = _safe(qbo_item, "ExpenseAccountRef")
            if expense_ref:
                expense_qbo_id = _safe(expense_ref, "value", "")
                expense_map = get_mapping_by_qbo_id(db, "account", expense_qbo_id)
                if expense_map:
                    expense_account_id = expense_map.slowbooks_id

            item = Item(
                name=name,
                item_type=item_type,
                description=_safe(qbo_item, "Description") or None,
                rate=_safe_decimal(qbo_item, "UnitPrice"),
                cost=_safe_decimal(qbo_item, "PurchaseCost"),
                income_account_id=income_account_id,
                expense_account_id=expense_account_id,
                is_taxable=_safe(qbo_item, "Taxable", False),
                is_active=_safe(qbo_item, "Active", True),
            )
            db.add(item)
            db.flush()

            create_mapping(db, "item", item.id, qbo_id, _safe(qbo_item, "SyncToken"))
            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "item",
                    "qbo_id": str(qbo_id),
                    "message": qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("invoices")
def import_invoices(db: Session) -> dict:
    """Import invoices from QBO into Slowbooks."""
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_invoices = _all_qbo_objects(QBOInvoice, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "invoices",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_inv in qbo_invoices:
        try:
            qbo_id = _safe(qbo_inv, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_inv, "DocNumber")
                or _safe(qbo_inv, "DisplayName")
                or _safe(qbo_inv, "Name"),
            )
            if not qbo_id:
                continue

            if get_mapping_by_qbo_id(db, "invoice", qbo_id):
                continue

            doc_num = _safe(qbo_inv, "DocNumber", "")

            # Check by invoice number for dedup
            if doc_num:
                existing = (
                    db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
                )
                if existing:
                    create_mapping(
                        db, "invoice", existing.id, qbo_id, _safe(qbo_inv, "SyncToken")
                    )
                    db.flush()
                    continue

            customer_id, job_id = _resolve_customer(db, _safe(qbo_inv, "CustomerRef"))

            # Determine status from balance
            total_amt = _safe_decimal(qbo_inv, "TotalAmt")
            balance = _safe_decimal(qbo_inv, "Balance")
            if balance == total_amt:
                status = InvoiceStatus.SENT
            elif balance > 0 and balance < total_amt:
                status = InvoiceStatus.PARTIAL
            elif balance == 0 and total_amt > 0:
                status = InvoiceStatus.PAID
            else:
                status = InvoiceStatus.SENT

            inv_date = _parse_qbo_date(_safe(qbo_inv, "TxnDate"))
            due_date = _parse_qbo_date(_safe(qbo_inv, "DueDate"))

            # Extract tax
            tax_amount = Decimal("0")
            txn_tax = _safe(qbo_inv, "TxnTaxDetail")
            if txn_tax:
                tax_amount = _safe_decimal(txn_tax, "TotalTax")

            subtotal = total_amt - tax_amount
            amount_paid = total_amt - balance

            invoice = Invoice(
                invoice_number=doc_num or None,
                customer_id=customer_id,
                job_id=job_id,
                date=inv_date,
                due_date=due_date,
                terms=(
                    _safe(qbo_inv, "SalesTermRef", {}).get("name", "Net 30")
                    if isinstance(_safe(qbo_inv, "SalesTermRef"), dict)
                    else "Net 30"
                ),
                status=status,
                subtotal=subtotal,
                tax_rate=Decimal("0"),
                tax_amount=tax_amount,
                total=total_amt,
                amount_paid=amount_paid,
                balance_due=balance,
                notes=(
                    _safe(qbo_inv, "CustomerMemo", {}).get("value")
                    if isinstance(_safe(qbo_inv, "CustomerMemo"), dict)
                    else None
                ),
            )
            db.add(invoice)
            db.flush()

            # Process line items — only SalesItemLineDetail
            line_order = 0
            lines = _safe(qbo_inv, "Line") or []
            for qbo_line in lines:
                detail_type = _safe(qbo_line, "DetailType", "")
                if detail_type != "SalesItemLineDetail":
                    continue  # Skip SubTotalLineDetail, DiscountLineDetail, etc.

                detail = _safe(qbo_line, "SalesItemLineDetail")
                if not detail:
                    continue

                # Resolve item
                item_id = None
                item_ref = _safe(detail, "ItemRef")
                if item_ref:
                    item_qbo_id = _safe(item_ref, "value", "")
                    item_map = get_mapping_by_qbo_id(db, "item", item_qbo_id)
                    if item_map:
                        item_id = item_map.slowbooks_id

                qty = _safe_decimal(detail, "Qty") or Decimal("1")
                rate = _safe_decimal(detail, "UnitPrice")
                amount = _safe_decimal(qbo_line, "Amount")

                inv_line = InvoiceLine(
                    invoice_id=invoice.id,
                    item_id=item_id,
                    description=_safe(qbo_line, "Description") or None,
                    quantity=qty,
                    rate=rate,
                    amount=amount,
                    line_order=line_order,
                )
                db.add(inv_line)
                line_order += 1

            create_mapping(
                db, "invoice", invoice.id, qbo_id, _safe(qbo_inv, "SyncToken")
            )

            # Phase 11 (audit fix): QBO-imported invoices must also move
            # inventory for tracked items. QBO itself manages inventory so
            # we only touch items that are track_inventory=True on OUR side.
            db.flush()
            db.refresh(invoice)
            from app.services.inventory_hooks import post_sale_for_invoice

            post_sale_for_invoice(db, invoice, txn_date=invoice.date)

            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "invoice",
                    "qbo_id": str(qbo_id),
                    "code": getattr(e, "error_code", None)
                    or (
                        "IMPORT_VALIDATION"
                        if isinstance(e, DataProblem)
                        else "IMPORT_OPERATION_FAILED"
                    ),
                    "document_number": _safe(qbo_inv, "DocNumber")
                    or _safe(qbo_inv, "PaymentRefNum"),
                    "message": _source_context("invoice", qbo_inv)
                    + ": "
                    + qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("payments")
def import_payments(db: Session) -> dict:
    """Import payments from QBO into Slowbooks."""
    from quickbooks.objects.payment import Payment as QBOPayment

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_payments = _all_qbo_objects(QBOPayment, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "payments",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_pmt in qbo_payments:
        try:
            qbo_id = _safe(qbo_pmt, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_pmt, "DocNumber")
                or _safe(qbo_pmt, "PaymentRefNum")
                or _safe(qbo_pmt, "DisplayName")
                or _safe(qbo_pmt, "Name"),
            )
            if not qbo_id:
                continue

            if get_mapping_by_qbo_id(db, "payment", qbo_id):
                continue

            customer_id, _ = _resolve_customer(db, _safe(qbo_pmt, "CustomerRef"))

            amount = _safe_decimal(qbo_pmt, "TotalAmt")
            pmt_date = _parse_qbo_date(_safe(qbo_pmt, "TxnDate"))

            # Resolve deposit account
            deposit_account_id = None
            deposit_ref = _safe(qbo_pmt, "DepositToAccountRef")
            if deposit_ref:
                deposit_qbo_id = _safe(deposit_ref, "value", "")
                deposit_map = get_mapping_by_qbo_id(db, "account", deposit_qbo_id)
                if deposit_map:
                    deposit_account_id = deposit_map.slowbooks_id

            payment = Payment(
                customer_id=customer_id,
                date=pmt_date,
                amount=amount,
                method=(
                    _safe(qbo_pmt, "PaymentMethodRef", {}).get("name")
                    if isinstance(_safe(qbo_pmt, "PaymentMethodRef"), dict)
                    else None
                ),
                reference=_safe(qbo_pmt, "PaymentRefNum") or None,
                deposit_to_account_id=deposit_account_id,
            )
            db.add(payment)
            db.flush()

            # Create allocations from QBO Line items
            lines = _safe(qbo_pmt, "Line") or []
            for pmt_line in lines:
                linked_txns = _safe(pmt_line, "LinkedTxn") or []
                line_amount = _safe_decimal(pmt_line, "Amount")
                for linked in linked_txns:
                    txn_type = _safe(linked, "TxnType", "")
                    txn_id = _safe(linked, "TxnId", "")
                    if txn_type == "Invoice" and txn_id:
                        inv_map = get_mapping_by_qbo_id(db, "invoice", txn_id)
                        if inv_map:
                            inv = (
                                db.query(Invoice)
                                .filter(Invoice.id == inv_map.slowbooks_id)
                                .first()
                            )
                            if inv:
                                alloc = PaymentAllocation(
                                    payment_id=payment.id,
                                    invoice_id=inv.id,
                                    amount=line_amount or amount,
                                )
                                db.add(alloc)

                                # Update invoice status
                                inv.amount_paid = (inv.amount_paid or Decimal("0")) + (
                                    line_amount or amount
                                )
                                inv.balance_due = inv.total - inv.amount_paid
                                if inv.balance_due <= 0:
                                    inv.status = InvoiceStatus.PAID
                                elif inv.amount_paid > 0:
                                    inv.status = InvoiceStatus.PARTIAL

            create_mapping(
                db, "payment", payment.id, qbo_id, _safe(qbo_pmt, "SyncToken")
            )
            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "payment",
                    "qbo_id": str(qbo_id),
                    "code": getattr(e, "error_code", None)
                    or (
                        "IMPORT_VALIDATION"
                        if isinstance(e, DataProblem)
                        else "IMPORT_OPERATION_FAILED"
                    ),
                    "document_number": _safe(qbo_pmt, "DocNumber")
                    or _safe(qbo_pmt, "PaymentRefNum"),
                    "message": _source_context("payment", qbo_pmt)
                    + ": "
                    + qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


@qbo_progress.stage("sales_receipts")
def import_sales_receipts(db: Session) -> dict:
    """Import sales receipts from QBO into Slowbooks.

    QBO's SalesReceipt is an invoice paid at the time of sale. Each one
    becomes an Invoice flagged is_sales_receipt (status PAID) plus a
    Payment for the full total — the same document pair the Enter Sales
    Receipts screen produces.
    """
    from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_receipts = _all_qbo_objects(QBOSalesReceipt, client)
    except Exception as e:
        qbo_progress.append_error(
            errors,
            {
                "entity": "sales_receipts",
                "message": f"Failed to query QBO: {qbo_progress.error_message(e, "QBO import")}",
            },
        )
        return {"imported": 0, "errors": errors}

    for qbo_sr in qbo_receipts:
        try:
            qbo_id = _safe(qbo_sr, "Id", "")
            qbo_progress.item(
                qbo_id,
                _safe(qbo_sr, "DocNumber")
                or _safe(qbo_sr, "DisplayName")
                or _safe(qbo_sr, "Name"),
            )
            if not qbo_id:
                continue

            if get_mapping_by_qbo_id(db, "sales_receipt", qbo_id):
                continue

            doc_num = _safe(qbo_sr, "DocNumber", "")

            # Dedup by document number against existing invoices/receipts
            if doc_num:
                existing = (
                    db.query(Invoice).filter(Invoice.invoice_number == doc_num).first()
                )
                if existing:
                    create_mapping(
                        db,
                        "sales_receipt",
                        existing.id,
                        qbo_id,
                        _safe(qbo_sr, "SyncToken"),
                    )
                    db.flush()
                    continue

            customer_id, job_id = _resolve_customer(db, _safe(qbo_sr, "CustomerRef"))

            total_amt = _safe_decimal(qbo_sr, "TotalAmt")
            sr_date = _parse_qbo_date(_safe(qbo_sr, "TxnDate"))

            # Extract tax
            tax_amount = Decimal("0")
            txn_tax = _safe(qbo_sr, "TxnTaxDetail")
            if txn_tax:
                tax_amount = _safe_decimal(txn_tax, "TotalTax")

            if not doc_num:
                from app.services.numbering import next_invoice_number

                doc_num = next_invoice_number(db)

            invoice = Invoice(
                invoice_number=doc_num,
                customer_id=customer_id,
                job_id=job_id,
                date=sr_date,
                due_date=sr_date,
                terms="Due on Receipt",
                status=InvoiceStatus.PAID,
                is_sales_receipt=True,
                subtotal=total_amt - tax_amount,
                tax_rate=Decimal("0"),
                tax_amount=tax_amount,
                total=total_amt,
                amount_paid=total_amt,
                balance_due=Decimal("0"),
                notes=(
                    _safe(qbo_sr, "CustomerMemo", {}).get("value")
                    if isinstance(_safe(qbo_sr, "CustomerMemo"), dict)
                    else None
                ),
            )
            db.add(invoice)
            db.flush()

            # Process line items — only SalesItemLineDetail
            line_order = 0
            lines = _safe(qbo_sr, "Line") or []
            for qbo_line in lines:
                detail_type = _safe(qbo_line, "DetailType", "")
                if detail_type != "SalesItemLineDetail":
                    continue  # Skip SubTotalLineDetail, DiscountLineDetail, etc.

                detail = _safe(qbo_line, "SalesItemLineDetail")
                if not detail:
                    continue

                item_id = None
                item_ref = _safe(detail, "ItemRef")
                if item_ref:
                    item_qbo_id = _safe(item_ref, "value", "")
                    item_map = get_mapping_by_qbo_id(db, "item", item_qbo_id)
                    if item_map:
                        item_id = item_map.slowbooks_id

                qty = _safe_decimal(detail, "Qty") or Decimal("1")
                rate = _safe_decimal(detail, "UnitPrice")
                amount = _safe_decimal(qbo_line, "Amount")

                inv_line = InvoiceLine(
                    invoice_id=invoice.id,
                    item_id=item_id,
                    description=_safe(qbo_line, "Description") or None,
                    quantity=qty,
                    rate=rate,
                    amount=amount,
                    line_order=line_order,
                )
                db.add(inv_line)
                line_order += 1

            # Payment for the full total, deposited where QBO says
            deposit_account_id = None
            deposit_ref = _safe(qbo_sr, "DepositToAccountRef")
            if deposit_ref:
                deposit_qbo_id = _safe(deposit_ref, "value", "")
                deposit_map = get_mapping_by_qbo_id(db, "account", deposit_qbo_id)
                if deposit_map:
                    deposit_account_id = deposit_map.slowbooks_id

            payment = Payment(
                customer_id=customer_id,
                date=sr_date,
                amount=total_amt,
                method=(
                    _safe(qbo_sr, "PaymentMethodRef", {}).get("name")
                    if isinstance(_safe(qbo_sr, "PaymentMethodRef"), dict)
                    else None
                ),
                reference=_safe(qbo_sr, "PaymentRefNum") or None,
                deposit_to_account_id=deposit_account_id,
            )
            db.add(payment)
            db.flush()
            db.add(
                PaymentAllocation(
                    payment_id=payment.id, invoice_id=invoice.id, amount=total_amt
                )
            )

            create_mapping(
                db, "sales_receipt", invoice.id, qbo_id, _safe(qbo_sr, "SyncToken")
            )

            # Same inventory treatment as QBO-imported invoices
            db.flush()
            db.refresh(invoice)
            from app.services.inventory_hooks import post_sale_for_invoice

            post_sale_for_invoice(db, invoice, txn_date=invoice.date)

            imported += 1
            qbo_progress.created()

        except Exception as e:
            qbo_progress.append_error(
                errors,
                {
                    "entity": "sales_receipt",
                    "qbo_id": str(qbo_id),
                    "code": getattr(e, "error_code", None)
                    or (
                        "IMPORT_VALIDATION"
                        if isinstance(e, DataProblem)
                        else "IMPORT_OPERATION_FAILED"
                    ),
                    "document_number": _safe(qbo_sr, "DocNumber")
                    or _safe(qbo_sr, "PaymentRefNum"),
                    "message": _source_context("sales_receipt", qbo_sr)
                    + ": "
                    + qbo_progress.error_message(e, "QBO import"),
                },
            )

    return {"imported": imported, "errors": errors}


# ============================================================================
# Journal entries
# ============================================================================


def _source_context(entity, source):
    source_id = _safe(source, "Id", "(missing ID)")
    document = _safe(source, "DocNumber") or _safe(source, "PaymentRefNum")
    context = f"{entity} QBO #{source_id}" + (
        f" (document {document})" if document else ""
    )
    linked = sorted(
        {
            f"{_safe(link, 'TxnType', '(missing type)')} QBO #{_safe(link, 'TxnId', '(missing ID)')}"
            for line in _safe(source, "Line", [])
            for link in _safe(line, "LinkedTxn", [])
        }
    )
    return context + (f"; linked {', '.join(linked)}" if linked else "")


def _journal_lines(qbo_entry, accounts) -> list[dict]:
    """Use PostingType, not account type or JournalEntry.TotalAmt (always 0)."""
    context = _source_context("JournalEntry", qbo_entry)
    rate = getattr(qbo_entry, "ExchangeRate", None)
    try:
        exchange_rate = Decimal(str(rate if rate is not None else 1))
    except InvalidOperation as exc:
        raise DataProblem(f"{context}: invalid exchange rate {rate!r}") from exc
    if not exchange_rate.is_finite() or exchange_rate <= 0:
        raise DataProblem(f"{context}: invalid exchange rate {rate!r}")

    lines = []
    posting_lines = 0
    for index, line in enumerate(_safe(qbo_entry, "Line", [])):
        detail_type = _safe(line, "DetailType", "")
        if detail_type in {"DescriptionOnly", "DescriptionOnlyLineDetail"}:
            continue
        detail = _safe(line, "JournalEntryLineDetail")
        account_ref = _safe(detail, "AccountRef")
        account_id = str(_safe(account_ref, "value", ""))
        line_id = getattr(line, "Id", None)
        location = (
            f"{context}, line #{line_id if line_id is not None else '(missing ID)'} "
            f"(position {index + 1}), account QBO #{account_id or '(missing ID)'} "
            f"({_safe(account_ref, 'name', '(missing name)')})"
        )
        if detail_type != "JournalEntryLineDetail":
            raise DataProblem(f"{location}: unsupported line type {detail_type!r}")
        posting_lines += 1
        posting_type = _safe(detail, "PostingType", "")
        if posting_type not in {"Debit", "Credit"}:
            raise DataProblem(
                f"{location}: missing Debit/Credit posting type "
                f"(PostingType={posting_type!r}, Amount={getattr(line, 'Amount', None)!r})"
            )
        value = getattr(line, "Amount", None)
        try:
            amount = Decimal(str(value))
        except InvalidOperation as exc:
            raise DataProblem(f"{location}: unreadable line amount {value!r}") from exc
        if not amount.is_finite() or amount < 0:
            raise DataProblem(
                f"{location}: line amount must be finite and non-negative; received {value!r}"
            )
        amount = (amount * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount == 0:
            continue  # QBO can return zeroed lines on voided entries.
        account = accounts.get(account_id)
        if account is None:
            raise DataProblem(
                f"{location}: no local account mapping; import QBO Accounts first"
            )
        lines.append(
            {
                "account_id": account.id,
                "debit": amount if posting_type == "Debit" else Decimal("0"),
                "credit": amount if posting_type == "Credit" else Decimal("0"),
                "description": str(_safe(line, "Description", ""))[:300],
            }
        )
    if posting_lines < 2:
        raise DataProblem(
            f"{context}: missing its debit and credit lines; received {posting_lines} posting line(s)"
        )
    debits = sum((line["debit"] for line in lines), Decimal("0"))
    credits = sum((line["credit"] for line in lines), Decimal("0"))
    if debits != credits:
        raise DataProblem(
            f"{context}: does not balance; debit {debits:.2f}, credit {credits:.2f}, "
            f"difference {debits - credits:.2f}; no lines were imported"
        )
    return lines


def _non_posting_journal(qbo_entry, client, txn_date):
    """Verify old empty QBO journal stubs against the posted General Ledger.

    A missing amount/sign on a financial line remains a validation error.
    Only a single empty account line, accompanied by description-only lines,
    can be skipped, and only when QBO's ledger confirms no monetary posting.
    """
    from app.services.qbo_ledger_import import _money, _report_periods

    source_lines = _safe(qbo_entry, "Line", [])
    posting = [
        line
        for line in source_lines
        if _safe(line, "DetailType")
        not in {"DescriptionOnly", "DescriptionOnlyLineDetail"}
    ]
    if (
        len(posting) != 1
        or len(source_lines) < 2
        or _safe(posting[0], "DetailType") != "JournalEntryLineDetail"
        or _safe(_safe(posting[0], "JournalEntryLineDetail"), "PostingType")
    ):
        return False
    # The SDK supplies Amount=0 when QBO omits this field.
    amount = getattr(posting[0], "Amount", None)
    try:
        if amount is not None and Decimal(str(amount)) != 0:
            return False
    except InvalidOperation:
        return False
    qbo_id = str(_safe(qbo_entry, "Id", ""))
    context = _source_context("JournalEntry", qbo_entry)
    qbo_progress.emit(
        "verify",
        f"{context}: verifying empty account line against General Ledger on {txn_date}",
    )
    try:
        for _, _, rows in _report_periods(client, txn_date, txn_date):
            for _, cells in rows:
                if str(cells[1].get("id", "")) == qbo_id and _money(
                    cells[6].get("value")
                ):
                    return False
    except Exception as exc:
        detail = qbo_progress.error_message(exc, "QBO empty journal verification")
        raise DataProblem(
            f"{context}: account line has zero/absent Amount and no PostingType; could not verify "
            f"whether it posts to General Ledger on {txn_date}: {detail}"
        ) from exc
    ref = _safe(_safe(posting[0], "JournalEntryLineDetail"), "AccountRef")
    qbo_progress.emit(
        "skip",
        f"{context}: line #{getattr(posting[0], 'Id', '(missing ID)')}, account QBO "
        f"#{_safe(ref, 'value', '(missing ID)')}, has zero/absent Amount and no PostingType; "
        f"no monetary posting found in QBO General Ledger on {txn_date}",
        level="warning",
        code="IMPORT_NON_POSTING_JOURNAL",
    )
    qbo_progress.skipped("Verified non-posting journal; no local transaction needed")
    return True


@qbo_progress.stage("journal_entries")
def import_journal_entries(db: Session) -> dict:
    """Query every JournalEntry page and post complete journals once.

    https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry
    The SDK sends SELECT * FROM JournalEntry STARTPOSITION n MAXRESULTS 100.
    This import works independently of General Ledger report availability.
    """
    from quickbooks.objects.journalentry import JournalEntry as QBOJournalEntry

    from app.services.accounting import create_journal_entry
    from app.services.closing_date import check_closing_date
    from app.services.qbo_ledger_import import _account_map

    errors = []
    try:
        client = get_qbo_client(db)
        qbo_entries = _all_qbo_objects(QBOJournalEntry, client)
        accounts = _account_map(db)
    except Exception as exc:
        return {
            "imported": 0,
            "errors": [
                {
                    "entity": "journal_entries",
                    "message": f"Failed to query QBO: {qbo_progress.error_message(exc, 'QBO journal import')}",
                }
            ],
        }

    ledger_mappings = {}
    for mapping in db.query(QBOMapping).filter_by(entity_type="ledger"):
        txn_type, separator, qbo_id = mapping.qbo_id.partition(":")
        if separator and is_journal_entry_type(txn_type):
            ledger_mappings.setdefault(qbo_id, []).append(mapping)

    pending = []
    token_updates = []
    seen = set()
    for qbo_entry in qbo_entries:
        qbo_id = str(_safe(qbo_entry, "Id", ""))
        context = _source_context("JournalEntry", qbo_entry)
        qbo_progress.item(qbo_id, _safe(qbo_entry, "DocNumber"))
        try:
            if not qbo_id or len(qbo_id) > 100 or qbo_id in seen:
                raise DataProblem(
                    f"{context}: missing, duplicate, or invalid journal entry ID"
                )
            seen.add(qbo_id)
            value = _safe(qbo_entry, "TxnDate", "")
            try:
                txn_date = date.fromisoformat(str(value))
            except ValueError as exc:
                raise DataProblem(
                    f"{context}: invalid transaction date {value!r}"
                ) from exc
            mapping = get_mapping_by_qbo_id(db, "journal_entry", qbo_id)
            legacy = ledger_mappings.get(qbo_id, [])
            if (
                not mapping
                and not legacy
                and _non_posting_journal(qbo_entry, client, txn_date)
            ):
                continue
            lines = _journal_lines(qbo_entry, accounts)
            txn = None
            repair = None
            local_id = mapping.slowbooks_id if mapping else None
            if legacy:
                local_ids = {m.slowbooks_id for m in legacy}
                if len(local_ids) != 1 or (
                    local_id is not None and local_id not in local_ids
                ):
                    raise DataProblem(
                        f"{context}: multiple existing ledger postings; local transaction IDs "
                        + ", ".join(
                            str(n)
                            for n in sorted(
                                local_ids | ({local_id} if local_id else set())
                            )
                        )
                    )
                local_id = local_id if local_id is not None else legacy[0].slowbooks_id
            if local_id is not None:
                txn = db.get(Transaction, local_id)
                if not journal_posting_matches(txn, txn_date, lines):
                    repair = (
                        legacy_rollup_repair(txn, txn_date, lines, accounts)
                        if legacy
                        else None
                    )
                    if not repair:
                        raise posting_mismatch(txn, local_id, txn_date, lines, accounts)
                    check_closing_date(db, txn_date)
                elif mapping:
                    # SyncToken also changes for non-posting metadata edits.
                    # Verify the financial posting before refreshing the token.
                    token_updates.append((mapping, _safe(qbo_entry, "SyncToken")))
                    qbo_progress.skipped(
                        f"Verified local transaction #{txn.id}; date and account amounts match QBO"
                    )
                    continue
            elif not lines:
                qbo_progress.skipped("Zero-value journal; no local transaction needed")
                continue
            else:
                check_closing_date(db, txn_date)
            pending.append((qbo_entry, txn_date, lines, txn, mapping, repair))
            qbo_progress.validated(qbo_id)
        except Exception as exc:
            detail = (
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else qbo_progress.error_message(exc, "QBO journal import")
            )
            qbo_progress.append_error(
                errors,
                {
                    "entity": "journal_entries",
                    "qbo_id": qbo_id,
                    "document_number": _safe(qbo_entry, "DocNumber"),
                    "code": getattr(exc, "error_code", "IMPORT_VALIDATION"),
                    "message": (
                        f"{context}: {detail}" if context not in detail else detail
                    ),
                },
            )

    if errors:
        qbo_progress.emit(
            "block",
            f"Journal batch not posted: {len(errors)} validation error(s); "
            f"{len(pending)} validated journal(s) waiting. No journal repairs or new postings saved.",
            level="warning",
            code="IMPORT_BATCH_BLOCKED",
            item_id="",
        )
        return {"imported": 0, "errors": errors}

    imported = 0
    qbo_id = ""
    try:
        with db.begin_nested():
            for qbo_entry, _, _, txn, _, repair in pending:
                if repair:
                    qbo_id = str(qbo_entry.Id)
                    qbo_progress.posting(qbo_id, _safe(qbo_entry, "DocNumber"))
                    apply_rollup_repair(txn, repair, accounts)
            db.flush()
            if pending:
                rebase_account_balances(db, accounts.values())
            for mapping, token in token_updates:
                mapping.qbo_sync_token = token
            for qbo_entry, txn_date, lines, txn, mapping, _ in pending:
                qbo_id = str(qbo_entry.Id)
                qbo_progress.posting(qbo_id, _safe(qbo_entry, "DocNumber"))
                if txn is None:
                    txn = create_journal_entry(
                        db,
                        txn_date,
                        _safe(qbo_entry, "PrivateNote")
                        or f"QBO Journal Entry {qbo_id}",
                        lines,
                        source_type="qbo_journal",
                        reference=str(_safe(qbo_entry, "DocNumber") or qbo_id)[:100],
                    )
                    imported += 1
                    qbo_progress.created(qbo_id)
                else:
                    txn.source_type = "qbo_journal"
                    qbo_progress.emit(
                        "update",
                        f"Reused local transaction #{txn.id}; pending commit",
                        item_id=qbo_id,
                    )
                if mapping:
                    mapping.qbo_sync_token = _safe(qbo_entry, "SyncToken")
                else:
                    create_mapping(
                        db,
                        "journal_entry",
                        txn.id,
                        qbo_id,
                        _safe(qbo_entry, "SyncToken"),
                    )
            db.flush()
    except Exception as exc:
        return {
            "imported": 0,
            "errors": [
                {
                    "entity": "journal_entries",
                    "qbo_id": qbo_id,
                    "message": f"JournalEntry QBO #{qbo_id or '(batch)'}: "
                    + qbo_progress.error_message(exc, "QBO journal import"),
                }
            ],
        }
    return {"imported": imported, "errors": []}


# ============================================================================
# Master import orchestrator
# ============================================================================


def import_all(db: Session) -> dict:
    """Import all entity types from QBO in dependency order.

    Returns counts of imported records and any errors.
    """
    result = {
        "accounts": 0,
        "customers": 0,
        "vendors": 0,
        "items": 0,
        "invoices": 0,
        "payments": 0,
        "sales_receipts": 0,
        "journal_entries": 0,
        "ledger": 0,
        "errors": [],
    }

    # 1. Accounts first (items reference income/expense accounts)
    r = import_accounts(db)
    result["accounts"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 2. Customers (invoices + payments reference customers)
    r = import_customers(db)
    result["customers"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 3. Vendors
    r = import_vendors(db)
    result["vendors"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 4. Items (invoice lines reference items)
    r = import_items(db)
    result["items"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 5. Invoices (payments reference invoices)
    r = import_invoices(db)
    result["invoices"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 6. Payments
    r = import_payments(db)
    result["payments"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 7. Sales receipts (self-contained invoice + payment pairs)
    r = import_sales_receipts(db)
    result["sales_receipts"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 8. Query journals directly so report failures cannot hide them.
    r = import_journal_entries(db)
    result["journal_entries"] = r["imported"]
    result["errors"].extend(r["errors"])

    # 9. Post all QBO financial activity after the chart is mapped. These
    # report postings also cover the documents above; they must be posted once.
    from app.services.qbo_ledger_import import import_ledger

    r = import_ledger(db)
    result["ledger"] = r["imported"]
    result["errors"].extend(r["errors"])

    db.commit()
    return result

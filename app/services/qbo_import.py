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
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.qbo_mapping import QBOMapping
from app.services.qbo_service import get_qbo_client


# ============================================================================
# QBO -> Slowbooks type mappings
# ============================================================================

_QBO_ACCOUNT_TYPE_MAP = {
    "Bank": AccountType.ASSET,
    "Accounts Receivable": AccountType.ASSET,
    "Other Current Asset": AccountType.ASSET,
    "Fixed Asset": AccountType.ASSET,
    "Other Asset": AccountType.ASSET,
    "Accounts Payable": AccountType.LIABILITY,
    "Credit Card": AccountType.LIABILITY,
    "Other Current Liability": AccountType.LIABILITY,
    "Long Term Liability": AccountType.LIABILITY,
    "Equity": AccountType.EQUITY,
    "Income": AccountType.INCOME,
    "Other Income": AccountType.INCOME,
    "Expense": AccountType.EXPENSE,
    "Other Expense": AccountType.EXPENSE,
    "Cost of Goods Sold": AccountType.COGS,
}

_QBO_ITEM_TYPE_MAP = {
    "Service": ItemType.SERVICE,
    "Inventory": ItemType.PRODUCT,
    "Group": ItemType.PRODUCT,
    "NonInventory": ItemType.MATERIAL,
}


# ============================================================================
# Mapping helpers
# ============================================================================

def _get_mapping(db: Session, entity_type: str, qbo_id: str) -> QBOMapping:
    """Look up existing mapping by QBO ID."""
    return db.query(QBOMapping).filter(
        QBOMapping.entity_type == entity_type,
        QBOMapping.qbo_id == str(qbo_id),
    ).first()


def _create_mapping(db: Session, entity_type: str, slowbooks_id: int,
                    qbo_id: str, sync_token: str = None):
    """Create a new QBO <-> Slowbooks mapping."""
    m = QBOMapping(
        entity_type=entity_type,
        slowbooks_id=slowbooks_id,
        qbo_id=str(qbo_id),
        qbo_sync_token=sync_token,
    )
    db.add(m)


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


# ============================================================================
# Import functions
# ============================================================================

def import_accounts(db: Session) -> dict:
    """Import accounts from QBO into Slowbooks."""
    from quickbooks.objects.account import Account as QBOAccount

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        # Query all active accounts, sorted by depth (parents first)
        qbo_accounts = QBOAccount.all(qb=client)
        # Sort by FullyQualifiedName depth so parents come first
        qbo_accounts.sort(key=lambda a: (_safe(a, "FullyQualifiedName", "") or "").count(":"))
    except Exception as e:
        errors.append({"entity": "accounts", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_acct in qbo_accounts:
        try:
            qbo_id = _safe(qbo_acct, "Id", "")
            if not qbo_id:
                continue

            # Skip if already mapped
            if _get_mapping(db, "account", qbo_id):
                continue

            name = _safe(qbo_acct, "Name", "")
            if not name:
                continue

            # Check if name already exists in Slowbooks
            existing = db.query(Account).filter(Account.name == name).first()
            if existing:
                _create_mapping(db, "account", existing.id, qbo_id,
                                _safe(qbo_acct, "SyncToken"))
                db.flush()
                continue

            # Map QBO account type to Slowbooks
            qbo_type = _safe(qbo_acct, "AccountType", "Expense")
            acct_type = _QBO_ACCOUNT_TYPE_MAP.get(qbo_type, AccountType.EXPENSE)

            # Resolve parent account
            parent_id = None
            parent_ref = _safe(qbo_acct, "ParentRef")
            if parent_ref:
                parent_qbo_id = _safe(parent_ref, "value", "")
                parent_map = _get_mapping(db, "account", parent_qbo_id)
                if parent_map:
                    parent_id = parent_map.slowbooks_id

            acct = Account(
                name=name,
                account_type=acct_type,
                account_number=_safe(qbo_acct, "AcctNum") or None,
                description=_safe(qbo_acct, "Description") or None,
                parent_id=parent_id,
                is_active=_safe(qbo_acct, "Active", True),
                balance=_safe_decimal(qbo_acct, "CurrentBalance"),
            )
            db.add(acct)
            db.flush()

            _create_mapping(db, "account", acct.id, qbo_id,
                            _safe(qbo_acct, "SyncToken"))
            imported += 1

        except Exception as e:
            errors.append({"entity": "account", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_customers(db: Session) -> dict:
    """Import customers from QBO into Slowbooks."""
    from quickbooks.objects.customer import Customer as QBOCustomer

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_customers = QBOCustomer.all(qb=client)
    except Exception as e:
        errors.append({"entity": "customers", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_cust in qbo_customers:
        try:
            qbo_id = _safe(qbo_cust, "Id", "")
            if not qbo_id:
                continue

            if _get_mapping(db, "customer", qbo_id):
                continue

            display_name = _safe(qbo_cust, "DisplayName", "")
            if not display_name:
                continue

            # Check by name
            existing = db.query(Customer).filter(Customer.name == display_name).first()
            if existing:
                _create_mapping(db, "customer", existing.id, qbo_id,
                                _safe(qbo_cust, "SyncToken"))
                db.flush()
                continue

            # Extract billing address
            bill_addr = _safe(qbo_cust, "BillAddr")
            bill_address1 = _safe(bill_addr, "Line1") if bill_addr else None
            bill_address2 = _safe(bill_addr, "Line2") if bill_addr else None
            bill_city = _safe(bill_addr, "City") if bill_addr else None
            bill_state = _safe(bill_addr, "CountrySubDivisionCode") if bill_addr else None
            bill_zip = _safe(bill_addr, "PostalCode") if bill_addr else None

            # Extract shipping address
            ship_addr = _safe(qbo_cust, "ShipAddr")
            ship_address1 = _safe(ship_addr, "Line1") if ship_addr else None
            ship_address2 = _safe(ship_addr, "Line2") if ship_addr else None
            ship_city = _safe(ship_addr, "City") if ship_addr else None
            ship_state = _safe(ship_addr, "CountrySubDivisionCode") if ship_addr else None
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
                website=_safe(qbo_cust, "WebAddr", {}).get("URI") if isinstance(_safe(qbo_cust, "WebAddr"), dict) else None,
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

            _create_mapping(db, "customer", cust.id, qbo_id,
                            _safe(qbo_cust, "SyncToken"))
            imported += 1

        except Exception as e:
            errors.append({"entity": "customer", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_vendors(db: Session) -> dict:
    """Import vendors from QBO into Slowbooks."""
    from quickbooks.objects.vendor import Vendor as QBOVendor

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_vendors = QBOVendor.all(qb=client)
    except Exception as e:
        errors.append({"entity": "vendors", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_vend in qbo_vendors:
        try:
            qbo_id = _safe(qbo_vend, "Id", "")
            if not qbo_id:
                continue

            if _get_mapping(db, "vendor", qbo_id):
                continue

            display_name = _safe(qbo_vend, "DisplayName", "")
            if not display_name:
                continue

            existing = db.query(Vendor).filter(Vendor.name == display_name).first()
            if existing:
                _create_mapping(db, "vendor", existing.id, qbo_id,
                                _safe(qbo_vend, "SyncToken"))
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

            _create_mapping(db, "vendor", vend.id, qbo_id,
                            _safe(qbo_vend, "SyncToken"))
            imported += 1

        except Exception as e:
            errors.append({"entity": "vendor", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_items(db: Session) -> dict:
    """Import items from QBO into Slowbooks."""
    from quickbooks.objects.item import Item as QBOItem

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_items = QBOItem.all(qb=client)
    except Exception as e:
        errors.append({"entity": "items", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_item in qbo_items:
        try:
            qbo_id = _safe(qbo_item, "Id", "")
            if not qbo_id:
                continue

            if _get_mapping(db, "item", qbo_id):
                continue

            name = _safe(qbo_item, "Name", "")
            if not name:
                continue

            existing = db.query(Item).filter(Item.name == name).first()
            if existing:
                _create_mapping(db, "item", existing.id, qbo_id,
                                _safe(qbo_item, "SyncToken"))
                db.flush()
                continue

            qbo_type = _safe(qbo_item, "Type", "Service")
            item_type = _QBO_ITEM_TYPE_MAP.get(qbo_type, ItemType.SERVICE)

            # Resolve income account
            income_account_id = None
            income_ref = _safe(qbo_item, "IncomeAccountRef")
            if income_ref:
                income_qbo_id = _safe(income_ref, "value", "")
                income_map = _get_mapping(db, "account", income_qbo_id)
                if income_map:
                    income_account_id = income_map.slowbooks_id

            # Resolve expense account
            expense_account_id = None
            expense_ref = _safe(qbo_item, "ExpenseAccountRef")
            if expense_ref:
                expense_qbo_id = _safe(expense_ref, "value", "")
                expense_map = _get_mapping(db, "account", expense_qbo_id)
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

            _create_mapping(db, "item", item.id, qbo_id,
                            _safe(qbo_item, "SyncToken"))
            imported += 1

        except Exception as e:
            errors.append({"entity": "item", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_invoices(db: Session) -> dict:
    """Import invoices from QBO into Slowbooks."""
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_invoices = QBOInvoice.all(qb=client)
    except Exception as e:
        errors.append({"entity": "invoices", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_inv in qbo_invoices:
        try:
            qbo_id = _safe(qbo_inv, "Id", "")
            if not qbo_id:
                continue

            if _get_mapping(db, "invoice", qbo_id):
                continue

            doc_num = _safe(qbo_inv, "DocNumber", "")

            # Check by invoice number for dedup
            if doc_num:
                existing = db.query(Invoice).filter(
                    Invoice.invoice_number == doc_num
                ).first()
                if existing:
                    _create_mapping(db, "invoice", existing.id, qbo_id,
                                    _safe(qbo_inv, "SyncToken"))
                    db.flush()
                    continue

            # Resolve customer
            cust_ref = _safe(qbo_inv, "CustomerRef")
            customer_id = None
            if cust_ref:
                cust_qbo_id = _safe(cust_ref, "value", "")
                cust_map = _get_mapping(db, "customer", cust_qbo_id)
                if cust_map:
                    customer_id = cust_map.slowbooks_id

            if not customer_id:
                # Try to find by name
                cust_name = _safe(cust_ref, "name", "") if cust_ref else ""
                if cust_name:
                    cust = db.query(Customer).filter(Customer.name == cust_name).first()
                    if cust:
                        customer_id = cust.id
                if not customer_id:
                    errors.append({"entity": "invoice", "qbo_id": str(qbo_id),
                                   "message": "Customer not found"})
                    continue

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
                date=inv_date,
                due_date=due_date,
                terms=_safe(qbo_inv, "SalesTermRef", {}).get("name", "Net 30") if isinstance(_safe(qbo_inv, "SalesTermRef"), dict) else "Net 30",
                status=status,
                subtotal=subtotal,
                tax_rate=Decimal("0"),
                tax_amount=tax_amount,
                total=total_amt,
                amount_paid=amount_paid,
                balance_due=balance,
                notes=_safe(qbo_inv, "CustomerMemo", {}).get("value") if isinstance(_safe(qbo_inv, "CustomerMemo"), dict) else None,
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
                    item_map = _get_mapping(db, "item", item_qbo_id)
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

            _create_mapping(db, "invoice", invoice.id, qbo_id,
                            _safe(qbo_inv, "SyncToken"))

            # Phase 11 (audit fix): QBO-imported invoices must also move
            # inventory for tracked items. QBO itself manages inventory so
            # we only touch items that are track_inventory=True on OUR side.
            db.flush()
            db.refresh(invoice)
            from app.services.inventory_hooks import post_sale_for_invoice
            post_sale_for_invoice(db, invoice, txn_date=invoice.date)

            imported += 1

        except Exception as e:
            errors.append({"entity": "invoice", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


def import_payments(db: Session) -> dict:
    """Import payments from QBO into Slowbooks."""
    from quickbooks.objects.payment import Payment as QBOPayment

    client = get_qbo_client(db)
    imported = 0
    errors = []

    try:
        qbo_payments = QBOPayment.all(qb=client)
    except Exception as e:
        errors.append({"entity": "payments", "message": f"Failed to query QBO: {str(e)}"})
        return {"imported": 0, "errors": errors}

    for qbo_pmt in qbo_payments:
        try:
            qbo_id = _safe(qbo_pmt, "Id", "")
            if not qbo_id:
                continue

            if _get_mapping(db, "payment", qbo_id):
                continue

            # Resolve customer
            cust_ref = _safe(qbo_pmt, "CustomerRef")
            customer_id = None
            if cust_ref:
                cust_qbo_id = _safe(cust_ref, "value", "")
                cust_map = _get_mapping(db, "customer", cust_qbo_id)
                if cust_map:
                    customer_id = cust_map.slowbooks_id

            if not customer_id:
                cust_name = _safe(cust_ref, "name", "") if cust_ref else ""
                if cust_name:
                    cust = db.query(Customer).filter(Customer.name == cust_name).first()
                    if cust:
                        customer_id = cust.id
                if not customer_id:
                    errors.append({"entity": "payment", "qbo_id": str(qbo_id),
                                   "message": "Customer not found"})
                    continue

            amount = _safe_decimal(qbo_pmt, "TotalAmt")
            pmt_date = _parse_qbo_date(_safe(qbo_pmt, "TxnDate"))

            # Resolve deposit account
            deposit_account_id = None
            deposit_ref = _safe(qbo_pmt, "DepositToAccountRef")
            if deposit_ref:
                deposit_qbo_id = _safe(deposit_ref, "value", "")
                deposit_map = _get_mapping(db, "account", deposit_qbo_id)
                if deposit_map:
                    deposit_account_id = deposit_map.slowbooks_id

            payment = Payment(
                customer_id=customer_id,
                date=pmt_date,
                amount=amount,
                method=_safe(qbo_pmt, "PaymentMethodRef", {}).get("name") if isinstance(_safe(qbo_pmt, "PaymentMethodRef"), dict) else None,
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
                        inv_map = _get_mapping(db, "invoice", txn_id)
                        if inv_map:
                            inv = db.query(Invoice).filter(
                                Invoice.id == inv_map.slowbooks_id
                            ).first()
                            if inv:
                                alloc = PaymentAllocation(
                                    payment_id=payment.id,
                                    invoice_id=inv.id,
                                    amount=line_amount or amount,
                                )
                                db.add(alloc)

                                # Update invoice status
                                inv.amount_paid = (inv.amount_paid or Decimal("0")) + (line_amount or amount)
                                inv.balance_due = inv.total - inv.amount_paid
                                if inv.balance_due <= 0:
                                    inv.status = InvoiceStatus.PAID
                                elif inv.amount_paid > 0:
                                    inv.status = InvoiceStatus.PARTIAL

            _create_mapping(db, "payment", payment.id, qbo_id,
                            _safe(qbo_pmt, "SyncToken"))
            imported += 1

        except Exception as e:
            errors.append({"entity": "payment", "qbo_id": str(qbo_id),
                           "message": str(e)})

    return {"imported": imported, "errors": errors}


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

    db.commit()
    return result

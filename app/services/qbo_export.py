# ============================================================================
# QBO Export Service — Push data from Slowbooks to QuickBooks Online
#
# Export order follows the same dependency chain as import:
#   accounts -> customers -> vendors -> items -> invoices -> payments
#
# For each entity: check qbo_mappings for existing QBO record ->
# if mapped, skip (v1 doesn't update) -> if not mapped, create in QBO ->
# record mapping with returned QBO ID + SyncToken.
# ============================================================================


from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.invoices import Invoice, InvoiceLine
from app.models.payments import Payment, PaymentAllocation
from app.services.qbo_common import (
    ACCOUNT_TYPE_TO_QBO,
    ITEM_TYPE_TO_QBO,
    create_mapping,
    get_mapping_by_slowbooks_id,
)
from app.services.qbo_service import get_qbo_client

# ============================================================================
# Export functions
# ============================================================================


def export_accounts(db: Session) -> dict:
    """Export Slowbooks accounts to QBO."""
    from quickbooks.objects.account import Account as QBOAccount

    client = get_qbo_client(db)
    exported = 0
    errors = []

    accounts = db.query(Account).filter(Account.is_active).all()

    # Sort by parent (null parent_id first)
    accounts.sort(key=lambda a: (a.parent_id or 0, a.id))

    for acct in accounts:
        try:
            if get_mapping_by_slowbooks_id(db, "account", acct.id):
                continue  # Already exported

            type_info = ACCOUNT_TYPE_TO_QBO.get(
                acct.account_type, ("Expense", "Other Miscellaneous Service Cost")
            )

            qbo_acct = QBOAccount()
            qbo_acct.Name = acct.name
            qbo_acct.AccountType = type_info[0]
            qbo_acct.AccountSubType = type_info[1]
            if acct.account_number:
                qbo_acct.AcctNum = acct.account_number
            if acct.description:
                qbo_acct.Description = acct.description

            # Set parent if mapped
            if acct.parent_id:
                parent_map = get_mapping_by_slowbooks_id(db, "account", acct.parent_id)
                if parent_map:
                    qbo_acct.ParentRef = {"value": parent_map.qbo_id}
                    qbo_acct.SubAccount = True

            saved = qbo_acct.save(qb=client)
            create_mapping(
                db, "account", acct.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append(
                {
                    "entity": "account",
                    "id": acct.id,
                    "name": acct.name,
                    "message": str(e),
                }
            )

    return {"exported": exported, "errors": errors}


def export_customers(db: Session) -> dict:
    """Export Slowbooks customers to QBO."""
    from quickbooks.objects.customer import Customer as QBOCustomer

    client = get_qbo_client(db)
    exported = 0
    errors = []

    customers = db.query(Customer).filter(Customer.is_active).all()

    for cust in customers:
        try:
            if get_mapping_by_slowbooks_id(db, "customer", cust.id):
                continue

            qbo_cust = QBOCustomer()
            qbo_cust.DisplayName = cust.name
            if cust.company:
                qbo_cust.CompanyName = cust.company

            # Billing address
            if cust.bill_address1 or cust.bill_city:
                qbo_cust.BillAddr = {
                    "Line1": cust.bill_address1 or "",
                    "Line2": cust.bill_address2 or "",
                    "City": cust.bill_city or "",
                    "CountrySubDivisionCode": cust.bill_state or "",
                    "PostalCode": cust.bill_zip or "",
                }

            # Shipping address
            if cust.ship_address1 or cust.ship_city:
                qbo_cust.ShipAddr = {
                    "Line1": cust.ship_address1 or "",
                    "Line2": cust.ship_address2 or "",
                    "City": cust.ship_city or "",
                    "CountrySubDivisionCode": cust.ship_state or "",
                    "PostalCode": cust.ship_zip or "",
                }

            if cust.email:
                qbo_cust.PrimaryEmailAddr = {"Address": cust.email}
            if cust.phone:
                qbo_cust.PrimaryPhone = {"FreeFormNumber": cust.phone}
            if cust.mobile:
                qbo_cust.Mobile = {"FreeFormNumber": cust.mobile}
            if cust.fax:
                qbo_cust.Fax = {"FreeFormNumber": cust.fax}
            if cust.notes:
                qbo_cust.Notes = cust.notes

            saved = qbo_cust.save(qb=client)
            create_mapping(
                db, "customer", cust.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append(
                {
                    "entity": "customer",
                    "id": cust.id,
                    "name": cust.name,
                    "message": str(e),
                }
            )

    return {"exported": exported, "errors": errors}


def export_vendors(db: Session) -> dict:
    """Export Slowbooks vendors to QBO."""
    from quickbooks.objects.vendor import Vendor as QBOVendor

    client = get_qbo_client(db)
    exported = 0
    errors = []

    vendors = db.query(Vendor).filter(Vendor.is_active).all()

    for vend in vendors:
        try:
            if get_mapping_by_slowbooks_id(db, "vendor", vend.id):
                continue

            qbo_vend = QBOVendor()
            qbo_vend.DisplayName = vend.name
            if vend.company:
                qbo_vend.CompanyName = vend.company

            if vend.address1 or vend.city:
                qbo_vend.BillAddr = {
                    "Line1": vend.address1 or "",
                    "Line2": vend.address2 or "",
                    "City": vend.city or "",
                    "CountrySubDivisionCode": vend.state or "",
                    "PostalCode": vend.zip or "",
                }

            if vend.email:
                qbo_vend.PrimaryEmailAddr = {"Address": vend.email}
            if vend.phone:
                qbo_vend.PrimaryPhone = {"FreeFormNumber": vend.phone}
            if vend.fax:
                qbo_vend.Fax = {"FreeFormNumber": vend.fax}
            if vend.notes:
                qbo_vend.Notes = vend.notes
            if vend.account_number:
                qbo_vend.AcctNum = vend.account_number

            saved = qbo_vend.save(qb=client)
            create_mapping(
                db, "vendor", vend.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append(
                {
                    "entity": "vendor",
                    "id": vend.id,
                    "name": vend.name,
                    "message": str(e),
                }
            )

    return {"exported": exported, "errors": errors}


def export_items(db: Session) -> dict:
    """Export Slowbooks items to QBO."""
    from quickbooks.objects.item import Item as QBOItem

    client = get_qbo_client(db)
    exported = 0
    errors = []

    items = db.query(Item).filter(Item.is_active).all()

    for item in items:
        try:
            if get_mapping_by_slowbooks_id(db, "item", item.id):
                continue

            qbo_item = QBOItem()
            qbo_item.Name = item.name
            qbo_item.Type = ITEM_TYPE_TO_QBO.get(item.item_type, "Service")

            if item.description:
                qbo_item.Description = item.description
            if item.rate:
                qbo_item.UnitPrice = float(item.rate)
            if item.cost:
                qbo_item.PurchaseCost = float(item.cost)

            # Link income account if mapped
            if item.income_account_id:
                acct_map = get_mapping_by_slowbooks_id(
                    db, "account", item.income_account_id
                )
                if acct_map:
                    qbo_item.IncomeAccountRef = {"value": acct_map.qbo_id}

            # Link expense account if mapped
            if item.expense_account_id:
                acct_map = get_mapping_by_slowbooks_id(
                    db, "account", item.expense_account_id
                )
                if acct_map:
                    qbo_item.ExpenseAccountRef = {"value": acct_map.qbo_id}

            # QBO requires IncomeAccountRef for Service/NonInventory items
            if not item.income_account_id or not get_mapping_by_slowbooks_id(
                db, "account", item.income_account_id
            ):
                # Find a default income account in QBO mappings
                default_income = (
                    db.query(Account)
                    .filter(
                        Account.account_type == AccountType.INCOME,
                        Account.is_active,
                    )
                    .first()
                )
                if default_income:
                    acct_map = get_mapping_by_slowbooks_id(
                        db, "account", default_income.id
                    )
                    if acct_map:
                        qbo_item.IncomeAccountRef = {"value": acct_map.qbo_id}

            saved = qbo_item.save(qb=client)
            create_mapping(
                db, "item", item.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append(
                {"entity": "item", "id": item.id, "name": item.name, "message": str(e)}
            )

    return {"exported": exported, "errors": errors}


def export_invoices(db: Session) -> dict:
    """Export Slowbooks invoices to QBO."""
    from quickbooks.objects.invoice import Invoice as QBOInvoice

    client = get_qbo_client(db)
    exported = 0
    errors = []

    invoices = db.query(Invoice).all()

    for inv in invoices:
        try:
            if get_mapping_by_slowbooks_id(db, "invoice", inv.id):
                continue

            # Customer must be mapped
            cust_map = get_mapping_by_slowbooks_id(db, "customer", inv.customer_id)
            if not cust_map:
                errors.append(
                    {
                        "entity": "invoice",
                        "id": inv.id,
                        "message": f"Customer {inv.customer_id} not mapped to QBO",
                    }
                )
                continue

            qbo_inv = QBOInvoice()
            qbo_inv.CustomerRef = {"value": cust_map.qbo_id}

            if inv.invoice_number:
                qbo_inv.DocNumber = inv.invoice_number
            if inv.date:
                qbo_inv.TxnDate = inv.date.isoformat()
            if inv.due_date:
                qbo_inv.DueDate = inv.due_date.isoformat()

            # Build line items
            lines = []
            inv_lines = (
                db.query(InvoiceLine)
                .filter(InvoiceLine.invoice_id == inv.id)
                .order_by(InvoiceLine.line_order)
                .all()
            )

            for inv_line in inv_lines:
                detail = {
                    "Qty": float(inv_line.quantity or 1),
                    "UnitPrice": float(inv_line.rate or 0),
                }

                # Link item if mapped
                if inv_line.item_id:
                    item_map = get_mapping_by_slowbooks_id(db, "item", inv_line.item_id)
                    if item_map:
                        detail["ItemRef"] = {"value": item_map.qbo_id}

                line = {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": float(inv_line.amount or 0),
                    "Description": inv_line.description or "",
                    "SalesItemLineDetail": detail,
                }
                lines.append(line)

            qbo_inv.Line = lines

            if inv.notes:
                qbo_inv.CustomerMemo = {"value": inv.notes}

            saved = qbo_inv.save(qb=client)
            create_mapping(
                db, "invoice", inv.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append({"entity": "invoice", "id": inv.id, "message": str(e)})

    return {"exported": exported, "errors": errors}


def export_payments(db: Session) -> dict:
    """Export Slowbooks payments to QBO."""
    from quickbooks.objects.payment import Payment as QBOPayment

    client = get_qbo_client(db)
    exported = 0
    errors = []

    payments = db.query(Payment).all()

    for pmt in payments:
        try:
            if get_mapping_by_slowbooks_id(db, "payment", pmt.id):
                continue

            # Customer must be mapped
            cust_map = get_mapping_by_slowbooks_id(db, "customer", pmt.customer_id)
            if not cust_map:
                errors.append(
                    {
                        "entity": "payment",
                        "id": pmt.id,
                        "message": f"Customer {pmt.customer_id} not mapped to QBO",
                    }
                )
                continue

            qbo_pmt = QBOPayment()
            qbo_pmt.CustomerRef = {"value": cust_map.qbo_id}
            qbo_pmt.TotalAmt = float(pmt.amount)
            if pmt.date:
                qbo_pmt.TxnDate = pmt.date.isoformat()
            if pmt.reference:
                qbo_pmt.PaymentRefNum = pmt.reference

            # Link deposit account
            if pmt.deposit_to_account_id:
                acct_map = get_mapping_by_slowbooks_id(
                    db, "account", pmt.deposit_to_account_id
                )
                if acct_map:
                    qbo_pmt.DepositToAccountRef = {"value": acct_map.qbo_id}

            # Build payment lines from allocations
            allocations = (
                db.query(PaymentAllocation)
                .filter(PaymentAllocation.payment_id == pmt.id)
                .all()
            )

            lines = []
            for alloc in allocations:
                inv_map = get_mapping_by_slowbooks_id(db, "invoice", alloc.invoice_id)
                if inv_map:
                    lines.append(
                        {
                            "Amount": float(alloc.amount),
                            "LinkedTxn": [
                                {
                                    "TxnId": inv_map.qbo_id,
                                    "TxnType": "Invoice",
                                }
                            ],
                        }
                    )

            if lines:
                qbo_pmt.Line = lines

            saved = qbo_pmt.save(qb=client)
            create_mapping(
                db, "payment", pmt.id, saved.Id, getattr(saved, "SyncToken", None)
            )
            exported += 1

        except Exception as e:
            errors.append({"entity": "payment", "id": pmt.id, "message": str(e)})

    return {"exported": exported, "errors": errors}


# ============================================================================
# Master export orchestrator
# ============================================================================


def export_all(db: Session) -> dict:
    """Export all entity types to QBO in dependency order.

    Returns counts of exported records and any errors.
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

    r = export_accounts(db)
    result["accounts"] = r["exported"]
    result["errors"].extend(r["errors"])

    r = export_customers(db)
    result["customers"] = r["exported"]
    result["errors"].extend(r["errors"])

    r = export_vendors(db)
    result["vendors"] = r["exported"]
    result["errors"].extend(r["errors"])

    r = export_items(db)
    result["items"] = r["exported"]
    result["errors"].extend(r["errors"])

    r = export_invoices(db)
    result["invoices"] = r["exported"]
    result["errors"].extend(r["errors"])

    r = export_payments(db)
    result["payments"] = r["exported"]
    result["errors"].extend(r["errors"])

    db.commit()
    return result

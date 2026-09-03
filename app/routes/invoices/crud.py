from decimal import Decimal

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.contacts import Customer
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
    _q,
)
from app.services.numbering import next_invoice_number
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router
from app.routes.invoices.helpers import (
    _due_date_from_terms,
    _compute_totals,
    _build_invoice_journal_lines,
    _reverse_and_delete_journal,
)


@router.get("", response_model=list[InvoiceResponse])
def list_invoices(
    status: str = None,
    customer_id: int = None,
    is_sales_receipt: bool = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load customer (used for customer_name) and lines (in the
    # response model). Without these, returning 500 invoices triggered
    # 1001 extra queries — one per .customer access and one per .lines
    # access during model_validate.
    q = db.query(Invoice).options(
        joinedload(Invoice.customer),
        selectinload(Invoice.lines),
    )
    if status:
        q = q.filter(Invoice.status == status)
    if customer_id:
        q = q.filter(Invoice.customer_id == customer_id)
    if is_sales_receipt is not None:
        q = q.filter(Invoice.is_sales_receipt == is_sales_receipt)
    invoices = q.order_by(Invoice.date.desc()).offset(skip).limit(limit).all()
    results = []
    for inv in invoices:
        resp = InvoiceResponse.model_validate(inv)
        if inv.customer:
            resp.customer_name = inv.customer.name
        results.append(resp)
    return results


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    resp = InvoiceResponse.model_validate(inv)
    if inv.customer:
        resp.customer_name = inv.customer.name
    return resp


@router.post("", response_model=InvoiceResponse, status_code=201)
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Parse terms for due date (explicit due_date wins; else derive from terms)
    due_date = data.due_date or _due_date_from_terms(data.date, data.terms)
    subtotal, tax_amount, total = _compute_totals(data.lines, data.tax_rate)

    # Capture every customer field we need post-flush, because we may have to
    # rollback the session (which expires `customer`) when two concurrent
    # requests both compute MAX+1 and race for the same invoice number.
    cust_id = customer.id
    cust_name = customer.name
    cust_fields = {
        "bill_address1": data.bill_address1 or customer.bill_address1,
        "bill_address2": data.bill_address2 or customer.bill_address2,
        "bill_city": data.bill_city or customer.bill_city,
        "bill_state": data.bill_state or customer.bill_state,
        "bill_zip": data.bill_zip or customer.bill_zip,
        "ship_address1": data.ship_address1 or customer.ship_address1,
        "ship_address2": data.ship_address2 or customer.ship_address2,
        "ship_city": data.ship_city or customer.ship_city,
        "ship_state": data.ship_state or customer.ship_state,
        "ship_zip": data.ship_zip or customer.ship_zip,
    }

    invoice = None
    invoice_number = None
    last_err = None
    # Retry the number assignment a few times — next_invoice_number is just
    # MAX+1 (no row-level lock), so two concurrent creates can both compute
    # the same number and one will hit the invoices.invoice_number UNIQUE
    # constraint at flush. The constraint is the safety net; this is the UX.
    from app.services.currency import convert_lines, resolve_rate

    doc_currency, doc_rate = resolve_rate(db, data.currency, data.exchange_rate)

    for _ in range(10):
        invoice_number = next_invoice_number(db)
        invoice = Invoice(
            invoice_number=invoice_number,
            currency=doc_currency,
            exchange_rate=doc_rate,
            customer_id=cust_id,
            date=data.date,
            due_date=due_date,
            terms=data.terms,
            po_number=data.po_number,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            balance_due=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            **cust_fields,
        )
        db.add(invoice)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "invoice_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            invoice = None
    if invoice is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique invoice number after several "
            "retries; please retry the request.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = InvoiceLine(
            invoice_id=invoice.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            class_name=line_data.class_name,
            job_id=line_data.job_id,
            class_id=line_data.class_id,
            line_order=line_data.line_order or i,
        )
        db.add(line)

    # ================================================================
    # Journal Entry
    # DR  Accounts Receivable (1100)     total
    # CR  Income per line item           line amount
    # CR  Sales Tax Payable (2200)       tax amount (if any)
    # ================================================================
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)

    if ar_id and default_income_id:
        journal_lines = []
        # Debit A/R for total
        journal_lines.append(
            {
                "account_id": ar_id,
                "debit": Decimal(str(total)),
                "credit": Decimal("0"),
                "description": f"Invoice #{invoice_number}",
            }
        )
        # Credit income for each line (use item's income account or default).
        # Round per line to match the rounded A/R debit (see helper above) —
        # otherwise sub-cent rates produce an unbalanced JE and a 500.
        for line_data in data.lines:
            line_amount = _q(
                Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
            )
            if line_amount == 0:
                continue
            income_id = default_income_id
            if line_data.item_id:
                item = db.query(Item).filter(Item.id == line_data.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            journal_lines.append(
                {
                    "account_id": income_id,
                    "debit": Decimal("0"),
                    "credit": line_amount,
                    "description": line_data.description or "",
                    "job_id": line_data.job_id,
                    "class_id": line_data.class_id,
                }
            )
        # Credit sales tax if any
        if tax_amount > 0 and tax_account_id:
            journal_lines.append(
                {
                    "account_id": tax_account_id,
                    "debit": Decimal("0"),
                    "credit": Decimal(str(tax_amount)),
                    "description": "Sales tax",
                }
            )

        txn = create_journal_entry(
            db,
            data.date,
            f"Invoice #{invoice_number} - {cust_name}",
            convert_lines(journal_lines, doc_rate),
            source_type="invoice",
            source_id=invoice.id,
            class_id=invoice.class_id,
            job_id=invoice.job_id,
            reference=invoice_number,
        )
        invoice.transaction_id = txn.id

    # ---- Phase 11: inventory/COGS posting ----
    # For each line with an inventory-tracked item, decrement qty and post
    # a DR COGS / CR Inventory journal at the current weighted-avg cost.
    # Services/labor/non-inventory items skip this entirely.
    from app.services.inventory_service import record_sale

    for line_data in data.lines:
        if not line_data.item_id:
            continue
        item = db.query(Item).filter(Item.id == line_data.item_id).first()
        if item and item.track_inventory:
            record_sale(
                db,
                item,
                quantity=Decimal(str(line_data.quantity)),
                source_type="invoice",
                source_id=invoice.id,
                memo=f"Invoice #{invoice_number}",
                txn_date=data.date,
            )

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    resp.customer_name = cust_name
    return resp


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, data: InvoiceUpdate, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit voided invoice")
    check_closing_date(db, invoice.date)

    update_data = data.model_dump(exclude_unset=True, exclude={"lines"})
    tax_rate_changed = "tax_rate" in update_data
    for key, val in update_data.items():
        setattr(invoice, key, val)

    # The SPA always sends due_date now (a field added in the UX pass). An
    # explicitly-cleared field arrives as null, which exclude_unset lets
    # through — without this, clearing the box would wipe the stored due
    # date to NULL. Mirror the create path: derive it from terms + date.
    if "due_date" in update_data and invoice.due_date is None:
        invoice.due_date = _due_date_from_terms(invoice.date, invoice.terms)

    # Recompute totals + journal whenever anything that affects them changes
    # (line list, tax rate, or both). Previously only lines triggered recompute,
    # which left totals stale after a tax-rate-only edit.
    needs_recompute = data.lines is not None or tax_rate_changed
    if needs_recompute:
        # Phase 11 (audit fix): snapshot the OLD lines before we rebuild so
        # we can post compensating inventory movements for the delta.
        from app.services.inventory_hooks import (
            snapshot_invoice_lines,
            reconcile_invoice_inventory_delta,
        )

        old_line_snapshot = (
            snapshot_invoice_lines(invoice) if data.lines is not None else None
        )

        if data.lines is not None:
            db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id).delete()
            db.flush()
            for i, line_data in enumerate(data.lines):
                db.add(
                    InvoiceLine(
                        invoice_id=invoice_id,
                        item_id=line_data.item_id,
                        description=line_data.description,
                        quantity=line_data.quantity,
                        rate=line_data.rate,
                        amount=_q(
                            Decimal(str(line_data.quantity))
                            * Decimal(str(line_data.rate))
                        ),
                        class_name=line_data.class_name,
                        line_order=line_data.line_order or i,
                    )
                )
            db.flush()
            # Reload so invoice.lines reflects the new rows
            db.refresh(invoice)
            effective_lines = data.lines
        else:
            # Tax-rate-only edit: keep existing lines but recompute amounts.
            effective_lines = list(invoice.lines)

        tax_rate = data.tax_rate if data.tax_rate is not None else invoice.tax_rate
        subtotal, tax_amount, total = _compute_totals(effective_lines, tax_rate)
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_amount
        invoice.total = total
        # Clamp at 0: if an invoice is edited to a smaller total than the amount
        # already paid, don't let balance_due go negative.
        invoice.balance_due = max(total - invoice.amount_paid, Decimal("0"))

        # Sync journal entry
        if invoice.transaction_id:
            ar_id = get_ar_account_id(db)
            default_income_id = get_default_income_account_id(db)
            tax_account_id = get_sales_tax_account_id(db)

            if ar_id and default_income_id:
                _reverse_and_delete_journal(db, invoice.transaction_id)
                new_journal_lines = _build_invoice_journal_lines(
                    db,
                    total,
                    tax_amount,
                    tax_account_id,
                    ar_id,
                    default_income_id,
                    effective_lines,
                    invoice.invoice_number,
                )
                # Rebuild txn lines under the same transaction_id
                from app.models.transactions import Transaction, TransactionLine

                txn = (
                    db.query(Transaction)
                    .filter(Transaction.id == invoice.transaction_id)
                    .first()
                )
                if txn:
                    txn.description = f"Invoice #{invoice.invoice_number} - {invoice.customer.name if invoice.customer else ''}"
                for jl in new_journal_lines:
                    debit = Decimal(str(jl.get("debit", 0)))
                    credit = Decimal(str(jl.get("credit", 0)))
                    if debit == 0 and credit == 0:
                        continue
                    db.add(
                        TransactionLine(
                            transaction_id=invoice.transaction_id,
                            account_id=jl["account_id"],
                            debit=debit,
                            credit=credit,
                            description=jl.get("description", ""),
                        )
                    )
                    account = (
                        db.query(Account).filter(Account.id == jl["account_id"]).first()
                    )
                    if account:
                        if account.account_type.value in ("asset", "expense", "cogs"):
                            account.balance += debit - credit
                        else:
                            account.balance += credit - debit

        # Phase 11 (audit fix): post compensating inventory movements for
        # lines that changed. No-op if nothing was inventory-tracked.
        if old_line_snapshot is not None:
            reconcile_invoice_inventory_delta(
                db,
                invoice,
                old_line_snapshot,
                txn_date=invoice.date,
            )

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp

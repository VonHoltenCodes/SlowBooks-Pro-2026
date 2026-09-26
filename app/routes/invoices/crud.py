from decimal import Decimal
from types import SimpleNamespace

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.contacts import Customer
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from app.services.accounting import (
    _q,
)
from app.services.numbering import next_invoice_number
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router
from app.routes.invoices.helpers import (
    confirm_zero_total,
    opening_status,
    refuse_due_before_date,
    resolve_line_taxable,
    _due_date_from_terms,
    _compute_totals,
    _post_invoice_journal,
    _reverse_and_delete_journal,
)
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db


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


def _check_fair_value(fair_value_amount, total) -> None:
    """A donation receipt's fair-value-of-goods can't exceed the gift."""
    if fair_value_amount is None:
        return
    fv = Decimal(str(fair_value_amount))
    if fv < 0 or fv > Decimal(str(total)):
        raise HTTPException(
            status_code=400,
            detail="Fair value of goods or services must be between 0 and the total",
        )


@router.post("", response_model=InvoiceResponse, status_code=201)
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    words = terms_from_db(db)
    check_closing_date(db, data.date)
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Parse terms for due date (explicit due_date wins; else derive from terms)
    due_date = data.due_date or _due_date_from_terms(data.date, data.terms)
    refuse_due_before_date(data.date, due_date)
    resolve_line_taxable(db, data.lines, customer)
    subtotal, tax_amount, total = _compute_totals(data.lines, data.tax_rate)
    confirm_zero_total(
        total,
        data.allow_zero_total,
        document_label(SimpleNamespace(is_pledge=data.is_pledge), words).lower(),
    )
    _check_fair_value(data.fair_value_amount, total)

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
    from app.services.currency import resolve_rate

    doc_currency, doc_rate = resolve_rate(db, data.currency, data.exchange_rate)

    for _ in range(10):
        invoice_number = next_invoice_number(db)
        invoice = Invoice(
            invoice_number=invoice_number,
            currency=doc_currency,
            exchange_rate=doc_rate,
            customer_id=cust_id,
            status=opening_status(total),
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
            is_pledge=data.is_pledge,
            fair_value_amount=data.fair_value_amount,
            fair_value_description=data.fair_value_description,
            **cust_fields,
        )
        face = document_label(invoice, words)
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
            cost_code_id=line_data.cost_code_id,
            is_taxable=(
                line_data.is_taxable if line_data.is_taxable is not None else True
            ),
            line_order=line_data.line_order or i,
        )
        db.add(line)

    txn = _post_invoice_journal(db, invoice, data.lines, cust_name)
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
                memo=document_reference(face, invoice_number),
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

    update_data = data.model_dump(
        exclude_unset=True, exclude={"lines", "allow_zero_total"}
    )
    # Checked against the dates the invoice will have after this edit; a
    # cleared due date is derived from the terms below, so it cannot be early.
    refuse_due_before_date(
        update_data.get("date") or invoice.date,
        update_data.get("due_date", invoice.due_date),
    )
    status_requested = "status" in update_data
    requested_status = update_data.pop("status", None)
    if status_requested and requested_status == InvoiceStatus.VOID:
        raise HTTPException(
            status_code=400,
            detail=(
                "An invoice is voided with its void action, not by editing its "
                f"status: use POST /api/invoices/{invoice_id}/void"
            ),
        )
    if status_requested and requested_status is None:
        raise HTTPException(
            status_code=400,
            detail="status cannot be empty; leave it out to keep the invoice's status",
        )
    date_changed = data.date is not None and data.date != invoice.date

    if "currency" in update_data or "exchange_rate" in update_data:
        from app.services.currency import resolve_rate

        currency = update_data.get("currency", invoice.currency)
        # A new currency cannot inherit the previous currency's rate. Reuse
        # CREATE's resolution, including rate 1 for the home currency.
        rate = update_data.get(
            "exchange_rate",
            invoice.exchange_rate if currency == invoice.currency else None,
        )
        update_data["currency"], update_data["exchange_rate"] = resolve_rate(
            db, currency, rate
        )

    repost_fields = {"tax_rate", "currency", "exchange_rate", "class_id", "job_id"}
    needs_recompute = data.lines is not None or any(
        key in update_data and update_data[key] != getattr(invoice, key)
        for key in repost_fields
    )
    if needs_recompute:
        # Validate the proposed total before changing headers, lines or journals.
        if data.lines is not None:
            customer = db.get(
                Customer, update_data.get("customer_id", invoice.customer_id)
            )
            resolve_line_taxable(db, data.lines, customer)
            effective_lines = data.lines
        else:
            effective_lines = list(invoice.lines)
        tax_rate = data.tax_rate if data.tax_rate is not None else invoice.tax_rate
        subtotal, tax_amount, total = _compute_totals(effective_lines, tax_rate)
        confirm_zero_total(
            total,
            data.allow_zero_total,
            document_label(invoice, terms_from_db(db)).lower(),
        )
        if total < invoice.amount_paid:
            raise HTTPException(
                status_code=400,
                detail="Invoice total cannot be less than the amount already paid",
            )

    if needs_recompute or date_changed:
        from sqlalchemy import and_, or_
        from app.models.transactions import Transaction
        from app.services.bank_posting import assert_not_reconciled

        postings = (
            db.query(Transaction)
            .filter(
                or_(
                    Transaction.id == invoice.transaction_id,
                    and_(
                        Transaction.source_type.in_(("invoice", "invoice_edit")),
                        Transaction.source_id == invoice.id,
                    ),
                )
            )
            .all()
        )
        # Check every affected posting before mutating any invoice/journal state.
        for posting in postings:
            assert_not_reconciled(posting)
        if date_changed:
            check_closing_date(db, data.date)
            for posting in postings:
                check_closing_date(db, posting.date)
            for posting in postings:
                posting.date = data.date

    for key, val in update_data.items():
        if key == "is_pledge" and val is None:
            continue
        setattr(invoice, key, val)

    # The SPA always sends due_date now (a field added in the UX pass). An
    # explicitly-cleared field arrives as null, which exclude_unset lets
    # through — without this, clearing the box would wipe the stored due
    # date to NULL. Mirror the create path: derive it from terms + date.
    if "due_date" in update_data and invoice.due_date is None:
        invoice.due_date = _due_date_from_terms(invoice.date, invoice.terms)

    # Date changes synchronize owned headers above without replacing splits.
    # Other accounting changes reuse the shared invoice posting authority.
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
                        class_id=line_data.class_id,
                        job_id=line_data.job_id,
                        cost_code_id=line_data.cost_code_id,
                        is_taxable=(
                            line_data.is_taxable
                            if line_data.is_taxable is not None
                            else True
                        ),
                        line_order=line_data.line_order or i,
                    )
                )
            db.flush()
            # Reload so invoice.lines reflects the new rows
            db.refresh(invoice)
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_amount
        invoice.total = total
        invoice.balance_due = total - invoice.amount_paid

        # Keep the existing journal identity while reusing create's posting path.
        if invoice.transaction_id:
            from app.models.transactions import Transaction

            txn = db.get(Transaction, invoice.transaction_id)
            if txn:
                _reverse_and_delete_journal(db, txn.id)
                _post_invoice_journal(
                    db,
                    invoice,
                    effective_lines,
                    invoice.customer.name if invoice.customer else "",
                    existing_transaction=txn,
                )

        # Phase 11 (audit fix): post compensating inventory movements for
        # lines that changed. No-op if nothing was inventory-tracked.
        if old_line_snapshot is not None:
            reconcile_invoice_inventory_delta(
                db,
                invoice,
                old_line_snapshot,
                txn_date=invoice.date,
            )

    # One payment-derived decision for total edits and status-only requests.
    # Unpaid invoices retain the explicitly supported draft/sent lifecycle.
    if needs_recompute or status_requested:
        if invoice.balance_due == 0 and invoice.amount_paid >= invoice.total:
            invoice.status = InvoiceStatus.PAID
        elif invoice.amount_paid > 0:
            invoice.status = InvoiceStatus.PARTIAL
        elif requested_status in (InvoiceStatus.DRAFT, InvoiceStatus.SENT):
            invoice.status = requested_status
        elif invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.SENT):
            invoice.status = InvoiceStatus.SENT

    _check_fair_value(invoice.fair_value_amount, invoice.total)
    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    return resp

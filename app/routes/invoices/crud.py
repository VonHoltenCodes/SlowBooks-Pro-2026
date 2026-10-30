from decimal import Decimal
from types import SimpleNamespace

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.routes._helpers import clamp_pagination, reject_null_updates
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.contacts import Customer
from app.services.sub_customers import INVOICE_ADDRESS_FIELDS, invoice_addresses
from app.schemas.invoices import InvoiceCreate, InvoiceUpdate, InvoiceResponse
from app.services.accounting import (
    _q,
)
from app.services.numbering import next_invoice_number
from app.services.payment_terms import (
    DocumentTerms,
    document_due_date,
    link_terms,
)
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router
from app.services.contact_balances import home_amount
from app.routes.invoices.helpers import (
    invoice_customer_name,
    confirm_credit_limit,
    confirm_zero_total,
    opening_status,
    refuse_due_before_date,
    resolve_line_taxable,
    _due_date_from_terms,
    _compute_totals,
    _post_invoice_journal,
    kept_tax,
    refuse_moving_money,
    refuse_negative_lines,
    _reverse_and_delete_journal,
    warn_or_refuse_oversell,
)
from app.services.donor_documents import document_label
from app.services.jobs_service import (
    refuse_other_customers_jobs,
    refuse_other_customers_jobs_on_edit,
)
from app.services.terminology import document_reference, terms_from_db
from app.services.audit import audited_bulk_delete


@router.get("", response_model=list[InvoiceResponse])
def list_invoices(
    status: str = None,
    customer_id: int = None,
    is_sales_receipt: bool = None,
    open_only: bool = False,
    billed_to: bool = False,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Newest first, a page at a time (500 by default, at most 1,000).
    open_only: the invoices money can still be applied to (draft, sent or
    partial, with a balance due) — what Receive Payment, Batch Payments
    and the credit screens offer. Filtered here, so an old open invoice is
    never lost behind the newest page of paid ones (issue #191).
    billed_to, with customer_id: the invoices billed to that customer, its
    own and those of sub-customers billed with it (#638), what Receive
    Payment offers a property."""
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
    if open_only:
        q = q.filter(
            Invoice.status.in_(
                (InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PARTIAL)
            ),
            Invoice.balance_due > 0,
        )
    if customer_id and billed_to:
        from app.services.sub_customers import billed_subs

        q = q.filter(
            Invoice.customer_id.in_(
                [customer_id] + [s.id for s in billed_subs(db, customer_id)]
            )
        )
    elif customer_id:
        q = q.filter(Invoice.customer_id == customer_id)
    if is_sales_receipt is not None:
        q = q.filter(Invoice.is_sales_receipt == is_sales_receipt)
    invoices = (
        q.order_by(Invoice.date.desc(), Invoice.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    results = []
    terms_reader = DocumentTerms(db, "invoice")
    for inv in invoices:
        resp = InvoiceResponse.model_validate(inv)
        terms_reader.fill(resp, inv)
        if inv.customer:
            # "Parent: Sub" for a sub-customer's, as the pickers say it
            resp.customer_name = invoice_customer_name(inv)
        results.append(resp)
    return results


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    resp = InvoiceResponse.model_validate(inv)
    DocumentTerms(db, "invoice").fill(resp, inv)
    if inv.customer:
        resp.customer_name = invoice_customer_name(inv)
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
    noun = document_label(SimpleNamespace(is_pledge=data.is_pledge), words).lower()
    # The job, on the header or a line, is this customer's (NEW-36).
    refuse_other_customers_jobs(db, customer.id, data.job_id, data.lines, noun, words)

    # Parse terms for due date (explicit due_date wins; else derive from terms)
    due_date = data.due_date or _due_date_from_terms(db, data.date, data.terms)
    refuse_due_before_date(data.date, due_date)
    refuse_negative_lines(db, data.lines)
    resolve_line_taxable(db, data.lines, customer)
    subtotal, tax_amount, total = _compute_totals(data.lines, data.tax_rate)
    confirm_zero_total(total, data.allow_zero_total, noun)
    _check_fair_value(data.fair_value_amount, total)

    # Capture every customer field we need post-flush, because we may have to
    # rollback the session (which expires `customer`) when two concurrent
    # requests both compute MAX+1 and race for the same invoice number.
    cust_id = customer.id
    cust_name = customer.full_name
    # The Bill To whole is the payer's (#638; the parent of a sub-customer
    # billed with it), its street with its name and country, and the
    # ship-to the customer's, its country with it; an address part the
    # request gives a value wins. One left out, or sent empty, is the
    # customer's, as it always was on create (an edit's "" blanks a part).
    given = {k: getattr(data, k) for k in INVOICE_ADDRESS_FIELDS}
    cust_fields = invoice_addresses(db, customer, {k: v for k, v in given.items() if v})

    invoice = None
    invoice_number = None
    last_err = None
    # Retry the number assignment a few times — next_invoice_number is just
    # MAX+1 (no row-level lock), so two concurrent creates can both compute
    # the same number and one will hit the invoices.invoice_number UNIQUE
    # constraint at flush. The constraint is the safety net; this is the UX.
    from app.services.currency import resolve_rate

    doc_currency, doc_rate = resolve_rate(db, data.currency, data.exchange_rate)
    # Past the customer's credit limit? Ask, on the API as in the form
    # (#256). A sales receipt, paid as it is made, comes through here with
    # the flag set by its own route: it owes nothing.
    confirm_credit_limit(
        db, customer, home_amount(total, doc_rate), data.allow_over_credit_limit, noun
    )
    # More of a stock item than is on hand (#642): said in the response,
    # or refused here when Settings says so. Nothing is written yet.
    stock_warnings = warn_or_refuse_oversell(db, data.lines)

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
            line_order=i,
        )
        db.add(line)

    txn = _post_invoice_journal(db, invoice, data.lines, cust_name)
    # A $0.00 invoice posts no journal, so it keeps transaction_id NULL.
    if txn is not None:
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
    DocumentTerms(db, "invoice").fill(resp, invoice)
    resp.customer_name = cust_name
    resp.warnings = stock_warnings
    return resp


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, data: InvoiceUpdate, db: Session = Depends(get_db)):
    """Edit an invoice: what the request sends changes, what it leaves out
    stays as stored.

    The addresses (2.31.1): a bill or ship address part sent, or
    ``ship_to_country``, is what the invoice gets, "" or null blanking it.
    The Bill To's name and country stay the ones the invoice was billed
    with. An edit that moves the invoice to another customer takes the
    new customer's addresses as create does, the payer's whole Bill To
    (street, name and country) and the customer's own Ship To with its
    country, and a part sent with the move wins over the one copied, ""
    or null blanking it. The invoice form sends no address part: its save
    keeps the stored addresses, and a change of customer there takes the
    new customer's. A move is refused (409 ``money_applied``) while a
    payment or credit memo is applied to the invoice, naming each; an
    unpaid invoice moves."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit voided invoice")
    check_closing_date(db, invoice.date)
    words = terms_from_db(db)
    # A pay application's invoice carries the retainage leg (1150); an edit here
    # would repost it without that leg, so only the notes change (#576).
    if invoice.pay_application_id is not None:
        other = set(data.model_fields_set) - {"notes"}
        if other:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This invoice comes from a payment application; change the "
                    "application instead. Only the notes can be edited here."
                ),
            )

    update_data = reject_null_updates(
        Invoice,
        data.model_dump(exclude_unset=True, exclude={"lines", "allow_zero_total"}),
    )
    # a discount taken under its terms and date keeps them (#664)
    from app.services.early_pay_discounts import refuse_moving_its_terms

    refuse_moving_its_terms(db, invoice, update_data, "invoice")
    if update_data.get("customer_id") not in (None, invoice.customer_id):
        moved_to = db.get(Customer, update_data["customer_id"])
        if moved_to is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        # The money on an invoice stays with its customer (round 3). Read
        # under the row lock a payment takes: a payment landing now is
        # either seen here or waits for the move.
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == invoice_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
        refuse_moving_money(db, invoice, moved_to, words)
    # What the edit changes — the customer, the header's job, a line's job —
    # must leave the invoice on its customer's jobs (NEW-36); what it leaves
    # alone is not judged, so a note saved over a job the user didn't touch
    # (books from before the rule) goes through, from the form too, which
    # sends every field as stored.
    refuse_other_customers_jobs_on_edit(
        db,
        invoice,
        update_data,
        data.lines,
        document_label(invoice, words).lower(),
        words,
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
    stock_warnings: list[str] = []

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

    repost_fields = {
        "tax_rate",
        "currency",
        "exchange_rate",
        "class_id",
        "job_id",
        "customer_id",
    }
    needs_recompute = data.lines is not None or any(
        key in update_data and update_data[key] != getattr(invoice, key)
        for key in repost_fields
    )
    if needs_recompute:
        # Validate the proposed total before changing headers, lines or journals.
        if data.lines is not None:
            refuse_negative_lines(db, data.lines, invoice)
            customer = db.get(
                Customer, update_data.get("customer_id", invoice.customer_id)
            )
            resolve_line_taxable(db, data.lines, customer)
            effective_lines = data.lines
        else:
            effective_lines = list(invoice.lines)
            new_customer_id = update_data.get("customer_id")
            if new_customer_id not in (None, invoice.customer_id):
                # A new customer without resending the lines: a non-taxable
                # customer's exemption covers the stored lines too, as it
                # does on create (found integrating the 2.17.3 fixes).
                resolve_line_taxable(
                    db, effective_lines, db.get(Customer, new_customer_id)
                )
        tax_rate = data.tax_rate if data.tax_rate is not None else invoice.tax_rate
        subtotal, tax_amount, total = _compute_totals(effective_lines, tax_rate)
        kept = kept_tax(invoice, tax_rate, effective_lines)
        if kept is not None:
            tax_amount, total = kept, _q(subtotal + kept)
        confirm_zero_total(
            total, data.allow_zero_total, document_label(invoice, words).lower()
        )
        if total < invoice.amount_paid:
            from app.services.early_pay_discounts import discounted_on

            part = discounted_on(db, invoice, "invoice")
            raise HTTPException(
                status_code=400,
                detail="Invoice total cannot be less than the amount already paid"
                + (f" (${invoice.amount_paid:,.2f}{part})" if part else ""),
            )
        # An edit that raises what this invoice leaves owing, or moves it to
        # another customer, asks about the credit limit as a new invoice
        # does (#256); one that lowers it never does, even over the limit.
        new_rate = update_data.get("exchange_rate", invoice.exchange_rate)
        new_owed = home_amount(_q(total - invoice.amount_paid), new_rate)
        now_owed = home_amount(invoice.balance_due, invoice.exchange_rate)
        new_customer_id = update_data.get("customer_id", invoice.customer_id)
        if new_owed > now_owed or new_customer_id != invoice.customer_id:
            confirm_credit_limit(
                db,
                db.get(Customer, new_customer_id),
                new_owed,
                data.allow_over_credit_limit,
                document_label(invoice, words).lower(),
                exclude_invoice_id=(
                    invoice.id if new_customer_id == invoice.customer_id else None
                ),
            )
        # New lines that take a stock item past what is on hand (#642):
        # only what the edit adds counts, since the stored lines already
        # came out of stock. Said in the response, or refused when Settings
        # says so; still before anything is written.
        if data.lines is not None:
            stock_warnings = warn_or_refuse_oversell(
                db, data.lines, before=list(invoice.lines)
            )

    # An invoice or sales receipt the QuickBooks Online import created
    # becomes ours when its amounts or date change: the import's posting for
    # it is reversed here, and it gets a posting of its own below, so A/R is
    # never counted twice. An edit of its words alone leaves it as it is.
    from app.services import qbo_documents

    adopting = (needs_recompute or date_changed) and qbo_documents.adopt_invoice(
        db, invoice
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

    previous_customer_id = invoice.customer_id
    for key, val in update_data.items():
        if key == "is_pledge" and val is None:
            continue
        setattr(invoice, key, val)
    # New terms tie the invoice to the entry they name now (#664).
    if "terms" in update_data:
        link_terms(db, invoice)
    # Moved to another customer: the Bill To whole is the new customer's
    # payer's as of now, as create makes it, the street with the name and
    # country, and the ship-to the new customer's with its country; an
    # address part sent with the move wins over the one copied, "" or null
    # blanking it (2.31.1 gate, W-59, W-72; round 3). An edit that keeps
    # the customer keeps the Bill To the invoice was made with, but for the
    # address parts it sends (set above).
    if invoice.customer_id != previous_customer_id:
        sent = {k: v for k, v in update_data.items() if k in INVOICE_ADDRESS_FIELDS}
        moved_to = db.get(Customer, invoice.customer_id)
        for key, val in invoice_addresses(db, moved_to, sent).items():
            setattr(invoice, key, val)

    # The SPA always sends due_date now (a field added in the UX pass). An
    # explicitly-cleared field arrives as null, which exclude_unset lets
    # through — without this, clearing the box would wipe the stored due
    # date to NULL. Mirror the create path: derive it from terms + date.
    if "due_date" in update_data and invoice.due_date is None:
        invoice.due_date = document_due_date(db, invoice)

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
            # each old line leaves a DELETE event with its amounts (#289)
            audited_bulk_delete(
                db, db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice_id)
            )
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
                        line_order=i,
                    )
                )
            db.flush()
            # Reload so invoice.lines reflects the new rows
            db.refresh(invoice)
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_amount
        invoice.total = total
        invoice.balance_due = total - invoice.amount_paid

        # Keep the existing journal identity while reusing create's posting
        # path. An edit crossing $0 in either direction is handled: an invoice
        # that had no journal (a $0 one) gets a fresh one when it gains a
        # total, and one edited down to $0 drops its now-empty header rather
        # than leave a husk behind. An invoice the QBO import created is
        # posted once, by the adopting branch below — posting it here too
        # would count its A/R twice.
        if not adopting:
            from app.models.transactions import Transaction

            old_txn = (
                db.get(Transaction, invoice.transaction_id)
                if invoice.transaction_id
                else None
            )
            if old_txn:
                _reverse_and_delete_journal(db, old_txn.id)
            new_txn = _post_invoice_journal(
                db,
                invoice,
                effective_lines,
                invoice_customer_name(invoice) or "",
                existing_transaction=old_txn,
            )
            if new_txn is None and old_txn is not None:
                db.delete(old_txn)
            invoice.transaction_id = new_txn.id if new_txn is not None else None

        # Phase 11 (audit fix): post compensating inventory movements for
        # lines that changed. No-op if nothing was inventory-tracked.
        if old_line_snapshot is not None:
            reconcile_invoice_inventory_delta(
                db,
                invoice,
                old_line_snapshot,
                txn_date=invoice.date,
            )

        # The payments on this invoice recognized its income as it stood when
        # they arrived; rewrite that from the lines as they now stand (#483).
        if not adopting and invoice.transaction_id is not None:
            from app.services import cash_basis

            cash_basis.recompute_invoice_cash_lines(db, invoice)

    if adopting:
        txn = _post_invoice_journal(
            db,
            invoice,
            list(invoice.lines),
            invoice_customer_name(invoice) or "",
            balance_on_income=True,
        )
        invoice.transaction_id = txn.id if txn is not None else None
        qbo_documents.post_adopted_cash_lines(db, invoice)

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
    DocumentTerms(db, "invoice").fill(resp, invoice)
    if invoice.customer:
        resp.customer_name = invoice_customer_name(invoice)
    resp.warnings = stock_warnings
    return resp

# ============================================================================
# Sales tax exemption (#864): the job's certificate, the document's exempt
# switch and its own copy of the certificate, and the reason an exempt sale
# is exempt.
#
# Three things say a sale is untaxed, in this order:
#   1. the customer is marked not taxable: never charged, whatever else says;
#   2. the document's own "Tax exempt" switch (estimates, invoices, sales
#      receipts), which starts as its job is (Job.is_taxable), else as its
#      customer is;
#   3. a line's own Tax box, as before.
# The first two untick every line; the third is a person's choice per line.
#
# The certificate lives on the job: an exempt job needs its number, state
# and kind (the expiry is optional), and every exempt job takes its own, so
# nothing is kept for a customer who never comes back. A document made
# exempt copies its job's certificate into its own columns; editing or
# clearing the job's later never changes that copy, so an old PDF and an old
# report reason stay as they were.
# ============================================================================

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

# What a certificate exempts the buyer as, and how the job form, the PDF and
# the Sales Tax report name each.
CERTIFICATE_KINDS = ("public_entity", "nonprofit", "resale", "other")
CERTIFICATE_KIND_LABELS = {
    "resale": "Resale",
    "public_entity": "Public entity",
    "nonprofit": "Nonprofit",
    "other": "Other exemption",
}

# The certificate's four columns, the same names on jobs, estimates and
# invoices.
CERTIFICATE_COLUMNS = (
    "exempt_certificate_number",
    "exempt_certificate_state",
    "exempt_certificate_kind",
    "exempt_certificate_expires",
)

# The Sales Tax report's Exempt sales reasons, in the order it lists them:
# the kind on the document's copy, an exempt sale with no copy, and lines
# left untaxed one by one on a taxed sale.
EXEMPT_REASONS = (
    "resale",
    "public_entity",
    "nonprofit",
    "other",
    "no_certificate",
    "line_by_line",
)
EXEMPT_REASON_LABELS = {
    **CERTIFICATE_KIND_LABELS,
    "no_certificate": "Exempt, no certificate on file",
    "line_by_line": "Untaxed lines on taxed sales",
}


def customer_exempt(customer) -> bool:
    """A customer marked Taxable: No."""
    return customer is not None and customer.is_taxable is False


def job_exempt(job) -> bool:
    """A job marked "Sales tax: exempt"."""
    return job is not None and job.is_taxable is False


def default_tax_exempt(db: Session, customer, job_id: Optional[int]) -> bool:
    """What a new document's switch starts as: the job's, else the
    customer's. A non-taxable customer is never charged, so its documents
    are always exempt."""
    from app.models.jobs import Job

    if customer_exempt(customer):
        return True
    return job_exempt(db.get(Job, job_id)) if job_id else False


def settle_tax_exempt(
    db: Session, sent: Optional[bool], customer, job_id, current=None
) -> bool:
    """The switch a save stores: on for a non-taxable customer; else what was
    sent; else what the document already had (an edit); else the default."""
    if customer_exempt(customer):
        return True
    if sent is not None:
        return bool(sent)
    if current is not None:
        return bool(current)
    return default_tax_exempt(db, customer, job_id)


# ── the job's certificate ────────────────────────────────────────────────


def _clean_kind(value) -> Optional[str]:
    if value is None:
        return None
    kind = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return kind or None


def _join(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return f"{words[0]} and {words[1]}"
    return ", ".join(words[:-1]) + f", and {words[-1]}"


def settle_job_certificate(values: dict, stored=None) -> None:
    """Settle the certificate fields of a job save in ``values`` (a create's
    fields, or an edit's changes over the ``stored`` job). Charged, the four
    are cleared. Exempt, the number, state and kind are required (from what
    was sent, else what the job holds) and the kind must be a known one;
    otherwise the save is refused with one sentence naming what is missing.
    """
    taxable = values.get("is_taxable")
    if taxable is None:
        taxable = True if stored is None else stored.is_taxable is not False
    if taxable:
        for col in CERTIFICATE_COLUMNS:
            values[col] = None
        return

    def current(col):
        if col in values:
            return values[col]
        return getattr(stored, col, None) if stored is not None else None

    number = (current("exempt_certificate_number") or "").strip() or None
    state = (current("exempt_certificate_state") or "").strip().upper() or None
    kind = _clean_kind(current("exempt_certificate_kind"))
    missing = []
    if not number:
        missing.append("number")
    if not state:
        missing.append("state or jurisdiction")
    if not kind:
        missing.append("kind (public entity, nonprofit, resale or other)")
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                "A tax-exempt job needs its exemption certificate, so enter "
                f"the certificate's {_join(missing)}."
            ),
        )
    if kind not in CERTIFICATE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "A certificate's kind is public entity, nonprofit, resale or "
                "other, so choose one of those."
            ),
        )
    values["exempt_certificate_number"] = number
    values["exempt_certificate_state"] = state
    values["exempt_certificate_kind"] = kind
    if "exempt_certificate_expires" not in values and stored is not None:
        values["exempt_certificate_expires"] = stored.exempt_certificate_expires


# ── the document's copy ──────────────────────────────────────────────────


def _put_copy(doc, source) -> None:
    """Copy ``source``'s certificate (a job, or a row of its columns) onto
    the document, or clear the copy when ``source`` has none."""
    has = source is not None and getattr(source, CERTIFICATE_COLUMNS[0], None)
    for col in CERTIFICATE_COLUMNS:
        value = getattr(source, col, None) if has else None
        if getattr(doc, col, None) != value:
            setattr(doc, col, value)


def fill_tax_exempt(connection, target) -> None:
    """On insert, a document made without setting its switch (an import, a
    recurring run, a pay application's invoice) starts as its job is, else
    its customer: unless it carries tax, which a switch on would contradict.
    An exempt one with no copy yet then copies its job's certificate, the
    one rule every create path shares."""
    from app.models.contacts import Customer
    from app.models.jobs import Job

    if getattr(target, "tax_exempt", None) is None:
        target.tax_exempt = False
        if not (target.tax_amount and target.tax_amount != 0):
            if target.customer_id is not None:
                taxable = connection.execute(
                    select(Customer.__table__.c.is_taxable).where(
                        Customer.__table__.c.id == target.customer_id
                    )
                ).scalar()
                if taxable is False:
                    target.tax_exempt = True
            if not target.tax_exempt and target.job_id is not None:
                taxable = connection.execute(
                    select(Job.__table__.c.is_taxable).where(
                        Job.__table__.c.id == target.job_id
                    )
                ).scalar()
                if taxable is False:
                    target.tax_exempt = True
    if not target.tax_exempt:
        _put_copy(target, None)
        return
    if getattr(target, CERTIFICATE_COLUMNS[0], None) or target.job_id is None:
        return
    t = Job.__table__.c
    row = connection.execute(
        select(*(t[col] for col in CERTIFICATE_COLUMNS)).where(t.id == target.job_id)
    ).first()
    if row is not None:
        _put_copy(target, row)


def refresh_certificate_copy(db: Session, doc, job_changed: bool = False) -> None:
    """After an edit: a document switched off keeps no copy; one exempt with
    no copy yet, or moved to another job, takes its job's certificate as it
    stands. A copy already taken is otherwise left alone, whatever the job
    says now."""
    from app.models.jobs import Job

    if not doc.tax_exempt:
        _put_copy(doc, None)
        return
    if doc.exempt_certificate_number and not job_changed:
        return
    _put_copy(doc, db.get(Job, doc.job_id) if doc.job_id else None)


def document_exempt(doc) -> bool:
    """Whether a sale is exempt as a whole: an estimate's, invoice's or sales
    receipt's own stored switch (every save of a non-taxable customer's
    document turns it on). A credit memo has no switch and follows the
    invoice it credits, else its customer."""
    if hasattr(doc, "tax_exempt"):
        return bool(doc.tax_exempt)
    original = getattr(doc, "original_invoice", None)
    if original is not None and getattr(original, "tax_exempt", False):
        return True
    return customer_exempt(getattr(doc, "customer", None))


def _copy_holder(doc):
    """The document whose copy says the certificate: its own, or for a
    credit memo the invoice it credits."""
    if hasattr(doc, "tax_exempt"):
        return doc
    return getattr(doc, "original_invoice", None)


def exemption_line(doc) -> Optional[str]:
    """The line an exempt document prints under its totals, else None:
    the certificate on its own copy, noted expired when the copy's expiry
    is before the document's date; plain "Sales tax exempt" with no copy."""
    if not document_exempt(doc):
        return None
    holder = _copy_holder(doc)
    number = getattr(holder, "exempt_certificate_number", None)
    if not number:
        return "Sales tax exempt"
    state = getattr(holder, "exempt_certificate_state", None)
    line = f"Sales tax exempt — certificate {number}"
    if state:
        line += f" ({state})"
    expires = getattr(holder, "exempt_certificate_expires", None)
    when = getattr(doc, "date", None)
    if expires is not None and when is not None and expires < when:
        line += " (expired)"
    return line


def exempt_reason(doc) -> str:
    """Why a sale's untaxed amount is untaxed (EXEMPT_REASONS): the kind on
    the document's copy when the sale is exempt as a whole, no certificate
    when it has no copy, else its lines were left untaxed one by one."""
    if not document_exempt(doc):
        return "line_by_line"
    holder = _copy_holder(doc)
    if holder is None or not getattr(holder, "exempt_certificate_number", None):
        return "no_certificate"
    kind = getattr(holder, "exempt_certificate_kind", None)
    return kind if kind in CERTIFICATE_KIND_LABELS else "other"

# ============================================================================
# Sales tax exemption (#864): the certificate on file, the job's and the
# document's exempt switch, and the reason an exempt sale is exempt.
#
# Three things say a sale is untaxed, in this order:
#   1. the customer is marked not taxable: never charged, whatever else says;
#   2. the document's own "Tax exempt" switch (estimates, invoices, sales
#      receipts), which starts as its job is (Job.is_taxable), else as its
#      customer is;
#   3. a line's own Tax box, as before.
# The first two untick every line; the third is a person's choice per line.
# The customer may name the certificate its exemption stands on (any kind on
# the reseller-permit record); an exempt document prints it under the totals.
# ============================================================================

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.models.reseller_permit import CERTIFICATE_KIND_LABELS

# The Sales Tax report's Exempt sales reasons (#864), in the order it lists
# them: a certificate's kind, an exempt sale with no certificate named, and
# lines left untaxed one by one on a taxed sale.
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


def fill_tax_exempt(connection, target) -> None:
    """On insert, a document made without setting its switch (an import, a
    recurring run, a pay application's invoice) starts as its job is, else
    its customer: unless it carries tax, which a switch on would contradict."""
    if getattr(target, "tax_exempt", None) is not None:
        return
    target.tax_exempt = False
    if target.tax_amount and target.tax_amount != 0:
        return
    from app.models.contacts import Customer
    from app.models.jobs import Job

    if target.customer_id is not None:
        taxable = connection.execute(
            select(Customer.__table__.c.is_taxable).where(
                Customer.__table__.c.id == target.customer_id
            )
        ).scalar()
        if taxable is False:
            target.tax_exempt = True
            return
    if target.job_id is not None:
        taxable = connection.execute(
            select(Job.__table__.c.is_taxable).where(
                Job.__table__.c.id == target.job_id
            )
        ).scalar()
        if taxable is False:
            target.tax_exempt = True


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


def certificate_for(db: Session, customer):
    """The active certificate the customer names, else None."""
    from app.models.reseller_permit import ResellerPermit

    if customer is None or not customer.exemption_certificate_id or db is None:
        return None
    cert = db.get(ResellerPermit, customer.exemption_certificate_id)
    if cert is None or cert.is_active is False:
        return None
    if cert.entity_type != "customer" or cert.entity_id != customer.id:
        return None
    return cert


def exemption_line(doc) -> Optional[str]:
    """The line an exempt document prints under its totals, else None."""
    if not document_exempt(doc):
        return None
    try:
        db = object_session(doc)
    except Exception:  # not a mapped row (a preview's stand-in)
        db = None
    cert = certificate_for(db, getattr(doc, "customer", None))
    if cert is None:
        return "Sales tax exempt"
    return f"Sales tax exempt — certificate {cert.permit_number} ({cert.jurisdiction})"


def exempt_reason(db: Session, doc) -> str:
    """Why a sale's untaxed amount is untaxed (EXEMPT_REASONS): the kind of
    the certificate its customer names when the sale is exempt as a whole,
    no certificate when none is named, else its lines were left untaxed
    one by one."""
    if not document_exempt(doc):
        return "line_by_line"
    cert = certificate_for(db, getattr(doc, "customer", None))
    if cert is None:
        return "no_certificate"
    return cert.kind if cert.kind in CERTIFICATE_KIND_LABELS else "other"


def check_certificate(db: Session, customer_id: Optional[int], certificate_id) -> None:
    """A customer names only a certificate on file for that customer."""
    from app.models.reseller_permit import ResellerPermit

    if certificate_id is None:
        return
    cert = db.get(ResellerPermit, certificate_id)
    if cert is None:
        raise HTTPException(
            status_code=404,
            detail="That exemption certificate is not on file.",
        )
    if customer_id is None or (
        cert.entity_type != "customer" or cert.entity_id != customer_id
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "That certificate is on file for someone else. Add this "
                "customer's own certificate on the Reseller Permits page, "
                "then name it here."
            ),
        )

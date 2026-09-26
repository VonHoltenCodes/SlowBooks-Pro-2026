# ============================================================================
# Document auto-numbering — the one home for every "next number" policy.
#
# The four document types previously each carried their own copy of MAX+1
# logic (invoices had two — routes/invoices.py and recurring_service.py).
# The strategies differ only in prefix, starting value, and zero-padding;
# estimates additionally seed from the user-configurable settings counter
# instead of the column MAX.
#
# These helpers only PROPOSE a number. Two concurrent creates can still pick
# the same one; every caller flushes inside a retry-on-IntegrityError loop
# with the column's UNIQUE constraint as the safety net (see create_invoice
# in app/routes/invoices.py for the canonical pattern).
# ============================================================================

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.credit_memos import CreditMemo
from app.models.vendor_credits import VendorCredit
from app.models.estimates import Estimate
from app.models.invoices import Invoice
from app.models.purchase_orders import PurchaseOrder
from app.services.settings_service import get_all_settings


def next_document_number(
    db: Session,
    column,
    *,
    prefix: str = "",
    first: int = 1001,
    pad: int = 0,
    seed: int | None = None,
) -> str:
    """Return the next unused number for `column` as a string.

    Starts from `seed` if given, otherwise from MAX(column)+1 when the
    current MAX is shaped like prefix+digits, otherwise from `first`.
    The candidate is collision-checked and bumped until free.

    `pad` zero-pads the numeric part; 0 means "match the width of the
    current MAX", which preserves zfill behavior for plain numeric series
    (invoice "0099" -> "0100").
    """
    n = None
    if seed is not None:
        n = seed
    else:
        last = db.query(sqlfunc.max(column)).scalar()
        if last and last.startswith(prefix):
            digits = last[len(prefix) :]
            if digits.isdigit():
                n = int(digits) + 1
                if not pad:
                    pad = len(digits)
    if n is None:
        n = first
    while True:
        candidate = f"{prefix}{str(n).zfill(pad)}"
        if db.query(column).filter(column == candidate).first() is None:
            return candidate
        n += 1


def _positive_int(raw) -> int | None:
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _highest_in_series(numbers, prefix: str) -> tuple[int | None, int]:
    """(highest numeric part, its zero-padded width or 0) among `numbers`
    shaped prefix + digits."""
    highest, pad = None, 0
    for number in numbers:
        number = number or ""
        digits = number[len(prefix) :]
        if not number.startswith(prefix) or not digits.isdigit():
            continue
        if highest is None or int(digits) > highest:
            highest = int(digits)
            # keep a zero-padded series padded ("0099" -> "0100")
            pad = len(digits) if digits.startswith("0") else 0
    return highest, pad


def next_invoice_number(db: Session) -> str:
    """Settings -> Invoice prefix + Next invoice #.

    Both were saved and never read: invoices stayed "1001..." with a prefix
    set, and the counter showed 1001 while invoices ran to 1005 (2.17.3
    exploratory W-M3, F6, F20). The number is now the prefix plus the larger
    of the setting and one past the highest number already used with that
    prefix, so raising the setting jumps ahead and lowering it can never
    reuse a number. Existing invoice numbers never change.

    The counter moves past the number it hands out, in the caller's
    transaction (rolled back with it if the insert fails), so Settings
    always shows the number the next invoice will get. Until the first
    invoice after this change the counter may still hold its untouched
    default (1001), which is not a choice anyone made: a file whose imported
    invoices run below it continues its own series instead of jumping to
    1001, and turning a prefix on continues the plain series (1005 ->
    HLB-1006). A number typed into Settings is a choice, so a new prefix can
    also start over ("0001" keeps its zeros: 2026-0001).
    """
    from app.models.settings import DEFAULT_SETTINGS
    from app.services.settings_service import set_setting

    settings = get_all_settings(db)
    prefix = (settings.get("invoice_prefix") or "").strip()
    raw = str(settings.get("invoice_next_number") or "").strip()
    seed = _positive_int(raw)
    chosen = seed is not None and raw != DEFAULT_SETTINGS["invoice_next_number"]

    numbers = [n for (n,) in db.query(Invoice.invoice_number)]
    top, pad = _highest_in_series(numbers, prefix)
    if top is None and prefix and not chosen:
        top, pad = _highest_in_series(numbers, "")
    if chosen:
        n = seed if top is None else max(seed, top + 1)
        if raw.startswith("0"):
            pad = max(pad, len(raw))
    else:
        n = top + 1 if top is not None else 1001

    while True:
        candidate = f"{prefix}{str(n).zfill(pad)}"
        if db.query(Invoice.id).filter(Invoice.invoice_number == candidate).first():
            n += 1
            continue
        set_setting(db, "invoice_next_number", str(n + 1))
        return candidate


def next_credit_memo_number(db: Session) -> str:
    return next_document_number(
        db, CreditMemo.memo_number, prefix="CM-", first=1, pad=4
    )


def next_vendor_credit_number(db: Session) -> str:
    """VC-0001. Its own series, not shared with credit memos — a vendor
    credit and a customer credit memo are different documents and an
    operator reading "CM-0007" on a supplier's paperwork would be right to
    doubt it."""
    return next_document_number(
        db, VendorCredit.credit_number, prefix="VC-", first=1, pad=4
    )


def next_po_number(db: Session) -> str:
    return next_document_number(
        db, PurchaseOrder.po_number, prefix="PO-", first=1, pad=4
    )


def next_estimate_number(db: Session) -> str:
    """Estimates seed from the user-configurable settings counter."""
    settings = get_all_settings(db)
    prefix = settings.get("estimate_prefix", "E-")
    raw = (settings.get("estimate_next_number", "1001") or "1001").strip() or "1001"
    try:
        seed = int(raw)
    except ValueError:
        seed = 1001
    return next_document_number(db, Estimate.estimate_number, prefix=prefix, seed=seed)

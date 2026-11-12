# ============================================================================
# The memo a bill payment's check carries (#800). Settings → Check Printing →
# Check memo picks it: the bill's reference ("Invoice 196132", the vendor's
# invoice number the bill was entered under), the bill's memo (the whole
# note, as every payment carried it from #602 until this), or nothing.
#
# The memo is written onto the payment when it is made, so the payment's
# Check memo field shows it and stays editable before the check prints; a
# memo typed on the payment is never replaced.
#
# A bill has no reference when its number was left blank and SlowBooks
# numbered it (date + the vendor's initials, "20260902-GK"): that number is
# ours, not the vendor's, and means nothing on the vendor's side of the
# check. Under the reference choice such a bill falls back to its memo, and
# then to nothing. A check paying several bills lists the references of the
# ones that have one ("Invoices 196132, 196140"); several memos are not
# joined, as before.
# ============================================================================

import re

SETTING = "check_memo"
REFERENCE, MEMO, NONE = "reference", "memo", "none"
CHOICES = frozenset({REFERENCE, MEMO, NONE})
DEFAULT = REFERENCE

REFUSAL = (
    "The check memo is the bill's reference, the bill's memo or nothing: "
    "check_memo must be one of reference, memo or none."
)

# The sample memo the test check prints under each choice.
SAMPLE = {REFERENCE: "Invoice 196132", MEMO: "Alignment test", NONE: ""}


def generated_bill_number_base(vendor_name, on) -> str:
    """The number a bill left blank is given (#501): the date and the
    vendor's initials, '20260902-GK' for Gin Kee on 2026-09-02, before any
    '-2', '-3' suffix."""
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", vendor_name or ""))
    return f"{on:%Y%m%d}-{initials.upper()[:4] or 'BILL'}"


def choice(db) -> str:
    """The saved choice; anything else stored reads as the default."""
    from app.services.settings_service import get_setting_raw

    value = (get_setting_raw(db, SETTING) or "").strip()
    return value if value in CHOICES else DEFAULT


def bill_reference(bill, vendor_name) -> str | None:
    """The vendor's invoice number the bill was entered under, or None when
    it has none: blank, or the number SlowBooks gave a bill left blank."""
    number = (bill.bill_number or "").strip()
    if not number:
        return None
    if bill.date is not None:
        base = generated_bill_number_base(vendor_name, bill.date)
        if number == base or re.fullmatch(re.escape(base) + r"-\d+", number):
            return None
    return number


def memo_for_bills(which: str, bills, vendor_name) -> str | None:
    """The check memo for a payment of `bills` under the choice `which`."""
    if which == NONE or not bills:
        return None
    if which == REFERENCE:
        refs = []
        for bill in bills:
            ref = bill_reference(bill, vendor_name)
            if ref and ref not in refs:
                refs.append(ref)
        if len(refs) == 1:
            return f"Invoice {refs[0]}"
        if refs:
            return "Invoices " + ", ".join(refs)
        # no bill has a reference: one bill's memo, as the memo choice would
    if len(bills) == 1:
        return (bills[0].notes or "").strip() or None
    return None

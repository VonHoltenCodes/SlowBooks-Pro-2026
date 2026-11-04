# ============================================================================
# What a report line's Edit opens (#689). Every drill-down line (a P&L,
# Balance Sheet, Trial Balance or P&L Detail line, a total's transactions, a
# General Ledger line) carries `edit`: the kind, the id its edit takes, and
# whether saving changes it in place or voids it and posts a new one.
#
#   in place  invoice, bill, deposit, a hand-entered journal entry, a
#             register entry: each opens its own edit form
#   replace   expense, card charge, transfer, credit memo, vendor credit,
#             customer payment, bill payment: the kind's form, filled in;
#             saving voids the original and posts the new one
#             (app/services/replace_transaction.py)
#
# A void, a reversal and every other kind (a pay run, an import, an opening
# balance) carry none: Open only.
# ============================================================================

from app.models.transactions import Transaction

# source_type: (kind, the id is the document's (source_id) or the entry's)
IN_PLACE = {
    "invoice": ("invoice", "source"),
    "invoice_edit": ("invoice", "source"),
    "bill": ("bill", "source"),
    "bill_edit": ("bill", "source"),
    "deposit": ("deposit", "txn"),
    "manual": ("journal", "txn"),
    "bank_entry": ("bank_entry", "txn"),
}
REPLACE = {
    "expense": ("expense", "txn"),
    "cc_charge": ("cc_charge", "txn"),
    "transfer": ("transfer", "txn"),
    "credit_memo": ("credit_memo", "source"),
    "vendor_credit": ("vendor_credit", "source"),
    "payment": ("payment", "source"),
    "bill_payment": ("bill_payment", "source"),
}


def edit_target(txn: Transaction, voided: bool) -> dict | None:
    """{kind, id, replace} for the line's Edit, or None (Open only)."""
    st = txn.source_type or ""
    if voided or st.endswith("_void"):
        return None
    for table, replace in ((IN_PLACE, False), (REPLACE, True)):
        hit = table.get(st)
        if hit is None:
            continue
        kind, by = hit
        rid = txn.source_id if by == "source" else txn.id
        if not rid:
            return None
        return {"kind": kind, "id": rid, "replace": replace}
    return None

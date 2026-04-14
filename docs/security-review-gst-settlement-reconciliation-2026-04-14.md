# Security Review — GST Settlement via Bank-Reconciliation Trigger (2026-04-14)

## Scope Reviewed
- `app/models/gst_settlement.py`
- `app/services/gst_settlement.py`
- `app/routes/reports.py`
- `app/static/js/reports.js`
- `alembic/versions/h8c9d0e1f2a3_add_gst_settlements.py`

## Review Focus
- Whether GST settlement now requires bank-confirmed evidence instead of manual unsupported clearing
- Whether bank transactions can be reused or mismatched across multiple GST periods
- Whether the new settlement-posting path respects existing closing-date and accounting controls

## Findings
1. **GST settlement is now tied to reconciled bank evidence**
   - Settlement only confirms from reconciled bank transactions whose amount exactly matches the GST payable/refund for the selected period.
   - This is materially safer than a free-form manual settlement journal because it links settlement to bank-statement-confirmed activity.

2. **Duplicate and mismatched settlement paths fail closed**
   - A GST period cannot be settled twice, and a bank transaction already linked to a journal cannot be reused.
   - Mismatched amounts and unreconciled bank transactions are rejected instead of silently forcing the ledger to fit.

3. **Settlement posting stays inside existing accounting controls**
   - The settlement route reuses the GST return calculation as the source of truth, posts balanced journals, and applies closing-date enforcement using the matched bank transaction date.
   - The confirm action is protected with the existing `accounts.manage` permission.

## Residual Risks
- Reconciliation coverage is still broader than GST: the banking/reconciliation modules themselves are not yet fully rolled into the RBAC rollout.
- Exact-match-only settlement is intentionally strict; edge cases like split payments or part-period adjustments will require a later, explicitly designed enhancement.
- This slice does not integrate directly with IRD payment rails or filing APIs; it only records the accounting confirmation once bank evidence exists.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM** and mainly tied to broader banking-module rollout rather than the GST settlement implementation itself.

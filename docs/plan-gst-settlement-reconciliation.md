# GST Settlement via Bank-Reconciliation Trigger Slice

## Summary
Add GST settlement as a bank-confirmed workflow so the GST control account is only cleared when a reconciled bank transaction or refund is explicitly matched to a GST period.

## Key Changes
- Add GST settlement records and migration linked to GST periods, bank transactions, and settlement journals.
- Extend GST return reporting to show settlement status and candidate reconciled bank transactions.
- Confirm settlement only from a reconciled bank transaction with the expected GST payment/refund amount.
- Post the GST settlement journal from the matched bank transaction and prevent duplicate settlement for the same period or bank transaction.
- Add targeted GST settlement tests, UI coverage, and security review.

## Test Plan
- Add failing backend tests first for payable/refundable settlement matching, duplicate prevention, mismatch rejection, and closing-date enforcement.
- Add targeted GST report UI tests for settlement state and candidate display.
- Re-run full Python/JS verification, syntax checks, and `git diff --check`.

## Defaults
- Settlement is confirmed only from reconciled bank transactions.
- Exact amount matching is required in this slice.
- This slice records accounting settlement only; it does not integrate directly with IRD payment rails.

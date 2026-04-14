# GST Settlement via Bank-Reconciliation Trigger Specification

## Goal
Clear the GST control account only when a bank-statement-confirmed payment/refund is matched to a GST period, keeping the GST return flow aligned with actual bank activity.

## Required Behavior
- Add a GST settlement model tied to GST period start/end, GST net position/amount, bank transaction, and settlement journal transaction.
- Extend the GST return response to include settlement state and candidate reconciled bank transactions whose amount exactly matches the expected GST settlement amount.
- Add a settlement-confirm route that accepts a reconciled bank transaction and posts the GST settlement journal.
- GST payable periods must post DR GST / CR bank; refundable periods must post DR bank / CR GST.
- A GST period cannot be settled twice, and a bank transaction cannot settle more than one GST period.
- Settlement posting must respect closing-date enforcement using the matched bank transaction date.

## Constraints
- No direct IRD integration.
- No new dependencies.
- Use existing GST return calculation as the source of truth for expected settlement amount.
- Reuse the existing bank transaction and journal foundations; do not invent a separate cash-settlement ledger.

## Verification
- Backend tests for payable/refundable settlement, duplicate prevention, amount mismatch, and closing-date blocking.
- UI test for GST return settlement state/candidate rendering.
- Full Python/JS suites, syntax checks, and `git diff --check`.

# Spec: implement NZ-relevant upstream carryover features

## Functional requirements
- Payments can be voided once, restore linked invoice balances/statuses, and post a reversing journal on the original payment date subject to closing-date checks.
- Bills prefer vendor-level default expense accounts after explicit line/item choices and before the global default expense role.
- Users with the right permissions can create manual journals, deposit pending customer receipts from receipt-clearing into a selected bank account, view a running-balance bank/check register, and enter credit-card charges.
- Invoice and estimate entry screens support creating a customer inline without losing the in-progress document.

## Data / API requirements
- Persist payment void state and deposit linkage.
- Persist vendor default expense-account selection with an account FK.
- Expose new protected API surfaces for journals, deposits, check register, and credit-card charges.
- Reuse existing `transactions` / `transaction_lines` records as the accounting source of truth for the new workflows.

## UX requirements
- New workflows are reachable from the existing app shell/navigation and honour RBAC.
- Payment detail UI clearly shows voided payments and hides void actions after voiding.
- Deposit UI presents pending customer receipts rather than free-form arbitrary clearing-account totals.

## Acceptance criteria
- Backend tests cover payment void restoration, deposit posting, manual journal balancing/voiding, credit-card charge posting, check-register balances, and vendor default expense fallback.
- Frontend tests cover inline customer actions, payment-void indicators, deposit / register / journal / vendor-default UI hooks, or equivalent focused behavior.
- Alembic migration integrity still passes and `git diff --check` is clean.

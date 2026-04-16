# Security Review — NZ forum carryover implementation (2026-04-16)

## Scope reviewed
- `app/routes/payments.py`
- `app/routes/journal.py`
- `app/routes/deposits.py`
- `app/routes/cc_charges.py`
- `app/routes/banking.py`
- `app/routes/bills.py`
- `app/routes/vendors.py`
- `app/models/payments.py`
- `app/models/contacts.py`
- related frontend wiring in `app/static/js/*.js`, `app/static/js/app.js`, and `index.html`
- migration `alembic/versions/i9d0e1f2a3b4_add_nz_forum_carryover_fields.py`

## Questions checked
1. Do the new accounting workflows stay behind the existing RBAC model?
2. Do new financial mutations preserve double-entry integrity and closing-date controls?
3. Do payment/deposit flows avoid unsafe state transitions that would leave ledgers inconsistent?
4. Does the vendor default-expense-account feature validate account existence/type rather than trusting arbitrary IDs?

## Findings

### 1. RBAC remains in place for new financial surfaces
- Manual journals require `accounts.manage`.
- Deposits, credit-card charges, and the check register use `banking.view`/`banking.manage`.
- Payment voiding stays on `sales.manage`.
- Inline customer creation only appears in invoice/estimate forms when the current user has `contacts.manage`.

### 2. Posting paths reuse the existing balanced-journal engine
- Manual journals, deposits, credit-card charges, payment creation, and payment voiding all route through `create_journal_entry()` / `reverse_journal_entry()`.
- This preserves the existing debit/credit balance enforcement and account-balance update behavior.

### 3. Closing-date controls are enforced on mutation paths
- Payment voiding checks the original payment date before posting the reversal.
- Deposit creation, manual journals, and credit-card charges all check the supplied transaction date before posting.
- This matches the existing NZ posting lifecycle hardening already present elsewhere in the branch.

### 4. Deposited payments fail closed
- A payment cannot be voided after it has been included in a deposit batch (`deposit_transaction_id` set).
- That prevents a sales-user void from silently undoing AR while leaving the later bank-side deposit journal intact.

### 5. Vendor default expense accounts are validated
- Vendor create/update now verifies that the selected default expense account exists, is active, and is actually an expense account.
- Bill entry still prefers explicit line/item choices first, then vendor default, then global default expense fallback.

## Residual risk
- Residual risk is **LOW to MEDIUM**.
- The biggest remaining risk is product/process rather than code injection/auth bypass: manual journals and deposit workflows are powerful accounting actions, so mistakes by authorized users can still create incorrect books.
- The broader repo still carries the known operational dependency on correct local/private deployment and RBAC hygiene; this slice does not widen that trust boundary, but it does add more high-impact financial actions inside it.

## Recommended follow-up
- Keep targeted regression tests around payment/deposit state transitions as future bank-rule or reconciliation automation lands.
- If cheque printing or secure document attachments are ever revived from upstream, review them separately as higher-risk financial/file-output surfaces rather than folding them into this slice.

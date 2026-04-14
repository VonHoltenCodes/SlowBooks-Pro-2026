# Security Review — Document Email Expansion (2026-04-14)

## Scope
Reviewed the shared SMTP document-delivery slice changes in:
- `app/services/email_service.py`
- `app/services/pdf_service.py`
- `app/routes/invoices.py`
- `app/routes/estimates.py`
- `app/routes/credit_memos.py`
- `app/routes/purchase_orders.py`
- `app/routes/payroll.py`
- `app/routes/reports.py`
- related PDF/email templates and frontend send actions

## Checks performed
- Verified SMTP send + `EmailLog` write now happen in one shared backend path to avoid route-level double logging.
- Verified new routes only attach a single generated PDF per send.
- Verified statement and payslip routes enforce entity existence and relevant state checks before sending.
- Verified read-only UI recipient prefills come from existing customer/vendor data and payslips still require explicit recipient entry.
- Re-ran full Python unittest suite, all JS tests, `py_compile`, `node --check`, and `git diff --check`.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **Outbound document delivery still inherits the repo's broader no-auth / permissive-CORS trust model**
   - Any caller that can already reach admin/business endpoints can trigger outbound emails for invoices, statements, estimates, credit notes, payslips, and purchase orders.
   - This is consistent with current app-wide risk, but document delivery now increases the impact of that existing trust boundary.
2. **Recipient entry is operator-controlled for several document types**
   - This is expected product behavior, but it means mistaken recipients remain an operational risk unless later approval/audit controls are added.

### LOW
1. **Statement emails log against `entity_type=statement` and the customer id rather than a separate statement document id**
   - This is sufficient for the current one-off send flow, but future resend/history UX may want richer statement-run identity.

## Positive controls
- SMTP/config failures are now logged once in a shared path and surfaced back to the caller.
- Payslip email is blocked for draft pay runs.
- Credit note email is blocked for void credit memos.
- Non-invoice document emails reuse localized formatting and generated PDFs instead of ad-hoc file creation.

## Overall assessment
- **No CRITICAL/HIGH regressions found for this slice.**
- **Residual risk is MEDIUM** because outbound business-document delivery now sits inside the repo's existing broad admin trust model until auth/privacy hardening lands.

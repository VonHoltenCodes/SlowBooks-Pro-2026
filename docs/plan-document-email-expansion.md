# Document Email Expansion Slice

## Summary
Extend the existing SMTP invoice-email pattern into a shared outbound-document flow for statements, estimates, credit memos, payroll payslips, and purchase orders.

## Key Changes
- Add shared backend helpers for document email rendering, delivery, and logging.
- Add outbound email endpoints for the supported document types.
- Add missing PDF/template support for credit memos and purchase orders.
- Add UI send actions that reuse the current modal-driven email pattern.

## Test Plan
- Add failing backend tests for each new email route and shared email behavior.
- Add PDF rendering coverage for new credit memo and purchase order PDFs.
- Add JS coverage for the new email action wiring.
- Run full repo verification plus an explicit email/PDF security review.

## Defaults
- Reuse `EmailLog` rather than changing schema.
- Require manual recipient entry for payslips because employee email is not modeled yet.

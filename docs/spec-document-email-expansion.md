# Spec: Document Email Expansion

## Scope
Implement shared SMTP delivery for statement, estimate, credit memo, payroll payslip, and purchase order PDFs, and migrate invoice email onto the same backend path.

## Delivery rules
- Each send action emails exactly one PDF attachment.
- Shared backend delivery must be responsible for SMTP send + `EmailLog` write.
- Routes must not duplicate success/failure log writes around the shared send helper.
- Statement emails are keyed to customer plus `as_of_date`.
- Payslip emails are allowed only for processed pay runs and require a matching employee stub.

## Rendering rules
- Reuse current localized formatting for dates/currency.
- Add missing credit memo and purchase order PDF templates.
- Use one shared generic document-email rendering path for non-invoice documents unless a document-specific path is required.

## UI rules
- Add send actions in existing statement, estimate, credit memo, payslip, and purchase order surfaces.
- Prefill recipients from customer/vendor email when available.
- Payslip send must accept manual recipient entry because employee email is not stored.

## Validation
- Missing entities return 404.
- Invalid document state returns 400 where applicable.
- SMTP/config/send failures must surface as HTTP errors after logging.

# Spec: NZ Demo Business

## Scope
Replace the remaining temporary legacy demo items, invoices, estimates, and payments with one cohesive NZ demo business.

## Rules
- Keep the current NZ/Xero-derived contact layer unless a contact clearly no longer fits.
- Use NZ-relevant GST assumptions and chart accounts.
- Keep the seed script idempotent.
- Do not turn this into a generic import feature.

## Validation
- Seeded items and transactions are NZ-relevant and internally coherent.
- The demo seed still creates journal-backed transactions successfully.
- README/docs no longer describe the data as IRS/Henry Brown/transitional for the transaction layer.

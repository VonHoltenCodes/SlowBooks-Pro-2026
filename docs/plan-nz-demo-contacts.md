# NZ Demo Contacts Slice

## Summary
Replace the demo customer and vendor contact seed data with NZ/Xero-derived contacts from the supplied example files, while keeping the demo seed script runnable and deferring the full demo dataset rewrite.

## Key Changes
- Replace hardcoded IRS/Henry Brown customer/vendor lists in `scripts/seed_irs_mock_data.py`.
- Use the supplied Xero NZ customer/supplier CSVs as source material.
- Update the seed script's idempotence sentinel and any contact-name references needed to keep invoices/payments/estimates runnable.
- Update README/docs to stop describing the demo contacts as the IRS/Henry Brown sample set.
- Defer full replacement of demo items/invoices/payments/estimates to a later slice.

## Test Plan
- Add script/data tests for seeded NZ/Xero-derived contacts and idempotence.
- Run full repo verification and explicit seed/data safety review.

## Defaults
- This slice updates only the demo customer/vendor layer.

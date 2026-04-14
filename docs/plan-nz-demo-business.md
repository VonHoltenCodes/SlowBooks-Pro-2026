# NZ Demo Business Slice

## Summary
Replace the remaining temporary legacy demo items, invoices, estimates, and payments with one cohesive NZ-relevant demo business while keeping the already-shipped NZ/Xero-derived contact layer.

## Key Changes
- Rewrite the remaining business scenario in `scripts/seed_irs_mock_data.py` around one internally consistent NZ demo business.
- Replace body-shop items and temporary transaction examples.
- Keep the demo seed script runnable and idempotent.
- Update README/docs so the sample-data section is no longer described as transitional/IRS-derived.

## Test Plan
- Add/adjust seed-data tests for NZ-relevant items and transactions.
- Run full repo verification and explicit seed/data safety review.

## Defaults
- Use a single cohesive NZ demo business rather than mixed cross-industry examples.

# Security Review — NZ Demo Contacts Seed (2026-04-14)

## Scope
Reviewed the NZ demo-contacts slice changes in:
- `scripts/seed_irs_mock_data.py`
- `README.md`
- `docs/localization_summary.md`

## Checks performed
- Verified the seed script now uses NZ/Xero-derived customer and supplier contact data.
- Verified the script still runs successfully and remains idempotent.
- Verified the contact swap did not introduce new file/path/network behavior; the source CSVs were used as source material for the in-repo data, not as a new runtime import dependency.
- Re-ran full repo tests, JS checks, `py_compile`, and `git diff --check`.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **The seeded transactional examples are still not NZ-native**
   - This slice intentionally updates only the customer/supplier contact layer.
   - Items, invoices, payments, and estimates remain temporary legacy examples and should be replaced in a later follow-up slice.

### LOW
1. **The script still carries historical transitional structure**
   - It is now less US-facing at the contact layer, but not yet a fully NZ-native demo/business scenario end-to-end.

## Positive controls
- Henry Brown/IRS contact markers are removed from the demo contact seed layer.
- The script remains idempotent.
- Runtime account usage inside the script was updated to use the newer NZ-chart-compatible helpers where needed.
- No new shell execution, external calls, or user-supplied file ingestion were introduced.

## Overall assessment
- **No CRITICAL/HIGH regressions found for this slice.**
- **Residual risk is MEDIUM** until the remaining demo items/invoices/payments/estimates are also replaced with NZ-native examples.

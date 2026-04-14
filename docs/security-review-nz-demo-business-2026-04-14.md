# Security Review — NZ Demo Business Seed (2026-04-14)

## Scope
Reviewed the NZ demo-business slice changes in:
- `scripts/seed_irs_mock_data.py`
- `README.md`
- `docs/localization_summary.md`

## Checks performed
- Verified the seeded business scenario is now NZ-relevant and internally coherent.
- Verified the seed script still runs successfully and remains idempotent.
- Verified the seed script uses the current NZ chart/runtime account helpers rather than stale fixed-number assumptions where needed.
- Re-ran full repo tests, JS checks, `py_compile`, and `git diff --check`.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **Demo data is illustrative, not regulatory guidance**
   - The new dataset is NZ-relevant and GST-consistent, but it is still sample/demo data rather than compliance-certified example data.

### LOW
1. **The script filename still reflects its historical origin**
   - `scripts/seed_irs_mock_data.py` now seeds an NZ demo business, but the filename remains unchanged for continuity.

## Positive controls
- The seeded business is no longer based on the old body-shop / Henry Brown scenario.
- Customer/supplier contacts, items, invoices, estimates, and payments now form one coherent NZ demo business.
- The script remains idempotent.
- Current NZ chart/runtime account helper usage kept the seeded journals compatible with the branch's current accounting model.

## Overall assessment
- **No CRITICAL/HIGH regressions found for this slice.**
- **Residual risk is MEDIUM-LOW** and mainly relates to demo data being illustrative rather than authoritative compliance material.

# NZ UI Copy Cleanup Slice

## Summary
Remove remaining user-facing US terminology from the NZ branch so the app, README, and visible examples consistently describe the current SlowBooks NZ product.

## Key Changes
- Update visible UI copy, help text, and README wording to remove stale US terms such as Schedule C, IRS sample-data captions, Sales Tax, and outdated payroll wording.
- Keep disabled/compatibility code paths intact, but avoid presenting them as active product features in user-facing docs or UI.
- Refresh payroll messaging so it matches the implemented NZ payroll outputs rather than earlier placeholder wording.
- Update documentation summaries so the completed slice is reflected in the NZ roadmap.

## Test Plan
- Add a failing UI copy regression test for the payroll page banner.
- Add a failing README regression test for the most visible stale US-facing wording.
- Re-run targeted tests first, then full Python/JS verification, syntax checks, and `git diff --check`.

## Defaults
- Preserve compatibility-only internal names and disabled endpoint messaging where they remain necessary.
- Treat this as copy cleanup only, not a schema/API/feature redesign slice.

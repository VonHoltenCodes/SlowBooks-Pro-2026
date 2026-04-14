# NZ UI Copy Cleanup Specification

## Goal
Ensure user-facing copy in the NZ branch no longer describes the product with stale US tax/payroll wording while preserving underlying compatibility code and the disabled Schedule C response path.

## Required Behavior
- The payroll page banner must describe the currently implemented NZ payroll capabilities and must not claim payroll outputs are still pending when payslips, Employment Information export, and starter/leaver filing already exist.
- README product copy must stop presenting the NZ branch with IRS-derived screenshot captions, Schedule C feature descriptions, or Sales Tax Payable examples as if they were current product behavior.
- Completed-roadmap documentation should reflect that repo-wide UI/copy cleanup has been completed for the major visible NZ surfaces touched in this slice.

## Constraints
- Do not change database fields, API contracts, or disabled endpoint behavior.
- Compatibility-only legacy wording may remain inside tests or explicit disabled-response text where it is part of guarding retired behavior.
- Keep the scope focused on visible copy and maintainers' high-signal guidance, not broad internal symbol renaming.

## Verification
- JS payroll UI regression test for the updated banner copy.
- README regression test for removed stale US-facing phrases.
- Full Python and JS suites, Python syntax compilation, targeted `node --check` for touched frontend files, and `git diff --check`.

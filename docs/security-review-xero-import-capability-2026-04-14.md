# Security Review — Xero Import Capability (2026-04-14)

## Scope Reviewed
- `app/services/xero_import.py`
- `app/routes/xero_import.py`
- `app/static/js/xero_import.js`
- related parser/import tests

## Review Focus
- Whether uploaded Xero report files are parsed and validated without unsafe execution paths
- Whether import is blocked on verification failures instead of partially mutating the ledger
- Whether the new import surface sits behind existing admin RBAC permissions

## Findings
1. **Dry-run verification is mandatory before import**
   - The importer refuses to run if the required file set is incomplete or report verification fails.
   - This reduces the risk of silently loading mismatched or partial Xero history.

2. **No dynamic execution or external API dependency was introduced**
   - The slice is CSV-only and uses local parsing/normalization logic only.
   - No live OAuth/API tokens, shell execution, or remote calls are part of this MVP.

3. **The import surface is protected by existing admin permissions**
   - Xero import routes are intended for the same `accounts.manage`-level admin context as other high-impact ledger operations.

## Residual Risks
- This MVP assumes CSV exports with supported column aliases and does not yet cover XLSX exports or live Xero API connectivity.
- Historic import currently expects a clean target ledger for the imported chart/journal set; more flexible merge behavior would need a separate design.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM** and mainly tied to future format coverage and broader RBAC rollout, not to the current import implementation itself.

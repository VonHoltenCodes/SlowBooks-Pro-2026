# Security Review — System Account Role Admin Workflow (2026-04-14)

## Scope
Reviewed the system-account role admin workflow slice changes in:
- `app/services/accounting.py`
- `app/routes/accounts.py`
- `app/static/js/app.js`
- `app/schemas/accounts.py`
- `tests/test_system_account_role_admin.py`

## Checks performed
- Verified read-only role status inspection does not auto-create missing accounts.
- Verified role updates only accept known role keys.
- Verified assigned accounts must exist, stay active, and match the required account type.
- Verified clearing a role mapping falls back to legacy resolution rather than deleting accounts.
- Re-ran full Python unittest suite, JS tests, `py_compile`, `node --check`, and `git diff --check`.

## Findings
### CRITICAL
- None found.

### HIGH
- None found.

### MEDIUM
1. **The workflow inherits the repo's broader no-auth / permissive-CORS admin model**
   - Any caller that can reach the existing admin UI/API can change runtime posting mappings.
   - This is consistent with the current application trust model, but it keeps financial-config changes in the same overall risk envelope as other admin actions.

### LOW
1. **Fallback and lazy-create behavior still exist for compatibility**
   - This is intentional for the transition period.
   - The new status UI makes that state visible, which reduces operator surprise but does not yet remove the compatibility path.

## Positive controls
- Account-type validation blocks obvious misconfiguration.
- Inactive accounts cannot be assigned.
- Read endpoints surface configured/fallback/missing states without mutating data.
- Operators can clear stale explicit mappings without editing raw settings rows.

## Overall assessment
- **No CRITICAL/HIGH regressions found for this slice.**
- **Residual risk is MEDIUM** because admin financial configuration still sits inside the repo's broader trust model until auth/RBAC hardening lands.

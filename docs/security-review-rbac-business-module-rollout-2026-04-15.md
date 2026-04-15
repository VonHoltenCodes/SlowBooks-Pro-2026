# Security Review — RBAC Business Module Rollout (2026-04-15)

## Scope Reviewed
- `app/services/auth.py`
- remaining business route modules under `app/routes/`
- `app/static/js/app.js` and affected business page modules
- `app/static/js/api.js`
- `tests/test_rbac_rollout_modules.py`
- `tests/js_rbac_rollout_ui.test.js`

## Review Focus
- Whether the remaining business modules now require auth/permissions consistently
- Whether frontend navigation/actions align with backend enforcement
- Whether protected downloads/uploads still work with bearer-token auth after route hardening

## Findings
1. **Remaining business modules now sit behind explicit permission families**
   - Contacts, items, sales, purchasing, banking, and import/export now use consistent `view`/`manage` permissions.
   - Dashboard/search/GST helper endpoints now require authentication without adding unnecessary new permission keys.

2. **Frontend visibility now matches backend enforcement**
   - Route metadata and page-level action hiding reduce accidental 401/403 flows for view-only users.
   - Manage-only operations such as imports, reconciliations, and document mutation actions are no longer exposed in the default UI for read-only users.

3. **Bearer-auth-aware file operations preserve protected workflows**
   - Protected CSV/IIF exports, PDFs, backups, payroll filing outputs, and multipart upload/import flows now use authenticated fetch/download helpers.
   - This prevents the RBAC rollout from silently breaking protected file workflows that previously relied on anonymous browser requests.

## Residual Risks
- `tax.py` and `uploads.py` remain outside this rollout and should be reviewed separately for whether they should also join the auth boundary.
- Custom permission overrides can still create unusual combinations (for example manage without related supporting views); the current slice assumes admins configure coherent permission sets.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM**, mostly around a few out-of-slice endpoints and future permission-combination ergonomics rather than missing enforcement on the rolled-out modules.

# RBAC Business Module Rollout Slice

## Summary
Extend the existing auth/RBAC foundation from payroll/admin surfaces to the remaining customer, vendor, item, sales, purchasing, banking, and import/export modules.

## Key Changes
- Add moderate-granularity permission families for contacts, items, sales, purchasing, banking, and import/export.
- Protect remaining backend business routes with view/manage permissions, while making dashboard/search/GST-code support endpoints authenticated-only.
- Roll out matching route/nav/action gating in the frontend shell and page modules.
- Keep authenticated file downloads/uploads working by using bearer-auth-aware frontend helpers for protected exports, PDFs, and multipart uploads.

## Test Plan
- Add backend auth tests covering new permission metadata, representative route protection, and auth-only support endpoints.
- Add frontend tests covering route metadata and hiding manage actions for view-only users.
- Run full Python/JS suites, syntax checks, and `git diff --check`.

## Defaults
- Use module-family view/manage permissions rather than more granular action-specific permissions.
- Treat customer credit memos as part of the sales module because the current implementation applies them to customer invoices.
- Do not add new migrations or redesign the auth model in this slice.

# RBAC Business Module Rollout Specification

## Goal
Require authentication and appropriate permissions across the remaining business modules while preserving the existing auth/user-management foundation.

## Required Behavior
- New permission keys must exist for contacts, items, sales, purchasing, banking, and import/export module families.
- Remaining business routes must require either `*.view` or `*.manage` according to whether they read data or mutate/import/process data.
- `dashboard`, `search`, and `gst-codes` must require authentication but no extra module permission.
- Frontend navigation and in-page actions must align with backend permissions so view-only users can browse but not create/edit/process/import.
- Protected exports, PDFs, downloads, and multipart uploads must continue working with bearer-token auth.

## Constraints
- Keep permission granularity at module-family view/manage level.
- Reuse the existing `require_permissions` and auth metadata surfaces.
- No new dependencies or schema migrations.
- Keep already protected admin/payroll modules intact unless needed for consistency.

## Verification
- Backend tests for permission metadata, representative protected routes, and auth-only support endpoints.
- Frontend tests for route permission metadata and hidden manage actions.
- Full Python suite, all JS tests, syntax checks, and `git diff --check`.

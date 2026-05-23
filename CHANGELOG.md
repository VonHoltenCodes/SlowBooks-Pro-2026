# Changelog

Notable changes between releases. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/). The internal build order
used during development is captured here so the README can stay focused
on what the software does, not on what sprint shipped what.

## [Unreleased]

### Payroll / HR UI additions
- **Portal-token admin view** — Employee Details > Portal Access now
  shows expires-at (red when <30 days), last-used-at, and a
  collapsible recent-access log pulled from `portal_accesses`.
- **PTO accrual editor** — `#/hr/pto` gained an Employee Accruals
  section with enroll-employee-in-policy form and per-row "Run Accrual"
  prompt. Closes the gap where admins had to enroll employees via curl.
- **E-Verify case tracking** — schema additions (`everify_status`,
  `everify_submitted_at`, `everify_closed_at`, `everify_notes`),
  GET/PUT `/api/employees/{id}/everify` endpoints, and a new section
  in the Employee Details modal with color-coded status. Pure
  record-keeping — the federal E-Verify submission still happens via
  the official portal or a vendor; this stores the case so DHS
  inspections find it in one place.

### Partial CSP tightening
- index.html's 11 inline `onclick=`/`oninput=` handlers moved to a new
  `app/static/js/bootstrap.js` that wires them via `addEventListener`
  after DOMContentLoaded. The static shell page now has zero inline
  handlers — would work under a stricter CSP today.
- `'unsafe-inline'` stays in `script-src` and `style-src` because the
  JS-rendered modal templates across the rest of the app still emit
  inline handlers + styles. Removing those is a multi-file refactor
  documented in `docs/todo.md`. Honest accounting added to
  `docs/security-hardening.md`.

### Polish
- `docs/release-checklist.md` section 4 (TLS) now mentions optionally
  submitting the domain to the HSTS preload list once TLS is locked in.

### Audit + ops automation
- **Portal access audit log** — new `portal_accesses` table records
  every authenticated and unauthenticated portal hit (employee_id, IP,
  truncated UA, path, success). Mirrors `LoginAttempt` and gives
  forensic queries something more granular than `portal_token_last_used`.
- **Encryption key rewrap CLI** — `python -m app.services.encryption
  rewrap` re-encrypts every bank-PII blob under the current key, so
  rotation can actually complete (transparent reads via PREV fallback
  was already shipped). Supports `--dry-run`.
- **Wiring audit as a unit test** — `tests/test_wiring.py` grep-and-
  resolves every JS `API.*` call against the registered FastAPI routes.
  Catches typos and stale paths automatically — CI fails when a JS
  caller goes nowhere.
- **End-to-end portal test** — single test walks the entire portal
  lifecycle (mint → claim → 5 cookieless pages → POST PTO → logout →
  cold-401 → force-expire → rotate → claim again).
- **Weekly `pip-audit` GitHub Action** — Sunday cron, opens a
  security-labeled issue on findings (de-duped), fails the workflow run.

### Frontend polish
- **Drag-and-drop document uploads** on Employee Details > Documents.
- **Portal logout button** in `portal/base.html` nav.
- **Branded portal favicon** — `/portal/favicon.ico` serves the
  employer's company logo so each customer's portal carries their own
  bookmark icon.

### Bug fix
- Pay-stub PDF was rendering accountable-plan reimbursements as
  positive line items in the **Deductions** table. Now they have their
  own **Additions to Net (non-taxable)** table above net pay. Net-pay
  math was always right; the display was confusing.

### Tax forms — PDFs, audit hashes, the works
- **WeasyPrint PDF endpoints** — `POST /api/payroll/forms/{w2,w3,940,941}/.../pdf`
  render real printable forms (Acme Co. branded, masked SSN, full box
  data). The existing JSON endpoints stay for future e-file integration.
- **Document audit hashes** — every tax-form PDF carries a SHA-256
  content hash and an audit ID in the footer. Backed by a new
  `document_audits` table with three lookup endpoints
  (`/api/document-audits`, `.../verify/{hash}`, etc.). An auditor with
  the PDF can recompute the hash and confirm authenticity against
  the trust-anchor row.

### Payroll / HR
- **Time-entry → pay-run auto-population** — the pay-run form now has
  a "Use approved time entries" checkbox + live-preview column showing
  unpaid approved hours per employee. Backend was already wired; only
  the frontend opt-in was missing.
- **PTO year-end carryover automation** — new
  `POST /api/pto/accruals/year-end-carryover?target_year=YYYY` endpoint
  caps every accrual at its policy `max_carryover` and resets YTD
  counters, returning a per-row before/after summary.
- **Portal cookie session** — after the first `/portal/{token}` claim,
  the token moves into a `HttpOnly Secure SameSite=Strict` cookie and
  every subsequent URL is cookieless. No more Referer leak, browser
  history, or shared-bookmark exposure. Backward-compat: emailed
  `/portal/{token}` links still work — they just redirect through the
  claim flow once.
- **Portal branding** — every page renders the employer's company name
  and logo in the header (was generic "Employee Portal").
- **State new-hire report PDF branding** — same treatment.

### Authentication / session hardening
- **Login attempt audit log** — new `login_attempts` table records
  every success and failure (IP, UA, timestamp). Catches the slow
  brute-force attacker who paces under the 5/min rate limit.
- **Session rotation on login** — `request.session.clear()` before
  issuing the auth flag, defense-in-depth against session fixation.
- **Idle session timeout** — sliding window via
  `SESSION_IDLE_TIMEOUT_SECONDS` (default 14400s = 4 hours). Sessions
  past the threshold get 401'd and cleared.

### Security
- **App-level `HTTPSRedirectMiddleware`** + HSTS (2-year, includeSubDomains,
  preload) when `FORCE_HTTPS=true`. Session cookie carries `Secure` flag
  in the same conditional.
- **Content-Security-Policy** — `frame-ancestors none`, `object-src
  none`, `form-action self`, Stripe origins allowlisted.
- **Startup fail-hard checks** (production only): refuses to start if
  `PAYROLL_ENCRYPTION_SECRET` is the dev default, `DATABASE_URL` lacks
  `sslmode`, or `FORCE_HTTPS=false`.
- **Portal token expiry** — 1-year hard + 90-day sliding idle. Expired
  tokens return `410 Gone`.
- **Portal headers** — `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store` on every portal response.
- **Encryption key versioning** — bank PII ciphertext now prefixed with
  `v1:`. `PAYROLL_ENCRYPTION_SECRET_PREV` env var supports
  zero-downtime key rotation; decrypt tries current key first, then
  previous.
- **Per-endpoint rate limiting** — portal at 30/min GET / 10/min POST,
  joining the existing 5/min on login.

### Dependency CVE pass
Bumped requirements.txt to close known CVEs surfaced by `pip-audit`:
- `cryptography` — cap raised from `<44.0` to `<47.0`, floor `46.0.5`
  (closes PYSEC-2026-35, CVE-2024-12797, CVE-2026-26007, etc.)
- `fastapi` — bumped from `0.115.0` to `>=0.121.0,<0.122` to allow
  starlette `0.47+`
- New explicit `starlette>=0.47.2,<0.50` pin (closes CVE-2024-47874,
  CVE-2025-54121)
- New explicit `pyjwt>=2.10.0,<3.0` pin (override intuit-oauth's
  transitive 2.7.0 with known CVE)

`pip-audit -r requirements.txt` now reports **zero known
vulnerabilities**.

### Wiring fixes
Spider-web audit of every `API.*` call against every `@router.*`
handler caught four real breakages:
- 3× `API.delete()` typos (`API.del` is the actual export) — `employees.js`,
  `deductions.js`
- Missing `GET /api/pto/policies/{id}` and `PUT /api/pto/policies/{id}` —
  the policy-edit form was 404'ing
- `/approve` and `/reject` alias routes for the PTO `/decision` endpoint —
  the buttons were hitting non-existent paths

### Docs + repo conventions
- **`CONTRIBUTING.md`**, **`.github/PULL_REQUEST_TEMPLATE.md`**,
  **`.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.{md,yml}`** — the
  standard set this size of repo should have had.
- **`docs/hipaa-compliance.md`** — Security Rule mapping, 8-gap honest
  assessment, deployment recommendations.
- **`docs/security-hardening.md`** + **`docs/wiring-audit.md`** + **`docs/todo.md`** — engineering
  logs for the hardening pass, the wiring audit methodology, and the
  internal TODO scratchpad.
- README de-Phased / de-Tiered — that history now lives in this
  CHANGELOG file instead of cluttering the user-facing readme.

### Cleanup
- **Alembic revision collision fixed** — tier1 was sharing
  `f6a7b8c9d0e1` with the Phase 11 inventory migration. Renamed to
  `f7a8b9c0d1e2`; chain is now linear.
- `test_frontend_pages.py` moved from repo root to
  `scripts/integration_test_frontend.py` (it's a live-HTTP integration
  script, not a unit test).
- `app/templates/invoice_pdf_v2.html` deleted — added 5 weeks ago but
  never wired into `pdf_service.py`.
- `backups/` directory kept tracked (via `.gitkeep`) but contents
  gitignored so dumps don't accidentally land in commits.

### Test coverage
276 tests passing. Up from 119 at the start of this branch's work.
All previously-passing tests still pass.

### Docs reorganization
Root now keeps only `README.md`, `INSTALL.md`, `SECURITY.md`,
`CHANGELOG.md`, `CONTRIBUTING.md` (the conventional set). Everything
else moved into `docs/`.

## [2.0.0] — May 2026

### Added
- **Analytics dashboard** at `#/analytics` — KPI cards plus four charts
  (12-month revenue line, expenses doughnut, A/R+A/P stacked bar,
  90-day cash forecast), MTD/QTD/YTD period selector, CSV/PDF export
  with branded headers.
- **AI Insights** — Optional one-shot executive brief (3 observations /
  3 risks / 3 recommendations) with seven supported providers (xAI Grok,
  Groq, Cloudflare Workers AI, Cloudflare self-hosted gateway, Anthropic
  Claude, OpenAI, Google Gemini). Bring-your-own-key, encrypted at rest.
- **AI Predefined Analyses** — 11 curated actions across 5 categories,
  replacing the earlier free-form chat (more reliable across providers).
- **Inventory ledger** — Perpetual inventory with weighted-average cost,
  automatic COGS journal entries on every sale, reorder points,
  Adjust modal for add/remove/set-to-count.
- **Drill-down reports** — P&L and Balance Sheet rows are click-through
  to source transactions with running balance and source-doc links.
- **Saved Reports** — Name and one-click rerun favorite report configs.
- **Duplicate detection** — Fuzzy matching on customer/vendor names with
  a confirm-and-create-anyway dialog.
- **Setup wizard** collects operator name + email + company name + email
  + password (was password-only).
- **Branded headers** on PDF/CSV exports (SlowBooks Pro 2026 wordmark +
  company logo).

### Changed
- AI provider config moved from a modal to a Settings sub-page with a
  curated model dropdown and Custom escape hatch.
- Items form gained the full inventory toolset (track checkbox, qty,
  reorder point, asset account).
- Customers/Vendors gained the duplicate-warning confirm dialog.

### Security
- **Single-user authentication** — Argon2id-hashed password, session
  cookie (`same_site=strict`, 30-day TTL).
- **Rate limiting** — slowapi at 5 logins/minute per IP.
- **Security headers** — X-Content-Type-Options, X-Frame-Options DENY,
  Referrer-Policy, Permissions-Policy on all responses.
- **CORS lockdown** — explicit origin allowlist, no wildcards.
- **Path traversal protection** — backup and attachment endpoints use
  `Path.is_relative_to()`.
- **Atomic secret writes** — session key uses `mkstemp` + `os.replace()`.
- **Fernet encryption** for AI provider API keys.
- **SSRF protection** — AI provider URLs validated against private IPs
  and metadata endpoints.
- **Constant-time secret compare** in the Cloudflare Worker gateway.
- **Schema-validated AI config payloads.**
- **CSV formula injection protection** — exports neutralize `=`, `+`,
  `-`, `@` cell prefixes.
- **Non-root Docker** — container runs as UID 1000.

### Performance
- Analytics dashboard: 10 SQL queries, ~26 ms engine on 3,000 invoices
  plus 1,500 bills.
- Test suite runs in under 30 seconds with zero network dependencies.

### Fixed
- Dark mode now works on every report subtotal row (missing `--gray-50`
  definition).
- `--text-main` typo fixed.

## Earlier releases

Internal build history before v2.0.0 lived under "Phases" 1-11. A
recap of what each phase covered:

| Phase | Scope |
|-------|-------|
| 1 | Foundation — audit log, full-text search |
| 2 | Accounts Payable — POs, bills, bill payments, credit memos |
| 3 | Productivity — recurring invoices, batch payments |
| 4 | Communication & Export — CSV import/export, uploads |
| 5 | Advanced integration — bank import (OFX/CSV), tax export, backups |
| 6 | Companies, employees, payroll |
| 7 | Online payments (Stripe) |
| 8 | QuickBooks Online sync |
| 9 | Analytics + journal entries + deposits + credit-card charges + checks |
| 9.5 | AI Insights layer |
| 9.7 | Single-user authentication, rate limiting, security audit pass |
| 10 | Bank rules, budgets, attachments, email templates |
| 11 | Inventory ledger, drill-down reports, fuzzy duplicate detection, saved reports |

The payroll/HR module was layered separately:

| Tier | Scope |
|------|-------|
| 1 | Onboarding checklists, time entries, PTO |
| 2 | Deductions, garnishments, gross-up calculator |
| 3 | Tax forms (W-2/W-3/940/941), employee self-service portal |

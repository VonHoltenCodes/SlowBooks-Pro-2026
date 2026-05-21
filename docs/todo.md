# TODO / Working Notes

Internal scratchpad for things we know we need to do but haven't shipped
yet. Not user-facing — the README and CHANGELOG don't link here on
purpose. When something on this list lands, move it to `CHANGELOG.md`
under `[Unreleased]` and delete it from here.

---

## ⚠ Test coverage gaps — models without direct test imports

The audit found 20 models that aren't directly imported by any test
module. Most are exercised indirectly through their API routes (a
`POST /api/bills` test exercises `app/models/bills.py`), but no test
imports the model class and pokes its constraints / defaults / hybrid
properties directly. That's a real risk surface: subtle regressions
in constructors, computed columns, or relationship cascades can ship
silently.

**Priority — financial integrity (test these first):**
- `app/models/credit_memos.py` — reversing journal entries, balance math
- `app/models/recurring.py` — schedule generation, next-occurrence math
- `app/models/banking.py` — reconciliation state, bank-transaction matching
- `app/models/deductions.py` — pre/post-tax classification affects pay-run math
- `app/models/purchase_orders.py` — convert-to-bill workflow

**Priority — HR / payroll adjacent:**
- `app/models/hr.py` — onboarding tasks, employee documents
- `app/models/time_entries.py` — approval state machine, overtime math
- `app/models/tax.py` — tax-rate snapshots used by historical reports

**Lower priority — config / admin:**
- `app/models/settings.py` — flat key/value store
- `app/models/audit.py` — automatic logging via SQLAlchemy hooks (covered by integration tests)
- `app/models/backups.py` — backup metadata only, no compute
- `app/models/companies.py` — multi-company switching
- `app/models/email_log.py`, `app/models/email_templates.py`
- `app/models/bank_rules.py`, `app/models/budgets.py`
- `app/models/qbo_mapping.py` — id mapping table only
- `app/models/saved_reports.py` — name + JSON blob
- `app/models/attachments.py` — file metadata; the file-upload routes are tested
- `app/models/estimates.py` — convert-to-invoice covered by API tests

Coverage strategy: a single `tests/models/test_<model>.py` per priority
item, asserting (a) the model can be constructed with required fields,
(b) defaults populate correctly, (c) computed columns / hybrid
properties return expected values, (d) cascade deletes behave.

---

## Payroll / HR — still open

- **State unemployment filings (SUI)** — `app/services/tax_forms/state_sui.py`
  has scaffolding; needs per-state form rendering + an endpoint.
- **E-Verify submission flow** — schema already has `everify_case_number`
  but there's no submit / status-check integration.
- **Portal-token UI on admin side** — admin Employee Details modal
  should show "Last used N days ago" + "Expires DATE" alongside the
  token (the `expires_at` is already in the API response).
- **PTO accrual editor UI** — no SPA form for `POST /api/pto/accruals`.
  Admins currently use curl. Either build the form or add a "Enroll
  all active employees in policy X" button on the policies page.

---

## Security / ops

- **CSP off `'unsafe-inline'`** — once the bootstrap `<script>` block in
  `index.html` moves to an external file, drop `'unsafe-inline'` from
  `script-src` and tighten to a nonce-based CSP.
- **Encryption key rewrap CLI** — the rotation flow supports
  `PAYROLL_ENCRYPTION_SECRET_PREV` for transparent reads under both
  keys, but we never wrote the offline
  `python -m app.services.encryption rewrap` command referenced in
  the encryption module docstring.
- **Portal access audit log** — every successful portal access already
  rolls `portal_token_last_used`. A dedicated audit row per request
  (with IP, user-agent) would make incident response easier and would
  layer cleanly on top of the existing `login_attempts` pattern.
- **Pen test against a staging deploy** — never done.
- **Quarterly `pip-audit` sweep** — should be a recurring task or a
  Dependabot rule. Manual recipe: `pip-audit -r requirements.txt`.

---

## Frontend polish

- **Drag-and-drop for document uploads** — current employee documents
  tab is click-to-browse only.
- **Portal logout button** — endpoint exists (`POST /portal/logout`)
  but the templates don't render a button.

---

## Tests

- **Wiring audit as a unit test** — the wiring disconnects we've fixed
  were all caught by manual grep. A lightweight test that imports the
  FastAPI app and checks "every JS path appears in `app.routes`" would
  catch the next batch automatically.
- **WeasyPrint smoke tests** — current PDF tests check the `%PDF-`
  header and size; one `test_*_pdf_renders_non_empty` per template
  per pixel-comparison or text-extraction would catch broken layouts.
- **Portal flow end-to-end** — TestClient covers each endpoint, but no
  test walks the full token-mint → claim → cookie session → expire →
  rotate cycle in one go.

---

## Polish

- **`.env.example`** — we document `PAYROLL_ENCRYPTION_SECRET`,
  `FORCE_HTTPS`, etc. in `docs/security-hardening.md` and SECURITY.md
  but there's no example env file to copy.
- **Docker Compose for production** — `docker-compose.yml` is dev-only.
  Need a prod compose with nginx + TLS + the env vars laid out.
- **Branded portal favicon** — currently inherits the SPA favicon.
  Could fall back to the company logo when set.

---

## Known small bugs

- Pay-stub PDF doesn't show negative deductions correctly — off-cycle
  reimbursements come through as positive line items.

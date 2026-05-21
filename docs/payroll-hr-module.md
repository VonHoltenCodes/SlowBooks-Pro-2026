# Payroll / HR Module — Tier 1-3

The payroll/HR module is layered in tiers so the surface area grows
intentionally rather than as a sprawl. This document is the reference for
what's in each tier, where each piece lives, and what's still pending.

## Status snapshot

| Layer | What it covers | Backend | Admin UI | Tests |
|-------|----------------|---------|----------|-------|
| **Tier 1** | Onboarding checklist, time entries, PTO policies/requests | ✅ | ✅ | ✅ |
| **Tier 2** | Deductions (401k, HSA, etc.), garnishments | ✅ | ✅ | ✅ |
| **Tier 3 — Tax forms (JSON)** | W-2, W-3, Form 940, Form 941 endpoints — machine-readable | ✅ | ✅ | ✅ |
| **Tier 3 — Tax forms (PDF)** | WeasyPrint-rendered, employer-branded, audit-hashed | ✅ | ✅ | ✅ |
| **Tier 3 — Document audit hashes** | SHA-256 chain in PDF footer + `document_audits` table | ✅ | n/a | ✅ |
| **Tier 3 — Portal** | Token-accessed self-service for pay stubs, W-4, bank, PTO | ✅ | n/a | ✅ |
| **Tier 3 — Portal cookie session** | URL token only at first claim; subsequent navigation is cookieless | ✅ | n/a | ✅ |
| **Tier 3 — Portal hardening** | Expiration, no-referrer, rate limiting, employer branding | ✅ | ✅ | ✅ |
| **PTO year-end carryover** | Batch endpoint applies policy carryover caps + resets YTD | ✅ | n/a | ✅ |
| **Time-entry → pay-run auto-population** | Pay-run form checkbox pulls approved unpaid hours | ✅ | ✅ | ✅ |

276 tests pass across the full suite.

---

## Tier 1 — Onboarding, time, PTO

### Models

| File | Class | Purpose |
|------|-------|---------|
| `app/models/hr.py` | `OnboardingChecklist`, `OnboardingTask` | 8-task new-hire checklist with sign-off |
| `app/models/time_entries.py` | `TimeEntry` | Per-day hours with state, approval status |
| `app/models/pto.py` | `PTOPolicy`, `PTORequest`, `PTOAccrual` | Policy definitions, requests, YTD balances |

### Routes

| Method + Path | Handler | Purpose |
|---------------|---------|---------|
| `GET /api/onboarding/{emp_id}` | `onboarding.get_checklist` | Fetch checklist (seeds if absent) |
| `POST /api/onboarding/{emp_id}/seed` | `onboarding.seed_checklist` | Reset / re-seed standard tasks |
| `PUT /api/onboarding/tasks/{task_id}` | `onboarding.update_task` | Mark signed / complete |
| `POST /api/onboarding/tasks/{task_id}/complete` | `onboarding.complete_task` | Convenience for the SPA checkbox |
| `GET /api/onboarding/{emp_id}/new-hire-report` | `onboarding.new_hire_report` | JSON for state new-hire reporting |
| `GET /api/onboarding/{emp_id}/new-hire-report/pdf` | `onboarding.new_hire_pdf` | Downloadable PDF |
| `GET /api/time-entries` | `time_entries.list_entries` | List, filterable by employee/date range |
| `POST /api/time-entries` | `time_entries.create_entry` | Create a time entry |
| `PUT /api/time-entries/{entry_id}` | `time_entries.update_entry` | Edit |
| `DELETE /api/time-entries/{entry_id}` | `time_entries.delete_entry` | Remove |
| `POST /api/time-entries/{entry_id}/approve` | `time_entries.approve_entry` | Manager workflow |
| `POST /api/time-entries/{entry_id}/reject` | `time_entries.reject_entry` | Manager workflow |
| `GET /api/pto/policies` | `pto.list_policies` | All policies |
| `GET /api/pto/policies/{policy_id}` | `pto.get_policy` | Single policy (added during wiring audit) |
| `POST /api/pto/policies` | `pto.create_policy` | New policy |
| `PUT /api/pto/policies/{policy_id}` | `pto.update_policy` | Edit policy (added during wiring audit) |
| `GET /api/pto/requests` | `pto.list_requests` | All PTO requests, filterable |
| `POST /api/pto/requests` | `pto.create_request` | Submit a request |
| `POST /api/pto/requests/{request_id}/decision` | `pto.decide_request` | Canonical approve/reject endpoint |
| `POST /api/pto/requests/{request_id}/approve` | `pto.approve_request` | Alias for `decision(status=approved)` |
| `POST /api/pto/requests/{request_id}/reject` | `pto.reject_request` | Alias for `decision(status=denied)` |

### Frontend

- `app/static/js/onboarding.js` (280 lines) — checklist UI with per-task signature
- `app/static/js/time_entries.js` (220 lines) — entry list + approve/reject buttons
- `app/static/js/pto.js` (250 lines) — policies + requests with workflow buttons

---

## Tier 2 — Deductions & garnishments

### Models

| File | Class | Purpose |
|------|-------|---------|
| `app/models/deductions.py` | `DeductionType` | Catalog: 401k, health insurance, HSA, union dues |
| | `EmployeeDeduction` | Per-employee election with amount + effective dates |
| | `Garnishment` | Court-ordered wage garnishment with priority |

### Routes

| Method + Path | Purpose |
|---------------|---------|
| `GET /api/deductions/types` | List deduction-type catalog |
| `POST /api/deductions/types` | Create custom deduction type |
| `POST /api/deductions/types/seed-standard` | One-shot seed for fresh installs |
| `GET /api/deductions/employee/{emp_id}` | All deductions for an employee |
| `POST /api/deductions/employee` | Enroll an employee in a deduction |
| `DELETE /api/deductions/employee/{deduction_id}` | Remove an enrollment |
| `GET /api/deductions/garnishments` | List, filterable by `?employee_id=...` |
| `POST /api/deductions/garnishments` | Create a garnishment order |
| `DELETE /api/deductions/garnishments/{order_id}` | Cancel an order |

### Frontend

- `app/static/js/deductions.js` (365 lines) — three-section page (types,
  per-employee, garnishments) with add/remove forms

---

## Tier 3 — Tax forms

### Routes

All return JSON for now (PDF generation via WeasyPrint is the pending
work — see "Pending items" below). The SPA buttons open the response in a
new tab.

| Method + Path | Returns |
|---------------|---------|
| `POST /api/payroll/forms/w2/{emp_id}?year=YYYY` | W-2 boxes 1-6 + employee/employer identifiers |
| `POST /api/payroll/forms/w3/{year}` | W-3 aggregate across all active employees |
| `POST /api/payroll/forms/940/{year}` | Form 940 FUTA — first $7K/employee at 0.6% |
| `POST /api/payroll/forms/941/{year}/{quarter}` | Quarterly FICA aggregation |

### Frontend

- `app/static/js/tax_forms.js` — picker for year/quarter, blob-opens the
  response

---

## Tier 3 — Self-service portal

### Models

`Employee.portal_token` (192-bit, from `secrets.token_urlsafe(24)`),
plus expiration tracking:

```python
portal_token = Column(String(64), nullable=True, unique=True)
portal_token_last_used = Column(DateTime(timezone=True), nullable=True)
portal_token_expires_at = Column(DateTime(timezone=True), nullable=True)
```

### Routes — admin (session-authed)

| Method + Path | Purpose |
|---------------|---------|
| `GET /api/employees/{id}/portal-token` | Mint or return existing token + expiry |
| `POST /api/employees/{id}/portal-token` | Rotate token (resets expiry windows) |

### Routes — employee (token-authed, rate-limited)

All return `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

| Method + Path | Rate limit | Purpose |
|---------------|------------|---------|
| `GET /portal/{token}` | 30/min | Dashboard |
| `GET /portal/{token}/paystubs` | 30/min | List processed pay stubs |
| `GET /portal/{token}/profile` | 30/min | W-4 + address form |
| `POST /portal/{token}/profile` | 10/min | Save W-4 + address |
| `GET /portal/{token}/bank` | 30/min | List direct-deposit accounts |
| `POST /portal/{token}/bank` | 10/min | Add bank account (Fernet-encrypted at rest) |
| `GET /portal/{token}/pto` | 30/min | Balances + request form |
| `POST /portal/{token}/pto` | 10/min | Submit a PTO request |

### Token lifecycle

- **Hard expiry**: 1 year from mint, in `portal_token_expires_at`.
- **Idle expiry**: 90 days since `portal_token_last_used`. Rolled forward
  on every authenticated request, so an active user never trips it.
- **Expired tokens** return `410 Gone`. The admin can issue a fresh one
  via the rotate endpoint.

---

## Admin UI pages

| Route | Page | JS module |
|-------|------|-----------|
| `#/hr/onboarding` | Employee list with completion % | `onboarding.js` |
| `#/hr/time-entries` | Time entries with approve/reject | `time_entries.js` |
| `#/hr/pto` | Policies + pending requests | `pto.js` |
| `#/hr/deductions` | Types, per-employee, garnishments | `deductions.js` |
| `#/hr/tax-forms` | W-2/W-3/940/941 generation | `tax_forms.js` |
| `#/employees/{id}` | Details modal with portal/YTD/bank/docs tabs | `employees.js` |

---

## Pending items

The major Tier 3 work has shipped — tax PDFs with audit hashes, the
cookie-based portal session, PTO year-end carryover, and time-entry →
pay-run auto-population are all live. What's left is in `docs/todo.md`:

- **State SUI filings** — `app/services/tax_forms/state_sui.py` has
  scaffolding; needs per-state form rendering + an endpoint
- **E-Verify submission flow** — schema has `everify_case_number` but
  no integration with the federal system
- **Portal-token UI on admin side** — show expiry and last-used
  inline (the API already returns `expires_at`)
- **CSP nonce mode** — drop `'unsafe-inline'` once the inline bootstrap
  script in `index.html` moves to an external file
- **Penetration test against a staging deploy** — never done
- **Encryption rewrap CLI** — `python -m app.services.encryption rewrap`
  for offline key rotation; in-flight rotation via
  `PAYROLL_ENCRYPTION_SECRET_PREV` already works

---

## Where it all lives

```
app/
├── models/
│   ├── hr.py                  # onboarding, employee documents
│   ├── pto.py                 # policies, accruals, requests
│   ├── time_entries.py        # daily hours tracking
│   ├── deductions.py          # types, enrollments, garnishments
│   ├── bank_accounts.py       # encrypted direct-deposit
│   └── payroll.py             # Employee, PayRun, PayStub
├── routes/
│   ├── onboarding.py          # Tier 1
│   ├── time_entries.py        # Tier 1
│   ├── pto.py                 # Tier 1
│   ├── deductions.py          # Tier 2
│   ├── tax_forms.py           # Tier 3 (UI placeholder routes)
│   ├── payroll.py             # Tier 3 — forms endpoints + core payroll
│   ├── portal.py              # Tier 3 — self-service portal
│   └── employees.py           # Cross-cutting: portal-token mint, documents
├── services/
│   ├── encryption.py          # Fernet with v1: versioning
│   ├── pto_accrual.py         # Per-period accrual math
│   └── payroll_service.py     # Withholding, FICA, FUTA, state tax
├── schemas/
│   ├── hr.py
│   ├── pto.py
│   ├── deductions.py
│   ├── time_entries.py
│   └── payroll.py
└── static/js/
    ├── onboarding.js
    ├── time_entries.js
    ├── pto.js
    ├── deductions.js
    ├── tax_forms.js
    └── employees.js           # extended with portal/YTD/bank/docs tabs
```

---

## Related docs

- [security-hardening.md](security-hardening.md) — the production-readiness pass that hardened the portal, encryption, and HTTPS layers
- [wiring-audit.md](wiring-audit.md) — the spider-web audit methodology and the four disconnects we fixed
- [../SECURITY.md](../SECURITY.md) — public security policy / responsible disclosure

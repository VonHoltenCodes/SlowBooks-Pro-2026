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
- `app/models/settings.py`, `app/models/audit.py`, `app/models/backups.py`,
  `app/models/companies.py`, `app/models/email_log.py`,
  `app/models/email_templates.py`, `app/models/bank_rules.py`,
  `app/models/budgets.py`, `app/models/qbo_mapping.py`,
  `app/models/saved_reports.py`, `app/models/attachments.py`,
  `app/models/estimates.py`

Coverage strategy: a single `tests/models/test_<model>.py` per priority
item, asserting (a) construction with required fields, (b) defaults,
(c) computed columns / hybrid properties, (d) cascade deletes.

---

## Payroll / HR — still open

- **State unemployment filings (SUI)** — `app/services/tax_forms/state_sui.py`
  has scaffolding; needs per-state form rendering + an endpoint. Only
  remaining payroll feature on the wishlist.

---

## Security / ops — still open

- **CSP `script-src` `'unsafe-inline'` removal** — index.html is clean
  as of the bootstrap.js refactor. What's left: the inline `onclick=`
  + inline `style=` attributes in JS-rendered modal HTML across roughly
  two dozen files. Two viable paths:
    1. Per-file rewrite — every `innerHTML = '<button onclick=...>'`
       becomes addEventListener after the assignment. Touches every JS
       file but each change is local.
    2. Delegated dispatcher — one document-level click handler reads
       `data-action` attributes and resolves them via a small registry.
       Less code total, but every modal template still needs updating
       from `onclick=` to `data-action=`.
  Same scope either way. Defense-in-depth, not an active vuln; tracked
  honestly in docs/security-hardening.md.
- **Penetration test against a staging deploy** — external scope,
  can't be done in-repo.

---

## Known small bugs

(none.)

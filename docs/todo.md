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

## Future work — flagged, not started

- **Activity log / CRM timeline per customer** — calls, emails, meetings,
  in-app notes logged against a Customer and rendered as a unified
  chronological feed on the customer details modal. Likely model: a
  polymorphic `ActivityEntry(entity_type, entity_id, kind, body, occurred_at,
  created_by)`. Rationale: notes-only is too thin; ops teams need to see
  "we called this customer last Tuesday." The audit_log table already
  captures system-level changes — this would be human-entered activity.
- **Email integration** — outbound real send via SMTP / SES / Postmark
  (currently `email_log` records intent but no transport is wired). Once
  transport lands, replies/bounces feed back into the activity log above.
  Will need a per-company SMTP config, bounce-handling webhook, and a
  rate-limit on auto-sent dunning / reminder emails.
- **Inbox watcher — AI-staged inbound email routing.** Higher-order feature
  built on top of the email integration above. The operator authorizes a
  mailbox (Microsoft Graph + Entra ID app registration, or IMAP for the
  self-host path); a background worker polls or subscribes for new mail.
  Each inbound message goes through:
  1. **Parse** — pull obvious refs from subject + body: PO numbers, invoice
     numbers, customer/vendor names, dollar amounts, dates.
  2. **Match** — fuzzy-match parsed refs against live records (the same
     duplicate-detection engine already at `/api/customers/check-duplicate`
     extends to invoice-number + PO-number lookup).
  3. **Classify + sentiment** — pass the body through the AI layer (the
     existing 7-provider BYOK config in `app/services/ai_service.py`) to
     tag intent (payment confirmation / dispute / inquiry / quote /
     unrelated) plus sentiment (positive / neutral / negative). Tag goes
     into the staged record; AI never auto-acts.
  4. **Stage** — write a row to a new `inbound_email_queue` table with the
     parsed refs, matched records, classification, sentiment, and the raw
     message text + attachments. NEVER auto-apply.
  5. **User review** — a "Mail queue" page lists the staged items grouped
     by suggested action (Apply payment / Acknowledge dispute / Reply to
     inquiry / Archive). Operator clicks Confirm; the action fires through
     the existing API (e.g. `POST /api/payments` with allocation derived
     from the matched invoice).
  Differentiator vs Power Apps / Zapier composites: it's one click in your
  bookkeeper, not a six-step automation built in a separate tool.
  Pre-reqs: `inbound_email_queue` table + worker process + per-mailbox auth
  config (Entra app registration walkthrough in setup-mail.md) + a Mail
  queue UI page. Depends on the Email integration item above (shared SMTP
  + IMAP wiring).
- **DocumentAudit (hash-chain) viewer UI** — endpoints ready
  (`/api/document-audits`, `/api/document-audits/verify/{hash}`); need an
  admin "Compliance" tab.
- **Portal time-entry submit flow** — server endpoint
  `POST /api/time-entries/{id}/submit` exists for employee self-service;
  portal UI page does not.
- **Stripe upgrade / checkout** — `POST /api/stripe/create-checkout-session`
  ready; surfacing requires a pricing-page + plan model. Single-tier today.

### Recently wired (was dark-endpoint backlog)
- ~~Inventory item movement history UI~~ — DONE: "History" button on each
  tracked item opens the movement ledger (ItemsPage.showMovements).
- ~~AP aging report in the Reports menu~~ — DONE: A/P Aging card alongside
  A/R Aging (ReportsPage.apAging).
- ~~Audit log viewer~~ — DONE: already built + nav-linked; fixed so the
  table loads on open instead of staying empty until a filter is touched.
- ~~Bill-payment void~~ — DONE: `POST /api/bill-payments/{id}/void`
  implemented with row-lock, closing-date guard, reversing JE, and
  `is_voided` idempotency. Mirrors the customer-payment void on the AP side.
  6 void-symmetry tests + closing-date guard test cover the new endpoint.

---

## Known small bugs

(none.)

---

## Production walkthrough — discovered, addressed

Findings from the 2026-05-30 live end-to-end production walkthrough.
All addressed; documenting here so the lessons don't get lost.

- ~~Payroll JE unbalanced when liability accounts missing~~ — DOCUMENTED:
  the failure mode (umbrella `2300` plus `2310`–`2360` required for
  per-tax accounts) is now called out in
  `docs/release-checklist.md §3a` with the full table of
  numbers/names/types. The 500 itself is not a bug — the JE balance
  check is doing its job — but the onboarding gap was real.
- ~~Tax rate format ambiguity~~ — `tax_rate` is a decimal fraction
  (`0.086` = 8.6%), not percentage points. Schema rejects values
  outside `[0, 1]`. Consistent across invoices, bills, POs,
  estimates, credit memos, and recurring.
- ~~Bill payment routing~~ — bills are paid via `POST /api/bill-payments`
  (vendor-level), not `POST /api/bills/{id}/pay` (which never existed).
  Mirrors customer payments at `POST /api/payments`.
- ~~PO→Bill convert response shape~~ — returns
  `{"bill_id": N, "message": "..."}`, not a full BillResponse. The
  caller fetches the bill via `GET /api/bills/{bill_id}` if it needs
  the full row.

# Data Model

Schema reference for a SlowBooks Pro company database: one SQLite file per
company on the desktop and Server Edition, or a PostgreSQL database. 105
tables on a double-entry accounting foundation (Alembic head
`4810bd59e481` in 2.31.3). For migration history, see the files under
`migrations/versions/`; for model code, see `app/models/`. The construction
and job-cost tables are described in their own sections below the table.

| Table | Purpose |
|-------|---------|
| `accounts` | Chart of Accounts — asset, liability, equity, income, expense, COGS; `parent_id` for sub-accounts, `detail_type` (one of 15, drives the statements' grouping) |
| `customers` | Customer contacts with billing/shipping addresses; `parent_customer_id` and `bill_with_parent` for sub-customers (2.31.1) |
| `vendors` | Vendor contacts |
| `items` | Product/service/material/labor items with rates |
| `invoices` | Invoice headers with status tracking |
| `invoice_lines` | Invoice line items |
| `estimates` | Estimate headers |
| `estimate_lines` | Estimate line items |
| `payments` | Payment records |
| `payment_allocations` | Maps payments to invoices (many-to-many) |
| `transactions` | Journal entry headers: `date`, `reference`, `description`, `memo`, `source_type`/`source_id` (the document that posted it), `class_id`, `job_id`, `vendor_id`, `is_adjusting`, `posted_by`; from an imported ledger (2.31.2) `source_ref` (QuickBooks' Num), `source_kind` (its Type) and `source_difference` (what posted to 3950 Import Differences) |
| `transaction_lines` | Journal entry splits (debit OR credit); `cleared` / `reconciliation_id` on bank-account lines |
| `accounts.bank_kind` | `bank` / `credit_card` — which chart accounts the register, pickers and transfers use |
| `bank_accounts` | A bank feed: the statement identity of a ledger account (`account_id`); `legacy_balance` = the pre-2.10 register balance until posted or dismissed |
| `bank_transactions` | Statement lines (feeds, OFX/CSV imports) with `match_status` and the ledger line they match (`transaction_line_id`) |
| `reconciliations` | Reconciliation sessions over a ledger account's lines (`account_id`, `beginning_balance`, `cleared_total`) |
| `settings` | Company settings key-value store |
| `audit_log` | Automatic change tracking for all entities |
| `purchase_orders` | Purchase order headers |
| `purchase_order_lines` | PO line items with received quantities |
| `bills` | Vendor bills (AP mirror of invoices) |
| `bill_lines` | Bill line items with expense account tracking |
| `bill_payments` | Bill payment records |
| `bill_payment_allocations` | Maps bill payments to bills |
| `credit_memos` | Customer credit memos |
| `credit_memo_lines` | Credit memo line items |
| `vendor_credits` | Supplier credits against Accounts Payable |
| `vendor_credit_lines` | Vendor credit line items (carry the expense account, like bill lines) |
| `vendor_credit_applications` | Which bill a vendor credit settled, and for how much |
| `credit_applications` | Maps credit memos to invoices |
| `recurring_invoices` | Recurring invoice templates |
| `recurring_invoice_lines` | Recurring invoice line items |
| `email_log` | Email delivery history |
| `tax_category_mappings` | Account-to-tax-line mappings; one string per account, read against the return the entity type files (Schedule C, Form 1065, 1120 or 1120-S) (#488) |
| `backups` | Backup file records |
| `companies` | Multi-company database list |
| `employees` | Employee records for payroll |
| `pay_runs` | Pay run headers with totals |
| `pay_stubs` | Individual pay stubs with withholding breakdowns |
| `qbo_mappings` | Which local record a QuickBooks Online record became, written by the connector retired in 2.23.2; read so those documents still void and edit right |
| `attachments` | File attachments linked to invoices, bills, etc. |
| `bank_rules` | Pattern-matching rules for auto-categorizing bank imports |
| `budgets` | Budget amounts by account and period |
| `email_templates` | Customizable email templates |
| `inventory_movements` | Per-item qty/cost ledger (purchases, sales, adjustments) |
| `saved_reports` | Named (report_type + parameters) tuples |
| `document_audits` | A SHA-256 hash per generated tax-form PDF (W-2/W-3/940/941), printed in the form's footer |
| `portal_accesses` | Audit log for self-service portal hits (success + failure) |
| `login_attempts` | Authentication-attempt audit log |
| `reseller_permits` | Per-entity sales-tax reseller permits with expiration + verification trail |
| `users`, `user_preferences` | Named sign-ins with a role (administrator, bookkeeper, accountant, read-only), end date, one-time-password flag; per-user preferences |
| `api_tokens` | Scoped API tokens: label, role (never administrator), `allow_backups`, hash and hint, revoked flag |
| `request_keys` | Idempotency keys seen on write requests |
| `classes` | Classes (funds in a nonprofit) with the Uncategorized default |
| `invoice_messages` | Saved messages for emailed invoices |
| `memorized_transactions`, `memorized_transaction_lines` | Memorized bills and journal entries with their schedule (2.31.1) |
| `duplicate_dismissals` | Possible Duplicates groups marked Not a duplicate |
| `stored_files` | Files kept inside the company file: the logo, attachments, employee documents (since 2.18) |
| `ocr_templates` | Per-merchant receipt layouts remembered by Scan Receipt |
| `accountants_copies` | Accountant's copies sent and applied, with the dividing date |
| `fixed_assets`, `fixed_asset_types` | Fixed assets, their type, depreciation method and runs |
| `employee_bank_accounts` | Direct-deposit accounts (routing and account numbers encrypted) |
| `benefit_codes`, `benefit_rates`, `employee_benefits`, `employee_groups`, `employee_group_benefits`, `benefit_ytd`, `pay_stub_benefits` | The benefits engine: codes with rules and effective-dated rates, enrolments by employee or group, year-to-date and per-stub amounts |
| `garnishment_orders` | Garnishment orders and their ends |
| `onboarding_tasks` | An employee's onboarding checklist |
| `time_entries` | Time entries with approval and job-cost posting |
| `pto_policies`, `pto_requests`, `pto_accruals` | Time off |
| `payroll_opening_balances`, `payroll_opening_quarters`, `payroll_opening_state_balances`, `payroll_opening_benefits` | Opening year-to-date payroll from an earlier system, quarter by quarter |
| `allocation_rules`, `allocation_rule_targets`, `functional_allocations`, `functional_allocation_lines` | Nonprofit functional-expense allocations |
| `in_kind_gifts`, `in_kind_gift_lines` | In-kind gifts |
| `restriction_releases` | Releases from restriction between net-asset classes |
| `jobs`, `job_budgets`, `job_costs`, `job_cost_lines`, `job_cost_to_complete`, `job_deposits`, `cost_codes`, `cost_types`, `equipment`, `trade_packages`, `trade_package_sections`, `sov_lines`, `change_orders`, `change_order_lines`, `pay_applications`, `pay_application_lines`, `lien_waivers` | Jobs, job costing and construction mode: described section by section below |

## jobs (Customer:Job / Projects)

| column | notes |
|---|---|
| id, customer_id (FK customers) | a job belongs to exactly one customer |
| name, job_number | name unique per customer, case-insensitive (API-enforced) |
| status | pending · awarded · in_progress · closed · not_awarded |
| job_type, description, site_address, notes | free text |
| start_date, projected_end_date, end_date | |
| contract_amount | Numeric(15,2), drives billed-vs-contract |
| is_active | inactive = hidden from pickers, kept on history |

`job_id` (nullable FK) lives on: transactions, transaction_lines, invoices,
invoice_lines, bills, bill_lines, estimates, estimate_lines, purchase_orders,
purchase_order_lines, credit_memos, credit_memo_lines, vendor_credits,
vendor_credit_lines, recurring_invoices, memorized_transactions and their
lines, in_kind_gifts and their lines, allocation_rule_targets, time_entries.
`class_id` rides transactions and every document header and line that
posts (transaction_lines, invoice_lines, bill_lines, credit_memo_lines, the
estimate, purchase-order, vendor-credit, memorized and in-kind lines);
`customer_id` rides transaction_lines too, for a deposit split among several
payers (#606).
Attribution rule for reports: `coalesce(line.job_id, transaction.job_id)`.
Migration `e9f0a1b2c3d4_add_jobs`.

## cost_codes (job-costing chart)

| column | notes |
|---|---|
| id, code (unique, case-insensitive via API; normalized to the `DD SS SS` division/section shape), name | e.g. `03` Concrete, `03 30 00` Cast-in-Place Concrete |
| level | `division` · `section` · null (a custom, non-standard code) |
| cost_type | per-code default: labor · material · subcontract · equipment · other |
| account_id (FK accounts, nullable) | default posting account |
| parent_id (self FK) | a section's division; set automatically |
| notes, is_active | inactive = hidden from pickers |

`cost_code_id` (nullable FK) on transaction_lines, bill_lines, invoice_lines,
estimate_lines, purchase_order_lines, vendor_credit_lines, time_entries. A
nullable `cost_type` (String(20)) rides every costable line —
transaction_lines, bill_lines, estimate_lines, purchase_order_lines,
vendor_credit_lines — chosen per line, falling back to the code's default when
blank. `is_billable` + `billed_invoice_line_id` (FK invoice_lines) on
transaction_lines and bill_lines. `purchase_orders.job_id` /
`purchase_order_lines.job_id` feed committed cost. Migrations
`f0a1b2c3d4e5_add_cost_codes`, `c8e4a2f1b3d5_division_section_cost_codes`
(#550: `level`, the four line `cost_type` columns, trade packages).

## Trade packages (#550)

| table | purpose |
|---|---|
| trade_packages | id, name (unique), notes, is_active, sort_order, timestamps — the bid packages a GC awards |
| trade_package_sections | (package_id, cost_code_id) composite PK — a package ↔ cost-code section, many-to-many |

A package is what a sub bids; a section is what costs post to. Seeded by
`POST /api/construction/setup` when the company has none.

## Construction estimating (#551)

`estimate_lines` gains the bid shape: `trade_package_id` (nullable FK
trade_packages, SET NULL — the bid package the line belongs to; null groups
under "Unassigned"), `line_kind` (String(12), NOT NULL default `base` — one of
`base` · `allowance` · `alternate` · `unit_price`), `unit` (String(20),
nullable — SF, LF, EA, CY…) and `alternate_accepted` (Boolean, NOT NULL
default false). `estimates` gains the four compounding markups `gc_pct`,
`insurance_pct`, `fee_pct`, `bond_pct` (Numeric(7,4), NOT NULL default 0 —
percent values, 5.5 = 5.5%), `markup_allowances` (Boolean, NOT NULL
default false — the markups also build on allowance lines when true) and `accepted_at` (DateTime, nullable).
`job_budgets.locked` (Boolean, NOT NULL default false) marks the rows an
accepted estimate owns — only a change order moves them. `invoice_lines` gains
`cost_type` (String(20)) and `unit_cost` (Numeric(12,4)), both nullable, so a
converted estimate carries its cost basis onto the invoice. The bid math
(lines at cost; the markup base, the four markups compounding in order, and
their total; the subtotal of the included lines; the pre-tax bid total as
subtotal + markups, with tax covering the lines and markups together;
priced-direct and allowance totals; the package, alternate and unit-price
breakdowns) is computed, not stored — `app/services/estimating.py`. Migration
`d9f6b2c4e7a1_construction_estimating` (additive and nullable/defaulted;
downgrade reverses).

## The contract on the job (#552)

`jobs` gains the contract terms, all nullable (a job with no `contract_type`
behaves exactly as before): `contract_type` (String(12) — `lump_sum` · `gmp` ·
`cost_plus`), `original_contract_amount` (Numeric(15,2) — the sum when the
contract was first set, stamped once; `contract_amount` stays the current /
revised sum so #554 shows original + changes = revised), `fee_pct`
(Numeric(7,4)) and `fee_amount` (Numeric(15,2) — a fixed fee, which wins when
both are set), `cap_amount` (Numeric(15,2) — the GMP maximum or a cost-plus
not-to-exceed), `contingency_amount` (Numeric(15,2) — owner contingency inside
the sum), `retainage_pct` (Numeric(5,2) — held per pay application,
#555), `contract_date` (Date) and `contract_estimate_id` (FK estimates, SET
NULL).

New table `sov_lines` — the owner's schedule of values:

| column | notes |
|---|---|
| id, job_id (FK jobs, CASCADE) | the job this schedule belongs to |
| line_no (Integer), item_no (String(10)) | 1-based order; printed number ("01"…), editable |
| description (String(200)) | |
| trade_package_id (FK, SET NULL), cost_code_id (FK, SET NULL) | optional links |
| kind (String(16), NOT NULL default `work`) | `work` · `allowance` · `contingency` · `general_conditions` · `insurance` · `fee` · `bond` |
| scheduled_value (Numeric(15,2), NOT NULL default 0) | |
| source_estimate_id (FK estimates, SET NULL) | set when built from an estimate |

Unique `(job_id, line_no)`. A `scheduled_value` may be negative (a deduct
alternate nets the sum down). A line's billed / stored / retainage figures are
not stored on it — a pay application bills it (#555, below), carrying those
columns on `pay_application_lines` and reading `previous` from history. The
contract sum and the schedule are **pre-tax** (tax is added per invoice). The
contract summary (changes, fee, cap remaining, contingency remaining, SOV
total and balance, billed to date) is computed, not stored —
`app/services/contracts.py`. Migration `e2a4c6d8f0b1_construction_contract`
(additive and nullable/defaulted; downgrade reverses).

## Buyout and commitments (#553)

A commitment is a purchase order — no new table. Additive columns:

- `purchase_orders.commitment_type` String(12) NOT NULL default `po`
  (`po` | `subcontract`), `trade_package_id` (FK `trade_packages.id`, SET
  NULL), `retainage_pct` Numeric(5,2) (held on every bill from the
  commitment), `scope_notes` Text.
- `purchase_order_lines.billed_amount` Numeric(15,2) NOT NULL default 0 — how
  much of the line (whose `amount` is its scheduled value) has been billed;
  remaining = `amount − billed_amount`. `received_qty` is left untouched.
- `bills.retainage_held` Numeric(15,2) NOT NULL default 0 (credited to
  Retainage Payable, 2050, not A/P; `balance_due` is the net), and
  `lien_waiver_status` String(16) (`not_required` | `pending` | `conditional`
  | `unconditional`).
- `bill_payments.coi_override` / `coi_override_note`: set when a Sub with an
  expired COI was paid by explicit override (#553).
- `bill_lines.purchase_order_line_id` (FK `purchase_order_lines.id`, SET
  NULL) — the back link partial billing tallies against.

`committed_cost` is now Σ `(amount − billed_amount)` over non-draft, non-closed
commitment lines (the remaining open value); `commitment_figures` adds
`committed_total` (Σ amount of non-draft commitments) and `billed_to_date` (Σ
billed) — `app/services/jobs_service.py`. Those two are whole-commitment
figures, so in a date-ranged `job_cost_tree` they are all-time; the totals
carry `commitments_all_time: true` to say so. Retainage is computed on the
pre-tax subtotal (tax is payable in full); the shared `vendor_compliance`
helper (`app/services/compliance.py`) feeds both the bills responses and the
buyout rows. The buyout table (bid vs bought per
package) is computed in `app/services/buyout.py`; `vendor_compliance` (Sub
W-9 / COI flags) is computed from the vendor. Migration
`f3b5d7e9a2c4_construction_buyout` (additive; backfills a closed PO's billed
lines from its bill; downgrade reverses). Retainage release (a debit back to
2050) is a release bill — see *Progress billing and retainage* (#555).

## Change orders (#554)

How a locked budget, its contract and its commitments move. Two new tables:

- `change_orders` — the document: `job_id` (FK, CASCADE), `number` String(80)
  unique (per job and kind: `<job number>-CO-001` / `-PCO-001` / `-SCO-001`,
  a PCO revision adds `-R2`; older `CO-0001` rows keep theirs), `kind` String(8)
  (`pco` | `owner` | `sub`), `status` String(10) (`draft` | `pending` |
  `approved` | `rejected` | `void` | `promoted` | `superseded`), `title`, `description`,
  `date`, `funding` String(12) (`contract` | `allowance` | `contingency`),
  `allowance_sov_line_id` (FK `sov_lines.id`, SET NULL — required when funding
  = allowance), `markup_pct` Numeric(7,4) (the CO's default markup, null →
  the job's `co_markup_pct`), `commitment_id` (FK `purchase_orders.id`, SET
  NULL — required for a sub CO), `owner_change_order_id` (FK self, SET NULL),
  `source_pco_id` (FK self, SET NULL), `apply_markup` Boolean (default true;
  the API defaults it off for allowance/contingency funding), `revision`
  Integer (default 1) and `supersedes_id` (FK self, SET NULL — the PCO revision
  this one replaced), `promoted_to_id` (FK self, SET NULL — the owner CO a PCO
  was rolled into; several PCOs may share one), `approved_at`, `voided_at`.
- `change_order_lines` — `change_order_id` (FK, CASCADE), `line_order`,
  `description`, `cost_type` String(20) NOT NULL (validated against
  `cost_types`), `cost_code_id`, `trade_package_id`, `quantity`, `unit`,
  `unit_cost`, `amount` (cost = qty × unit_cost, server-computed), `markup_pct`
  (per-line override) and `sell_amount` (cost × (1 + markup/100), server-
  computed; = cost under allowance funding or on a sub CO).

Additive columns: `jobs.co_markup_pct` Numeric(7,4) (the default markup on
owner change orders), and `sov_lines.change_order_id` (FK `change_orders.id`,
SET NULL — the owner CO that owns a `change_order`-kind SOV line; `change_order`
is added to `SOV_KINDS`). The math and the apply/reverse effects live in
`app/services/change_orders.py`; the append-only commitment helper is in
`app/routes/purchase_orders.py`. On approve an owner CO writes `job_budgets`
rows with `source = change`, `locked = True` (the one path that may move a
locked budget), which the job-cost tree reads into its Changes column.
Migrations `a7c9e1f3b5d6_construction_change_orders` and
`b8d0f2a4c6e8_change_order_revisions_markup_flag` (additive; downgrade
reverses). A billed `change_order` SOV line can no longer be voided (409
`co_billed`, filled by #555); the change-order log report is #556.

## Progress billing and retainage (#555)

The owner's progress bill from the schedule of values. Two new tables:

- `pay_applications` — `job_id` (FK jobs, CASCADE), `application_no` Integer
  (1, 2, … per job, unique `(job_id, application_no)`), `number` String(20)
  unique (`PA-<job>-<no:02>`), `period_to` Date, `date` Date, `status`
  String(10) (`draft` | `submitted` | `approved` | `void`), `retainage_pct`
  Numeric(5,2) (copied from the job at creation, editable while draft),
  `release_retainage` Numeric(15,2) default 0 (retainage billed back this
  application), `deposit_applied` Numeric(15,2) default 0 (owner deposit
  applied to the current due), `lien_waiver_status` String(16),
  `invoice_id` (FK invoices, SET NULL — the invoice approve created; null for
  an application that only moves stored material into the work),
  `tax_rate` Numeric(7,6), `charge_tax` Boolean default true (#576),
  `deposit_payment_id` (FK payments, SET NULL — the payment that drew the
  owner deposit down),
  `contract_sum_original` / `contract_sum_to_date` Numeric(15,2) nullable (the
  contract sums as of approval), `approved_at`, `voided_at`, `notes`. Retainage
  to date is net of releases.
- `lien_waivers` (#576) — `payment_id` (FK payments, CASCADE), `job_id`,
  `pay_application_id` (FK, SET NULL), `waiver_type` String(24)
  (`conditional_progress` | `unconditional_progress` | `conditional_final` |
  `unconditional_final`; unique per payment and type), `amount`,
  `through_date`, `signed_by`, `notes`.
- `pay_application_lines` — `pay_application_id` (FK, CASCADE), `sov_line_id`
  (FK `sov_lines.id`, SET NULL), `line_no`, `item_no`, `description`,
  `scheduled_value`, `this_period` and `stored` (Numeric(15,2); `previous` and
  completed / retainage are computed from history, not stored).

New table `job_deposits` — owner deposits: `job_id` (FK, CASCADE), `date`,
`amount`, `deposit_to_account_id` (FK accounts — the bank), `memo`,
`transaction_id` (FK), `voided_at`. A deposit posts DR bank / CR Customer
Deposits (2210, source_type `job_deposit`); a pay application's
`deposit_applied` draws it down with a payment to 2210 on approve.

Additive columns: `invoices.retainage_held` and `invoices.retainage_released`
(Numeric(15,2) NOT NULL default 0) and `invoices.pay_application_id` (FK
`pay_applications.id`, SET NULL); `bills.retainage_released` and
`purchase_orders.retainage_released` (Numeric(15,2) NOT NULL default 0). The
pure math and the history helpers live in `app/services/pay_applications.py`;
approve builds the invoice and its retainage journal (A/R net, Retainage
Receivable 1150 held / released, income, tax) in
`app/routes/pay_applications.py`, with the sub side (schedule and the 2050
release bill) in `app/routes/purchase_orders.py` and the deposits in
`app/routes/jobs.py`. Migration `e5c9a1f7b2d4_construction_progress_billing`
(additive and nullable/defaulted; downgrade reverses). The WIP schedule,
cost-to-complete and the other job reports are #556.

## Job cost model (milestone 3)

| table | purpose |
|---|---|
| cost_types | code (key), name, is_labor, burden_pct, burden_method (flat), default_account_id, offset_account_id, burden_offset_account_id, sort_order, is_active — the five starting types (labor/material/subcontract/equipment/other) are saved at the company's first write that needs them, never by a read (2.31.2) |
| cost_codes.parent_id | hierarchy (self FK) |
| equipment | code, name, hourly_rate, cost_code_id, recovery_account_id |
| job_costs | number (JC-000001), date, job_id (NULL for allocations), memo, source manual/time_entry/allocation, status posted/void, transaction_id, total |
| job_cost_lines | job_id, cost_code_id, cost_type, qty/rate/amount, debit_account_id, credit_account_id, employee_id, equipment_id, time_entry_id, is_burden, is_billable |
| job_budgets | job_id × (cost_code_id and/or cost_type, or neither), amount, revenue_amount, source manual/estimate/change, estimate_id — the unique key is (job, code, type, source) (#554), so a change row coexists with an estimate/manual row on the same code and type |
| job_cost_to_complete | job_id (CASCADE), cost_code_id (the section; NULL = Unassigned), cost_type ('' for Unassigned), amount — the typed cost to complete the WIP schedule reads (#556); unique (job, code, type), migration `c6e8f0a2d4b6` |
| employees.cost_rate, burden_pct · estimate_lines.unit_cost · time_entries.job_cost_id · transaction_lines.cost_type | |

Migration `a1b2c3d4e5f6_job_cost_model`.


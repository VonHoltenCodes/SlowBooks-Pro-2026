# Projects / Jobs / Job Costing — design notes

Status: BUILDING on `feat/projects` (seeded 2026-09-03). Second most requested
feature after receipt intake. Target audience: contractors and Sage 50 / QuickBooks
Desktop evaluators, for whom job costing is the deal-breaker.

## What "Projects" means to the people asking

Two products hide behind the word:

- **QuickBooks Online Projects** — a sub-customer with income, costs, time and a
  profitability view.
- **QuickBooks Desktop Customer:Job**, especially the Contractor edition — jobs
  under customers, cost codes, estimates vs actuals, committed costs from POs,
  change orders, progress billing, unbilled time and costs, WIP and over/under
  billing.

The full-suite target is the Desktop Contractor set, delivered with the Online
experience as the default surface. Nothing here is a "sub-customer with a
report"; every layer below is designed so the later layers do not require a
rewrite.

## What existed before this branch (inventory 2026-09-03)

- One flat, header-level **class** dimension (`classes` table; `class_id` on
  transactions, invoices, bills, estimates, credit memos, recurring invoices),
  P&L by Class that reconciles to the plain P&L, IIF CLASS resolution.
- **No job or project entity.** Customers are a flat list; the Customer Center
  header comment says Customer:Job was flattened on purpose. The IIF importer
  stored `Smith:Kitchen Remodel` verbatim as one customer; the QBO importer
  ignored sub-customers.
- `time_entries.project_id` pointed at *items* and was dead code (no route, page
  or test read it).
- No billable flags, no unbilled-costs flow, no progress invoicing, no change
  orders, no estimate-vs-actual. Income by Customer is revenue only. Budgets are
  keyed by account and month. Expenses and card charges are bare journal
  entries with no model of their own.
- **The structural constraint:** journal lines carried no dimension at all, so
  nothing could be attributed below the document header.

## The full suite, no corners cut

1. **Dimensions on every posted line.** `job_id` and `class_id` on journal
   lines, invoice lines, bill lines, PO lines, estimate lines. Header values
   remain as the default that fills lines that do not set their own. This is the
   foundation; it ships first.
2. **The job entity.** `jobs` under customers: name, number, status (pending,
   awarded, in progress, closed, not awarded), type, dates, site address,
   contract amount, notes. Display name "Customer: Job" so QB migrants feel at
   home. Customers gain nothing structural; the job is the child.
3. **Cost codes.** `cost_codes` with a CSI-style default list, each mapped to an
   expense or COGS account, with a cost type (labor, material, subcontract,
   equipment, other). Job cost detail is job × cost code × cost type.
4. **Costs in.** Bills, expenses, card charges, checks and PO lines take job,
   cost code and a billable flag. Inventory issued to a job posts to job WIP or
   COGS by policy. POs to a job are committed cost until billed.
5. **Labor in.** Time entries take job and cost code, billable flag and service
   item. Employees get a cost rate. Payroll distributes gross wages plus the
   employer burden (taxes, and the benefits engine's employer-paid codes through
   its burden-routing seam) to jobs by hours.
6. **Revenue and billing.** Estimate lines by cost code with quantity, cost and
   markup so estimate-vs-actual works. Change orders as numbered documents that
   revise the estimate and contract amount with an audit trail. Progress
   invoicing by percentage or amount per estimate line, tracking what has been
   billed. "Add unbilled time and costs" pulls billable lines into an invoice
   with markup. Retainage held and released on AR and AP.
7. **Reporting.** Job profitability summary and detail, job cost by vendor,
   estimates vs actuals by cost code, unbilled costs and time by job, committed
   costs, time by job, P&L by job, WIP schedule with over/under billing on
   percent complete. Every report reconciles to the general ledger the way P&L
   by Class does.
8. **Interop.** IIF and QBO importers split `Customer:Job` into customer plus
   job, import class lists as a real hierarchy, and read Desktop's Job
   Profitability and Estimates vs Actuals exports the way the sales-receipt
   importer reads report CSVs. Both exporters write jobs and classes back out.
   MYOB, Xero and Wave importers map their job / tracking-category fields.
9. **Surface.** A Projects page per customer with the Online-style tabs
   (overview, transactions, time, reports), job and cost-code pickers on every
   form, Settings for cost codes and job types.

## Sequencing (each step shippable, one integration branch, one PR to main)

| Step | Scope | Status |
|---|---|---|
| M1 | line dimensions · `jobs` table + API · posting carries job to every line · job pickers on invoice / bill / expense / card charge / estimate / journal · Jobs page + Customer Center jobs · **Job Profitability** report reconciling to the P&L · IIF + QBO `Customer:Job` split | built 2026-09-03 (301d480) |
| M2 | cost codes + cost types · job cost detail by code · PO committed cost · billable flags on cost lines (bill lines, expenses; `billed_invoice_line_id` reserved for M3) | built 2026-09-03 |
| M3 | **reshaped after the first lap** ("low customizability, no drill-down, no extra costs, no burden"): cost-code tree + CSV import · editable cost types w/ burden & accounts · Job Cost Entry (labor at loaded rate, equipment hours, mileage, burden, corrections) · allocations · time → job labor cost · employee cost rate/burden · budgets (per code / type / job, seeded from estimate incl. unit cost) · drill-down job page w/ Procore/QB columns · Job Budget vs Actual report | built 2026-09-03 |
| M3b | change orders (budget "changes" column is ready for them) · progress invoicing · unbilled time & costs → invoice (billable flag + `billed_invoice_line_id` ready) · retainage | next |
| M4 | time entries → jobs, employee cost rates, payroll burden distribution (needs benefits-engine seam) | |
| M5 | WIP schedule, over/under billing, estimates vs actuals, job cost by vendor · exporters write jobs/classes | |

## M1 decisions (2026-09-03)

- **Attribution rule.** A posted line's job is `coalesce(line.job_id,
  transaction.job_id)`; same for class. Reports group on that expression so a
  header-only document and a line-tagged document reconcile identically. The
  "No job" bucket holds everything untagged, so Job Profitability totals equal
  the P&L for the same period.
- **Jobs are never deleted once referenced.** Same protection as classes:
  archive (`is_active = false`) hides them from pickers; historical rows keep
  them.
- **Name uniqueness is per customer, case-insensitive.** Two customers may both
  have a job called "Kitchen".
- **`Customer:Job` on import.** First colon splits customer from job; deeper
  levels (`A:B:C`) become job "B:C" under customer "A" — Desktop's sub-jobs are
  rare and flattening one level is honest. A flat customer literally named
  `A:B` that already exists keeps matching, so re-imports of old files are
  stable.
- **`time_entries.project_id` stays as the dead column it was** (dropping it
  is a separate cleanup); `job_id` is the live link.
- **Expenses and card charges** stay journal entries in M1: a header `job_id`
  on the transaction, mirrored onto both lines, covers job cost detail. The
  proper models arrive with the billable flag in M2.
- **RBAC.** `/api/jobs` is bookkeeper-writable like `/api/classes`.
- **Migration** `e9f0a1b2c3d4_add_jobs` follows the class-tracking template
  (batch_alter_table for SQLite, named FKs `fk_<table>_job_id`).

## What to ask a real user for

| From QuickBooks Desktop | Why |
|---|---|
| Customer & Job list (IIF list export) | the hierarchy and job fields people actually fill in |
| Item list and Class list | cost codes in disguise, and how they nest |
| Job Profitability Detail, Estimates vs Actuals Detail (CSV) | the two reports that define "job costing" for them |
| Unbilled Costs by Job, Time by Job Detail | the billing loop |
| Transaction Detail by Date with the Name column | how costs actually got tagged |
| From Online: Projects list, Project Profitability, Time Activities export | the sub-customer flavour |

Anonymised is fine. Start each importer and report from a real file (the MYOB
lesson: synthetic inputs hide every real fault).

## M2 decisions (2026-09-03)

- **Cost codes are line-level only.** No header default: a bill routinely
  mixes codes, and a header default would silently mis-code the lines
  nobody touched. Companies without cost codes see no column at all.
- **Committed cost** = PO lines (job = line's, else header's) on POs in
  `sent` / `partial` / `received`. Draft POs are intent, not commitment;
  converting to a bill closes the PO and the cost moves into the ledger.
- **Billable** lives on the cost line (bill lines, and the expense's debit
  line as a posted journal line) with `billed_invoice_line_id` for M3 to
  mark it billed. Card charges and journal lines carry the column too;
  their forms expose it in M3 with the invoice pull.
- **Invoice and estimate lines** accept `cost_code_id` through the API
  (revenue by code for estimates vs actuals); the line UI for them is part
  of M3's estimate rework.

## M3 decisions (2026-09-03)

- **Column vocabulary** follows Procore's standard budget view and QuickBooks
  Desktop's Estimates vs Actuals (researched 2026-09-03): Original ·
  Changes · Revised (Budget) · Committed (open POs) · Actual (JTD) ·
  Projected (= actual + committed) · Variance (= revised − projected,
  positive = under) · % Used · Est. Revenue · Act. Revenue. "Changes" is
  the slot change orders fill in M3b (`job_budgets.source = "change"`).
- **Applied-cost pattern for non-bill costs.** A Job Cost Entry debits the
  cost account (tagged to the job) and credits an offset that is *not*
  tagged, so the credit lands in "No job" and Job Profitability still
  totals to the P&L. Offsets: Payroll Clearing (labor), Applied Labor
  Burden, Applied Equipment Cost, Applied Overhead; `setup-offsets` creates
  them (keeping the suggested number only if the chart hasn't used it) and
  never overrides a choice already made.
- **Labor cost rate** = employee cost_rate, else pay rate (salary ÷ 2080);
  OT × 1.5, DT × 2; burden % = employee's, else the labor type's. Only
  submitted/approved entries post, once each (`time_entries.job_cost_id`).
- **Budgets have no period** (a budget is a job-to-date number); actuals
  honour the period filter. Estimate-seeded rows are replaced on re-seed;
  rows edited by hand become `manual` and survive.
- **Cost types stay a string key** on cost codes / lines (`cost_types.code`),
  validated against the table by the routes, so the earlier `cost_type`
  column needed no migration.
- **Routes with a parameter**: `App.navigate` now matches `/jobs/:id`, so
  the job page is a real page (bookmarkable), not a modal.


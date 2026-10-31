# Payroll and HR

SlowBooks runs payroll in-house: employees, pay runs with federal and state
withholding, direct deposit files, W-2/W-3/940/941 and the state SUI
report, benefits and garnishments, time entries and time off, new-hire
onboarding, and a self-service portal for employees. This document is the
reference for where each piece lives and how the routes fit together; the
state tables are in [state-withholding.md](state-withholding.md).

**Who can use it.** Payroll and HR need an administrator signed in.
Bookkeeper, accountant and read-only sign-ins, and every API token (a token
is never an administrator since 2.29.0; older administrator tokens stopped
working in 2.31.2), are refused on `/api/payroll`, `/api/benefits`,
`/api/deductions`, `/api/onboarding`, `/api/tax-forms`,
`/api/document-audits` and the employee portal-token, bank-account and YTD
routes. Payroll forms stay on the calendar year whatever fiscal year the
company reports on.

---

## State withholding

All 50 states + DC: dedicated engines for WA / CA / NY / OR, a table-driven engine for the rest (`app/services/state_tax/tables.py`). Parameters, sources and the employee inputs (allowances, extra withholding, elected rate, local rate) are in [state-withholding.md](state-withholding.md). Reciprocity in `state_tax/reciprocity.py`.

## Onboarding, time entries and time off

### Models

| File | Class | Purpose |
|------|-------|---------|
| `app/models/hr.py` | `OnboardingChecklist`, `OnboardingTask` | 8-task new-hire checklist with sign-off |
| `app/models/time_entries.py` | `TimeEntry` | Per-day hours with state, approval status |
| `app/models/pto.py` | `PTOPolicy`, `PTORequest`, `PTOAccrual` | Policy definitions, requests, YTD balances |

### Routes

| Method + Path | Purpose |
|---------------|---------|
| `GET /api/onboarding/{emp_id}` | Fetch checklist (the default, unsaved and without task ids, until it is seeded) |
| `POST /api/onboarding/{emp_id}/seed` | Save the default tasks (adds any missing; never resets completed ones). The Onboarding page calls it when View Checklist is opened |
| `PUT /api/onboarding/tasks/{task_id}` | Mark signed / complete |
| `POST /api/onboarding/tasks/{task_id}/complete` | Convenience for the checkbox |
| `GET /api/onboarding/{emp_id}/new-hire-report` | JSON for state new-hire reporting |
| `GET /api/onboarding/{emp_id}/new-hire-report/pdf` | Downloadable PDF |
| `GET /api/time-entries` | List, filterable by employee/date range |
| `POST /api/time-entries` | Create a time entry |
| `PUT /api/time-entries/{entry_id}` | Edit (not `status`, and blocked once paid or costed to a job) |
| `DELETE /api/time-entries/{entry_id}` | Remove (blocked once paid or costed to a job) |
| `POST /api/time-entries/{entry_id}/submit` | Move draft → submitted |
| `POST /api/time-entries/{entry_id}/approve` | Manager workflow; approver is the signed-in user (body `approved_by` ignored) |
| `POST /api/time-entries/{entry_id}/reject` | Manager workflow |
| `POST /api/time-entries/{entry_id}/post-to-job`, `POST /api/time-entries/post-to-job` | Post an approved entry (or several) to its job as labor cost, one job cost entry each |
| `POST /api/time-entries/classify` | Run the overtime engine (FLSA plus state rules) over raw daily hours |
| `GET /api/time-entries/summary?period_start=&period_end=` | Approved unpaid hours per employee for a pay period |
| `GET /api/pto/policies` | All policies |
| `GET /api/pto/policies/{policy_id}` | Single policy |
| `POST /api/pto/policies` | New policy |
| `PUT /api/pto/policies/{policy_id}` | Edit policy |
| `GET /api/pto/requests` | All time-off requests, filterable |
| `POST /api/pto/requests` | Submit a request |
| `POST /api/pto/requests/{request_id}/decision` | Canonical approve/reject endpoint |
| `POST /api/pto/requests/{request_id}/approve` | Alias for `decision(status=approved)` |
| `POST /api/pto/requests/{request_id}/reject` | Alias for `decision(status=denied)` |
| `POST /api/pto/requests/{request_id}/cancel` | Cancel a pending or approved request; an approved one puts the hours it drew back into the bank and reverses its relief journal |

### Frontend

- `app/static/js/onboarding.js` — checklist UI with per-task signature
- `app/static/js/time_entries.js` — entry list + approve/reject buttons
- `app/static/js/pto.js` — the Time Off page: policies + requests with workflow buttons

---

## Benefits and garnishments

### Models

| File | Class | Purpose |
|------|-------|---------|
| `app/models/benefits.py` | `BenefitCode`, `BenefitRate` | The rule + its effective-dated rates/limits (replaces `DeductionType`) |
| | `EmployeeGroup`, `EmployeeGroupBenefit` | Templates: a set of codes applied to everyone in the group |
| | `EmployeeBenefit` | Per-employee assignment with overrides, caps, loan balance (replaces `EmployeeDeduction`) |
| | `BenefitYTD`, `PayStubBenefit` | YTD accumulators; posted-run snapshots |
| | `Garnishment` | Court-ordered wage garnishment with priority |

### Routes

| Method + Path | Purpose |
|---------------|---------|
| `GET/POST /api/benefits/codes`, `PUT/DELETE …/{id}` | Benefit code catalog (retire, never delete) |
| `POST /api/benefits/codes/seed-standard` | Section 125, HSA, 401(k) w/ tiered match, Roth, union, loan, employer health, GTL |
| `GET/POST /api/benefits/codes/{id}/rates` | Effective-dated rates + limits (previous row closes itself) |
| `GET/POST/PUT/DELETE /api/benefits/groups…` | Employee groups, their code sets and members |
| `GET/POST/PUT/DELETE /api/benefits/enrollments` | Per-employee assignments (end-date keeps history) |
| `GET /api/benefits/employee/{id}/resolved` | What the next run applies, in sequence |
| `GET /api/benefits/ytd`, `POST …/ytd/rebuild` | Accumulators, repair from snapshots |
| `GET /api/benefits/remittance`, `POST …/remittance/bill` | Per-vendor withheld + employer totals; vendor bill relieving the liability |
| `POST /api/pto/accruals/{id}/revalue` | Restate a PTO bank's dollars at the current rate |
| `GET /api/deductions/garnishments` | List, filterable by `?employee_id=...` |
| `POST /api/deductions/garnishments` | Create a garnishment order |
| `POST /api/deductions/garnishments/{order_id}/end` | End an order (the page's **End order** button); it is no longer withheld and its record stays. `DELETE` is refused (405) |

### Frontend

- `app/static/js/benefits.js` — codes / groups / enrollments / remittance tabs
- `app/static/js/deductions.js` — garnishments page (was three sections: types,
  per-employee, garnishments) with add/remove forms

---

## Tax forms

### Routes

Two endpoint families per form. JSON endpoints are the
machine-readable contract (useful for future e-file integration); PDF
endpoints render through WeasyPrint with the employer's name + EIN in
the header and a tamper-evident audit hash in the footer. The SPA's
"Generate" buttons hit the PDF variants.

| Method + Path | Returns |
|---------------|---------|
| `POST /api/payroll/forms/w2/{emp_id}?year=YYYY` | W-2 boxes 1-6, boxes 15-17 per state (`states`) + employee/employer identifiers (JSON) |
| `GET, POST /api/payroll/forms/w2/{emp_id}/pdf?year=YYYY` | W-2 PDF + `document_audits` row |
| `POST /api/payroll/forms/w3/{year}` | W-3 aggregate across all active employees (JSON) |
| `GET, POST /api/payroll/forms/w3/{year}/pdf` | W-3 PDF |
| `POST /api/payroll/forms/940/{year}` | Form 940 FUTA — first $7K/employee at 0.6% (JSON) |
| `GET, POST /api/payroll/forms/940/{year}/pdf` | Form 940 PDF |
| `POST /api/payroll/forms/941/{year}/{quarter}` | Quarterly Form 941 figures: lines 1-3, 5a-5e, 6, 7 and 12 (JSON). Deposits and balance due (lines 11-13), Schedule B and Form 941-X are not produced |
| `GET, POST /api/payroll/forms/941/{year}/{quarter}/pdf` | Form 941 PDF |
| `GET /api/document-audits` | List audit rows (newest first), filterable by `doc_type` + `doc_key` |
| `GET /api/document-audits/{id}` | One audit row — for verifying a PDF by its footer ID |
| `GET /api/document-audits/verify/{content_hash}` | Find rows by full SHA-256 hash |
| `GET /api/document-audits/{id}/check` | Re-figure the form from today's books; `matches`, both hashes, `voided_runs` and a sentence (`message`) |

### Frontend

- `app/static/js/tax_forms.js` — picker for year/quarter, blob-opens the
  response

---

## Self-service portal

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
| `GET /api/employees/{id}/portal-token` | The token + expiry as they stand; never mints (null with a note when there is none) |
| `POST /api/employees/{id}/portal-token` | Make the token, or rotate it (resets expiry windows) |

On the employee's Details, **Make Link** makes the first link and **Rotate
Token** replaces it; **Copy Link** and **Email to Employee…** send it.
Reading never creates one, and only an administrator sees the section.

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
| `POST /portal/pto/{request_id}/withdraw` | 10/min | Withdraw the employee's own request while it is still pending |

After the first visit the browser is redirected to `/portal/…` without the
token, which lives in an HttpOnly cookie for 30 days; the token appears in a
URL only on that first claim.

### Token lifecycle

- **Hard expiry**: 1 year from mint, in `portal_token_expires_at`.
- **Idle expiry**: 90 days since `portal_token_last_used`. Rolled forward
  on every authenticated request, so an active user never trips it.
- **Expired tokens** return `410 Gone`. The admin can issue a fresh one
  via the rotate endpoint.

**Where the portal link works.** The link is absolute and uses the address the request came in on, so it is only as reachable as the server: a desktop install is `127.0.0.1` on that PC only; Server Edition on the LAN gives a LAN address; employees outside the building need a server they can reach: a cloud host behind HTTPS (`docs/cloud-hosting.md`) or a tunnel to the office host with a bypass for `/portal/` (`docs/server-edition.md`, "Reaching it from outside the office"). In the desktop app the link opens in the system browser on purpose — the portal is a separate cookie-based site and must not be rendered in the app's document window.

---

## Admin UI pages

| Route | Page | JS module |
|-------|------|-----------|
| `#/hr/onboarding` | Employee list with completion % | `onboarding.js` |
| `#/hr/time-entries` | Time entries with approve/reject | `time_entries.js` |
| `#/hr/pto` | Time Off: policies + pending requests | `pto.js` |
| `#/hr/benefits` | Benefit codes, groups, enrollments, remittance | `benefits.js` |
| `#/hr/deductions` | Garnishments | `deductions.js` |
| `#/hr/tax-forms` | W-2/W-3/940/941 generation + Form history (who, when, hash, Check) | `tax_forms.js` |
| `#/employees` → Details | Overview, Portal Access, E-Verify, YTD Totals, Opening YTD, Bank Accounts, Documents | `employees.js` |

---

## Direct deposit

SlowBooks makes the ACH file (NACHA format); the bank moves the money.
The business account needs ACH origination from the bank, which then
gives the numbers Settings → Direct Deposit (ACH) asks for: the bank's
routing number and name, the company account the net pay is paid from
(`ach_company_account`, encrypted at rest like the SMTP password) and the
two 10-character identifiers (`ach_immediate_origin`, `ach_company_id`),
which are "1" + the EIN unless the bank says otherwise and are filled from
the company tax id when blank. Employees' own accounts are entered on the
employee (`/api/employees/{id}/bank-accounts`) or by them in the portal.

| Method + Path | Purpose |
|---------------|---------|
| `GET /api/payroll/{run_id}/nacha` | The file for a processed run, from the saved settings; the pay run view's **Direct deposit file** button. `attachment`, named `Direct-Deposit_<pay date>.ach` |
| `POST /api/payroll/{run_id}/nacha` | The same with any originating field in the body outranking the saved one (`effective_date` sets the batch's effective entry date); an empty body is the saved settings |

Each file made is an `EXPORT` row in the audit trail on `pay_runs`: who
made it, for which run, how many it credits, the total and the effective
date. The account numbers stay in the file, never in the trail. A refused
download leaves no row.

A refusal says why: 400 when the run is not processed; 409 naming the
settings still blank, or that no one on the run has an active account (an
account in its prenote waiting period is not paid). Each stub of a run
carries `direct_deposit`: true when the file pays it, false means pay by
check; the view shows the split and a Paid by column. Admin only, like the
rest of payroll.

---

## Voiding a pay run

`POST /api/payroll/{run_id}/void` (the run's **Void** button) reverses the
journal entry the run posted, line for line, marks the run void and puts
back what it took: time entries are released, benefit and loan balances
step back, YTD figures drop the run. A run whose entry was already reversed
by hand, or whose bank line sits in a finished reconciliation, is refused
with the reason. Three things make the void ask first (409 `confirm_void`,
sent again with `confirm=1` to go ahead): a direct deposit file was made
from the run, a liability the run credited was paid out since its pay date
(a 941 deposit, net pay paid out of 2300), or a W-2, W-3, 940 or 941 was
generated since that counted it. The response's `warnings` repeat them.

---

## Posting accounts (#620)

A pay run posts one journal entry: a debit for gross wages, one for the
employer's payroll taxes, a credit to each payable for what was withheld or
accrued, and a credit for the net pay. Each piece has a setting that names
its account (Settings → Payroll Accounts, administrator only, saved with the
rest of the company settings by `PUT /api/settings`). Left blank, the piece
posts to the seeded account it always has, so a company that chooses
nothing posts exactly as before. The table is
`app/services/payroll_accounts.py`.

| Piece | Setting | Takes | Default |
|-------|---------|-------|---------|
| Net pay | `payroll_net_pay_account_id` | a bank account, or a liability | 1000 Checking |
| Gross wages | `payroll_wages_account_id` | an expense or cost of goods sold account | 6110 Wages & Salaries (6000 on a chart without it) |
| Employer payroll taxes | `payroll_employer_tax_account_id` | an expense or cost of goods sold account | 6120 Payroll Tax Expense (6000) |
| Federal income tax withheld | `payroll_federal_withholding_account_id` | a liability | 2310 (2300) |
| State income tax withheld | `payroll_state_withholding_account_id` | a liability | 2320 (2300) |
| Social Security payable (both shares) | `payroll_social_security_account_id` | a liability | 2330 (2300) |
| Medicare payable (both shares) | `payroll_medicare_account_id` | a liability | 2340 (2300) |
| FUTA payable | `payroll_futa_account_id` | a liability | 2350 (2300) |
| SUTA payable | `payroll_suta_account_id` | a liability | 2360 (2300) |
| Other payroll deductions payable | `payroll_other_deductions_account_id` | a liability | 2370 (2300) |

Reimbursements (6140, else 6950) and benefit codes (each code's own
accounts, 6150 for the rest) are not settings. The save refuses an account
of the wrong type, an inactive one, a credit card, or a control liability
(2000 Accounts Payable, 2200 Sales Tax Payable, whose balances are kept by
their own documents), in a sentence saying what the piece takes; nothing is
saved when one choice is refused. `GET /api/settings/payroll-accounts`
lists the pieces with their chosen and default accounts and, per kind, the
accounts each may be pointed at; the Settings page draws its pickers from
it. A chosen account that has since been deleted, made inactive or retyped
refuses the process (400) naming it and where to fix it, rather than
posting the piece somewhere else.

**Net pay paid later.** With Net pay set to 2300 Payroll Liabilities (or a
liability made for it, "Wages Payable"), processing the run credits that
account instead of the bank: the wages are a liability on the Balance Sheet
until they are paid, and the bank is untouched. When the pay goes out,
record it against that account: a check or register entry from the bank
with 2300 as its category, a bill with a line on 2300 and its bill payment,
or a journal entry debiting 2300 and crediting the bank. The direct deposit
file is made the same way as before, from the processed run; it does not
post. The Process button's overdraft question is for a bank account; a
liability is not asked about.

**Void.** The void reverses the entry the run posted, line for line, so it
puts back whatever accounts the run used at the time (2300 for the net pay,
say), whatever the settings say by then. A payment out of a liability the
run credited, dated since its pay date, is a caution the void asks about
first, as a 941 deposit is: net pay paid out of 2300 included.

---

## Opening YTD balances (#433)

A company that switches to SlowBooks mid-year enters what the old payroll
paid that year, once per employee, quarter by quarter through `as_of`, the
day before the first SlowBooks pay run (`app/models/payroll_opening.py`,
`app/services/payroll_opening.py`). No pay run and no journal entry: the old
system booked that payroll, and the opening balance sheet carries what it
still owes.

| Table | Holds |
|-------|-------|
| `payroll_opening_balances` | employee, year, `as_of`, note |
| `payroll_opening_quarters` | per quarter (1 to the quarter of `as_of`): gross, federal withholding, SS / Medicare / FUTA wages and tax both sides, pre- and post-tax deductions, garnishments, net |
| `payroll_opening_state_balances` | per quarter and state: wages, withholding, SUTA wages and tax, other employee / employer state taxes |
| `payroll_opening_benefits` | per benefit code, for the year: employee and employer amounts (the annual caps count them) |

Who reads it, each on purpose:

- **The paycheck's caps** (`ytd.wage_base_ytd`): Social Security from the
  opening's SS wages, the Additional Medicare threshold from its Medicare
  wages, FUTA from its FUTA wages, SUTA and the state's capped taxes from
  the work state's SUTA and state wages (no state rows: its gross stands in
  for the work state). A stub counts its FICA wages (gross less Section
  125 / HSA, `payroll_service.stub_taxable_wages`) toward Social Security,
  Additional Medicare, FUTA and SUTA (SUTA in the state it was worked in),
  and its gross toward the state's capped taxes, which the state engines
  measure on gross. `employee_ytd` and the stub's YTD count every quarter.
- **The Social Security wage base is the year's** (`SS_WAGE_BASES`,
  `ss_wage_base(year)`: 2023 $160,200, 2024 $168,600, 2025 $176,100, 2026
  $184,500) for the opening's check, the paycheck and the 941 and W-2. A
  year with no base here is measured against the nearest one; the opening
  (`warnings`) and the pay run (`warnings`) say so. FUTA's $7,000 is
  statutory and the same every year.
- **941**: the quarter's own opening figures (the old system filed its
  finished quarters); earlier quarters fill the wage base and the Additional
  Medicare threshold.
- **Tax liability schedule**: the quarter's opening amounts, as
  `prior_system` on each line beside `due` (what is left to deposit), so the
  941 line equals line 12.
- **940**: payments and FUTA tax for the year; FUTA wages fill the $7,000
  first.
- **W-2 / W-3**: every quarter; boxes 3 and 5 from the taxable wages, 16 and
  17 from the state rows. Boxes 15-17 go one line per state (stubs by the
  state they were worked in, the opening's state rows by theirs; no state
  rows: the work state). With two or more, `compute_w2` carries `states`;
  with one it keeps its old shape, so its Form history hash is unchanged.
  The PDF prints two states per form; a third goes on an additional W-2
  with only the identifying boxes and the state lines (`w2_forms`).
- **State SUI report**: the quarter's state rows, by their own state (stubs
  by the state they were worked in); a stub's SUTA-taxable wages are its
  FICA wages up to what is left of that state's base.
- **Form history Check**: a W-2, W-3, 940 or 941 generated before 2.29.0
  was figured with each stub's gross toward the bases, one state line and
  the 2026 base for every year. When today's figures differ, Check
  re-figures it that way too (`legacy=True`); the same hash means the pay
  runs are as they were (`matches`, `figured_before_2_29`), and the
  sentence says the form comes out differently now.
- Not counted: benefit remittances, the pay-run list, NACHA and the payroll
  journal.

Locked once a pay run in the year is processed for the employee: a change or
a delete then needs a reason, written to the audit trail as `CORRECT` with
the before and after, and `as_of` stays before that run. A change or a
delete sets `stale_reason` on the employee's draft runs for the year;
processing one is refused (409) until `acknowledge_stale=1` is sent, or it
is discarded (`DELETE /api/payroll/{id}`) and created again.

| Method + Path | Purpose |
|---------------|---------|
| `GET /api/payroll/opening-ytd?year=` | Every employee's opening balance for the year |
| `GET /api/employees/{id}/opening-ytd?year=` | One employee's (year totals, `states` summed, `quarters` as entered), with `locked` and `first_pay_date` |
| `PUT /api/employees/{id}/opening-ytd` | Create or replace: `year`, `as_of`, `quarters[]` (each with `states[]`), `benefits[]`, `reason` once locked |
| `DELETE /api/employees/{id}/opening-ytd?year=&reason=` | Remove (`reason` once locked) |
| `GET /api/payroll/opening-ytd/template` | The CSV template |
| `POST /api/payroll/opening-ytd/import?dry_run=1\|0&reason=` | Import the template: the plan, then the same plan applied |
| `POST /api/payroll/{id}/process?acknowledge_stale=1` | Process a draft flagged by an opening change, as figured |
| `DELETE /api/payroll/{id}` | Discard a draft: delete it and its stubs; take its benefit amounts back out of `benefit_ytd` (a row only it made goes), give loan balances back what it paid down, release its time entries; flag later drafts that counted it. 409 for a processed or void run |

A reader for QuickBooks Desktop's Payroll Summary waits for a real export to
build against.

---

## Related docs

- [security-hardening.md](security-hardening.md) — the production-readiness pass that hardened the portal, encryption, and HTTPS layers
- [../SECURITY.md](../SECURITY.md) — public security policy / responsible disclosure

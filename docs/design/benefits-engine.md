# Benefits / PTO / Job-Costing Engine — design notes

Status: **BUILT** (v2.8 stage, 2026-09-03) — all six steps below are in the
product. Design captured 2026-08-25. The "gap analysis" section is kept as
the historical record of what the old deduction module lacked.

## Where it lives

| Piece | Code |
|---|---|
| Models | `app/models/benefits.py` (BenefitCode, BenefitRate, EmployeeGroup, EmployeeGroupBenefit, EmployeeBenefit, BenefitYTD, PayStubBenefit) |
| Engine | `app/services/benefits_engine.py` — `resolve_for_employee()`, `compute()`, `record_on_stub()`, `gl_groups()`, `remittance_rows()`, `create_remittance_bill()` |
| Routes | `app/routes/benefits.py` under `/api/benefits` (codes + dated rates, groups, enrollments, resolved view, YTD, remittance) |
| Payroll | `app/routes/payroll/runs.py` — `compute()` feeds the three wage bases into `calculate_withholdings(pretax_deductions, pretax_state, pretax_fica)`; processing posts per-code liability/expense accounts and calls `distribute_payroll_burden()` |
| PTO dollars | `app/services/pto_liability.py`; `pto_policies.accrue_liability / valuation / accounts`, `pto_accruals.dollar_balance` |
| Job costing seam | `cost_types.burden_method` (`flat` \| `payroll`), `BenefitCode.burden_routing` (`fringe_pool` \| `job_burden`), `job_costing.distribute_payroll_burden()` |
| UI | `app/static/js/benefits.js` (`#/hr/benefits`), garnishments stay on `deductions.js` |
| Migration | `d5e6f7a8b9c0_benefits_engine` — carries `deduction_types` / `employee_deductions` onto codes and enrollments |
| Tests | `tests/test_benefits_engine.py` |

Decisions made while building:

- **Group precedence**: an enrollment wins for its code, but a field left
  blank on the enrollment falls back to the group's override for that
  code, then to the dated rate (so "Bob pays $50 instead of $100" keeps
  the group's employer contribution).
- **Employer methods**: any employee method, plus `match_percent` — flat
  (rate % of the contribution on at most `employer_match_limit_pct` of
  gross) or tiered (`[{"up_to_pct": 3, "match_pct": 100}, {"up_to_pct": 5,
  "match_pct": 50}]`).
- **Three wage bases** are tracked separately all the way into the tax
  calculator: a code can reduce federal, state and FICA wages in any
  combination. Net is gross − taxes − every employee amount − garnishments
  + reimbursements, computed in the run, not from the calculator.
- **YTD accumulators** are bumped when a run is created (drafts count, as
  the tax YTD already does) and there is a rebuild endpoint that
  recomputes a year from the stub snapshots.
- **PTO dollars**: `current_rate` (revalues on raises via the Revalue
  action) or `average_rate` (historical cost). Relief posts DR liability /
  CR PTO expense because the pay run expenses the wages; carryover caps
  forfeit hours and their dollars.
- **Burden seam**: with `burden_method = payroll`, time entries post base
  labor only and the pay run posts one Job Cost Entry (source `payroll`)
  spreading employer taxes + `job_burden` codes over the jobs the
  employee's entries hit, by cost hours; no-job hours keep their share in
  the pool. Credits go to the accounts the payroll entry expensed, so the
  P&L is unchanged and the job carries the real cost.
- **Employer-taxable benefits** (GTL over $50k) are flagged and reported
  in the stub detail; imputed-income withholding is not computed.
- **Post-tax codes take what is left** (first macOS lap, 2026-09-04): the
  engine runs once for the pre-tax codes and the wage bases, taxes and
  garnishments are computed, and if the post-tax total would overdraw the
  check the engine runs again with the real room (`posttax_available`).
  Post-tax codes are trimmed in sequence, the shortfall is noted on the
  stub, and net pay never goes negative; a garnishment stack that still
  exceeds pay is refused with a 422 instead of posting an unbalanced entry.
- **Remittance vendor follows the code** when the stub snapshot has none:
  the vendor changes who gets paid, not what was withheld, so assigning
  one after a run still lets the report and the bill find that run.

Source: field ask from a Sage 50 evaluator (Matt Beebe, @TheMattBeebe)
who has "been thinking about this for a long time across several
systems" — the entity design below is substantially his, shared
publicly on X, 2026-08-25. His reference point for getting-it-right
(if cumbersomely): Acumatica. His dealbreaker gap: job costing.

## The core idea: a benefit is a CODE, not a feature

Define calculation rules as data, attach them to a class, and payroll
just evaluates whatever's attached. Everything else falls out of that.
(Trent's "fit PTO and benefits into one box" problem dissolves — the
box is the rule engine; PTO and benefits are rows.)

## Entities

- **BenefitCode** — the rule definition. Type (deduction / benefit /
  both), calc method (fixed_amount, percent_of_gross, amount_per_hour,
  tiered), employee-paid rate, employer-paid rate, tax treatment
  (pre/post-tax AND which taxes it exempts — that is not one flag; our
  reduces_federal/state/fica booleans already got this right),
  remittance vendor, effective dates, explicit **sequence** field.
- **EmployeeClass** — the template: a set of code assignments applied
  to everyone in the class. (Distinct from our document-dimension
  Class tracking — naming needs care: maybe "Employee Group".)
- **EmployeeBenefit** — the assignment, with per-employee overrides of
  rate and caps. Class provides the default; assignment wins.
- **PTOBank** — same philosophy, separate object. Accrual method (per
  hour worked / fixed per period / front-load), rate, carryover cap,
  max balance, accrual-year basis (calendar vs hire anniversary),
  pays-out-on-termination flag.
- **GLMapping** — expense + liability account per code, resolvable by
  department/cost center rather than one global pair.
- **YTD accumulators as a first-class table** — per employee per code
  per year. Never recomputed by summing history.

## The hard-won rules (verbatim from the field)

1. **Ordering**: pre-tax deductions apply in defined sequence — each
   changes the taxable base for the next. Explicit sequence field;
   never rely on insertion order.
2. **Limits are plural**: per-period cap, annual cap, and wage-base
   ceiling are three different rules; codes commonly need all three.
3. **Effective-date everything**: rates change mid-year. Dated rows,
   resolved against pay-period END date.
4. **Posted runs snapshot the resolved rule set** into the run record.
   Re-deriving from current config changes history retroactively.
   (We already learned this lesson with tax-rate snapshots.)
5. **Arbitrary employee-side balances**: HSA-like deductions, company
   loans — arbitrary deductions with arbitrary balances that may or
   may not hit the GL.

## PTO accounting specifics

Accrual books a liability as earned, relieved when taken — so a bank
needs BOTH an hours balance and a dollar balance; they diverge when
wage rates change. Decide explicitly whether the liability revalues at
current rate (ideal: a PTO liability account adjusted on raises).
Companies handle this in widely varied ways — make it a policy choice,
not an assumption.

## Job costing (the gap that loses Sage 50 evaluators)

If job costing lands, any employer-paid cost on any code must route to
either a fringe pool account or be distributed to jobs as burden on
labor hours. Job costing is its own design (job entity, cost codes,
labor distribution from time entries — we already have time_entries),
but the benefits engine must be built with this routing seam or it
gets retrofitted painfully.

## Gap analysis vs. current module (2026-08-25, v2.6.1)

Current `deductions.py` / `pto.py` have: DeductionType with per-tax
exemption flags (good), per-employee assignment with per-period +
annual limits (two of the three limit kinds), garnishments with
priority ordering (sequence exists there but not for deductions),
PTOPolicy with accrual methods + carryover/max (hours only).

Missing, mapped to the design: employer-paid side entirely (no match,
no employer cost, no remittance vendor) · EmployeeClass templates ·
sequence on deductions · wage-base ceiling (third limit) · effective
dating (rates are single mutable values) · posted-run rule snapshots ·
YTD accumulator table (currently recomputed) · GL mapping per code
(global accounts only) · PTO dollar balance / liability postings ·
job costing anywhere.

## Sequencing thought (when unparked)

This is v3.0-scale surgery on the payroll spine. Natural order:
(1) BenefitCode + sequence + plural limits + YTD table, migrating
existing DeductionTypes onto it; (2) employer-paid side + GL mapping +
remittance; (3) effective dating + posted-run snapshots; (4) PTO
dollar-liability; (5) EmployeeClass templates; (6) job costing as its
own feature with the burden-routing seam. Each step shippable.

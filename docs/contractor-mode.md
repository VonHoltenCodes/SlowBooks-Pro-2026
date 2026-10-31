# Contractor mode

Settings → Company Information → Company Type → **Construction** sets a
company up the way a contractor keeps its books, the same way **Nonprofit**
does for a ministry: one switch, no change to any data, reversible at any
time. It is the counterpart to QuickBooks Desktop's Contractor Edition.
Construction mode adds the contractor accounts, division/section cost codes,
trade packages, a Sub vendor type, construction estimating, the contract and
schedule of values, commitments, change orders, progress billing with
retainage, and seven job reports. Every role reaches these screens; only
payroll and HR are the administrator's alone.

The five starting cost types (labor, material, subcontract, equipment,
other) appear in the Cost Types list once the company has saved something
that uses them — construction setup, a new cost type or code, or a costed
line; a read alone never saves them (2.31.2). An agent that reads
`GET /api/cost-types` on a company that has written none sees an empty list,
not a missing chart.

## The switch

Picking Construction in Settings saves `company_type = construction` and
calls `POST /api/construction/setup`, which:

1. creates the contractor accounts below if they are missing,
2. loads the standard division titles and a set of common sections
   **only when the company has no cost codes of its own** (the same list the
   Cost Codes page's **Load standard divisions and common sections**
   button loads), ensuring the labor / material / subcontract / equipment /
   other cost types first, and
3. seeds the default **trade packages**, mapped to the sections just loaded
   (only when the company has no packages yet).

Setup is idempotent — safe to run again. It never renumbers or renames an
account you already have, never touches an existing cost code (a company
that has built its own cost-code chart gets no standard codes added), and changes
no posting. Switching back to Business leaves the accounts and codes in
place (as switching away from Nonprofit leaves its net-asset accounts); they
are ordinary accounts once created.

## The accounts

Each is created only if an account of that name does not already exist, and
each keeps its suggested number only if the chart has not already used it
(an imported chart that owns 1150 keeps it; the new account takes no
number). Each is pinned to a balance-sheet detail type so it lands on the
right line whatever the number-and-name rule would otherwise guess.

| Number | Name                     | Type      | Detail type            |
| ------ | ------------------------ | --------- | ---------------------- |
| 1150   | Retainage Receivable     | Asset     | Other Current Asset    |
| 1350   | Construction in Progress | Asset     | Other Current Asset    |
| 2050   | Retainage Payable        | Liability | Other Current Liability|
| 2210   | Customer Deposits        | Liability | Other Current Liability|
| 2220   | Use Tax Payable          | Liability | Other Current Liability|
| 5400   | Equipment Cost           | COGS      | Cost of Goods Sold     |
| 5500   | Other Job Costs          | COGS      | Cost of Goods Sold     |

Retainage is the portion of a billing held back until a job is accepted —
receivable when a customer holds it from you, payable when you hold it from
a sub. Construction in Progress carries costs on a job not yet billed.
Customer Deposits holds money taken before work. Use Tax Payable is the use
tax a contractor self-assesses on materials. Equipment Cost and Other Job
Costs are job-cost buckets beside the seed chart's Materials, Labor and
Subcontractor Costs.

## The cost codes

Codes use the **two-level division/section numbering**:

- a **division** — a two-digit number and title (`03` Concrete, `26`
  Electrical); the reserved, un-titled division numbers are left out (add
  them yourself); and
- a **section** — `DD SS SS` with an optional `.SS` extension (`03 30 00`
  Cast-in-Place Concrete, `07 42 13.16`), which sits under its division
  through the cost-code tree's parent link, set automatically.

Each code carries a `level` of `division`, `section`, or null for a code that
isn't division/section shaped. **What you type is normalized on save and
import**: `033000`, `03-3000`, `03 3000` and `03.30.00` all become
`03 30 00`. Only a two-digit division or a 6-/8-digit section is reshaped; a
code that numbers its own way (`L-100`, `EQ`, or a numeric `1000`/`2000`
chart) is left exactly as typed, with a null level, and re-importing it never
rewrites it. Everything sorts by code — list, tree, pickers, import result.

The seeded set is the division titles plus a **modest set of common
sections**. The full numbered section catalog isn't ours to publish, so it is
not shipped; import your own full list through **Import CSV** on the Cost
Codes page — a CSV of just `code,name` is enough (a few hundred sections land
normalized and auto-parented in one call, each section's division created
from the title table if the chart is missing it). Cost type is optional on
import and defaults to `other`.

### Trade packages

A **trade package** (Concrete, Drywall and Ceilings, HVAC, Electrical …) is
what a subcontractor bids and gets awarded; a cost-code **section** is what
costs post to. The two are mapped many-to-many — a package can span
divisions, and a division can be split across packages. Construction setup
seeds a default set of packages mapped to the sections it has; edit them, and
the section mapping, under **Settings → Trade Packages** (shown only for
construction companies). Estimate lines, commitments, the schedule of values
and the Buyout tab all use them.

### Cost type per line

A cost code carries a **default** cost type, but the type is chosen **per
line** on a budget, estimate, commitment or bill — a `03 30 00` line can post
as subcontract on one job and as labor on another. Leave the line's type
blank to use the code's default, so existing books are unchanged. The picker
shows in construction mode only; a Business or Nonprofit company keeps its
line forms as they were and posts the code's type (the API accepts a line
type for any company). Job-cost reports read the line's type first and fall
back to the code's, and roll a section's cost up under its division.

## Estimating

In construction mode the estimate is a bid, not just a priced list (#551). A
Business or Nonprofit company's estimate form and PDF are exactly as they were.

**Packages and sections.** Each line can belong to a **trade package** — the
bid package a sub is awarded (Concrete, Electrical …) — and to a cost-code
**section** (`03 30 00`), as every costable line does. The package is the
bid's view (lines group under it, Unassigned when blank); the section is where
the cost posts. A unit of measure (SF, LF, EA, CY…) sits beside the quantity.

**Line kinds.** A line is one of four:

- **Base** — ordinary scope. A base line that carries a unit cost is priced
  **at cost** (its rate mirrors the unit cost, so its amount is quantity ×
  unit cost) and its cost is what the markups build on. A base line with no
  unit cost is **priced direct** — its rate is its price, and it is never
  marked up.
- **Allowance** — a budgeted figure for work not yet designed. Its amount is
  in the subtotal and carries no markup unless the estimate's **Markup on
  allowances** flag is on (see below). On accept it budgets its amount as cost
  (a budgeted figure is spent, not margined).
- **Alternate** — an add or deduct the customer can choose. Its amount is in
  no total until it is **accepted**; a deduct carries a negative amount, so it
  subtracts. An alternate that carries a unit cost is shown to the customer at
  its **marked-up price** (cost × the markup factor); one with no cost shows
  its rate price. Accepting it moves it into the scope (at cost, with its
  markups in the markup rows) and adds its cost to the markup base; the
  Alternates block then offers only the ones not yet taken.
- **Unit price** — a rate card (a price per unit for work whose quantity is
  unknown, like rock excavation). It is never in the bid and never seeds a
  budget.

**The lines stay at cost; the markups are their own figures under the
subtotal.** The subtotal is the sum of the included line amounts — base lines
(at cost), accepted alternates and allowances. Beside the subtotal, four
percents are figured on the markup base (the base and accepted-alternate lines
that carry a unit cost, plus the allowances when that flag is on) and **compound in order** — each figured on the
running total before it. Worked example — a $100,000 cost of work, no
allowances, at 8 / 1.5 / 5 / 1 percent:

| Step | Figured on | Amount |
| --- | --- | --- |
| Subtotal | lines at cost | 100,000.00 |
| General conditions (8%) | 100,000 | 8,000.00 |
| Insurance (1.5%) | 108,000 | 1,620.00 |
| Fee (5%) | 109,620 | 5,481.00 |
| Bond (1%) | 115,101 | 1,151.01 |
| **Bid total** | subtotal + the four | **116,252.01** |

The **bid total** is the subtotal plus the markups, and it is **pre-tax**: the
owner's contract sum and schedule of values (#552) are pre-tax,
and tax is added on each invoice. Tax covers the lines and the markups
together — the tax rate times the taxable line subtotal plus the markups —
when any included line is taxable; an exempt customer pays no tax on anything.
The **Total** is subtotal + markups + tax. The markups are a construction-mode
field: a Business or Nonprofit estimate stores 0 for all four whatever an API
caller sends, so its totals are the plain Subtotal / Tax / Total.

**Markup on allowances is the GC's call, and it is off by default.** Each
estimate carries a `markup_allowances` flag (a checkbox under Markups on the
form). Off, allowances sit in the subtotal at their amount and the markups skip
them. On, the allowance amounts join the markup base, so the four markups are
figured on them too (and the budget revenue split follows). The estimate, the
form's totals panel, the view and the customer PDF all show "Markup on
allowances" as On or Off. The flag freezes with the rest of the price once the
estimate is accepted. There is no separate contingency line kind; a contingency
carried as an allowance line follows the same flag.

**Accept locks the budget.** Accepting an estimate (it needs a job) seeds the
job budget from the bought scope — a budget row you edited by hand still wins —
and then **locks** the seeded rows: they are the contract budget. The lock is
per row: a manual budget row on a code the estimate never touched still saves
after acceptance, but a locked row's own key, or re-seeding the whole budget
from another estimate, is refused. A job whose budget one estimate locked will
not accept a second, and a construction estimate that locked a budget refuses
changes to its lines, markups, customer, job, tax and **status** (its notes and
expiration still edit, and a save that resends the same values is not a
change; moving status off accepted or converted is refused with a 409, since
the next accept would re-seed the locked budget). Business and Nonprofit
estimates lock no budget and edit as they always did. **There
is no unlock** — a new estimate or, later, a change order is how a locked
budget moves. The customer PDF groups the bid by package with the section code,
then blocks the allowances, the alternates not yet taken (at their marked-up
price) and unit prices, and foots Subtotal, a row per non-zero markup, Bid
total, Tax and Total. The seeded budget distributes the markups across the rows'
revenue pro rata to each row's share of the markup base, so the budgeted
revenue foots to the bid. A code you budgeted by hand keeps its own row and its
own share of the markups; that share is not pushed onto the other rows.
**General conditions are both revenue and cost:** the GC percent stays markup
revenue, and on accept the GC amount is also budgeted as a cost on a "GC
General Conditions" code (created if the books have none, reused if they do),
so the job carries it. That row carries cost only; its revenue is already in
the rows above, so it is not counted twice. Insurance, fee and bond are revenue
only. Converting an accepted estimate to an invoice drops
the unit-price lines and any un-accepted alternate, carries each line's cost
type and unit cost onto the invoice, and adds one invoice line per non-zero
markup (on the job) so the invoice foots to the bid.

**Change orders move the locked budget.** A **change order** (#554, see
*Change orders* below) is how a locked budget moves — a numbered document that
revises the contract amount and the budget with an audit trail. Until one is
approved, the lock simply holds: the budget stays as the accepted estimate
left it.

## The contract on the job

An accepted estimate prices the work; the **contract** is the deal with the
owner. A construction job grows a **Contract** tab (construction mode only;
every other company sees the job pages exactly as before) that holds the
terms, a few summary tiles and the owner's schedule of values. A job with no
contract type set behaves the way it always has — the plain Contract amount on
the job form still works for everyone.

**The three types.** A job's contract is a **lump sum** (one fixed price), a
**GMP** — guaranteed maximum price (cost-plus up to a ceiling the owner is
protected by), or **cost-plus** (actual cost plus a fee, optionally with a
not-to-exceed). The type decides how the fee and the cap read:

- **Fee** — the contractor's fee. A **fixed fee** (a dollar amount) wins when
  it is set; otherwise a **fee percent** applies to actual cost on a cost-plus
  or GMP job. A lump-sum job earns no separate fee (it is inside the price).
  The Job Profitability period fee (`fee_period`) is the percent on the
  period's cost; a fixed fee shows none, since it is a job figure.
- **Cap** — the guaranteed maximum on a GMP (normally the contract sum, and
  defaulted to it), or an optional not-to-exceed on cost-plus. *Cap remaining*
  is the cap less cost-to-date and fee.
- **Contingency** — the owner's contingency carried inside the contract sum
  (it rides the schedule as its own line, and building from an estimate adds
  it to the sum so the schedule still balances), drawn down as change orders
  use it (#554 tracks the draw-down).
- **Retainage %** — the portion of each billing the owner holds back until the
  job is accepted. Progress billing (below) holds it back and releases it.
- **Contract date** and the **source estimate**, shown when the contract was
  built from one.

The summary stamps the **original contract amount** once, the first time a sum
is set with a type (building from an estimate stamps it too, even when the sum
came from the job form). The **contract sum and the contingency edit freely**
(and the original follows the sum) until the job has any billing on the
schedule of values or any approved owner change order (a draft or pending
one, a potential change order or a sub change order does not lock it, and a void
frees it again). From then on the Contract tab and the
job form refuse a different sum or contingency (409 `contract_sum_locked` /
`contingency_locked`), and only an approved change order (#554) moves them, so
the tab shows original + changes = revised. Amounts are rounded to the cent and
the fee percent must be between 0 and 100. Building from an estimate also
copies the estimate's fee markup into the job's fee percent when the job has
none, so the fee tile and the SOV fee line agree on a GMP.

**The schedule of values.** The SOV is the contract sum broken into the lines
an owner pay application bills against — each with an item number, a
description, an optional package and section, a kind (work, allowance,
contingency, or one of the four markups: general conditions, insurance, fee,
bond) and a scheduled value. The sum (and the schedule) is **pre-tax**: sales
tax is added on each invoice, not carried in the contract. **Build from
accepted estimate** lays it out automatically: one **work** line per trade
package (its base lines plus any accepted alternates, **at cost** — the lines
carry no markup), an **allowance** line for each allowance, then one line per
**non-zero markup** in order (general conditions, insurance, fee, bond), each
labelled with its percent, and a **contingency** line when the job carries one.
The work, allowance and markup lines foot exactly to the pre-tax bid. Lines
with no package roll into one "Unassigned" work line. You
can add, remove and reorder rows by hand and edit any value; a value may be
**negative** — a deduct alternate carried onto the schedule nets the sum down.
The SOV must **balance to the contract sum**: a running total and the balance
show at the foot of the grid, and the balance stays highlighted until it is
zero. When the contract sum came from the estimate (the pre-tax bid, plus any
contingency), a freshly built schedule already balances.

Change orders, progress billing and the job reports (below) build on this:
change orders revise the contract amount, the budget and the sub commitments
together (work paid for out of an allowance carries no markup), progress
billing turns the SOV into an owner pay application that holds retainage back
and releases it at the end, and the job reports read the lot.

## Buyout and commitments

The contract is the money coming in; the **buyout** is the money going out — what
the subs and suppliers are committed to. A **commitment is a purchase order**
(#553). There is no new document type: in construction mode a PO is either a
plain **purchase order** or a **subcontract**, chosen with a Type select, and
it can carry a **trade package**, a **retainage %** and **scope notes**. A
subcontract to a vendor not marked a Sub is allowed — the form just notes that
its license and insurance aren't tracked; nothing is blocked.

**The schedule of values is the lines.** A commitment's lines are its schedule
of values: each line's amount is its scheduled value, and the commitment bills
against those lines a period at a time. **Partial billing** — *To Bill* on a
commitment opens the usual convert dialog with an amount-this-period column
prefilled with each line's remaining (scheduled value less what's already
billed). Bill some or all of it: each line tallies its billed-to-date, the
commitment reads **partial** while anything remains and **closed** once every
line is fully billed. A business company's PO bills in one go exactly as before. The dialog takes a
**bill date**, defaulting to today: the bill, its journal entry and the
closing-date check all use it, so a progress bill ages from the day it is
billed and a later period can still be billed once the PO's own month is
closed. (`convert-to-bill` accepts an optional `date`; left out, the PO's date
is used.) Editing the lines of a converted bill keeps each line tied to its
commitment line and re-tallies billed-to-date; a void gives it all back.
A line with no price ($0) is not a bill line — the converted bill carries only
the lines with money on them (a change from the earlier behaviour, where a
description-only line rode along).

**Retainage held.** When the commitment carries a retainage percent, that
percent of each bill's **pre-tax subtotal** is held back — tax is always
payable in full — so the bill credits Accounts Payable for the net and
**Retainage Payable** (2050) for the retainage, its balance due is the net, and
the journal still balances with the gross debits. Pay Bills pays only what's
owed now, and the A/P aging and vendor balances show the net (the held amount
sits on 2050, not A/P). A bill entered by hand can hold retainage the same way.
The 2050 line carries the bill's job, so release can find it by job.
Editing the lines of a converted bill re-computes the retainage at the commitment's rate on the new subtotal, unless the edit sends `retainage_held`. A retainage that was set by hand (sent as `retainage_held` through the API; the bill's `retainage_manual` is then true) is kept across later edits of the lines, tax or date, while one the commitment's rate derived keeps following that rate even after the rate is changed; it still may not exceed the new subtotal or leave less than has been paid. A fully billed (closed) commitment refuses a higher amount (409): raise it with a sub change order.
Release it with **Release retainage** on the commitment
(`POST /api/purchase-orders/{id}/release-retainage`): a release bill debits
2050 back for the amount released, see *Progress billing and retainage* below.

**Retainage on the cash basis.** A bill's held retainage is not an expense on
a cash-basis report until it is paid: paying the net spreads that amount over
the bill's expense lines, the held part waits on 2050 (which reads zero on the
cash Balance Sheet while it waits), and a release of that retainage recognizes
the rest of the expense on the day it is paid. Accrual reports are unchanged:
the whole bill on its date, the held part on Retainage Payable.

**What committed and billed mean now.** *Committed* cost is the **remaining**
open commitment — the scheduled value of every issued, not-yet-closed
commitment, less what has been billed — so it keeps meaning "ordered but not
yet in the ledger". Beside it the job now carries **committed total** (the
whole of every issued commitment) and **billed to date** (what has been billed
against them), shown as tiles on the job Overview (construction mode) and in the
cost-tree totals; the Overview's "Billed to date (subs)" is the sub/supplier
side and is distinct from the Contract tab's owner "Billed to date". Both are
whole-commitment figures — a commitment has no period — so on a date-ranged
cost tree they are all-time (the response flags this with
`commitments_all_time`), unlike the period-scoped actuals beside them.

**Bid vs buyout.** A **Buyout** tab on the job (after Contract, construction
mode only) pairs each trade package's **bid** — the accepted estimate's cost
for that package — against what has been **bought** (the commitments tagged to
it), with the **variance**, the committed-remaining, the billed-to-date and the
commitments behind each package linked through. Packages bid but not yet bought
show with nothing bought; commitments with no package gather under
*Unassigned*. `GET /api/jobs/{id}/buyout` is the data, and the seam the buyout
report and job-cost-by-vendor (#556, see *Job reports* below) are built on.

**Compliance flags.** Bills and Pay Bills carry each Sub vendor's paperwork —
whether a **W-9** is on file and whether the **certificate of insurance** has
expired (or is missing). The Pay Bills rows and the Buyout tab's commitments
show a compact flag for a Sub that's short either one, and Pay Bills offers a
**lien waiver** status per bill (not required / pending / conditional /
unconditional). A missing W-9 or missing COI is a warning. A Sub whose
certificate has **expired** (by the payment date) is not paid unless the payment
is overridden: Pay Bills asks first, saying why, and the payment records that it
was paid with an expired COI (`coi_override`, with a note naming the expiry),
shown on the bill's payments and the payment's own view. Through the API,
`POST /api/bill-payments` answers 409 until the request carries
`coi_override: true`. The lien waiver is only a status: saving it works on a
bill in a closed period, since it posts nothing.

Sub **change orders** (see *Change orders* below) revise a commitment the way
owner change orders revise the contract; **progress billing** (see *Progress
billing and retainage* below) bills a commitment's schedule of values as a sub
pay application and releases retainage on both sides; the **Buyout Report** and
**Job Cost by Vendor** (see *Job reports* below) read it all.

## Change orders

A **change order** is how a locked budget, its contract and its commitments
move, with an audit trail (#554). It comes in three kinds and prices material
and labor separately, each carrying its own markup — except work an allowance
pays for, which carries none.

**The three kinds.**

- A **potential change order (PCO)** prices a change the owner has not yet
  approved. It has no effect on the books; when the owner says yes you
  **promote** it to an owner change order (copying its lines), and the PCO is
  marked *promoted*. Tick several PCOs on one job (same funding, allowance line
  and markup setting) and **Promote selected PCOs** rolls them into **one**
  owner change order: it carries all their lines, its amount is the sum, and
  each PCO links to it. A PCO can be **revised** (*Revise*): the new revision
  (`…-PCO-001-R2`) starts as a draft copy and supersedes the prior one, which is
  kept as history (the log shows the latest; `GET /api/change-orders?job_id=…&history=true`
  shows them all). Only the latest revision can be revised or promoted.

**Numbering.** Change orders are numbered **per job**: the job number, then
`CO-001`, `CO-002`… (`PCO-001`… for PCOs, `SCO-001`… for sub change orders), so
two jobs each start at `CO-001`. A job without a job number uses `J<id>`.
Numbers from before this (`CO-0001`) keep theirs.
- An **owner change order** revises the owner's contract. On approve it moves
  the contract amount, adds a schedule-of-values line and writes the budget.
- A **sub change order** revises a commitment (a purchase order) — it appends
  lines to the sub's scope. A sub change order moves a commitment by **cost**;
  it carries no markup, because the markup is the contractor's and lives on the
  owner side.

**Material and labor priced separately, with markup.** Each line is a cost
type (labor, material, subcontract, equipment, other), a quantity and a unit
cost; its cost is quantity × unit cost. The **markup** that turns cost into the
sell price is the line's own percent, else the change order's, else the job's
**Markup on change orders** contract term, else zero. Labor and material each
carry their own markup, so the two price independently. Worked example — an
owner change order with 10 labor hours at $50 marked up 15% and $1,000 of
material marked up 10%: labor sells at 500 × 1.15 = **$575.00**, material at
1,000 × 1.10 = **$1,100.00**, a change order total of **$1,675.00** on
**$1,500.00** of cost (**$175.00** of markup).

**Markup is off by default when an allowance or contingency pays.** An owner
change order has a **funding**: *contract* (it adds to the contract sum),
*contingency* (it draws the owner's contingency down) or *allowance* (it spends
an allowance line, which must be an allowance line on the same job). Allowance-
and contingency-funded work carries **no markup** by default — the sell equals
the cost — because the money was already held. The contractor can still mark it
up: the change order's **Apply markup** flag turns it on, and the form, the log
(*+ markup*) and the PDF show whether markup was applied. Switching a change
order's funding resets the flag to that funding's default. The same $1,500 of
cost funded by an allowance sells at $1,500 unless markup is applied. A marked-up allowance
change order draws its **sell** price from the allowance (as contingency does),
and the approve limit checks that figure: $2,000 of cost at 15% draws $2,300.
Contract-funded work is always marked up; the flag only governs allowance and
contingency work. A markup
percent must be above -100% and at most 1000%.

**Commitment and budget stay together.** A sub change order made from an owner
change order writes no budget of its own, so it approves only once that owner
change order is approved (409 `owner_co_not_approved`); an owner change order
cannot be rejected or voided while an approved sub change order stands under it
(409 `approved_sub_co`; void the sub CO first). A plain purchase-order edit
cannot drop a line a sub change order added or change its amount, quantity,
rate, job, cost code or cost type (409 `commitment_co_line`); a linked sub change
order cannot raise commitments past its owner change order: at approve, the cost
of the approved sub change orders under it plus this one cannot exceed the owner
change order's cost (409 `owner_co_budget_exceeded`; a rejected or void one does
not count), and a sub change order is still refused on a fully billed
commitment (409 `commitment_not_open`).

**What approve does.** Every effect lands on **approve**, in one transaction,
and reverses on **void**:

- *Owner CO, contract-funded* — adds the sell total to the **contract amount**
  (the original sum is never touched, so the Contract tab reads original +
  changes = revised), appends a read-only **change_order** line to the
  schedule of values for the sell total, and writes locked budget rows
  (`source = change`) with the cost on the cost side and the sell on the
  revenue side. The job-cost tree shows these in its **Changes** column.
- *Owner CO, contingency-funded* — the contract is unchanged; the budget rows
  carry the **cost with no revenue** (the contract already holds the
  contingency money); the draw counts against the contract's contingency
  (refused 409 `contingency_exceeded` when it would pass the contingency
  amount).
- *Owner CO, allowance-funded* — the contract is unchanged and no SOV line is
  added; the budget rows carry the **cost with no revenue** (the allowance
  already holds the money, and the work carries no markup); the draw counts
  against the allowance line (refused 409 `allowance_exceeded` when it would
  pass the allowance). The Contract tab shows each allowance's used and
  remaining.
- *Sub CO* — appends the lines to its commitment, which must be on the **same
  job** and **open** (refused 409 `commitment_job_mismatch` or
  `commitment_not_open` otherwise), and recomputes the commitment's total; the
  buyout table's *bought* grows automatically. The budget rows carry the cost
  with no revenue (and none at all when the sub CO backs an owner CO, which
  already carried that cost).

Because the budget key includes the row's source, a change-order budget row
sits beside an estimate-locked or manual row on the same code and type — the
job-cost tree reads one under **Original** and the other under **Changes**.

A change order's figures **freeze** when it is approved: the cost, sell and
the effective markup each line carried are stored, so changing the job's
markup or a contract term afterward never moves an approved change order, and
void reverses the exact figures it applied.

**Void** reverses each of these — the contract and SOV line, the budget rows,
and the appended commitment lines (exactly the ones it added) — except that a
commitment line already billed against cannot be pulled (409 `co_billed`). The
lifecycle is draft → submitted (*pending*) → approved, with reject and void
beside it; a PCO runs
draft → pending → promoted (asking to *promote* rather than approve).

**Billed change orders are locked.** Once a pay application has billed a
change order's schedule-of-values line (#555, below), that change order can no
longer be voided — the void answers 409 `co_billed`, the same guard a sub
change order's billed commitment line already carried. The change-order **log
report** (#556, see *Job reports* below) lists every change order and its effect.

## Progress billing and retainage

Progress billing turns the owner's schedule of values into a **pay
application** — the contractor's periodic bill against the contract, the
counterpart to a commercial payment application and its line detail (#555).
Each application bills a period; approving it creates the invoice.

**The application and its math.** Every SOV line (work, allowance, contingency
and change-order lines all bill the same way) carries, per period:

- **Scheduled value** — the line's amount.
- **Previous** — the sum of *this period* on this job's earlier approved
  applications for the line.
- **This period** — the work completed this period.
- **Stored** — materials presently stored, not yet in the work (not
  cumulative).
- **Completed to date** = previous + this period + stored.
- **% complete** = completed ÷ scheduled.
- **Balance to finish** = scheduled − completed.
- **Retainage** = completed × the retainage percent (copied from the contract
  at creation, editable while the application is a draft).

The application totals read the way the owner's payment application does:
completed and stored to date, **retainage to date** (the sum of the line
retainage, *net of every release*, so it is what is still held), **retainage
this period** (newly withheld), **total earned less retainage** (completed −
retainage to date), **less previous payment applications** (the prior
applications' payment due, before tax), **sales tax** on this application's
billing (below), and the **current payment due** (total earned less retainage −
previous payment applications + tax), then the balance to finish. A release is
just retainage going down, so the figures keep lining up with the invoices and
account 1150. A **deposit applied** (below) nets off the current due to the
**net due**.

**A worked two-period example with stored materials and a release.** A
$100,000 contract on two lines, 10% retainage:

| | Scheduled | Previous | This period | Stored | Completed | Retainage | Current due |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **App 1** | 100,000 | 0 | 30,000 | 5,000 | 35,000 | 3,500 | **31,500** |
| **App 2** | 100,000 | 30,000 | 20,000 | 5,000 | 55,000 | 5,500 | **18,000** |
| **App 3** (release) | 100,000 | 50,000 | 0 | 5,000 | 55,000 | 0 (5,500 − 5,500 released) | **5,500** |

App 1 bills 35,000 of work-and-stored, holds 3,500 of retainage, and certifies
$31,500. App 2 bills 20,000 more; retainage to date is now 5,500, so retainage
*this period* is 2,000, and with $31,500 previously certified the current due
is $18,000. App 3 does no new work but **releases** the 5,500 held: no
retainage this period, and the current due is the 5,500 released. Across the
three, the owner has certified $55,000 and been billed the 5,500 of retainage
back.

A release followed by more work: bill 30,000 (10% retainage, 3,000 held, 27,000
due), release 1,500 on the next application (retainage to date 1,500, 1,500
due), then bill 10,000. Retainage to date is 4,000 − 1,500 = 2,500, the
application's current due is 9,000, the invoice's balance is 9,000 and account
1150 holds 2,500.

**What approve posts.** The invoice carries one line per SOV line with a
non-zero this-period amount, where the amount is *this period plus the change
in stored materials* — so stored materials are billed the period they are
stored and reversed the period they go into the work, and the income billed to
date always equals completed-to-date. The journal is DR Accounts Receivable for
the net (total − retainage held this period + retainage released), DR
**Retainage Receivable** (1150) for the retainage held, CR 1150 for any
released, CR income per line, CR sales tax as any invoice. Sales tax is **on by
default** and can be switched off per application (the *Charge tax* box); it is
charged on the work billed this period at the application's **tax rate**,
defaulted from the job's accepted estimate (kept to six decimals, so 8.875%
stays 8.875%) and editable while the application is open. A tax-exempt owner is never taxed. The application shows the tax and
its current due includes it, so the application and the invoice match. The
invoice's balance due is that net; the invoice view and PDF show
*Retainage withheld this period*, *Retainage released* and *Current payment
due*. A dropped retainage percent makes the retainage this period negative and
the 1150 leg a credit; the entry still balances. **Void** reverses the
invoice (and the 1150 entries), and later applications stay correct because
each reads its *previous* and *retainage to date* from the approved history
live. An application's invoice is voided with the application: voiding it from
the Invoices screen is refused (409 `invoice_belongs_to_pay_application`). Only
the **latest approved** application can be voided (409 `later_application`
otherwise); void the later ones first. Only one application
may be open (draft or submitted) per job at a time.

**Rules the application keeps.** Completed to date and stored never go below
zero (or above zero on a deduct line): a negative this-period or stored that
would do that is refused (422). An application that only moves stored material
into the work has nothing to bill: it is approved with no invoice (current due
zero); an application with nothing at all to bill, hold, release, apply or move
is still refused. Retainage to date can't go below zero either: a release is
refused (422) when it is more than the retainage held at the application's own
percent on the work completed, so dropping the percent to 0 and releasing on
the same application is refused (release at the held percent, or bill the
release on its own application). A lien waiver is for one payment and one
job, and its amount is the part of the payment applied to that job's invoices. At approval the application keeps the contract sums as they
stood (original, and to date with change orders), and its PDF prints those, not
today's job. An invoice made from a payment application can't be edited on the
Invoices screen except for its notes (409); change the application instead.

**Owner deposits.** Money an owner pays ahead of the work is a **deposit**
(Pay apps tab → Owner deposits, or `POST /api/jobs/{id}/deposits`): it posts DR
the bank / CR **Customer Deposits** (2210) and shows as received, applied and
unapplied. A pay application's **deposit applied** draws the unapplied balance
down — on approve a payment whose deposit-to account is 2210 is created and
applied to the invoice (DR 2210 / CR A/R), so the owner's prepayment settles
the current due. Voiding the application restores the deposit — the deposit
payment, the invoice and the application are voided together, so a refusal
(a payment still on the invoice, the closing date) leaves all three standing.
A deposit that has been applied cannot be voided until the application is, and
the deposit payment cannot be voided from Payments while the application
stands (409).

**The sub side.** A commitment bills a period at a time exactly as buyout
described (the **Sub pay app** button opens the partial-convert dialog with
previous / this period / remaining). Its held retainage is released with
**Release retainage** (`POST /api/purchase-orders/{id}/release-retainage`),
which writes a bill for the vendor carrying no expense account: DR **Retainage
Payable** (2050) / CR Accounts Payable for the amount, payable through Pay Bills
like any bill. A release over what is held is refused (422), and voiding the
release bill gives the retainage back to the commitment. Retainage is held and
released this way on **both** sides — 1150 receivable on the owner side, 2050
payable on the sub side.

**Lien waivers.** On the sub side a bill carries its lien-waiver status
(not required / pending / conditional / unconditional), as it has since buyout;
the commitment's pay-app schedule lists its bills with that status. On the owner
side, a pay application warns — it never blocks — about the job's sub bills
dated on or before the period whose waiver is still missing or pending, so the
GC sees what to collect before submitting.

On the owner side, each payment received on a payment application's invoice can
carry the full set of waivers the contractor gives the owner: **conditional
progress**, **unconditional progress**, **conditional final** and
**unconditional final** (Pay apps tab → Waivers, or `POST /api/lien-waivers`
with `payment_id`, `waiver_type` and, when the payment pays more than one
job's invoices, the `job_id` it is for — left out, a payment on one job needs
no `job_id`, and one spanning two jobs is refused with 400 `job_required`
naming them). One of each type per payment and job; the waiver takes the part
of the payment applied to that job's invoices and a through date (the
application's period, else the payment's date), and
`GET /api/lien-waivers/{id}/pdf` prints it. A
conditional waiver takes effect only once the payment is received; an
unconditional one says it has been; a final one covers the whole job. Some
states require their own waiver form, so check yours before relying on these.

**The schedule is now anchored.** Once an approved application has billed the
schedule of values, it can no longer be rebuilt from the estimate (409
`sov_has_billings`), a line cannot be dropped or shrunk below what it has billed
(422), and a billed change order cannot be voided (409 `co_billed`). New lines
can still be added.

**The job reports read these figures.** The WIP schedule, cost-to-complete, the
change-order log, the buyout report and job cost by vendor (#556, see *Job
reports* below) are built on the contract, the cost tree, the commitments, the
change orders and the pay applications above.

## Job reports

Seven reports sit on the Report Center for a construction company (its first
cards, after the three job reports every company has; a Business or Nonprofit
company never sees them), each a plain grid with a JSON route, a PDF and a CSV
like P&L by Job. Each takes an `as_of` date (or
`start_date`/`end_date` where a period applies) and optional `job_id`,
`customer_id`, `status` and `include_closed`; by default closed and not-awarded
jobs are left out.

**WIP schedule** (`wip-schedule`). The work-in-progress schedule lists every job
with a contract — contract sum, approved change orders, revised contract,
estimated total cost and gross profit, cost to date, percent complete, earned
revenue, billed to date, over and under billing, retainage outstanding, cost to
complete, projected gross profit, projected loss and loss provision; a job with
no contract terms is listed with blanks. It is **cost-to-cost**: percent complete
is cost to date ÷ estimated total cost (capped at 100%), earned revenue is the
revised contract times that percent, and **over billing** (billed − earned, a
liability — billings in excess of costs and estimated earnings) or **under
billing** (earned − billed, an asset — costs and estimated earnings in excess of
billings) is the difference. The **estimated total cost is actual cost plus the
cost-to-complete forecast** (below), so a job past its budget with work still to
do reads under 100%; with no forecast entered the default is budget − actual per
cell, floored at zero, and a job with no budget and no forecast has nothing to
measure against, so it earns nothing: it is listed with a warning ("no budget or
forecast; nothing earned"); its contract, cost to date, billed to date and
retainage count in the totals, but its earned revenue, over/under billing and
loss provision are left out, and so is it from the adjusting entry, until
its cost to date reaches the contract (then it reads 100% complete, and the loss
is the true loss, with no extra provision). Every figure reads **as of the date**: cost to date and
posted revenue to that day, billing and retainage from the applications approved
by then, and the revised contract with the change orders approved by then (so the
schedule reproduces the books on an earlier close). Billed to date is the sum of
the approved pay applications' completed-to-date when the job has one by the date
(else the posted revenue carrying the job — the `billed_basis` column says which).

**Projected loss.** When actual + cost to complete is over the revised contract,
the whole loss is recognised now, the usual treatment of a loss contract. Earned
revenue at the percent complete already carries percent × loss, so the **loss
provision** is the rest: loss × (1 − percent). Example: contract 100,000, cost to
date 60,000, 60,000 still to spend — projected cost 120,000, loss 20,000, 50%
complete, 50,000 earned (so 10,000 of the loss is already in), provision 10,000.

**The adjusting entry** (`adjusting_entry` in the JSON, a second grid in the PDF,
a block under the grid in the CSV and a table on screen). Per job and net, with
amounts:

| line | debit | credit |
|---|---|---|
| Costs and Estimated Earnings in Excess of Billings (under-billing, asset) | under | |
| Billings in Excess of Costs and Estimated Earnings (over-billing, liability) | | over |
| Contract Revenue (WIP adjustment) | over − under when over is larger | under − over when under is larger |
| Loss on Uncompleted Contracts (expense) | provision | |
| Provision for Loss on Uncompleted Contracts (liability) | | provision |

The entry balances. These are the balances to carry **as of the date**; if an
earlier WIP entry was posted, reverse it first or post only the difference. **The
report posts nothing** — there is no post button; the entry is for the accountant
to journal.

**Cost-to-complete forecast** (`cost-to-complete-forecast`). A per-job table of
section × cost type: budget, actual (to the date), the default cost to complete
(budget − actual, floored at zero), the amount you entered, the cost to complete
that counts, and the projected final. A sub-code rolls up to its section; budget
and cost with no code share one Unassigned cell. Type an amount in the Report
Center table (or `PUT /api/jobs/{id}/cost-to-complete-forecast` with
`{"entries": [{"cost_code_id", "cost_type", "amount"}]}`; `null` clears a cell
back to its default). Entries are stored in `job_cost_to_complete` and are
job-to-date, not dated: a WIP schedule run back-dated (a year-end run in February)
stops actuals at the date but uses today's typed amount, so percent complete can
read high, and the JSON carries a `warnings` entry when the amount was entered
after the as-of date. The older **Cost to complete** report defines cost to
complete as remaining budget plus open commitments, which differs from this
forecast, so the two can show different figures for the same job. PDF and CSV
like the other reports.

**Commitment status** (`commitment-status`). Each commitment (PO or subcontract,
not a draft, dated by `as_of`) per job: committed (pre-tax value), billed to date
(bills dated by then; a retainage-release bill pays money already billed and is
not counted), retainage held (held less released) and remaining (committed less
billed, open commitments only), with totals per job and grand. Committed leaves
out lines added by a sub change order approved after the date. A closed
commitment counts closed on every date, unless a bill against it is dated after
the date: then it was still open on it, and remaining is committed less billed by
then. PDF and CSV.

**Cost to complete** (`cost-to-complete`). Per job, per cost code (division ›
section), with budget revised, committed (remaining), actual, cost to complete
(the remaining budget plus open commitments), projected final (actual + cost to
complete), variance to budget and percent complete by cost, with totals per job
and grand totals. The totals **foot the rows** — cost to complete, projected and
variance are the sum of the clamped per-code figures (Σ max(…) ≠ max(Σ …) once a
code runs over), budget/committed/actual the raw sums. The JSON keeps the
cost-code tree (`children`); the PDF and CSV flatten it with the code path.

**Change order log** (`change-order-log`). Every change order per job — number,
kind, status, title, date, funding, cost, markup, sell, approved and voided
dates, source PCO and (for a sub CO) its commitment — with the running approved
total (the change orders approved by `as_of`) and the contract it builds
(original + approved changes = revised), the pending total (as of today) and
counts by status.

**Buyout report** (`buyout`). The job buyout across jobs: each trade package's
bid cost against what has been bought, with the variance, committed-remaining and
billed-to-date and the commitments behind each, the Unassigned row kept, totals
per job and grand. As of `as_of`, only commitments dated by then count and
billed-to-date is the non-void bills dated by then.

**Job cost by vendor** (`job-cost-by-vendor`). Posted job cost grouped job →
vendor (the transaction header's vendor; a No vendor bucket holds journals and
job cost entries), with each vendor's bills, paid to date (the bill payments
allocated to that vendor's bills on the job), retainage held outstanding (held −
released on those bills) and open A/P. The cost is for the period; the bill
figures are job-to-date and **prorated by the job's share of each bill's line
amounts** — a header-only bill counts whole, a bill split across jobs splits its
payment, retainage, A/P and count — so a job's bills can read fractional.

The existing **Job Profitability** and **Job Budget vs Actual** reports gained
PDF and CSV routes, so every contractor report exports the same way.

## The words

A contractor keeps almost every business word — Customer, Invoice, Estimate,
Job all stay, as they do in QuickBooks' Contractor Edition. The one that
changes is **Subcontractor → Sub**, on screens and in prose. Nothing in the
API, the database or the export formats changes name.

Customer:Job in QuickBooks prints with no space around the colon; SlowBooks
prints "Customer: Job" (with a space) everywhere today, and that is left
unchanged in Phase 2 — see *What this mode does not do yet*.

## The subcontractor vendor type

A vendor can be marked a **Sub** (the UI word; `vendor_type` is
`"sub"`, its only value for now — a Subcontractor). A sub carries:

- **License #** — the trade license number;
- **Insurance Carrier**, **Policy #**, **Insurance Expires** — the
  certificate of insurance a general contractor collects before a sub works.

These fields are blank on an ordinary vendor and reveal only when the vendor
is marked a sub; un-marking a sub clears the license and COI fields but
leaves the 1099 answer as it is. 1099 is off by default for every new vendor
— a lumber yard or the power company is not reportable. **The first time a
vendor is marked a Sub, 1099-NEC turns on** (a sub's labor is reportable);
you can change it afterward, and a sub already set to 1099-MISC keeps it.
W-9 on file is a fact you record, not a default, so it stays No.

## The reports

In construction mode the Report Center's first ten cards are the three job
reports that always existed — **Job Profitability**, **Job Budget vs Actual**
and **P&L by Job** — then the seven construction reports: **WIP Schedule**,
**Cost to Complete**, **Change Order Log**, **Buyout Report**, **Job Cost by
Vendor**, **Cost-to-Complete Forecast** and **Commitment Status** (see *Job
reports* above). The seven show in construction mode only. Every other report
stays where it is.

## What this mode does not do yet

- **No first-run prompt.** `company_type` still defaults to Business for a
  new company file; Construction is chosen in Settings afterward, as
  Nonprofit is. A "what kind of company is this?" step at creation is a
  later issue.
- **Customer:Job** still prints with a space; the no-space form is deferred.
- **No full section catalog** (the numbered catalog isn't ours to publish);
  the division titles and a set of common sections ship, and you import the
  rest.

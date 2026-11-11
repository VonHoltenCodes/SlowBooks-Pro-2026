# Contractor mode

Settings → Company → Company Information → Company Type → **Construction** sets a
company up the way a contractor keeps its books, the same way **Nonprofit**
does for a ministry: one switch, no change to any data, reversible at any
time. It is the counterpart to QuickBooks Desktop's Contractor Edition.
Construction mode adds the contractor accounts, division/section cost codes,
trade packages, a Sub vendor type, construction estimating, the contract and
schedule of values, commitments, change orders, progress billing with
retainage, field tickets (T&M tickets, material slips, delivery tickets), a
contractor's dashboard, and ten job reports. Every role reaches these screens; only
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
2. points each starting cost type that has no default account at its
   job-cost account — material → 5100 Materials Cost, labor → 5200 Labor
   Cost, subcontract → 5300 Subcontractor Costs, equipment → 5400 Equipment
   Cost, other → 5500 Other Job Costs (the COGS account with that number,
   else the account of that name; a type you already mapped keeps yours, and
   one whose account the chart has under neither is left unset),
3. loads the standard division titles and a set of common sections
   **only when the company has no cost codes of its own** (the same list the
   Cost Codes page's **Load standard divisions and common sections**
   button loads), ensuring the labor / material / subcontract / equipment /
   other cost types first, and
4. seeds the default **trade packages**, mapped to the sections just loaded
   (only when the company has no packages yet).

Setup is idempotent — safe to run again. It never renumbers or renames an
account you already have, never touches an existing cost code (a company
that has built its own cost-code chart gets no standard codes added), never
re-points a cost type you mapped, and changes no posting already made.
Switching back to Business leaves the accounts and codes in
place (as switching away from Nonprofit leaves its net-asset accounts); they
are ordinary accounts once created.

**Downgrading the database** (Alembic `downgrade` to a version before
construction mode) is lossier than the switch: the field tickets tables are
dropped, so **tickets and their lines are gone**; the SOV's Tax box and
revenue account columns are dropped, so every SOV line **returns taxable** and
posts to the default income account again; and the received quantity a delivery ticket wrote on a
purchase order line **stays** on the line, with no ticket left to void it.
Take a backup first.

**Upgrading an existing construction company** changes no posting. Schedule
lines made before the upgrade are pointed at the default income account
(4000), where their earlier pay applications credited, and a line added to
such a job afterwards (a change order's line, a new row on the grid, the
contingency line, a rebuilt schedule) takes that same account, so a job
part-way through keeps billing to one account. A new job's lines default to
4050. A
change order approved before the upgrade keeps taking effect on the day of its
click, except one whose schedule line is billed on a draft or submitted pay
application, which takes the earliest of the click, its own date and that
application's date, so the draft stays saveable (a change order with no date
takes the draft's date when that is before the click). An allowance-funded
change order approved before the upgrade never wrote its draw out of the
allowance's budget cell, so voiding it takes back its lines and nothing more,
including from a shared budget row the upgrade could not tie to it. A job
closed before the upgrade has no closing day on file (see *A closed job is
finished*). A downgrade past these migrations loses the schedule lines' revenue
accounts, the allowance-draw flags and the change orders' effective dates; an
upgrade after it fills them in again from the rules above, not as they were.

## The accounts

Each is created only if an account of that name does not already exist, and
each keeps its suggested number only if the chart has not already used it
(an imported chart that owns 1150 keeps it; the new account takes no
number). Each is pinned to a detail type so it lands on the right line
whatever the number-and-name rule would otherwise guess.

| Number | Name                                                | Type      | Detail type             |
| ------ | --------------------------------------------------- | --------- | ----------------------- |
| 1150   | Retainage Receivable                                | Asset     | Other Current Asset     |
| 1350   | Construction in Progress                            | Asset     | Other Current Asset     |
| 1360   | Costs and Estimated Earnings in Excess of Billings  | Asset     | Other Current Asset     |
| 2050   | Retainage Payable                                   | Liability | Other Current Liability |
| 2060   | Billings in Excess of Costs and Estimated Earnings  | Liability | Other Current Liability |
| 2070   | Provision for Loss on Uncompleted Contracts         | Liability | Other Current Liability |
| 2210   | Customer Deposits                                   | Liability | Other Current Liability |
| 2220   | Use Tax Payable                                     | Liability | Other Current Liability |
| 4050   | Contract Revenue                                    | Income    | Income                  |
| 4060   | Contract Revenue – WIP Adjustment                   | Income    | Income                  |
| 5400   | Equipment Cost                                      | COGS      | Cost of Goods Sold      |
| 5500   | Other Job Costs                                     | COGS      | Cost of Goods Sold      |
| 5600   | Loss on Uncompleted Contracts                       | COGS      | Cost of Goods Sold      |

Retainage is the portion of a billing held back until a job is accepted —
receivable when a customer holds it from you, payable when you hold it from
a sub. Construction in Progress is for a company that capitalises job cost
until it bills: nothing posts there on its own (job cost goes to the 5100–5500
job-cost accounts, and the WIP schedule's adjusting entry is the over- and
under-billing mechanism), so a company that wants it books it by journal
entry. Customer Deposits holds money taken before work. Use Tax Payable is the
use tax a contractor self-assesses on materials: a material bill on a job
whose contractor pays the tax accrues it there (see *Sales tax and use tax on
a job*). Contract Revenue is where a pay
application's billing posts unless its schedule-of-values line names another
income account. Equipment Cost and Other Job Costs are job-cost buckets
beside the seed chart's Materials, Labor and Subcontractor Costs. 1360, 2060,
2070, 4060 and 5600 take the WIP schedule's posted adjusting entry (under and
over billing, the revenue adjustment and the loss provision; see *Job
reports*). A company set up before they existed gets them the next time setup
runs, or the first time the WIP entry is posted.

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
sections**, including the ones a GC bids by: `02 41 19` Selective Demolition,
`09 21 16` Gypsum Board Assemblies and `23 00 00` HVAC, each already on its
default package (Demolition, Drywall and Ceilings, HVAC), and **Fences and
Gates** (`32 31 00`), which is the **Fences and Gates** trade package; a fence
sub adds the sections under it that it bids by. The full numbered section catalog isn't ours to publish, so it is
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
the section mapping, under **Settings → Lists → Trade Packages** (shown only for
construction companies). Estimate lines, commitments, the schedule of values
and the Buyout tab all use them. **A section you add later joins a package
for you:** the standard catalog's own package if it is a catalog section, else
the one package its division's other sections already belong to (when it is
exactly one; with two or none the section stays unmapped until you map it
here). Packages **list in bid-book order** everywhere they appear (the
estimate and its PDF, the schedule of values, the Buyout): by their lowest
mapped section code, then name, so Demolition (02) precedes Concrete (03);
a package with no mapped section follows those that have one, and Unassigned
is last. **Load standard divisions and common sections** on a company that
already has packages adds any standard package it lacks (Fences and Gates on a
company set up before it existed) and maps the new codes into it. It adds a
package only when no package of that name exists and none of its sections is
already mapped, so a package you renamed, remapped or edited is left as it is,
and loading again changes nothing. A standard package you deleted comes back
on the next load (unless a package of that name, or one holding its sections,
exists). A construction company with **no** packages at all (switched to
construction before it had sections) gets the standard set from the load, as
construction setup gives it; a Business or Nonprofit company gets none.

### Cost type per line

A cost code carries a **default** cost type, but the type is chosen **per
line** on a budget, estimate, commitment or bill — a `03 30 00` line can post
as subcontract on one job and as labor on another. Leave the line's type
blank to use the code's default, so existing books are unchanged. The picker
shows in construction mode only; a Business or Nonprofit company keeps its
line forms as they were and posts the code's type (the API accepts a line
type for any company). Job-cost reports read the line's type first and fall
back to the code's, and roll a section's cost up under its division.

**The cost type picks the account.** In construction mode a bill, purchase
order or vendor credit line with a cost code or cost type and no account of
its own posts to the code's account, else the default account of the line's
cost type (its own, else the code's) — the accounts construction setup maps
(subcontract → 5300, equipment → 5400 …) — before the vendor's default
expense account is tried. The account chosen on the line, then the item's
expense account, still come first. The **Sub pay app** / To Bill dialog
pre-fills each line's account the same way, and the bill form saves a costed
line with no account picked. A Job Cost Entry already resolves this way
(line account, then code, then type). A Business or Nonprofit company's lines
resolve as they always have.

## Estimating

A construction company prices its estimates one of two ways, chosen under
Settings → Company → Company Information as the **Estimate style** (key
`estimate_style`, #774). A Business or Nonprofit company's estimate form and
PDF are exactly as they were.

- **Markup per line** (`line_markup`) is how a
  remodeler, a trade contractor or a small builder prices: each line is
  Item, Description, Cost Code, Cost Type, Qty, Unit, Cost, Markup % and
  Price, and the price is the cost plus that line's markup (a 25% markup on a
  $16.00 cost is a $20.00 price). **Default markup (%)** in Settings (key
  `estimate_default_markup_pct`, 0 to 1000, blank is 0) fills in the markup
  of each new line; change it on any line. A line with no cost takes a typed
  price and no markup. The totals show Subtotal, Tax when it is charged and
  Total, and under them, for you only, the estimate's cost, profit and margin
  and how many priced lines have no cost. The owner's PDF prints each line's
  description, quantity and amount: no cost, no markup, no unit rate, no cost
  code.
- **Commercial bid** (`commercial_bid`, the default) is the general
  contractor's bid sheet described in the rest of this section: lines at cost, trade packages,
  allowances, alternates and unit prices, and four markups under the
  subtotal.

Every construction company starts on Commercial bid, the screen it had
before this setting arrived, and stays on it until it picks Markup per line.
An estimate that uses packages, a kind other than Base or any of the four
markups always opens as a commercial bid, whatever the setting says, so
nothing on it is hidden; one whose lines carry their own markups opens as
markup per line. Either way the job budget comes from each line's cost and the
contract and schedule of values from its price. On either form a costed
estimate whose price equals its cost says *Priced at cost: there is no markup
on this estimate*, and the totals panel leaves out any row other than Subtotal and Total that is $0.00,
and a Cost of work or Bid total that only repeats the Subtotal or the Total.

Through the API a line carries its markup as `markup_pct` (25 means 25%,
−100 to 10,000); a base or alternate line with a unit cost and a markup is
priced at unit cost × (1 + markup ÷ 100), to four places, and one with no
markup at cost (a line sent with a price above its cost and no `markup_pct`
is priced at cost, and the response's `warnings` say so). A markup and a unit
cost are kept to four places. Any other line keeps the
markup it carries without being priced by it: an allowance or unit price, a
line with no cost, or any line while the company is Business or Nonprofit, so
switching the company type and back, or a line's kind away and back, does not
lose the price. The estimate's `style` says which layout it gets, and its
totals carry `cost_total`, `profit` and `margin_pct`.

The commercial bid is a bid, not just a priced list (#551).

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
  (a budgeted figure is spent, not margined), and the Buyout reads that amount
  as the allowance's bid cost.
- **Alternate** — an add or deduct the customer can choose. Its amount is in
  no total until it is **accepted**; a deduct carries a negative amount, so it
  subtracts. An alternate that carries a unit cost is shown to the customer at
  its **marked-up price** (cost × the markup factor); one with no cost shows
  its rate price. Accepting it moves it into the scope (at cost, with its
  markups in the markup rows) and adds its cost to the markup base; the
  Alternates block then offers only the ones not yet taken. **An accepted
  alternate shows at cost everywhere** — in its package on the view and the
  owner's PDF, tagged *Add alternate, accepted* or *Deduct alternate,
  accepted*, and in the form's Alternates panel, which marks it *taken, at
  cost* — because that is the figure the subtotal holds, with the markup in the
  markup rows (or in the package price, see *The bid the owner receives*). Only
  an alternate not yet taken shows marked up.
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

Each markup is 0 to 999.9999 percent; a negative markup, which would price
the bid below its cost, is refused.

The **bid total** is the subtotal plus the markups, and it is **pre-tax**: the
owner's contract sum and schedule of values (#552) are pre-tax,
and tax is added on each invoice. Tax covers the taxable lines and **their
share of the markups** — the tax rate times (the taxable line subtotal + the
markups × the taxable lines' part of the markup base), the same pro-rata split
the budget and the owner's bid use — so a markup on an exempt line is never
taxed. A taxable line priced direct (no unit cost) carries no markup, so only
its own price is taxed; an exempt customer pays no tax on anything. Worked
example: A $60,000 not taxable and B $40,000 taxable, at 8 / 1.5 / 5 / 1 and
6.5%: the markups are 16,252.01, B's 40% share is 6,500.80, and the tax is
6.5% × 46,500.80 = **3,022.55** (40,000 × 1.16252 × 6.5%).
The **Total** is subtotal + markups + tax. The markups are a construction-mode
field: a Business or Nonprofit estimate stores 0 for all four whatever an API
caller sends, so its totals are the plain Subtotal / Tax / Total.

**Markup on allowances is the GC's call, and it is off by default.** Each
estimate carries a `markup_allowances` flag (a checkbox under Markups on the
form). Off, allowances sit in the subtotal at their amount and the markups skip
them. On, the allowance amounts join the markup base, so the four markups are
figured on them too (and the budget revenue split follows). The form's totals
panel and the view show "Markup on allowances" as On or Off; **the owner's PDF
does not**, since the setting is the GC's own. The flag freezes with the rest of the price once the
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
**General conditions, insurance and bond are both revenue and cost:** each
percent stays markup revenue, and on accept each amount is also budgeted as a
cost on a code of its own (`GC` General Conditions, `INS` Insurance, `BOND`
Bond; created if the books have none, reused if they do), so the job carries
the premiums a GC really pays. Those rows carry cost only; their revenue is
already in the rows above, so it is not counted twice. The **fee** is the only
markup that is not also a cost, so the job's budgeted margin is exactly the
fee. **Accept also awards the job:** a job still `pending` becomes `awarded`
(a job already awarded, in progress or closed is left alone), and the first
approved pay application moves a pending or awarded job to `in progress`. The
Accept confirm names the budget it locks (the cost budget against the bid
total), says how many lines have no cost code (their budget lands on no code),
and warns when the estimate's expiration date has passed. Accept still goes
ahead; `POST /api/estimates/{id}/accept` returns the same count as a sentence in
its `warnings` list (`uncoded_lines` on the estimate carries the number). Converting an accepted estimate to an invoice drops
the unit-price lines and any un-accepted alternate, carries each line's cost
type and unit cost onto the invoice, and adds one invoice line per non-zero
markup (on the job) so the invoice foots to the bid.

**Convert is for jobs that do not bill by pay application.** A job with a
contract type, a schedule of values or any pay application bills through
pay applications, with retainage; converting its estimate would post the whole
contract as income a second time, so `POST /api/estimates/{id}/convert` answers
409 ("This job bills by pay application; …") and the estimate list and view
hide Convert (the estimate's `bills_by_pay_app` says which). A plain job
converts as before.

**Accept, Lost and the list.** On the Estimates list a pending estimate with a
job shows **Accept** as its main action (Convert is secondary, or absent on a
contract job); one with no job says "Needs a job to accept" instead. **Mark
lost** (`POST /api/estimates/{id}/lost`, with an optional `reason` of up to
500 characters; any other field, a non-text reason or a longer one is a 422)
sets the
status to `rejected` and keeps the reason, dated, in the notes; an accepted or
converted estimate can't be marked lost. The list carries a **Status** filter
(Pending / Accepted / Lost / Converted); `GET /api/estimates?status=` filters
the same way.

### The bid the owner receives

The customer PDF (and Print) of a construction estimate is built to go out
under the contractor's name, so it carries no Status line (the owner is not told
it is "Pending"); a Business or Nonprofit estimate PDF is unchanged.

- **Header and cover.** The company's **contractor license #** (Settings →
  Company → Company Information, shown once the company is Construction) prints
  in the header under the company name. A **Project** block beside *Prepared
  for* carries the job's name and site address (left out when the estimate has
  no job).
- **Show markup rows** is a checkbox on each estimate (Markups, default on —
  the rows under the subtotal described above). Off, the PDF drops the General
  conditions, Insurance, Fee and Bond rows and spreads the markups into each
  package's price: the budget's own split, pro rata to each row's share of the
  markup base with the last row taking the rounding remainder, so the package
  prices, the allowances (which carry a share only when *Markup on allowances*
  is on) and any priced-direct package foot to the bid to the cent. A package
  prints its price on its heading row with its lines listed by description and
  quantity; one **Subtotal** (the bid total) stands in for the rows, then Tax
  and Total. The stored subtotal, markups, tax and total do not change, the
  view and the form still show the markup rows (a note on the view says the
  owner's PDF hides them), and the flag edits after the estimate is accepted
  since it moves no figure.
- **Terms, inclusions and exclusions.** A default text in Settings (**Default
  estimate terms, inclusions and exclusions**, key `estimate_terms`) is what a
  new construction estimate starts with, through the form or the API when no
  `terms` is sent; edit it on any estimate (it stays editable after accept). It
  prints above the acceptance block. A blank `terms` is kept blank.
- **Acceptance block.** An owner signature line, a printed-name line and a date
  line, kept together on one page.
- **The totals block stays together**, so a page break never leaves Total alone
  on the next page.
- **Email.** The estimate view has an **Email** button
  (`POST /api/estimates/{id}/email`: `recipient`, `cc`, `bcc`, `subject`,
  `message`). It sends the PDF as an attachment through the same SMTP settings,
  address checks, always-copy settings and Recent sends log as an invoice; the
  recipient defaults to the customer's email, and an estimate or customer with
  no address and none typed is a 400.

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
the job form still works for everyone. A newly created job opens on its
**Overview**; each job you open keeps the tab you left it on for the rest of
the session, so one job's Pay apps no longer carries over to the next (#723).

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
- **Sales tax** — who pays the tax on the job: **Charge sales tax to the
  owner** (the default, and every job before this) or **Contractor pays tax
  on materials (no sales tax to the owner)**, the lump-sum real-property rule
  in Florida and many states (`sales_tax_treatment`: `charge_owner` or
  `contractor_pays`). See *Sales tax and use tax on a job* below.

**Sales tax and use tax on a job.** On a *charge the owner* job the pay
applications tax the taxable SOV lines (see *Progress billing*). On a
*contractor pays* job the contractor is the consumer of the materials it
installs:

- **Its pay applications bill no sales tax at all** — work, stored materials
  and change orders alike, whatever the SOV's Tax boxes say. The *Charge tax*
  box on the application is off and greyed, saying why. The estimate keeps
  its own tax figures; it is the bid, not the billing.
- **Its material bills accrue use tax.** A bill line whose cost type is
  *material*, on the job (the line's, else the bill's), on a bill the vendor
  charged **no** sales tax on, accrues the company's **use-tax rate** × the
  line's amount in the bill's own journal: DR the line's job-cost account
  with its job, section and cost type / CR **2220 Use Tax Payable** with the
  job. Accounts Payable is still the bill's total (the use tax is owed to the
  state, not the vendor), and the job's cost carries the tax, as it would
  had the vendor charged it. A commitment billed through **To Bill** accrues
  the same way. Voiding the bill reverses the accrual with the rest of its
  journal; editing it re-posts it. A stock item (received into Inventory)
  accrues none.
- **The Use tax box** on a bill line (beside Cost Type) accrues use tax on
  that line whatever the job or cost type: equipment bought untaxed, material
  for a *charge the owner* job used on the company's own work. Through the
  API, `use_tax` is null (follow the rule), true (accrue) or false (never).
  A memorized bill keeps the box on each line (Memorize Bill carries it from
  the bill), and every bill entered from it accrues as the box says, at the
  rate in force when that bill is entered.
- **The rate** is Settings → Invoice Defaults → **Use tax rate**, a percent
  like the default tax rate; blank (the default) uses the default tax rate.
  **A bill keeps the rate it was booked at.** The rate is stored on each bill
  line when its use tax first accrues (`bill_lines.use_tax_rate`), and every
  edit of the bill re-posts at it: changing the setting from 6.5 to 7 and then
  changing a line's description, amount or the bill number leaves an 8/20
  accrual at 6.5%. The edit sends each existing line with its
  `id`, and a line keeps its own booked rate wherever it sits afterwards
  (delete the first line, or insert one on top, and the others do not move);
  a line sent without an id, or one that accrues for the first time on the
  edit (one newly marked Use tax, say), takes today's setting. An id that is
  not a line of the bill is refused. A void reverses the booked figures.
  Books from before this have the rate filled in from each bill's entry (the
  rate on the memo, else the accrual over the line's amount).
  **When both are 0 nothing accrues, and the program says so:** saving the
  Contract tab with *Contractor pays tax on materials* returns a warning
  (`warnings` on the response, shown as a toast) naming the Use tax rate
  setting, and a bill (or a commitment billed through To Bill) with a line that
  would owe use tax at 0% carries the same kind of sentence in its response.
  The bill still posts.
- **The Sales Tax report** has a **Use tax** section: each accrual in the
  dates (bill, vendor, job, line, the **rate it was booked at**, amount; a void
  shows negative; the rate is the one stored on the bill line, written on the
  journal line's memo, so changing the setting later does not rewrite it), the use tax
  accrued and paid in the dates, and what is owed on 2220 at the end date. On
  the cash basis a bill's use tax is accrued as the bill is paid. **Paying
  it** is an ordinary payment to the tax agency: a check or bill to the
  agency on account 2220 Use Tax Payable (Pay Sales Tax is for 2200).

Changing the setting changes what is posted from then on; nothing already
approved or posted moves.

The summary stamps the **original contract amount** once, the first time a sum
is set with a type (building from an estimate stamps it too, even when the sum
came from the job form). A sum typed on the job form alone is not a contract
yet: the type stays empty until the job gets a schedule of values. Saving the
schedule of values by hand, making a pay application or approving one then
takes a sum with no type as a **lump sum** and stamps its original, the same
as building from an estimate. A job that already has a sum and no original
(one billed before 2.31.8) reads its original as the contract sum less the
approved change orders, on the Contract tab, the WIP schedule, the
change-order log and the pay application PDF alike (#723). The **contract sum and the contingency edit freely**
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

**The schedule of values.** (Saving one that does not add up to the contract
sum to date is allowed, since a job may be mid-setup, but the save answers with
a `warnings` sentence ("The schedule of values totals $116,500.00, $2,000.00
short of the contract sum to date of $118,500.00…"), shown as a toast, and
every read of a pay application on that job carries it, approve included: the
application prints the contract sum above line detail that totals something
else.) The SOV is the contract sum broken into the lines
an owner pay application bills against — each with an item number, a
description, an optional package and section, a **revenue account**, a
**Tax** box, a kind (work, allowance, contingency, or one of the four
markups: general conditions, insurance, fee, bond) and a scheduled value. The
section is the cost code the line's billing posts with and the revenue
account is the income account it credits (blank means 4050 Contract
Revenue), so revenue lands by section beside cost; the Tax box says whether
sales tax is charged on the line's billing (see *Progress billing* below). The sum (and the schedule) is **pre-tax**: sales
tax is added on each invoice, not carried in the contract. **Build from
accepted estimate** lays it out automatically: **work** lines per trade
package, in bid-book order (its base lines plus any accepted alternates, **at cost** — the lines
carry no markup), a work line for each line with no package, an **allowance** line for each allowance, then one line per
**non-zero markup** in order (general conditions, insurance, fee, bond), each
labelled with its percent, and a **contingency** line when the job carries one.
The work, allowance and markup lines foot exactly to the pre-tax bid. A
package is one work line per section its estimate lines carry: a package
whose lines share one section is one line under the package's name, and one
whose lines sit on several is a line per section, in code order, named
"Concrete — 03 30 00 Cast-in-Place Concrete". A line with **no package** is
its own work line under its own description (an accepted deduct alternate
reads as its own text), so the owner's schedule, pay applications and PDF
never print "Unassigned", and neither does the owner's bid PDF (lines with no
package print without a heading; with the markup rows off their price stands on
the one line, or on an unlabelled row under them); the internal reports (Buyout,
the cost tree) and screens keep that row. Each built line credits **4050 Contract Revenue** (or, rebuilding a schedule whose lines all credit one account, that account) and carries:

- **its section** — a work line carries the cost code its estimate lines
  carry, the code Accept budgets them on, so its billing lands on the section
  its budget and cost are on; a line with no code takes its package's lowest
  section (none when the package maps none). An allowance takes its own
  line's cost code, else its package's section. A markup or contingency line
  carries none.
- **its Tax box** — a work or allowance line is taxable when its estimate
  lines are. A package section whose lines are partly taxable becomes **two** work
  lines, the taxable part under its name and the rest as
  "*Package* (not taxable)", so a pay application at 100% charges the tax the
  bid quoted. A **markup** line follows the taxable share of the markup base:
  wholly taxable when every marked-up line is, not taxable when none is, and
  on a mixed bid **two** lines — "General conditions (8%)" for the part on
  the taxable lines and "General conditions (8%) (not taxable)" for the rest.
  The taxable parts sum to the estimate's taxable share to the cent (the last
  markup takes the rounding), so the A/B example above bills exactly 3,022.55
  of tax at 100%, in one application or several. An application that brings a
taxable line to 100% complete trues the tax up: it charges the unrounded tax of
every application so far, rounded once, less the tax already billed, so a
rounding cent left by the earlier applications comes out there. Billed at
33.33%, 33.33% and the rest, the A/B example charges 1,007.42, 1,007.42 and
1,007.71: 3,022.55, not 3,022.56. A partial application rounds its own tax. A
contingency line is taxable when any line on the schedule
  is. **Convert to invoice** splits its markup lines the same way, so the
  converted invoice charges the bid's tax.

Each of the three is editable per line in the grid. A line saved through the
API without `revenue_account_id` or `is_taxable` keeps what it had (a new
line: taxable, and the account every line on the schedule credits, else
4050; a row added in the grid starts on that account too); a revenue account that is not an income account is
refused (422). A schedule built before this has no account on its lines (they
post to 4050) and is taxable throughout, so it taxes as it always did. You
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
line is fully billed. A billed line cannot be removed or cut below what was
billed on it (409, code `commitment_billed`): on a subcontract, change it with
a sub change order; on a supplier PO, void the bill first. A business company's PO bills in one go exactly as before. The dialog takes a
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
sits on 2050, not A/P). Both agings read what was open on the report's date: a
bill or invoice paid since still counts then, so the total equals account 2000
(1100) on the Balance Sheet for that date. A bill entered by hand can hold retainage the same way.
The 2050 line carries the bill's job, so release can find it by job.
Editing the lines of a converted bill re-computes the retainage at the commitment's rate on the new subtotal, unless the edit sends `retainage_held`. A retainage that was set by hand (sent as `retainage_held` through the API; the bill's `retainage_manual` is then true) is kept across later edits of the lines, tax or date, while one the commitment's rate derived keeps following that rate even after the rate is changed; it still may not exceed the new subtotal or leave less than has been paid. A fully billed (closed) commitment refuses a higher amount (409), and it takes no sub change order: write the added work on a new commitment.
Release it with **Release retainage** on the commitment
(`POST /api/purchase-orders/{id}/release-retainage`): a release bill debits
2050 back for the amount released, see *Progress billing and retainage* below.

**Retainage on the cash basis.** A bill's held retainage is not an expense on
a cash-basis report until it is paid: paying the net spreads that amount over
the bill's expense lines, the held part waits on 2050 (which reads zero on the
cash Balance Sheet while it waits), and paying the release bill recognizes the
rest of the expense on the day it is paid, on the held bills' own expense
lines (account, job, cost code and cost type), never on 6990 Unapplied Cash
Bill Payment Expense. A release draws on the commitment's bills held before
it in proportion to what each still holds, so partial releases that add up
to all that was held recognize each bill's held part once. Once every bill
and release is paid, cash-basis cost, P&L by Job and Job Profitability equal
the accrual figures. Editing a held bill's lines rewrites the cash lines of
its release's payments too. Accrual reports are unchanged: the whole bill on
its date, the held part on Retainage Payable.

**What committed and billed mean now.** *Committed* cost is the **remaining**
open commitment — the scheduled value of every issued, not-yet-closed
commitment, less what has been billed — so it keeps meaning "ordered but not
yet in the ledger". Beside it the job now carries **committed total** (the
whole of every issued commitment) and **billed to date** (what has been billed
against them), shown as tiles on the job Overview (construction mode) and in the
cost-tree totals; the Overview's "Billed to date (subs)" is the sub/supplier
side and is distinct from the Contract tab's owner "Billed to date". A
contractor's cost tree, Job Budget vs Actual and Cost to Complete read all
three **as of the end date**, as Commitment Status does that day: a commitment
dated by then, a sub change order's lines once it is in effect, billed less the
linked bill lines dated after it, and a closed commitment still open when a
bill after the date closed it (`commitments_all_time` is then false). A
Business or Nonprofit company's are whole-commitment figures on every date, as
before (`commitments_all_time` true).

**Issuing a commitment.** A new commitment is a **draft**, and a draft is in no
Buyout, committed or commitment-status figure. **Issue** (on the Purchase
Orders list and the view) moves it to `sent` (issued); Edit has a **Status**
select too. In construction mode the list and the view label `sent` as
**Issued**, as the form and the button do. `PUT /api/purchase-orders/{id}` with a status that isn't one of
`draft`, `sent`, `partial`, `received`, `closed` answers 422 naming them.

**Bid vs buyout.** A **Buyout** tab on the job (after Contract, construction
mode only) pairs each trade package's **bid** — the accepted estimate's cost
for that package, an allowance at its amount — against what has been **bought**
(the commitments tagged to it). Beside them:

- **Changes** — the approved change-order cost for the package (owner change
  orders and standalone sub change orders; a sub change order that carries an
  owner change order is counted once, on the owner's). The **variance** is
  bid + changes − bought, so an owner change order that funds a sub change
  order reads as covered, not as a loss. A line is placed by its own package,
  else the package its cost code maps to. An **allowance-funded** change order
  draws the allowance down rather than adding scope: besides its own cost,
  which lands where its lines place it, it takes the same cost off the
  allowance's row (the package of the allowance line's cost code), so
  bid + changes − bought on that row is what the allowance has left. The
  allowance's bid is placed the way its changes are: an estimate line with a
  cost code and no package is bid in the package its code maps to, not under
  *Unassigned* (a deduct alternate keeps to its own package, or to *Unassigned*
  when its code is in none).
- **Spent, no commitment** — coded lines of bills with no commitment, placed
  by their cost code's package, so a plain bill is not a $0 bought. It counts
  bills only: a material slip (a field ticket that posts job cost from stock)
  is job cost in no Buyout column, so the job's cost to date can run past
  bought plus spent here by the slips on it.
- the committed-remaining, the billed-to-date and the commitments behind each
  package, linked through.

The three markups that are also costs show as rows of their own after the
packages (**General conditions**, **Insurance**, **Bond**), so the table's bid
total is the cost budget the accepted estimate locks (the Overview and Cost
Detail budget). Packages bid but not yet bought show with nothing bought;
commitments, changes and bills with no package gather under *Unassigned*. The
Buyout Report carries the same two columns. `GET /api/jobs/{id}/buyout` is the data (construction mode only, 409
elsewhere, as the Buyout Report is), and the seam the buyout
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
percent must be above -100% and at most 999.9999%.

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

**A change order takes effect on the day you say, not the day you click.**
Approve takes an `approved_on` date. It defaults to the change order's own
date, else today, and the Approve dialog shows it and lets you change it: a
change order entered a few weeks late still moves the contract on the day the
owner signed. That date is the one every as-of reader uses, the WIP schedule,
the change order log's running approved total, a sub change order's
commitment, and the Contract tab, while `approved_at` stays the stamp of the
click (the audit trail). A pay application dated before a change order takes
effect does not carry it: its **contract sum to date** and **net change by
change orders** read the change orders that took effect on or before the
application's date, whether it is a draft or approved (a change order entered
before the application but taking effect after it shows on the next one). That
change order's line on the schedule of values stays off the application too (on
screen, in the totals and on the PDF), so the schedule total equals the contract
sum to date; a draft picks the line up when its date moves past the change
order's effective date. The application cannot bill that line before then:
saving it with a date before the change order takes effect while the line
bills something on it (work this period, or a stored balance other than the
one the earlier applications left) is refused, 409 code
`change_order_not_in_effect`, naming the change order, its date and the line
(its item number, else its description, else "the change order's line": "CO-003
takes effect on 2026-09-20, after this application's date (2026-09-01), so its
line 05 cannot bill on it …"); clear the line or keep the later date. Submit
and approve check the same. A line with nothing on it just drops off the
application, as before.
The exception is a line an earlier approved application already billed work
on **or left stored materials on**: it stays on a later-numbered application
dated before the change order takes effect, at the stored balance it was left
with, so completed-to-date never falls below what was billed and approve never
posts a retainage release for the difference. Such an application's **contract
sum to date** and **net change by change orders** count every change order
whose line it carries, so the summary foots against the line detail: 100,000
plus a 10,000 change order its schedule carries is 110,000, the schedule's
total, and the balance to finish is figured off the same 110,000.
Approve refuses a date that cannot be right, each with a sentence (422): an
`approved_on` **earlier than the change order's own date**, and any effective
date (given or defaulted) **earlier than the job's contract date**, since a WIP,
pay application or report would show the change order before the contract
existed. Approving with **no date** when the change order's own date is more
than 30 days back takes effect that long ago; the response carries a `warning`
saying so and the Approve dialog notes it under the date. Approving with an
effective date **on or before a date a WIP entry is posted for** (given or
defaulted) changes those schedules while the entries on the books stay as
posted, so the response's `warnings` names each posted date ("…takes effect on
2026-08-10, so it changes the WIP entries posted for 2026-08-31 and
2026-09-30; void and re-post them on the WIP schedule.") and the dialog stays
open with the sentences until OK. The WIP schedule flags the same entries
(*Job reports*).
Approving in a closed period is refused like a bill or a pay application there:
403 with "Transaction date … is on or before the closing date (…). Modifications
to closed periods are not allowed." (the closing-date password override works
the same way). Voiding an approved change order is refused the same way when the
date it took effect falls in a closed period. Change orders approved before this keep the local day of their
approve click.

- *Owner CO, contract-funded* — adds the sell total to the **contract amount**
  (the original sum is never touched, so the Contract tab reads original +
  changes = revised), appends a read-only **change_order** line to the
  schedule of values for the sell total, and writes locked budget rows
  (`source = change`) with the cost on the cost side and the sell on the
  revenue side. The job-cost tree shows these in its **Changes** column.
  The change order's SOV line takes the cost code its lines share, else the
  lowest of them, and extends the schedule's line for that section: it takes
  that line's **revenue account** (when there is none, the account every
  line on the schedule credits, else 4050 Contract Revenue)
  and the next **item number**, so the pay-application PDF's Item column is
  filled. Its **Tax box** is the section's lines' when they agree; else that
  of the lines of the change order's package; else the schedule's — taxed
  when every line on it is taxed (or it is empty), and **not taxed when the
  schedule is mixed** or taxes nothing. So a change order on exempt work
  (electrical on a bid that taxes only materials) bills no tax; tick its Tax
  box on the Contract tab's grid if it should.
  Every kind of change order writes **rows of its own** (one per code and cost
  type, linked by `change_order_id`), and every report run as of a date takes
  only the rows of the change orders **in effect by then** (their approved-on
  date, the day the revenue side reads too): the WIP's estimated total cost,
  Budget vs Actual, the cost tree, Cost to Complete and the forecast. So a
  month already posted reproduces its entry after a later change order.
- *Owner CO, contingency-funded* — the contract is unchanged; the budget rows
  carry the **cost with no revenue** (the contract already holds the
  contingency money); the draw counts against the contract's contingency
  (refused 409 `contingency_exceeded` when it would pass the contingency
  amount).
- *Owner CO, allowance-funded* — the contract is unchanged and no SOV line is
  added. The allowance was budgeted as cost when the estimate was accepted, so
  the change order **moves** that cost rather than adding to it: its cost goes
  on the change order's code and type, and the same cost comes out of the
  allowance's cell (a locked change row against the allowance's code), so the
  job's total budgeted cost stays what it was. No revenue is written (the
  allowance already holds the money, and the work carries no markup). Void
  puts the cost back in the allowance's cell. A job whose allowance has no
  budget cell (a schedule typed by hand) just gets the change order's cost.
  The draw counts against the allowance line (refused 409
  `allowance_exceeded` when it would pass the allowance). The Contract tab
  shows each allowance's used and remaining.
- *Sub CO* — appends the lines to its commitment, which must be on the **same
  job** and **open** (refused 409 `commitment_job_mismatch` or
  `commitment_not_open` otherwise), and recomputes the commitment's total; the
  buyout table's *bought* grows automatically. The budget rows carry the cost
  with no revenue (and none at all when the sub CO backs an owner CO, which
  already carried that cost).

**Return unspent allowance.** An allowance is billed as scheduled whether or
not the owner's choices used it all. The Contract tab's allowance row shows
**Return unspent** (when something is left; or
`POST /api/change-orders/allowance-return` with the allowance line, an
optional `amount` (default everything unspent) and an optional `date`
(default today; the dialog asks for it). The date is the change order's date,
and approving it cannot take effect earlier, so a month-end return is dated in
that month; it cannot be before the contract date (422) or in a closed period
(403). It makes a **draft owner
change order for minus the unspent amount**, no markup (whatever the job's
default says), funded against that allowance: it is contract-funded, because
it takes the money off the contract, and it names the allowance line. Submit
and approve it like any other (it takes effect on its date, in the open
period). Approving it:

- takes the amount off the contract sum, and the budget gives up that cost and
  that revenue (the cost will not be spent);
- takes the **unbilled** part off the allowance line's scheduled value, so the
  line's balance to finish is what is left to bill, **0** once billed up to
  it, with no $3,500 left on the application forever;
- when part of the allowance has already been billed, that part cannot come off
  the line: it becomes a read-only credit line on the schedule that the next
  pay application bills.

"Unspent" is what the Contract tab shows as **remaining**: the allowance line's
scheduled value less the allowance-funded change orders that drew it (and any
earlier return's credit). A return of an allowance with nothing unspent is
refused (409 `allowance_nothing_unspent`, "… has nothing unspent to return");
so is a second return while one is still draft or pending (409
`allowance_return_open`) and one for more than is unspent (422). Its funding,
allowance line and markup are fixed; change the amount, title or date, or reject
it. Void gives the line and the contract back what the return took. A
**contract-funded deduct** made by hand is an ordinary contract-funded change
order, so it **carries markup** (a −3,500 cost sells at −4,025 at 15%) unless
its markup is set to 0, and it leaves the allowance line itself untouched; use
**Return unspent** for an allowance.

Because the budget key includes the row's source, a change-order budget row
sits beside an estimate-locked or manual row on the same code and type — the
job-cost tree reads one under **Original** and the other under **Changes**.
A change order's own rows are keyed on the change order, so an allowance draw
and a later return of the same allowance are two rows, each in effect from its
own date. Books from before the link had one shared change row per code and
type; upgrading splits a shared row into one row per change order when the
approved change orders' own figures add up to it to the cent, and leaves it
shared (counted on every date, as before) when they do not, as when the
allowance cell is no longer the largest row on its code.

A change order's figures **freeze** when it is approved: the cost, sell and
the effective markup each line carried are stored, so changing the job's
markup or a contract term afterward never moves an approved change order, and
void reverses the exact figures it applied.

**Void** reverses each of these — the contract and SOV line, the budget rows,
and the appended commitment lines (exactly the ones it added) — except that a
commitment line already billed against cannot be pulled (409 `co_billed`).
The SOV line comes off any open (draft or submitted) pay application that
carries it with no amounts, so the draft no longer lists a voided change or
a balance to finish on it; an approved application keeps the line as history. The
lifecycle is draft → submitted (*pending*) → approved, with reject and void
beside it; a PCO runs
draft → pending → promoted (asking to *promote* rather than approve).

**Billed change orders are locked.** Once a pay application has billed a
change order's schedule-of-values line (#555, below), that change order can no
longer be voided — the void answers 409 `co_billed`, the same guard a sub
change order's billed commitment line already carried. Any amount counts,
either sign (a deduct's credit too), on any application not void: an
approved one reads "PA-1-03 has billed this change order; it cannot be
voided."; a draft or submitted one names itself ("PA-1-04, a draft pay
application, bills this change order. Take its amounts off that application
(or delete it), then void the change order."). The change-order **log
report** (#556, see *Job reports* below) lists every change order and its effect.

## Progress billing and retainage

Progress billing turns the owner's schedule of values into a **pay
application** — the contractor's periodic bill against the contract, the
counterpart to a commercial payment application and its line detail (#555).
Each application bills a period; approving it creates the invoice.

**A job needs a schedule of values first.** An application has one line per
SOV line, so on a job with none the Pay apps tab says "This job has no
schedule of values yet: set the contract on the job's Contract tab (Build
from accepted estimate, or add the lines by hand), then bill against it
here." in place of **+ New pay application**, with a link to the Contract
tab, and `POST /api/pay-applications` refuses with 409 and the same sentence
(#714).

**An open application can be deleted.** A draft or submitted application is
deleted whatever its lines, so a draft that has gone stale or was started by
mistake is never a trap: its row on the Pay apps tab (and its form) offers
**Delete** beside Submit and Approve, and the job can then start a new one
(`DELETE /api/pay-applications/{id}`; it has no invoice or journal to undo). An
approved application is never deleted: it is voided (409
`application_not_open`). One made before 2.31.8 on a job with no schedule of
values has no lines at all, so it could not be given amounts or approved; Edit
says why there is nothing on it to bill and offers Delete in place of Submit
and Approve. While an application is open, New is refused with a sentence that
names it and says to submit, approve or delete it. An approved application with
no lines and a zero total is voided like any other (#723).

**A draft follows the schedule of values.** A change order approved, or a line
added to the SOV, after a draft was made would leave the draft without it.
Opening a draft on the Pay apps tab refreshes it (`POST
/api/pay-applications/{id}/refresh-lines`): every SOV line the draft lacks is
added, and each line's item, description and scheduled value are read from the
SOV as it stands now. The amounts already typed are kept. A line whose SOV
line is gone (its change order voided, or the line taken off the schedule) is
dropped when it carries no amounts. Saving, submitting or
approving a draft refreshes it first, so an API client never meets "SOV line N
is not on this pay application". A submitted application is as it was sent and
is not refreshed; `refresh-lines` on an approved or void one is refused (409,
"PA-1-03 is approved; only a draft picks up changes to the schedule of
values."). **No approve saves a line whose SOV line no longer exists:** a
submitted application drops such a line when it carries nothing, and one
that carries amounts is refused (409, "02 Extra is no longer on the job's
schedule of values. Take its amounts off PA-1-04 (or delete the
application), then approve.").

**The application date and its terms.** The form carries an **Application
date** beside Period to. It is the invoice date and the date the invoice posts,
and it starts as the period-to date (else today) and follows Period to until it
is set by hand; the closing-date guard applies when it is saved and when the
application is approved. The invoice is due by the customer's payment terms
(their terms, else the company's Default Terms, through the same parser as
every other invoice), counted from the application date: an owner on Net 45
billed July 31 owes on September 14, and one on Due on Receipt owes that day. Request
dates are held to 1900–2199 by the general validator, so a date near the
calendar's end cannot reach this through the API; one stored by an import and
dated so near 9999-12-31 that the terms run past it is refused with a 422
sentence on approve ("runs past the calendar"). Terms are never clamped to a
last day: text longer than 99,999 days ("Net 3000000") reads as no terms at all,
so the invoice takes the default 30 days with a note that the terms were not
understood (dated 2199-12-01 it falls due 2199-12-31 by that 30 days), and "Net
99999" is simply due on the day 99,999 days on. The
invoice is tied to that entry of the Payment Terms list, which is seeded first on
a company whose list is still empty.

**The application and its math.** Every SOV line (work, allowance, contingency
and change-order lines all bill the same way) carries, per period:

- **Scheduled value** — the line's amount.
- **Previous** — the sum of *this period* on this job's earlier approved
  applications for the line.
- **This period** — the work completed this period.
- **Stored** — materials presently stored, not yet in the work (not
  cumulative). A new application starts each line at the stored balance the
  earlier approved applications left on it; lower it as the materials go into
  the work.
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
released, CR each line's **revenue account** (its SOV line's, else 4050
Contract Revenue) with the job and the line's **cost code**, CR sales tax as
any invoice. Revenue by section then shows in the job cost tree's *Revenue
by section* table (beside each section's cost) and P&L by Job. Sales tax is **on by
default** and can be switched off per application (the *Charge tax* box); it is
charged on the work billed this period **on the taxable SOV lines** (a line
whose Tax box is off bills untaxed, and the invoice line says so; a job whose
contractor pays the tax bills none, see *Sales tax and use tax on a job*) at the
application's **tax rate**,
defaulted from the job's accepted estimate (kept to six decimals, so 8.875%
stays 8.875%) and editable while the application is open. A job with no accepted estimate has no rate to default from, so its
applications start at **0**: while an application has a taxable line, tax is
charged and the rate is 0, the application carries a `warnings` sentence naming
the Tax rate field (shown as a toast), and it clears once the rate is set or
Charge tax is unticked. A *contractor pays* job's application reads `taxable_billed` 0 beside its 0 tax,
since nothing it bills is taxed. A tax-exempt owner is never taxed. The application shows the tax and
its current due includes it, so the application and the invoice match. The
invoice's balance due is that net; the invoice view and PDF show
*Retainage withheld this period*, *Retainage released* and *Current payment
due*. On the application's PDF every amount keeps its minus sign on the same
line as its figure (a negative current payment due prints "-$7,894.13" whole),
and a deduct line with nothing billed prints 0.0%, never -0.0%.

**Stored materials and tax.** On a *charge the owner* job, materials stored
are billed (and taxed on a taxable line) the period they are stored, and the
change is reversed the period they go into the work, as above. On a
*contractor pays* job they bill no tax, like everything else on it. Stored
materials are booked as billed revenue when billed — there is no
stored-material asset — and the WIP schedule's adjusting entry takes out any
over-billing they cause.

**The customer statement** lists such an invoice at what it put on Accounts
Receivable (its total less the retainage held, plus any released; the line says
how much was held or released), so its **Balance Due** is the amount due and
equals A/R Aging for that customer. The retainage still held across the
customer's invoices (held − released, to the statement date) prints on its own
line, *Retainage held, not yet due*, beside the balance. A late fee (Apply Late Fees)
posts its own A/R debit and is part of the invoice's balance from the day it posts: an
edit of the invoice keeps it, and an aging dated before that day reads the invoice
without it, so A/R Aging ties to 1100 on every date. The statement takes
`as_of_date` only; it has no start date. A dropped retainage percent makes the retainage this period negative and
the 1150 leg a credit; the entry still balances. **Void** reverses the
invoice (and the 1150 entries), and later applications stay correct because
each reads its *previous* and *retainage to date* from the approved history
live. The Pay apps tab offers Void only on the application that can be voided:
the latest approved one, with no draft or submitted application after it,
whose invoice has no payment on it other than its own deposit (`can_void` on
the application). An application's invoice is voided
with the application: voiding it from
the Invoices screen is refused (409 `invoice_belongs_to_pay_application`). Only
the **latest approved** application can be voided (409 `later_application`
otherwise); void the later ones first. Nor is it voided while a later
application is still open (409 `later_application`, naming it): that one was
filled from it, its stored materials carried forward from the approved
balance, so delete it first and make it again after the void. Only one application
may be open (draft or submitted) per job at a time.

**The Pay apps list and the printed application foot.** The list shows, per
application, **Billed** (the work this period plus the change in stored
materials), **Retainage** (withheld this period), **Released**, **Tax** and
**Current due**, so Billed − Retainage + Released + Tax = Current due on every
row. The PDF's summary adds the same way: *Retainage held, previous
applications* + *Retainage this period* − *Less retainage released* =
*Retainage to date*; *Completed and stored to date* − retainage to date =
*Total earned less retainage*; and total earned less retainage − *Less previous
payment applications* + *Sales tax* = *Current payment due* (a negative
application prints a negative tax line), then *Less deposit applied* and *Net
due*. A release is inside the retainage figure, never a row of its own to add.
The line table's **Totals** row prints once, at the end of the table, not as a
footer repeated on every page, and its retainage column is before releases; a
note under the table gives the whole sentence: "The retainage column is before
releases: its total of $38,795.58, less retainage released to date of
$38,795.58, equals retainage to date of $0.00."

**Rules the application keeps.** Completed to date and stored never go below
zero (or above zero on a deduct line): a negative this-period or stored that
would do that is refused (422). An application that only moves stored material
into the work has nothing to bill: it is approved with no invoice (current due
zero); an application with nothing at all to bill, hold, release, apply or move
is still refused. Retainage to date can't go below zero either: a release is
refused (422) when it is more than the retainage held at the application's own
percent on the work completed, so dropping the percent to 0 and releasing on
the same application is refused (release at the held percent, or bill the
release on its own application). A **credit** (a negative line) also takes its
share of retainage back, so billing one **after** the final release is refused
(422, "Retainage to date would be −350.00 …: bill the credit before the final
retainage release, or release less retainage now and the rest after the
credit"): bill the credit first, then release what is held. We refuse rather
than let retainage go below zero because the release has already paid out the
credit's share. A lien waiver is for one payment and one
job, and its amount is the part of the payment applied to that job's invoices. At approval the application keeps the contract sums as they
stood (original, and to date with change orders), and its PDF prints those, not
today's job. The PDF's **net change by change orders** is the owner change
orders that take effect on or before the application's **date**, plus any whose
line the application carries (see *A change order takes effect on the day you
say*; the application's JSON carries the same three rows as `contract_sums`),
and the original plus the net change
is the contract sum to date; an application approved before 2.31.8 with no
original kept reads it as the sum less the change orders approved by its
approval, so it prints right with no repair (#723). An invoice made from a payment application can't be edited on the
Invoices screen except for its notes (409); change the application instead.

**Owner retainage on the cash basis.** The mirror of the sub side. A payment
on an application's invoice recognizes income only on the cash received: its
sales tax whole (retainage is held on the work, before tax) and its income
lines less the retainage held, so held retainage is not income and 1150 reads
zero on the cash Balance Sheet while it waits. Paying a release application
recognizes the held income on the earlier applications' own income lines
(account, job and cost code), never on 4950 Unapplied Cash Payment Income; an
application that releases and bills new work does both. Once everything is
paid, cash income and tax equal the accrual figures. A dropped retainage
percent (retainage this period below zero) is a release of what was held.

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
On the **cash basis** a deposit is income when it is applied, not when it is
received: until then it is money held for the owner, a liability on 2210 on
the cash Balance Sheet as on the accrual one (QuickBooks' cash basis reads a
customer deposit the same way). Applying it is a payment on the
application's invoice, so it recognizes that invoice's income and tax like
any payment. An accountant who reports a deposit as income on receipt
adjusts it by journal entry.

**The sub side.** A commitment bills a period at a time exactly as buyout
described (the **Sub pay app** button opens the partial-convert dialog with
previous / this period / remaining). Its held retainage is released with
**Release retainage** (`POST /api/purchase-orders/{id}/release-retainage`),
which writes a bill for the vendor carrying no expense account: DR **Retainage
Payable** (2050) / CR Accounts Payable for the amount, payable through Pay Bills
like any bill. A release over what is held is refused (422), and voiding the
release bill gives the retainage back to the commitment. A release bill's lines,
tax, job, class or number can't be edited (409, "A retainage release bill can't
be re-lined; void it and release again."), since a re-post would put its line on an
expense account instead of 2050; its memo, due date and terms still save. Retainage is held and
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
(422; a deduct line billed down to -1,000 is pinned the same way, and cannot be
cut back above -1,000), and a billed change order cannot be voided (409 `co_billed`). New lines
can still be added.

**The job reports read these figures.** The WIP schedule, cost-to-complete, the
change-order log, the buyout report and job cost by vendor (#556, see *Job
reports* below) are built on the contract, the cost tree, the commitments, the
change orders and the pay applications above.

## How construction maps to the books

Every construction document either posts one journal entry or posts none and
moves only the job's figures. The table is the accrual entry, account by
account (cash-basis reports re-read the same entries; see *Retainage on the
cash basis* above). "Job" means the line carries the job; "section" means it
carries the cost code.

| Document | Debit | Credit | Notes |
| --- | --- | --- | --- |
| **Estimate accepted** | — | — | No entry. Seeds and locks the job budget (cost per section and cost type, revenue spread pro rata so it foots to the bid). |
| **Contract built / schedule of values saved** | — | — | No entry. Each SOV line carries its section, its revenue account (4050 Contract Revenue unless changed) and its Tax box. |
| **Owner deposit received** | the bank account chosen | 2210 Customer Deposits (job) | Held until a pay application applies it. |
| **Owner change order approved** (or voided) | — | — | No entry. A contract-funded one moves the contract sum and appends an SOV line (the change order's section, that section's revenue account, else the one every line on the schedule credits, else 4050, taxed as that section is; see *Change orders*); every kind writes budget rows of its own, in effect from its approved-on date (void deletes them). |
| **Commitment issued; sub change order approved** | — | — | No entry. Committed cost on the job, by section and cost type. |
| **Pay application approved** | 1100 Accounts Receivable for the net: total − retainage held + retainage released + sales tax (job; PA 1 of one job posted 102,111.87 − 10,211.19 + 5,410.23 tax = 97,310.91); 1150 Retainage Receivable for the retainage held this period (job) | each SOV line's **revenue account** (its own, else 4050 Contract Revenue) for this period plus the change in stored (job, section); 2200 Sales Tax Payable for the rate × the billing on **taxable** SOV lines (none on a *contractor pays* job) | A deduct line or a dropped retainage percent flips its leg to the other side; the entry still balances. **Stored materials** are revenue the period they are billed (no stored-material asset; the WIP entry adjusts any over-billing) and taxed with their line on a *charge the owner* job. |
| … with a deposit applied | 2210 Customer Deposits | 1100 Accounts Receivable | A payment from the deposit, applied to the application's invoice. |
| … with retainage released | 1100 Accounts Receivable (job) | 1150 Retainage Receivable (job) | Inside the application's own entry; a release-only application bills nothing else. |
| **Pay application voided** | — | — | Reverses the invoice's entry and any deposit payment, on their original dates. |
| **Sub or supplier bill** (Sub pay app / To Bill, or entered by hand) | each line's cost account (job, section, cost type), with its share of the bill's tax: the line's account, else the item's expense account, else (construction) the code's account, else the cost type's default — 5100 material, 5200 labor, 5300 subcontract, 5400 equipment, 5500 other — else the vendor's default expense account | 2000 Accounts Payable for the net; 2050 Retainage Payable for the retainage held (job) | A stock item posts to Inventory. A line nothing names is refused with a sentence. |
| … with use tax (a material line on a *contractor pays* job, no tax on the bill; or a line's Use tax box) | the line's cost account (job, section, cost type) for the rate × the line | 2220 Use Tax Payable (job) | In the bill's own journal, so a void or an edit reverses or re-posts it. |
| **Sub retainage released** (Release retainage on the commitment) | 2050 Retainage Payable (job) | 2000 Accounts Payable | A bill with no expense line, paid through Pay Bills. |
| **Bill payment** | 2000 Accounts Payable | the bank (1000 Checking unless another is chosen) | |
| **Customer payment** | the deposit-to account (Undeposited Funds unless a bank is chosen) | 1100 Accounts Receivable | Retainage still held stays on 1150 until a release bills it. |
| **Job Cost Entry** | the line's cost account (the line's, else the code's, else the cost type's default) (job, section, cost type) | the line's offset account, else the equipment's recovery account, else the cost type's offset account | |
| **Sales tax paid** | 2200 Sales Tax Payable | the bank | |
| **Material slip posted** (field ticket) | the line's cost account (material type; job, section) at the item's average cost | the item's inventory asset account | A Job Cost Entry; a return slip is the reverse, and a void reverses at the booked cost. T&M and delivery tickets post nothing. |
| **Use tax paid** (a check or bill to the tax agency) | 2220 Use Tax Payable | the bank (or A/P) | The Sales Tax report's Use tax section shows it as paid. |
| **WIP adjusting entry posted** (Post on the WIP schedule) | 1360 Costs and Estimated Earnings in Excess of Billings for an under-billing (job); 4060 Contract Revenue – WIP Adjustment when over-billed (job); 5600 Loss on Uncompleted Contracts for a loss provision (job) | 2060 Billings in Excess of Costs and Estimated Earnings for an over-billing (job); 4060 when under-billed (job); 2070 Provision for Loss on Uncompleted Contracts (job) | Dated the as-of date, reversed the next day. Each line carries its job, so P&L by Job and Job Profitability show each job's adjustment and its gross profit there ties to the WIP's gross profit to date (less any loss provision). The WIP's own inputs, the cost tree, Budget vs Actual, Cost to Complete and Job Cost by Vendor leave it out. |
| **Construction in Progress** | — | — | 1350 takes nothing on its own: job cost posts to 5100–5500 and the WIP entry carries over- and under-billing. A company that capitalises cost until billing books it by journal entry. |

Because each pay-application line credits its own revenue account with its
section, revenue by section appears beside cost by section in the job cost
tree and P&L by Job. A pay application's revenue has no cost type, so the
cost tree compares revenue **per section, across cost types**: its type
branches carry cost only, and its *Revenue by section* table (and
`revenue_sections` in the JSON, a grid of its own in the PDF and CSV) gives
each section's budgeted cost, actual cost, budgeted revenue and actual
revenue together, a sub-code rolling up to its section. A line or budget
with no section reads under Unassigned, except the **markups**: the budget
spreads them over the sections by markup base (an accepted deduct alternate
takes its negative share), so what the markup lines with no section bill is
spread the same way: each section takes its budgeted markup in step with its
own billing (budgeted markup × its actual revenue ÷ its budgeted revenue
before markup), scaled so the sections take exactly what was billed. A
section billed in full then reads its budgeted revenue, markups and all. Because each
cost type has a default account, the P&L's cost lines (5100–5500) and the job
reports' cost types agree for a costed line that names no account of its own.

## Field tickets

A **field ticket** is the paper that comes back from the job site: what was
done or what arrived, on what day, signed by somebody. It lives on the job, on
the job's **Field tickets** tab (construction mode only, after Pay apps), and
comes in three kinds:

| Kind | Number | Signed by | What it leads to |
|---|---|---|---|
| **T&M ticket** | `<job>-TM-001` | the owner's rep (or the GC) | a change order, or an invoice |
| **Material slip** | `<job>-MS-001` | not signed | a Job Cost Entry: stock onto the job |
| **Delivery ticket** | `<job>-DT-001` | the receiver | received quantity on a PO's lines |

Numbers count per job and kind, like change orders; a job with no job number
uses `J<id>`. Each ticket has a date, a default cost code for its lines, a
description, who submitted it, and lines of labor, equipment, material or
other with a quantity, unit and cost rate (a T&M ticket also has a bill rate;
its quantities are not negative, since a return slip is the only negative
kind).
A blank cost rate takes the employee's loaded rate (the same rate time
entries post at), the equipment's hourly rate, or the item's average cost
(its cost when it has no average yet); a blank bill rate is the cost rate
plus the job's **Markup on change orders**. A line's cost type follows its
kind (labor, equipment, material), else its cost code's.

**Figures are held to what the books store.** A line's quantity, cost rate
and bill rate (a filled-in bill rate too) are each less than 100,000,000,000,
and its amount (quantity × either rate) and the ticket's totals at most
9,999,999,999,999.99, the most a money column holds: anything past that is
refused (422, a sentence naming the line) before the ticket is saved. A
change order's lines are held the same way (quantity and unit cost, each
line's cost and sell, the change order's totals), on create, edit (a markup
change too), promote and make-change-order. A ticket stored past these limits
before they existed still reads, on the tab, the list, and the Field Ticket
Log and its CSV.

**Status.** A ticket is `draft` while it is written, `signed` once someone
signs it, `closed` once it has had its effect (billed, its change order
approved, posted), and `void`. Only a draft is edited or deleted; a signed
ticket is what was signed, so a change means voiding it and writing another.
**Signing** in this version is the signer's typed name and the date (`POST
/api/field-tickets/{id}/sign`), with a photo of the signed paper attached to
the ticket (attachments take `field_ticket` as their record type); deleting a draft
ticket deletes its attachments too. A T&M or material line that names a
purchase order line must name one that exists on the ticket's own job. An
on-screen signature pad is a later issue. A ticket is voided while nothing it
led to is approved: a T&M ticket on a change order that still stands, or on an
invoice that is not void, is refused until that is rejected or voided. The
ticket's `can_void` says whether a void would be accepted now (it is false on
a closed T&M ticket that is still priced or billed, on a void ticket, and on a
posted slip or a delivery dated in a closed period of a company with no
closing-date password); the tab shows **Void** only then. Void takes no
fields: a body with one (`{"foo": 1}`) is a 422, as on sign and post. A delivery ticket
cannot be **created** in a closed period either (403, the closing-date sentence).

### T&M tickets

Extra work, foreman's hours by person, equipment hours, material and other,
at cost and bill rates, signed by the owner's rep. **A T&M ticket posts no
cost.** Labor reaches the job through time entries and payroll, equipment
through Job Cost Entries, material through bills; if the ticket posted cost
as well, the job would count the work twice. It is the quantity and billing
record. A sub's ticket names the sub's **commitment**.

Two ways out, from the tab (tick the signed tickets first):

- **Make change order** (`POST /api/field-tickets/make-change-order`): one or
  several signed tickets on one job become one **potential change order** to
  the owner, a line per ticket line **at cost** (quantity × cost rate, the
  line's cost type and code), marked up by the job's change-order markup. The
  ticket's bill rates are not used on this path. A sub's ticket is
  subcontract cost on the PCO, and also goes, at cost with no markup, on a
  draft **sub change order** on its commitment (one per commitment), so the
  sub's contract rises with the owner's; link it to the owner CO once the PCO
  is promoted. Each ticket links to the PCO and follows it through revision
  and promotion; it **closes when the owner change order is approved**, and
  goes back to signed if that CO is voided. A rejected PCO frees its tickets
  to price again.
- **Bill it** (`POST /api/field-tickets/bill`): for a job with **no schedule
  of values** (a T&M or cost-plus job billed by invoice), an invoice to the
  job's customer with a line per ticket line at its **bill rate**, tagged to
  the job and the line's code. The tickets close; voiding the invoice opens
  them again. A job with a schedule of values bills on pay applications, so
  it is refused there: make a change order instead.

Job Cost Entry's **Bill?** box (`is_billable`) is left as it is: nothing
reads it, and Bill it does not either. Its lines are at cost and carry no bill
rate, and work billed from both would be billed twice. In construction mode,
extra work to bill goes on a T&M ticket.

### Material slips

Material taken from the company's own stock onto a job. Each line is an
inventory item and a quantity; a **negative quantity is a return** to stock.
**Post** (`POST /api/field-tickets/{id}/post`) writes one Job Cost Entry, source `material_slip`: DR job cost (cost
type material, the line's cost code; the account is the code's, else the
material cost type's default account) / CR the item's inventory asset account
(the item's own, else 1300), at the item's **average cost**, and the item's
on-hand quantity goes down through the inventory service (a return: the same
entry the other way, the stock back at the average). An item with no average
cost yet is refused (the slip would post nothing). The slip posts **on its own
date**, with the closing date checked on that date, and its stock movements
carry that date too (so does the void's); a `date` in the request that is not
the slip's date is a 422, since a slip dated 9/20 posted on 10/02 would put the
cost in a different month than the paper says. To post on another day, change
the slip's date first. A slip that takes an item **past the stock on hand**
follows the company's *Refuse a sale of more than is on hand* setting as an
invoice does: when it is on, the post is refused (422, naming each item, how
many are on hand and how many short, and nothing is written); when it is off,
the slip posts and the response's `warnings` say it ("Rebar #4: 100 on hand;
this slip takes it to -50."). A return never counts as short. The slip closes. **Voiding**
it reverses the entry and puts each stock movement back **at the cost it was
booked at**, not today's average. The entry shows on Job Cost Entries; its
void there is refused (void the slip, which puts the stock back too).

### Delivery tickets

Received against an **open purchase order** on the job (sent, partly received
or received; not a draft or a fully billed one): a line per PO line with the
quantity received, and the supplier's own ticket number. **Saving** a delivery
ticket adds its quantities to each PO line's received quantity and moves the
order to `partial` (something received) or `received` (every line in full);
an edit moves it by the change, and deleting or voiding the ticket takes the
quantity back. Editing the order moves it too: a received order whose line is
raised (or a line added) goes back to `partial`, and one cut to what arrived
is `received`. Nothing posts: received-not-billed accruals are the
accountant's. When the supplier's bill is entered from the PO, the
construction **To Bill** dialog of an order being received this way shows
ordered and received per line beside what is billed, and **warns** (never
refuses) when a line would bill more than the value received (received
quantity × the line's rate); the convert's response carries the same
`warnings`. **Commitment Status** gains a **Received** column.

**Over-receipt.** A delivery that would take a PO line past its ordered
quantity (received so far plus this ticket's) is refused (409, code
`over_receipt`) with a sentence naming both: "Conduit: 100 ordered and 60
already received, so 50 more would make 110 received." The ticket form asks
"Record the over-delivery anyway?" and sends the ticket again with
`over_receipt: true` (create and edit both take it). The ticket then saves,
the line is marked (`over_receipt` on the ticket line, an **over** tag beside
its received quantity), and the ticket carries a `warnings` sentence on this
and every later read until it is voided. A delivery that would take a PO line's
received quantity past 99,999,999.9999 (counting what is already received) is
refused even then (the column's limit). An edit that keeps an
over-delivered line unchanged does not ask again.

**Deliveries and the purchase order's lines.** A PO line that a delivery
ticket (not void) received on cannot be removed by `PUT /api/purchase-orders/
{id}`, and an order with a delivery received on it cannot be set back to
`draft` (409, code `commitment_received`, naming the tickets; void them
first). A line cannot be cut below the quantity received: 409, code
`commitment_received`, naming the ticket ("Conduit has 100 received on delivery
ticket 2601-DT-001, so it cannot be cut to 20. Void the ticket, or keep at
least 100."). Void the delivery ticket first. A line can still grow.
Voiding a ticket is construction mode's (409 "Field tickets are part of
construction mode…" outside it), so once a company leaves construction mode a
delivery ticket can no longer be voided and the quantity it received stays on
the purchase order line. Switch back to Construction to void it.

**Closed periods.** A delivery ticket changes received quantity as of its
date, so editing it, deleting it or voiding it with that date on or
before the closing date is refused like any entry there (403; the closing-date
password override applies, and an edit that moves the date is checked on both
dates).

### Field ticket log

The **Field Ticket Log** report (`field-ticket-log`, JSON, PDF and CSV, on
the Report Center in construction mode): per job, the T&M tickets dated by
`as_of` that are still money on the table, **unsigned** ones and **signed
ones not yet billed**, each at its bill rates, with the change order a signed
ticket is priced on while that is not yet approved. Totals per job and grand;
a job with no open ticket is left out. Each ticket is read **as of the date**:
signed by then (else still unsigned), not billed on an invoice dated by then,
not voided by then. A ticket a change order closed has no date for that on
file, so it is not listed. The report keeps a job closed after the date, and
a `status` filter matches the job's status on that date.

## Job reports

Ten reports sit on the Report Center for a construction company, after the
three job reports that always existed (thirteen cards in all; a Business or
Nonprofit company never sees the ten), each a plain grid with a JSON route, a PDF and a CSV
like P&L by Job. Each takes an `as_of` date (or
`start_date`/`end_date` where a period applies) and optional `job_id`,
`customer_id`, `status` and `include_closed`; by default closed and not-awarded
jobs are left out. As of a date, a job closed after it was still open and stays
on Commitment Status, Buyout, Job Cost by Vendor (as of its end date) and the
Field Ticket Log, as on the WIP schedule; a `status` filter matches the status
the job had on the date, the one its row shows (on the Retainage report too).

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
do reads under 100%; with no forecast entered the default is the section's
budget − actual, floored at zero (cost types net within a section). An accepted
**deduct alternate** whose section has nothing else to deduct from is a
negative budget: it takes its amount off the cost still to come elsewhere, so
it adds no estimated cost and reads no variance (see the forecast below). A job
with no budget and no forecast has nothing to measure against, so it earns
nothing — and a budget written only by change orders does not count as one (a
hand-typed SOV with one owner change order is still unmeasured): it is listed
with a warning ("no budget or forecast; nothing earned"); its contract, cost to date, billed to date and
retainage count in the totals, but its earned revenue, over/under billing and
loss provision are left out, and so is it from the adjusting entry, until
its cost to date reaches the contract (then it reads 100% complete, and the loss
is the true loss, with no extra provision). Every figure reads **as of the date**: cost to date and
posted revenue to that day, billing and retainage from the applications approved
by then, the revised contract with the change orders that stood approved on
the day (one voided since still counts; one approved later does not: the
Change Order Log's rule), and the
estimated total cost from the budget with the same change orders (so the
schedule reproduces the books on an earlier close, and a month already posted
reads the same entry after a later change order). Billed to date is the sum of
the approved pay applications' completed-to-date when the job has one by the date
(else the posted revenue carrying the job — the `billed_basis` column says which).
A job whose **contract date** is after the as-of date had no contract then and
is not on that schedule (nor in its entry). Each row's `status` is the one the
job had on the day: a job closed after it reads in progress.

**Gross profit to date** is earned revenue less cost to date; **backlog** is the
revised contract less earned revenue (the work still to earn); **estimated gross
profit** is the revised contract less the estimated total cost. All three are in
the JSON, the PDF, the CSV and on screen. The screen opens the schedule in the
wide dialog, since its columns don't fit a narrow one.

**A closed job is finished.** A job whose status is Closed reads 100% complete
with no cost to complete, whatever its budget: earned is the revised contract,
and any difference from billing is over or under billing. Closed jobs are left
out unless *include closed* (or the status filter) asks for them. Closing is
read **as of the date**: the job keeps the day it went to Closed (cleared if it
reopens), so a job closed in November is an open job, measured as any other,
in September's schedule and its adjusting entry. A job already closed when
this was added takes the day its record was last changed (the latest it can
have closed); one with no date on file counts as closed on every date.

**Warnings about cost to complete.** Two cases are named in `warnings` (and under
the table, in the PDF note and the CSV):

- *A budget that never burns down.* On a job billed 90% or more of its revised
  contract, a section with a budget, no cost posted and no forecast entered is
  named with its budget and the cost to complete it adds. The usual one is
  general conditions — accepting an estimate budgets the GC markup on its own
  code, and a small GC's costs never post there. **To clear it, enter 0** for
  that section in the Cost-to-Complete Forecast (or the amount truly still to
  spend).
- *A finished job still showing cost to come.* A job billed in full (billed to
  date at or over the revised contract), its retainage collected and every
  commitment closed, that still has cost to complete. Close the job (it then
  reads 100% complete) or enter 0 in the forecast.

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
| 1360 Costs and Estimated Earnings in Excess of Billings (under-billing, asset) | under | |
| 2060 Billings in Excess of Costs and Estimated Earnings (over-billing, liability) | | over |
| 4060 Contract Revenue – WIP Adjustment | over − under when over is larger | under − over when under is larger |
| 5600 Loss on Uncompleted Contracts (expense) | provision | |
| 2070 Provision for Loss on Uncompleted Contracts (liability) | | provision |

The entry balances. Each line names the account by its number in the chart (an
account not yet created shows the number setup would give it; a chart that
already owns that number shows the name alone). These are the balances to carry
**as of the date**.

**Post** (on screen under the entry, or `POST /api/reports/wip-schedule/post?as_of=`)
books it: an adjusting journal dated the as-of date, per job (each line carries
its job and its memo names it), with an automatic **reversal dated the next
day**, so each month's entry carries that month's balances and the next starts
from zero. Only an accountant's or an administrator's sign-in may post (a
bookkeeper's is refused, read-only sees no button), and only in construction
mode (a Business or Nonprofit company is refused: "The WIP adjusting entry is
part of construction mode…"). It posts the whole schedule as it reads with no
filter, and creates the five WIP accounts if the chart lacks them. The closing
date applies, and a date with no next day (9999-12-31) is refused with a
sentence. Because the lines carry the job, **P&L by Job and Job Profitability
show each job's WIP adjustment** (no "No job" row for it), and a job's gross
profit there for the period ending on the date is the schedule's gross profit
to date (less a loss provision, which is an expense). Every figure the schedule
is computed from leaves the WIP journals out — cost to date, posted revenue and
the billed basis, cost to complete, the cost tree's actuals, the cost-plus fee —
as do Budget vs Actual, Cost to Complete, Job Cost by Vendor and a cost
allocation by revenue or cost, so the schedule reads the same after posting as
before. The schedule says when an entry is posted for the date
(`adjusting_entry.posted` and `posted_entry`: the journal's id, date, who posted
it and the reversal's id and date). Once posted, the entry the schedule shows
(on screen, in the JSON, PDF and CSV) is **the posted one**, line for line;
the schedule's own figures ride under `adjusting_entry.computed`. When they no
longer match (something dated on or before the day changed after the post: a
change order approved back-dated, a late bill), `posted_differs` is true and a
sentence in `warnings` (`posted_warning`), and on screen beside Void, gives
the posted and the current over-billings, under-billings and loss provision
and says to void the posted entry and post again.

**Posting the same date again is refused** (409) while an entry for it stands,
two posts sent at the same moment included: Post first writes a row keyed on the
date (`wip_entry_locks`), so the second meets the database's unique key, and
Void deletes it.
To redo it, **Void posted entry** on the WIP schedule (or
`POST /api/reports/wip-schedule/void?as_of=`): it voids the entry and its
reversal together, each by a mirror image on its own date, and the date can
then be posted again. The journal's own Void refuses either half, since voiding
one alone would leave the other on the books. An accountant voids only the
entries it posted. The report itself still posts nothing on its own: running,
printing or exporting it writes nothing.

**Cost-to-complete forecast** (`cost-to-complete-forecast`). A per-job table of
section × cost type: budget, actual (to the date), the default cost to complete,
the amount you entered, the cost to complete that counts, and the projected
final. A sub-code rolls up to its section; budget and cost with no code share
one Unassigned cell. **Cost types net within a section:** the section's default
is its budget less its actual, floored at zero, so a trade bid as subcontract
and bought as material does not keep its whole subcontract budget still to
spend. The cost-type split is for display: the section's default is shown
against the cells that still have budget left, in proportion to what each has
left (the last cell takes the cent of rounding), so the cells foot to the
section. A section whose budget is below zero (an accepted deduct alternate
with nothing in its section to deduct from) shows that budget as a **negative
cost to complete**, which comes off the cost still to come in the other cells;
cost posted there is cost beyond the plan, so it does not deepen the deduct.
The job's cost to complete never goes below zero: once the other cells have
nothing left to come, the deduct is scaled back (it cannot un-spend cost
already posted). Budget vs Actual, the cost tree and Cost to Complete treat a
negative budget the same way: it reads no variance until cost posts against it,
and it comes off the budget not yet committed. An amount you enter replaces its
own cell's share. A closed job shows
no cost to complete. Type an amount in the Report
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
not a draft, dated by `as_of`) per job: committed (pre-tax value), received (the
quantities on its delivery tickets dated by then, not void, at the PO line
rates; see *Field tickets*), billed to date
(bills dated by then; a retainage-release bill pays money already billed and is
not counted), retainage held (held less released) and remaining (committed less
billed, open commitments only), with totals per job and grand. Committed leaves
out lines added by a sub change order approved after the date, and keeps the
lines of one that stood on the date and was voided since. A closed
commitment counts closed on every date, unless a bill against it is dated after
the date, or a sub change order voided since still had lines on it: then it was
still open on it, and remaining is committed less billed by
then. PDF and CSV.

**Cost to complete** (`cost-to-complete`). Per job, per cost code (division ›
section), with budget revised, committed (remaining), actual, cost to complete
(the remaining budget plus open commitments), projected final (actual + cost to
complete), variance to budget and percent complete by cost, with totals per job
and grand totals. Budgets take the change orders in effect by `as_of` and
committed is Commitment Status's remaining that day; a deduct alternate's code
carries its negative budget as cost to complete, so it reads no variance. The totals **foot the rows** — cost to complete, projected and
variance are the sum of the clamped per-code figures (Σ max(…) ≠ max(Σ …) once a
code runs over), budget/committed/actual the raw sums. The JSON keeps the
cost-code tree (`children`); the PDF and CSV flatten it with the code path.

**Change order log** (`change-order-log`). Every change order per job — number,
kind, status, title, date, funding, cost, markup, sell, approved and voided
dates, source PCO and (for a sub CO) its commitment — with the running approved
total (the change orders approved by `as_of`) and the contract it builds
(original + approved changes = revised), the pending total (as of today) and
counts by status **as of the date**. **A change order counts as of a day in the
state it had that day**: approved on it when it took effect by then
(`approved_on` on or before) and was not voided until after; one approved or
voided after the day reads pending. The status column, the counts, the running
total and the contract all use that rule (and so does the WIP schedule, its
budget included), and a change order dated after the day is not on that log.
The JSON rows carry `approved_on`, `voided_on` and `status_today`; the CSV puts
every change order under one header with those columns, the source PCO and the
commitment, then each job's contract (original, approved changes, revised)
under a header of its own.

**Buyout report** (`buyout`). The job buyout across jobs: each trade package's
bid cost against what has been bought, with the variance, committed-remaining and
billed-to-date and the commitments behind each, the Unassigned row kept, totals
per job and grand. As of `as_of`, only commitments dated by then count and
billed-to-date is the non-void bills dated by then, leaving out a retainage
release, which pays money already billed (as Commitment Status does), so the two
reports agree on the same date.

**Job cost by vendor** (`job-cost-by-vendor`). Posted job cost grouped job →
vendor, with each vendor's bills, paid to date (the bill payments allocated to
that vendor's bills on the job), retainage held outstanding (held − released on
those bills) and open A/P. Each posted entry's vendor is read from the document
behind it — the bill (and its void), the bill payment, the vendor credit, or an
expense's payee vendor — not from a stamp on the entry, so books posted before
this report read right with no backfill; a No vendor bucket holds journals and
job cost entries. The cost is for the period; the bill figures are job-to-date
**as of the report's end date** (bills and payments dated by then; a void or
draft bill is not counted) and in home currency at each bill's booked rate, so
a vendor's open A/P ties to A/P Aging and Commitment Status for the same job and
date. They are **prorated by the job's share of each bill's line amounts** — a
header-only bill counts whole, a bill split across jobs splits its payment,
retainage, A/P and count — so a job's bills can read fractional. Open A/P is
**net of the vendor's credits on the job not yet applied to a bill** (prorated
the same way), as A/P Aging nets them: a $500 credit for Ready Mix on the job
reads open A/P −500.00, so cost = paid + retainage held + open A/P for the job
to date, and the vendor's open A/P equals its A/P Aging total. Material slips
post job cost entries, so their cost sits under No vendor (stock is the
company's own); that is expected.

**Retainage** (`retainage`). Retainage in one place, per job, as of a date.
*Owed to me* (account 1150) is what the owner holds back: per job, **held**
(the retainage on the job's pay-application invoices dated by then),
**released** (billed back) and **outstanding**. *I owe* (account 2050) is what
you hold from subs: one row per commitment and one per hand-entered bill with
retainage, giving the vendor, the held and released retainage on its bills
(a release bill is the commitment's release; a bill split across jobs splits
its retainage by the money on each) and what is outstanding. Each side has
job totals and a grand total, and **the grand totals are the Balance Sheet's
Retainage Receivable (1150) and Retainage Payable (2050) for the same date**.
That holds whatever the documents say: a ledger amount no pay application or
bill explains (a journal entry posted straight to 1150 or 2050, a void
reversed on a later day, a bill with no job) is shown as its own row,
*Journal entries and other*, so the totals still tie and the difference is
in view. Closed jobs are included, since retainage outlives the job; a job
with no retainage held, released or on the books is left out. `job_id`,
`customer_id` and `status` narrow it (then the jobless row is left out, and
`filtered` is true); `status` matches the status the job had on the date, so
a job closed after it is in progress there. The report reads the accrual ledger. PDF and CSV carry
the two grids, each ending in its total.

**Sub Compliance** (`compliance`). The subs a GC should chase, one row per
problem, as of a date (the W-9 and lien-waiver flags and a vendor's active
state have no history on file, so they read as they stand today; the
certificate date and the payment dates are read as of the date): a certificate of insurance **expired**, **expiring
within 30 days** (the thirtieth day counts) or with **no date on file**; **no
W-9 on file**; and a **paid bill whose lien waiver is missing or still
pending** (the bill, its job, the day it was first paid and its total; a bill
not yet paid, or paid after the date, is not listed, and *not required*,
*conditional* and *unconditional* are all fine). Only active vendors marked
Sub are read. Nothing here blocks a payment. PDF and CSV like the others.

The existing **Job Profitability** and **Job Budget vs Actual** reports gained
PDF and CSV routes, so every contractor report exports the same way. In
construction mode both read each job's **contract as of their end date** (the
WIP schedule's revised contract on that day: a change order approved later is
out, one voided since is back in); with no end date, today's. Job
Profitability's PDF, print page and CSV say *(cash basis)* after the dates on
a cash run, as the other cash-basis reports do.

## The dashboard

A construction company's Dashboard opens on the money a GC watches. The
default layout leads with Total Receivables, Total Payables and five tiles,
then the usual cards; a layout you saved before keeps your cards, and the
tiles are in **Customize → Add a card**. A Business or Nonprofit dashboard is
exactly as it was, and is never offered these cards.

| Tile | Figure | Opens |
| --- | --- | --- |
| **Retainage Owed to Me** | Retainage Receivable (1150) today | the Retainage report |
| **Retainage I Owe** | Retainage Payable (2050) today | the Retainage report |
| **Over / Under Billed** | billed ahead of the work, and work done not yet billed, from the WIP schedule as of today | the WIP schedule |
| **Backlog** | revised contract less earned revenue on open jobs (the WIP schedule's backlog) | the WIP schedule |
| **Committed, Not Yet Billed** | what open subcontracts and POs still have to bill, from Commitment Status today | Commitment Status |

Hover a tile to see the jobs that make up its figure; click it for the
report. The retainage tiles are the report's grand totals, so they equal the
Balance Sheet lines. **Total Receivables and Total Payables leave retainage
out** (it sits on 1150 and 2050), and in construction mode their titles say
**(excl. retainage)**. Closed jobs are left out of the WIP and commitment
tiles, as they are from those reports.

## The job Overview

The Overview tab of a construction job:

- **Retainage owed to me** and **Retainage I owe** tiles, the job's own
  figures from the Retainage report (click one to open it).
- **Projected** is the projected final cost: **actual + committed remaining +
  budget not yet committed**. The last is figured **per cost code, floored at
  zero**: a code's revised budget (all its cost types together) less the cost
  posted to it and the commitment still to bill on it, never below zero, then
  summed. So a trade that has not been bought is cost still to come, not a
  saving, and an overrun on one code does not eat another's unspent budget. A
  trade bid as subcontract and bought as material nets within its code, as it
  does in the cost-to-complete forecast. Budget with no cost code shares one
  cell with the cost and commitments that have none. The figure is job to
  date (one with an end date reads the change orders and commitments as of
  it) and a job closed by then has none. Projected final cost is the whole
  job's, so a cost tree or Budget vs Actual dated from a start date counts it
  too, and the cost posted before the start (`cost_before_period`, a row of
  its own beside Budget not yet committed): the Overview, Job Budget vs Actual
  (its PDF and CSV) and the cost tree read the same Projected and Variance. A code with a negative budget (a deduct alternate) takes it off the
  others' budget not yet committed, never below zero. **Budget not yet committed** is its own tile, a row under the
  cost-type table and a row on Cost Detail (and in its PDF and CSV), so the
  Projected column foots to the total; **Variance** is the budget less the
  new Projected. The Jobs list, Job Budget vs Actual and the cost-tree totals
  read the same figure; a Business or Nonprofit job's Projected stays actual
  + committed.
- **Revenue billed (gross of retainage)** is the revenue posted, including
  what the owner holds back. **Billed margin** is revenue billed less cost
  over revenue billed, not the job margin (the WIP schedule's estimated and
  projected gross profit are those).

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

In a construction company the **vendor list** shows a **Sub** badge and the
**COI expires** date beside each sub: red once expired (or when there is no
date), amber within 30 days. The **Sub Compliance** report (see *Job reports*)
lists the same subs, the missing W-9s and the paid bills without a lien
waiver.

## The reports

In construction mode the Report Center's first thirteen cards are the three job
reports that always existed — **Job Profitability**, **Job Budget vs Actual**
and **P&L by Job** — then the ten construction reports: **WIP Schedule**,
**Cost to Complete**, **Change Order Log**, **Buyout Report**, **Job Cost by
Vendor**, **Cost-to-Complete Forecast**, **Commitment Status**, **Field Ticket
Log** (see *Field tickets*), **Retainage** and **Sub Compliance** (see *Job
reports* above). The ten show in construction mode only, except Job Cost by Vendor (below).
Every other report stays where it is.

A Business or Nonprofit company that opens **WIP Schedule**, **Cost to
Complete**, **Cost-to-Complete Forecast**, **Commitment Status**, **Change Order
Log**, **Buyout Report**, **Field Ticket Log**, **Retainage** or **Sub
Compliance** by address (`#/reports/retainage`, a
saved report, a bookmark) is sent to the Report Center with a sentence saying
they are construction-mode reports, and their APIs (JSON, PDF, CSV, print)
answer 409 outside construction mode, as field tickets do. (The WIP post and void
routes have their own rule.) **Job Cost by Vendor** stays open to every company.

**A filter the route does not read is refused, not dropped.** `GET
/api/field-tickets`, `/api/field-tickets/purchase-orders`, `/api/estimates`,
`/api/change-orders`, `/api/pay-applications`, `/api/jobs/{id}/cost-tree`
(and its PDF and CSV), `/api/jobs/budget-vs-actual`, the WIP Schedule,
Retainage, Sub Compliance, Field Ticket Log, Cost to Complete,
Cost-to-Complete Forecast, Commitment Status, Change Order Log, Buyout, Job
Cost by Vendor, P&L by Job, Job Profitability and Job Budget vs Actual
reports (and their PDF, CSV and print), the A/R and A/P Agings and their CSV
(in every mode: `customer_id` or `vendor_id` there is refused too, since the
aging is always the whole company), and `POST
/reports/wip-schedule/post` and `/void` answer 422 with a sentence naming the
accepted parameters when a request carries any other (`jobid`, `asof`,
`window_days`), so a misspelling cannot return the whole list or post the whole
schedule. Retainage also refuses a `status` that is not a job status, and the
estimate list's `status` takes `lost` for the estimates marked lost (stored as
`rejected`; `rejected` still works).

## What this mode does not do yet

- **Field tickets, version 1:** no mobile or foreman app, no on-screen
  signature pad (a typed name and a photo), no daily reports or timesheets
  on the ticket, and no received-not-billed accrual from delivery tickets.

- **No first-run prompt.** `company_type` still defaults to Business for a
  new company file; Construction is chosen in Settings afterward, as
  Nonprofit is. A "what kind of company is this?" step at creation is a
  later issue.
- **Customer:Job** still prints with a space; the no-space form is deferred.
- **No full section catalog** (the numbered catalog isn't ours to publish);
  the division titles and a set of common sections ship, and you import the
  rest.

**Amounts that do not fit.** A bill, purchase order, estimate or invoice line
whose quantity × rate (or amount) is past 9,999,999,999,999.99, or whose lines
sum past it, is refused with a 422 sentence before anything is saved. So is an
estimate, purchase order, invoice or bill whose subtotal, tax or total (after
markups and tax) would not fit. Approving an
owner change order is refused the same way when the contract plus its sell
total would not fit.

**Inputs that are refused, not ignored.** A bill edit that sends the same line
`id` twice, and a new bill whose line carries an `id`, are 422s. Marking an
estimate lost and the WIP schedule's Post and Void take no body field beyond
the one they document; any other is a 422 naming it. The Field Ticket Log
refuses a `status` that is not a job status, with the Retainage report's
sentence. Listing field tickets and returning an allowance outside construction
mode are 409s, like the writes. A ticket line naming a purchase order line on a
purchase order with no job is told the line is on no job. A query parameter a
list or report does not read is a 422 that names it ("a is not a filter
here", "a and b are not filters here", "a, b and c are not filters here").

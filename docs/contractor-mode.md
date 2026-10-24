# Contractor mode

Settings → Company Type → **Construction** sets a company up the way a
contractor keeps its books, the same way **Nonprofit** does for a ministry:
one switch, no change to any data, reversible at any time. It is the
counterpart to QuickBooks Desktop's Contractor Edition. This is Phase 1 — a
setting, the accounts, the cost codes and the subcontractor vendor type. No
new accounting posts yet.

## The switch

Picking Construction in Settings saves `company_type = construction` and
calls `POST /api/construction/setup`, which:

1. creates the contractor accounts below if they are missing, and
2. loads the 26 standard CSI MasterFormat cost codes **only when the company
   has no cost codes of its own** (the same list the Cost Codes page's
   **Load Standard Codes** button loads), ensuring the labor / material /
   subcontract / equipment / other cost types first.

Setup is idempotent — safe to run again. It never renumbers or renames an
account you already have, never touches an existing cost code (a company
that has built its own cost-code chart gets no CSI codes added), and changes
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

The standard set is the 24 CSI MasterFormat **divisions** (01 General
Requirements … 33 Utilities) plus Labor and Equipment Rental — 26 codes,
each tagged with a cost type. This is the division level most US contractors
already speak, not the full MasterFormat line-item list, which is not
shipped. Add your own codes beneath a division on the Cost Codes page.

## The words

A contractor keeps almost every business word — Customer, Invoice, Estimate,
Job all stay, as they do in QuickBooks' Contractor Edition. The one that
changes is **Subcontractor → Sub**, on screens and in prose. Nothing in the
API, the database or the export formats changes name.

Customer:Job in QuickBooks prints with no space around the colon; SlowBooks
prints "Customer: Job" (with a space) everywhere today, and that is left
unchanged in Phase 1 — see *What this mode does not do yet*.

## The subcontractor vendor type

A vendor can be marked a **Sub** (the UI word; `vendor_type` is
`"sub"`, its only value for now — a Subcontractor). A sub carries:

- **License #** — the trade license number;
- **Insurance Carrier**, **Policy #**, **Insurance Expires** — the
  certificate of insurance a general contractor collects before a sub works.

These fields are blank on an ordinary vendor and reveal only when the vendor
is marked a sub; clearing the mark clears them. 1099 is off by default for
every new vendor — a lumber yard or the power company is not reportable.
**Marking a vendor a Sub turns on 1099-NEC** (a sub's labor is reportable),
which you can still change. W-9 on file is a fact you record, not a default,
so it stays No.

## The reports

The Report Center leads with the contractor reports that already exist:
**Job Profitability**, **Job Budget vs Actual** and **P&L by Job**. Committed
Costs and Job Cost by Vendor are not standalone reports yet, so they do not
appear. Every other report stays where it is.

## What this mode does not do yet

- **No first-run prompt.** `company_type` still defaults to Business for a
  new company file; Construction is chosen in Settings afterward, as
  Nonprofit is. A "what kind of company is this?" step at creation is a
  later issue.
- **No new accounting.** No change orders, draws (AIA-style pay
  applications) or progress-billing screens; the accounts are there to post
  to by hand until those land.
- **No COI-expiry warning** in Pay Bills — a later issue.
- **Customer:Job** still prints with a space; the no-space form is deferred.
- **No full MasterFormat** line-item cost-code list; divisions only.

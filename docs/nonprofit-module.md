# Nonprofit mode

SlowBooks is free and source-available, and always will be. Small nonprofits — churches,
clubs, PTOs, community arts groups, small foundations — are the people it
can help most, and they pay for a "nonprofit edition" of everything else.
Nonprofit mode is one switch in Settings. Nothing in your data changes name;
the screens, the printed documents and the reports do.

## Turning it on

**Settings → Company Information → Company Type → Nonprofit.** The page
reloads in nonprofit words and the four accounts the mode needs are created
if they are missing (3300 Net Assets Without Donor Restrictions, 3400 Net
Assets With Donor Restrictions, 4400 In-Kind Contributions, 6960 Bad Debt
Expense — the numbers yield to an existing chart). Switch back any time;
nothing is lost either way. The statements never add these accounts on a
read (2.31.2): a company switched to nonprofit over the API with
`PUT /api/settings` alone, or an older file that never ran setup, shows a
net-asset line by name with no account until a release, in-kind gift or
write-off posts; run Settings → Company Type again, or
`POST /api/nonprofit/setup-accounts`, if a statement shows one.

## What changes on screen

| Business word | Nonprofit word |
|---|---|
| Customer / Customer Center | Donor / Donor Center |
| Invoice | Pledge |
| Sales Receipt | Donation |
| Income | Revenue & Support |
| Net Income | Change in Net Assets |
| Equity | Net Assets |
| Profit & Loss | Statement of Activities |
| Balance Sheet | Statement of Financial Position |
| Class | Fund |
| Job | Grant |
| A/R Aging | Pledge Aging |

The QuickBooks import screens, the tax screens and the employee pages keep
their own vocabulary on purpose. The API and the database never change
name: `/api/customers` is still `/api/customers`.

**Printed documents are literal, not vocabulary.** A donation prints as
DONATION RECEIPT, a pledge as PLEDGE, and a program fee or a hall rental —
which a nonprofit still issues — prints as INVOICE. The pledge flag on the
invoice form decides which.

## Funds

A class is a fund. **Settings → Funds → Edit** sets:

- **Restriction** — without donor restrictions, with donor restrictions
  (purpose or time), or with donor restrictions (permanent). The last two
  report together as "with donor restrictions" (ASU 2016-14); the three-way
  label is there because treasurers still think in it.
- **Default function** — program services, management & general, or
  fundraising. Every expense posted to the fund takes this function unless
  the line says otherwise.
- **Donor / grantor** and **Purpose** — printed on the fund balance report.

The untagged bucket ("Uncategorized") is always without restrictions.

## Grants

A grant is a job: it belongs to the funder (a donor), carries a budget by
cost code, a period and an award amount, and rolls costs up the same way a
contractor's job does. Tag the award and the program's bills to both the
restricted fund and the grant, and the job page shows budget, committed,
actual and variance for the grant while the fund reports show restriction.

## Recording gifts

- **A gift with no pledge** — enter a **Donation** (a sales receipt). It is
  revenue at once. If the donor got something back (the gala dinner), enter
  the fair value and a description; the receipt states the deductible
  portion.
- **A pledge** — a **Recurring Pledge** for monthly or annual giving (each
  installment is generated as a pledge), or a one-off pledge on the Create
  Pledges page. Payments against pledges are the gifts; the pledge report
  shows what was promised, invoiced, received, written off and still
  outstanding.
- **A pledge that will never be paid** — **Write Off** on the pledge row.
  It posts a credit memo to Bad Debt Expense and applies it at once. Void
  the credit memo to undo.
- **A recurring pledge that should stop** — set its **End Date**. That is
  what ends a pledge and what the pledge report counts; clearing **Active**
  only pauses it (the report still shows the full promise). A recurring
  pledge that has already generated installments can't be deleted (its
  pledges would be left orphaned) — set the End Date instead. Re-ticking
  Active after a pause resumes from today and skips the paused months rather
  than back-dating a pledge for each one; a schedule past its End Date must
  have the date moved or cleared first (409 otherwise, 2.31.1).
- **Property** — an **In-Kind Gift**: each line names what the property is
  (an asset or expense account) and the donor's estimate of its value. The
  credit is In-Kind Contributions. The acknowledgment describes the
  property and never states a value — the donor values the gift, not the
  charity (IRS Publication 1771).

### One deposit, several donors

When the bank shows one deposit of $1,500 that is really two $750 gifts,
book it as one entry with a line per donor, so the bank balance matches the
statement and each donor is still credited with their own gift:

1. **From the register** — **+ Entry**, enter the deposit's total as the
   amount, then **Split this entry…**. Give each line its income account,
   its share, and the **Donor**. The **Fund** column appears on each line
   when you use funds; a line left on "Same as header" takes the entry's
   fund. **+ New Donor** in the Donor list adds a donor without leaving the
   entry.
2. **Against the bank feed** — book the split deposit in the register as
   above and **Match** the feed's deposit row to it, as for any entry
   already in your books. The bank feed's add call (`POST
   /api/banking/transactions/{id}/add`) also takes the same `splits`, each
   with its own `customer_id` and `class_id`, for an import that books rows
   itself.
3. **A plain deposit with one donor** — the **Donor** box on the entry
   names that one gift, no split needed.

The lines must add up to the deposit. Each donor's year-end giving
statement lists their line as a gift on the deposit's date, and the donor
counts are the same everywhere the gifts are read. Voiding the deposit
takes every donor's line out. The register shows the donor's name for a
one-donor deposit and "Split (2 names)" for several, unless you typed a
payee.

To add a donor to an entry you already booked, open it in the register and
choose the donor on its line, then **Save**. This works on a reconciled
deposit too: the donor is a label, so the amounts, the date and the
reconciliation do not change.

Each donor line gets the same thank-you letter a donation does: open the
deposit in the register to edit it and use **Acknowledgment (PDF)** or
**Email** beside the donor's name.

Check images on a deposit are not stored yet.

A payment recorded with nothing to apply to is a donor credit on the
receivable until it meets a pledge; enter a gift with no pledge as a
Donation so it is revenue the day it arrives.

## Acknowledgments

Every gift gets a written acknowledgment: **Acknowledgment (PDF)** and
**Email Acknowledgment** on the donation, payment and in-kind views. The
wording is yours — **Settings → Email Templates → donation_acknowledgment**
(press **Seed Default Templates** first if the list is empty; until then the
built-in wording is used) — with `{{ irs.text }}` supplying the sentence the
IRS requires (no goods
or services were provided; or the fair value of what was, and the
deductible portion). A receipt's own payment is not a second gift; the
receipt is what gets acknowledged.

## Month end

1. **Allocate shared costs.** Rent, utilities, the office manager's wages:
   post them **Unassigned (allocate later)** (the function picker's last
   choice, or omit the function on a line in a fund with no default). A saved **allocation
   rule** (Functional Allocations page) says how they divide — by percent,
   by square feet, or by hours worked on grants — across functions and/or
   funds. **Run** the rule for the month: it moves everything still
   unassigned on the rule's source account into the columns as a
   same-account reclass, so the Statement of Activities does not change by
   a cent and the Statement of Functional Expenses has its columns. Running
   the same month twice finds nothing to move; void a run to put the cost
   back. The same rules drive the **Split** button on a bill or journal
   line when you would rather allocate at entry.
2. **Release from restriction.** When a restricted fund spends for its
   purpose, release that much to net assets without donor restrictions:
   Releases from Restriction → Release → pick the fund. The amount suggested
   is the fund's spending in the period less what was already released.

## Year end

- **Giving statements** — Report Center → Year-End Giving Statements:
  **Download all (PDF)**, every donor in one PDF with a page break each
  (print the stack), or **Email all** (donors who opted out on their record
  are skipped); one donor's own statement is **Giving Statement (last year)**
  on the donor's record (`GET /api/donors/{id}/giving-statement/pdf?year=`).
  Cash gifts with the amount eligible for a deduction and a running total
  (whether a donor can deduct it depends on their own return: from tax year
  2026 non-itemizers may deduct some cash gifts and itemizers face a floor);
  non-cash gifts described without amounts; the IRS sentence at the foot.
- **The ministry's own words** — Settings → Giving Statement (nonprofit mode)
  keeps the ministry's own message and footer on the statement. The **Opening
  messages** are saved the same way as Invoice Messages (a short list, one the
  default); the default one prints above the gift table (a thank-you from the
  pastor or the board). The **Footer text** replaces the fixed footer (the
  ministry's address, website, EIN, a verse) and prints in each page's bottom
  margin, so a long footer on a multi-page statement never overprints the gift
  table. Both are plain text with line breaks, printed as typed and escaped as
  text (no HTML); a message is at most 1500 characters and the footer 500.
  They ride on all three outputs the same — the single PDF, the all-donors
  stack and the emailed copy. Leave the message or footer unset to print what
  printed before; either way a donor's statement carries no product name. The
  IRS acknowledgment sentence is always printed and is not editable. **Preview
  one donor's statement** renders the first donor's (a sample donor on a file
  with no gifts yet) with the saved words — save first, then preview. The
  giving-statement messages and the footer are an administrator's to change,
  audited like the settings around them.
- **No closing entry is needed.** Income and expense are never closed to
  equity in this ledger; the Statement of Financial Position splits the
  change in net assets by restriction at report time, the way the balance
  sheet already synthesizes net income. The two net-asset lines always add
  up to the balance sheet's total equity.

## The statements

All five are computed from the same posted lines as the P&L and balance
sheet and reconcile to the cent; each has PDF and CSV. The Statement of
Activities, Financial Position and Functional Expenses take a **Basis**
(accrual or cash) like the P&L (2.30.0), so a church on the cash basis
reports on it.

| Statement | What it shows | Reconciles to |
|---|---|---|
| Statement of Activities | Revenue & support and expenses in two columns, without / with donor restrictions, releases between them | Change in net assets = P&L net income |
| Statement of Financial Position | Assets, liabilities, net assets without / with restrictions | Total net assets = balance sheet equity |
| Fund Balances | Per restricted fund: beginning, contributions, spent, released, ending, unreleased | Sum of ending = net assets with restrictions |
| Statement of Functional Expenses | Expense accounts by program / management / fundraising (Form 990 Part IX columns), plus program by program | Total = P&L expenses |
| Pledge Report | Promised, invoiced, received, written off, outstanding — by donor and by campaign fund | Invoiced = received + written off + outstanding |

The functional-expense CSV is in Part IX column order (A total, B program,
C management, D fundraising) for the preparer; the row mapping to Part IX
line numbers is not done for you.

**Year over year.** The Statement of Activities and the Statement of
Functional Expenses have a "Compare to prior year" box: the same dates one
year earlier appear as two more columns (prior year, change) on screen, in
the PDF and in the CSV (`?compare=prior_year` on the API).

**Where a PDF goes.** In the desktop app, Save PDF writes the file to
*Documents → SlowBooks Pro → Reports*, opens it in a viewer window, and shows
a "Saved to …" notice with a *Show in folder* button. Filenames carry the
period, so two runs never overwrite each other. Saved report *definitions*
(the "Add to Saved Reports…" button) are a different thing: they are listed under
Saved Reports at the top of the Report Center, as a collapsible list.

## Driving nonprofit mode from the API

Everything above is an API call, so a bring-your-own-AI agent with a scoped
token can do the month-end and year-end work unattended, in this order:

1. `GET /openapi.json` — every nonprofit path is described.
2. `PUT /api/settings {"company_type": "nonprofit"}` then
   `POST /api/nonprofit/setup-accounts` (a signed-in administrator; a
   bookkeeper token gets 403 on settings).
3. `POST /api/classes` with `restriction` and `default_function`;
   `POST /api/customers` with `donor_type`, `salutation`,
   `send_year_end_statement`; `POST /api/jobs` for a grant.
4. Gifts: `POST /api/sales-receipts` (with `fair_value_amount` /
   `fair_value_description` when goods were provided), `POST /api/recurring`
   + `POST /api/recurring/generate?as_of=` for pledges, `POST /api/payments`
   against them, `POST /api/in-kind-gifts` for property.
5. Shared costs: post the line with `"function": null` (omit the key to take
   the fund's default). `POST /api/nonprofit/allocation-rules`, then
   `GET …/{id}/split?amount=` at entry or `GET …/{id}/preview` and
   `POST /api/nonprofit/allocations` at month end.
6. `GET /api/nonprofit/releases/suggest?class_id=&start_date=&end_date=`
   then `POST /api/nonprofit/releases` (omit `amount` to take the
   suggestion).
7. `POST /api/invoices/{id}/write-off`; `POST /api/credit-memos/{id}/void`
   to undo.
8. Statements: `GET /api/reports/statement-of-activities`,
   `/statement-of-financial-position`, `/fund-balances`,
   `/functional-expenses`, `/pledges`, each with `/pdf` and `/csv`.
9. Acknowledgments: `GET /api/donors/gifts/{invoice|payment|in-kind}/{id}/acknowledgment/pdf`
   and `POST …/acknowledgment/email`; year end:
   `GET /api/donors/giving-statements/pdf?year=` and
   `POST /api/donors/giving-statements/batch-email`.

## What is deliberately out

Form 990 filing itself; a donor CRM (campaigns, appeals, events — a
donation's fund is the campaign); endowment spending-policy accounting
(permanently restricted is a label here); grant compliance rules beyond the
budgets jobs carry; a year-end closing entry.

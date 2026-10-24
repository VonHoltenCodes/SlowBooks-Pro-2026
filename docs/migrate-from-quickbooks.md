# Migrating from QuickBooks

SlowBooks imports QuickBooks data two ways, depending on which
QuickBooks you're leaving:

| You're on | Use | Where in SlowBooks |
|---|---|---|
| **QuickBooks Online** | Its own Export data zip (no Intuit app) | sidebar → **Migrate Data** → QuickBooks Online export |
| **QuickBooks Desktop** (2003–current) | IIF file import | sidebar → **Interoperability** |

The Desktop path imports accounts, customers, vendors, items, invoices,
payments, and **sales receipts**. Sales receipts come across as what
they are underneath: a paid invoice plus its payment, flagged so they
show up on SlowBooks' own Sales Receipts page. The Online path brings
the chart, every transaction as a journal entry, and the customer and
vendor lists; documents start fresh from the cut-over date.

Until 2.23.2 there was a third way, a connection to QuickBooks Online
over Intuit's API. It needed an app in Intuit's developer portal, and
keys that reach a real company sit behind an app questionnaire written
for software vendors, so it never served anyone; it is gone, and the
Export data path replaces it.

---

## Path 1 — QuickBooks Online (Export data)

QuickBooks Online's own export is enough, and needs no Intuit app at all.

1. In QuickBooks Online: the **gear** → **Tools** → **Export data**. Leave
   every report and list ticked, and set the date range to **All**. Before
   you export, set the company's report basis to **Accrual** (the gear →
   Account and settings → Advanced → Accounting method) if you want the
   Trial Balance to verify the import; on the cash basis it is set aside
   with a note.
2. QuickBooks downloads a zip named for the company and the date. Keep it
   as it is.
3. In SlowBooks: sidebar → **Migrate Data** → source **QuickBooks Online
   export** → upload the zip (or the workbooks inside it, or those saved as
   CSV) → **Dry run**, read the result, then **Import**.

What comes across:

- **The chart** from the Balance Sheet and the Profit and Loss: every
  account with a figure, typed by the section it sits under (Bank Accounts
  are banks, Credit Cards are cards), sub-accounts named `Parent:Child` as
  QuickBooks writes them elsewhere and placed under their parents.
  QuickBooks Online accounts have no numbers, so these arrive without one;
  give them numbers in Chart of Accounts afterwards if you like.
- **Every transaction in the Journal** as a journal entry: invoices,
  payments, bills, checks, deposits, transfers and journal entries alike,
  with the date, type, number, name and memo, each block checked against
  the totals the export carries. They are journal entries here, not
  invoices or bills, which is what the ledger needs for history; start
  documents fresh from the cut-over date.
- **Customers and Vendors** with their emails, phones and addresses, named
  as QuickBooks has them. The Employee list is left alone (the export
  carries no payroll), as is the General Ledger, which repeats the Journal
  by account.

Re-uploading the same export is safe: every transaction is tracked by its
type, number and date, and nothing posts twice. Afterwards, follow **After
the import** below.

**Receivables and payables.** The Journal posts every invoice and payment
to the export's own receivable account, "Accounts Receivable (A/R)", so
what is still open sits there. To carry it over, download QuickBooks
Online's **A/R Aging Detail** report (Reports → A/R Aging Detail → Export to
Excel) and upload it with the zip. Each open invoice comes across as an
invoice with its number, date, due date and open balance, and each payment
not yet applied as an unapplied payment, so the A/R aging and every
customer's balance are right; their postings move the open balance to 1100
Accounts Receivable and touch no income, and "Accounts Receivable (A/R)"
ends at zero. The dry run checks the report against the Journal and names
any difference. Already imported the zip without it? Upload both again: the
journals are skipped and only the open invoices come across. Unpaid bills
are entered after the import, against the imported "Accounts Payable"
account.

## Path 2 — QuickBooks Desktop (IIF files)

### What Desktop can export by itself

QuickBooks Desktop's built-in IIF export (`File → Utilities → Export →
Lists to IIF Files`) covers **lists only**: chart of accounts,
customers, vendors, items. Export those and drop the `.iif` file(s) on
**Interoperability → Import** — there's a **Validate** button that
checks the file before anything is written.

Names typed in ALL CAPS in QuickBooks come across as typed. If you'd rather
have them in normal capitalization, Validate shows a few of the file's names
as they would import and offers **Change ALL-CAPS names to normal
capitalization**. It covers customer, vendor and account names; item names
stay as typed (they are often part numbers), and nothing already in your
books is renamed. Look over the examples first: a name that should stay in
capitals (a company's initials) may not. A transactions file imported later
still finds each name, whichever way it spells it.

Opening balances come with the **first** import of the lists. Importing the
lists again later (to pick up new customers or vendors, say) posts none; the
result names any account it created that carries a balance in the file, so
you can enter the real ones by hand.

An IIF that carries transaction blocks imports those too (a file another
program wrote, or SlowBooks' own export brought back): invoices, payments,
sales receipts, estimates, bills and deposits as documents, and since 2.24.0
checks, card charges, transfers, bill payments and general journal entries
as the journal-only entries they are here (a BILLPMT lands on the bank
account and Accounts Payable with the vendor named; the bills it paid stay
open, since the file does not say which they were). A block already in the
books, same day and same lines, posts nothing twice, and two genuinely
identical blocks in one file both post. Tick **This is an accountant's
file** to post the GENERAL JOURNAL blocks as adjusting entries.

Going the other way, **Interoperability → Export to IIF** writes the whole
ledger since 2.24.0: the lists, invoices, sales receipts, payments, bills and
deposits as before, and now checks and register deposits, card charges,
transfers, bill payments and journal entries, each in the block QuickBooks
keeps it in. Exported from one company and imported into a fresh one, the
two trial balances match.

### Transactions: the Transaction Detail by Account report

The simplest way to bring every transaction across: in QuickBooks Desktop,
**Reports > Accountant & Taxes > Transaction Detail by Account**, set the
dates to **All**, and save it as CSV (keep the report's default columns).
In **Customize Report → Filters → Account**, choose **All accounts**.
That is a filter, not a column: do **not** add an **Account** column. The
account-grouped report already names each account above its transactions.
An account-grouped report with that added column is refused before import;
export it again with the default columns and the All accounts filter.
The refusal says:

> This appears to be Transaction Detail by Account with an added Account column.
> Nothing was imported. Export again using the default columns; set Customize
> Report > Filters > Account > All accounts rather than adding an Account column.

Import your lists IIF first, then upload the report under
**Interoperability → Import from Report CSV**. Every deposit, check,
transfer, general journal entry and opening balance comes across as a
journal entry with its date, number, name and memo, and each account ends
with the balance QuickBooks shows. The file is checked first and refused
whole if anything is off, and uploading it again posts nothing twice. A
refusal lists every transaction that is off, however many; **Save the
list** under the box writes them to a text file to work from.

Journal entries carry the history; the A/R and A/P aging read invoices and
bills, so a company that invoices in QuickBooks brings its open invoices in
from the invoice report below.

**Parent accounts with sub-accounts.** QuickBooks reports a parent account's
own postings in a section it names "<Parent> - Other" (for example "Advertising
and Promotion - Other"). That is not an account in its chart, and the import
posts those lines to the parent account itself.

**Long histories.** QuickBooks' Save as CSV stops a report at 32,768 rows with
no warning: the last section ends without its Total line, and the income and
expense accounts, which come after the balance sheet accounts, may be missing
altogether. Export **one calendar year per file** (set From and To to January 1
and December 31, keep All accounts and the default columns) and import them
oldest first; a transaction's lines share a date, so a year never splits one,
and a line already posted is skipped on the next file. Keep to one year even
when a longer range would fit under the cap: in a range that spans several
years, QuickBooks prints the balance sheet accounts for the whole range but the
income and expense accounts for its last year only, so every earlier sale and
expense arrives without its other half and the file is refused.

### Time: the Time by Name by Job detail report

QuickBooks Desktop does not export timesheets to IIF (it only imports them),
so the way across is a report: **Reports > Jobs, Time & Mileage > Time by
Name by Job detail** (Time by Job by Name detail reads the same), with the
dates set to **All**, saved as CSV or Excel. Upload it under
Interoperability → Import from Report CSV. Every row becomes an approved
time entry under its employee, with the hours (2.25 or 2:15; QuickBooks'
minutes are kept exact, and an Excel file's Total rows read from the values
Excel computed), the notes, the
service item, the billing status (not billable, unbilled, billed) and the
customer or job label as QuickBooks printed it; the entry links to the job
when the label is one on the Jobs list, so import the lists IIF first for
the customer:jobs. A person who is not on the Employees list is added from
the name and named in the result; set their pay type and rate on the
Employees page. Imported time is history: HR → Time Entries shows it marked
as QuickBooks', and a pay run never picks it up. The whole file is checked
against QuickBooks' own Total rows before anything is written, and refused
whole if it does not add up; a second upload posts nothing twice.

### A categorized bank sheet (the Transaction Pro / SaasAnt layout)

A bookkeeper catching up a client's books often cleans the bank export in a
spreadsheet, adds the account each line belongs to, and bulk-posts the sheet
with a paid importer. The sheet is the finished work, and **Interoperability →
Import from Report CSV** takes it as it is. Its columns, in any order:

| Column | Meaning |
|---|---|
| Bank Account | the bank or card account the money moved through: named in the header itself ("Bank Account BofA 9959", the cells left empty), or on each row; the whole name, part of it, or the digits a statement shows |
| Transaction Date | the date |
| Memo | the line's memo |
| Payee | who was paid, or who paid |
| Account | the other side: the expense, income or other account, by number, name or Parent:Child path; another bank or card account makes the row a transfer |
| Amount | money out negative, money in positive; tick *In this sheet money out is positive* when yours is the other way round |

Each row posts as the register entry it is. Rows that share the bank account,
date, payee and memo post as one entry split across their accounts (a loan
payment's principal and interest). The whole file is checked first and refused
whole with the reasons if a bank account or an account is not found or a date
or amount cannot be read. A row already in the books posts nothing twice.
Payees not on the Vendors (money out) or Customers (money in) list are added
and named in the result. CSV or Excel.

### What does not come across yet

Payroll history (pay stubs and liabilities), estimates and purchase orders
as documents, inventory on hand, memorized transactions and attachments.
Each importer here was built around a real export someone sent; if one of
these is in your way, open an issue with a sample export (change the names)
and it gets built in.

### Other transaction reports

Desktop does not export transactions to IIF natively. Two other options:

- **A third-party IIF transaction exporter** (several exist for QB
  Desktop). The importer understands `INVOICE`, `PAYMENT`,
  `CASH SALE` (sales receipts), `ESTIMATE`, `BILL`, and `DEPOSIT`
  blocks. Import lists first, then transactions, so customer/item/
  account names resolve.
- **For sales receipts, deposits, and checks: a report CSV imports
  directly.** Three Desktop report exports upload straight into
  **Interoperability → Import from Report CSV** (the report type is
  auto-detected):

  - **Deposit Detail** (`Reports → Banking → Deposit Detail`) — each
    deposit imports as a journal entry moving its payments from
    Undeposited Funds into your bank account, so the check register and
    reconciliation see them.
  - **Check Detail** (`Reports → Banking → Check Detail`) — each check
    imports as a journal entry (bank credit, expense debits). Payroll
    checks work too: withholding lines credit their liability accounts
    and the net matches the check.
  - **Transaction Detail filtered to Sales Receipt** — each receipt
    imports as a paid sale + payment (details below).
  - **Transaction Detail filtered to Invoice** — each invoice imports
    with its lines (item, quantity, price, tax) and its Customer:Job;
    add the **Open Balance** column and each one lands open, partly paid
    or paid, with a payment for what was paid (details below).

  Import your **lists first** (chart of accounts, customers, items via
  IIF) so account and item names resolve — deposit and check blocks
  whose accounts are missing are reported with a pointer to do exactly
  that. All three are safe to re-upload; duplicates are skipped.
- **A bank or credit card statement (CSV, OFX, QFX) is not an
  Interoperability import.** It goes in from the account itself, under
  **Banking → the account → Import file**. The vendors and accounts on the
  statement do not need to exist first: each line comes in as a statement
  line with its payee kept as text. A line that matches exactly one entry
  already in the books is matched to it on import; every other line waits in
  the account's **To review** list, to be matched or categorized from there
  (a bank rule can suggest the category from the payee). Nothing new posts
  until you add it. See
  [docs/banking.md](banking.md).

  The messy-report trap is exporting a summary report; the sales
  receipt path uses a detail report filtered to one transaction type:

  1. **Reports → Custom Reports → Transaction Detail**
  2. Set the date range to **All**.
  3. **Filters** tab → *Transaction Type* → **Sales Receipt**.
  4. Keep the report's **default columns** — the importer needs at
     least Date, Num, Name, Account, Split, Debit, and Credit (Memo,
     Item, Qty, and Sales Price are used when present).
  5. **Excel → Create New Worksheet**, save as CSV (or Print → Save as
     CSV).
  6. Upload it under **Interoperability → Sales Receipts from Report
     CSV**. Each receipt imports as a paid sale + its payment, with
     balanced journals; applied-deposit and discount lines keep their
     signs, tax rows are recognized by the percentage in Sales Price,
     and re-uploads skip duplicates.

  Import your **lists first** (chart of accounts, customers, items via
  IIF) so the report's account and item names resolve — unmatched
  accounts post to your default income account, with a warning naming
  them.

### Invoices from a report CSV

The same report, filtered to **Invoice** instead of Sales Receipt:

1. **Reports → Custom Reports → Transaction Detail**, date range **All**.
2. **Filters** tab → *Transaction Type* → **Invoice**.
3. **Customize Report → Display → Columns**: keep the defaults and add
   **Open Balance** (and **Due Date** if you want due dates). Without
   Open Balance the importer cannot tell a paid invoice from an open one,
   so every invoice comes in open and the import says how many did; upload
   the report again with the column and the invoices here that have
   nothing paid on them are marked paid or partly paid from it.
4. Excel → Create New Worksheet, save as CSV, and upload it under
   **Interoperability → Import from Report CSV**. The report type is
   detected from the rows: an invoice's header row posts to Accounts
   Receivable, a receipt's to the deposit account, so one file can hold
   both.

Each invoice imports with its item lines (item, quantity, unit price, from
the Item, Qty and Sales Price columns), its sales tax from the percentage
row, its Customer:Job (the job is created under the customer on first
sight), and its number. With Open Balance: **0.00** lands as **paid**, with
a payment for the total dated the invoice's date, deposited to Undeposited
Funds; between zero and the total lands **partly paid**, with a payment for
the paid part; equal to the total lands **open**. Re-uploads skip an
invoice already here: the same customer's under the report's number, as it
was numbered here or as the import renumbered it when that number was
taken (`QB-1031`), or, unnumbered, the customer's invoice of that date and
total. The
payments QuickBooks recorded are not in this report, so an imported paid
invoice carries one payment dated the invoice; a Deposit Detail import
then moves that money from Undeposited Funds to the bank.

### How CASH SALE blocks import

QuickBooks writes each sales receipt as a `CASH SALE` transaction
block: the header line carries the deposit account (bank or Undeposited
Funds), the split lines carry income and sales-tax credits. SlowBooks
imports each one as:

- an **invoice** flagged as a sales receipt, status **Paid**, with the
  split lines as line items (splits against accounts containing "tax"
  become the tax amount);
- a **payment** for the full total, deposited to the header account
  (falling back to Undeposited Funds if the account name doesn't
  match);
- balanced journal entries for both, so the ledger nets to
  cash/Undeposited Funds + income — identical to a receipt entered on
  the Sales Receipts screen.

Details worth knowing:

- **Customer:Job names** — the colon splits the customer from the job: `Smith:Kitchen Remodel` becomes customer *Smith* with job *Kitchen Remodel*, and the invoice, sales receipt or estimate is tagged to that job (see the Jobs page). A deeper `A:B:C` path becomes job `B:C` under `A`.
- **Blank customer** — QB allows counter sales with no Customer:Job.
  Those import against an auto-created **"Walk-In Customer"** (the
  import report notes each one).
- **Unnumbered receipts** — receipts without a `DOCNUM` get the next
  SlowBooks invoice number. Numbered receipts keep their numbers.
- **Duplicates** — re-importing the same file skips receipts whose
  document number already exists (or, for unnumbered ones, an existing
  receipt with the same customer + date + total).

---

## Classes: entities kept apart in one file

If your QuickBooks file keeps several entities apart by class (a nonprofit
and two LLCs in one file, say), the class travels with every posting on
both paths (v2.23.1): the IIF import takes the TRNS row's class as the
document's and each split row's as its line's, and the report CSV import
reads the Class column on every report it takes, header and lines. A class
the file names that your books do not have is created on first sight, and
the import's result says so. Check P&L by Class afterwards: every column
should tie to the entity's own Profit & Loss in QuickBooks, and anything
under Uncategorized is a posting QuickBooks had no class on either.

## After the import

- Spot-check **Reports → Trial Balance** against the same report in
  QuickBooks as of your cutover date (mind the date range — set it to
  cover the imported history).
- Run **Reports → Possible Duplicates** once on each bank and card
  account, with a Custom Date range covering the imported history. A
  QuickBooks register often carries a bank-feed line on top of the same
  payment keyed by hand, or a statement entered twice, and the import
  brings those across as they are. The report groups entries with the
  same amount on the same side and the same or a similar payee up to
  three days apart; open each one, void the copy (the usual void, so a
  reconciled entry or a closed period is protected as anywhere else), or
  mark the group **Not a duplicate** so it stays off the report. It never
  voids anything by itself.
- Sales receipts are listed on **Sales Receipts** in the sidebar;
  regular invoices stay on **Invoices**.
- Going forward, enter point-of-sale style sales on the **Sales
  Receipts** screen — one form records the sale and the payment
  together.

## Leaving QuickBooks Contractor Edition

If your file is a QuickBooks Desktop **Contractor Edition** company, set
SlowBooks to the matching mode after you import: **Settings → Company Type →
Construction**. It changes no data and can be switched back at any time. What
it does:

- Adds the contractor accounts — Retainage Receivable and Payable,
  Construction in Progress, Customer Deposits, Use Tax Payable, Equipment
  Cost and Other Job Costs — each only if your imported chart does not
  already have one by that name, and each keeping its standard number only
  where your chart has not already used it. Your own retainage or deposit
  accounts are left as they are; move their balances by journal entry if you
  want the standard numbers.
- Loads the standard CSI MasterFormat cost-code divisions (01–33, plus Labor
  and Equipment Rental) if you have not built your own. QuickBooks' job
  "Items" do not come across as cost codes — rebuild the ones you use under
  the matching division on the Cost Codes page.
- Shows a subcontractor as a **Sub**, and gives each vendor you mark a
  Subcontractor a trade-license and certificate-of-insurance record. Mark
  your subs after the import; QuickBooks does not export the COI.

The seed chart SlowBooks starts a brand-new company with is already the
Contractor template (Materials, Labor and Subcontractor Costs on the COGS
side), so a fresh contractor file and an imported one converge on the same
accounts. QuickBooks' **Customer:Job** list imports as SlowBooks jobs under
their customers; the name prints as "Customer: Job" here.

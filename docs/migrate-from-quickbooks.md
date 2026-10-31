# Migrating from QuickBooks

SlowBooks imports QuickBooks data two ways, depending on which
QuickBooks you're leaving:

| You're on | Use | Where in SlowBooks |
|---|---|---|
| **QuickBooks Online** | Its own Export data zip (no Intuit app) | sidebar → **Migrate Data** → QuickBooks Online export |
| **QuickBooks Desktop** (2003 through 2024 / Enterprise 24.0, the current release) | IIF file import | sidebar → **Interoperability** |

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

1. In QuickBooks Online: **Settings** (the gear) → **Export data**, pick a
   reason and **Continue**. On the **Reports** tab set the date range to
   **All dates**, leave every report and list switched on, and press
   **Export to Excel**. Before you export, set the company's report basis to
   **Accrual** (Settings → Account and settings → Advanced → Accounting →
   Accounting method) if you want the Trial Balance to verify the import; on
   the cash basis it is set aside with a note. A very large company may be
   told some files were too big to export; export shorter ranges then.
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
Online's **Accounts receivable aging detail** report (Reports → Standard
reports → Who owes you → Accounts receivable aging detail → Export → Export
to Excel) and upload it with the zip. Each open invoice comes across as an
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
customers, vendors, items. Export those and drop each `.iif` file, one at
a time, on **Interoperability → Import from IIF**; press **Validate**, which
checks the file before anything is written, then **Import**.

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
dates to **one year** (see *One year per file* below), and save it as CSV:
**Excel → Create New Worksheet → Create a comma separated values (.csv)
file → Export** (keep the report's default columns).
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
with the balance QuickBooks shows. Uploading it again posts nothing twice.

The file is checked first. One that checks out imports at once. A row
whose date or amount cannot be read refuses the whole file, every such row
listed: a guess would post a wrong date or amount. A date is read as
month/day/year (01/31/2024), the way QuickBooks Desktop writes it in the
United States, and an amount as 1,234.56. QuickBooks Desktop writes its
dates in the order of Windows' region setting, so a report exported under
another region's format has day-first dates (20/03/2024) and is refused,
the sentence above the rows saying the dates look day first: in Windows,
set the regional format to English (United States) (Windows 11: Settings >
Time & language > Language & region > Regional format; or Win+R, `intl.cpl`,
Formats tab), restart QuickBooks, export the report again and import that. One with other problems
imports nothing yet (since 2.31.2): the page lists the problems, grouped,
for you to tick the ones to accept and leave the rest:

- **A transaction that does not balance**, with its date, type, number,
  name, amount and how far off it is, and on which side ("$10.00 more in
  debits"). Ticked, it posts with the difference
  on **3950 Import Differences**, an equity account made the first time it
  is needed (or the next free number after 3950 when your chart has that
  one), for you or your accountant to clear. Common causes: a report
  filtered to some accounts (set Filters → Account to All accounts), and a
  range of more than a year (see *One year per file* below).
- **An account not in the chart**, once, with how many transactions touch
  it. Choose its type (QuickBooks' own types, Bank to Other Expense) and it
  is ticked to be made; the lists IIF is still the better way to bring the
  chart. A sub-account of an account already in the chart is made by the
  chart's own rules: it is offered only the types its parent takes (the
  parent's own, or under a bank, card, A/R or A/P account another of its
  kind), starting on the parent's, and one the chart cannot take there (an
  inactive parent, a chain too deep) says why. A transaction that touches
  an account left unticked is left out.
- **The file may be cut off**: a section whose lines do not add up to the
  total QuickBooks printed, a file that ends inside a section or after a
  section's Total line without the report's closing TOTAL row, or a TOTAL
  row that is not the sum of the file's lines (a section taken out).
  Ticked, what is there imports; unticked, nothing does. See *Long
  histories* below.
- **Transactions in a closed period**: a notice, with nothing to tick, of
  how many are dated on or before the closing date and which dates. They
  are left out and the rest imports; to bring them in, move the closing
  date (Settings > Closing Date) and import the file again.

Each list has Select all and Select none, and is one stop for Tab however
long: the arrow keys move from row to row (in the accounts, Tab goes on to
the row's type first). **Save the list** writes the problems to a text file
to work from. **Import** sends the same file again
with your answers: what checks out and what you accepted posts, and the
result says how many were left out and why.

A transaction you left out comes in from a corrected file later, and what
posted whole is skipped then. One you accepted with its difference is a
**partial entry**, and a later file never posts it a second time. A
transaction in a file is the one in the books when the two have the same
QuickBooks Type, Num and date and every line of the one, its account and
amount, is among the other's lines, whatever its name (a cut can lose
it). A later file that has more of a partial entry's transaction lists it
among the transactions that **replace a partial entry in the books**, the
partial entry's date and amount beside it and what it put on Import
Differences (a debit when the file had more credits, a credit when it had
more debits); the parts of same-day transactions a cut merged into one
partial entry are listed together, and the list says when the file's copy
is still off or is several transactions. A transaction of the same Type, Num and date that holds none of
its lines (another deposit that day) imports on its own. Ticked, the
partial entry is voided (its usual reversal, on its own date) and the
file's copy posts in its place; a copy that still does not balance posts
as a partial entry again, and a later file that has it whole replaces
that in turn. Unticked, both stay as they are and the file's copy is left
out. Nothing is ticked to start with. A cut file whose transactions the
books already hold (the same cut file again, or the old one after the
corrected file) posts nothing: they are counted with the duplicates. A
partial entry held by a completed reconciliation is never replaced (a
finished reconciliation cannot be reopened): leave it as it is and clear
its difference on 3950 Import Differences with a journal entry. One that
was voided, by you or by a replacement, no longer counts as imported, so
the next file that has that transaction brings it in.

In a bank or card register each entry reads as one typed there does:
QuickBooks' **Name** is the payee, its **Num** the Ref #, each line's
**Memo** the memo, and its **Type** the Type column ("Check (imported)").
A number on an imported check, bill payment or paycheck is known to the
duplicate check number warning; the next check number follows only the
checks written here. An entry with no name, such as a general journal
entry or a transfer, has no payee and reads as its memo on the Journal
Entries page and in reports. Possible Duplicates does not pair two imported
entries, which QuickBooks already keeps apart, unless one of them is a
partial entry: that is where a transaction can come in twice. Those two
pair when they have the same QuickBooks Type, Num and date, whatever their
payees (a cut file can lose the line that carried the name), or a payee
alike. Books imported before 2.31.2
showed "Check 1001 — Name: memo" as the payee; upgrading rewrites those
entries the same way, and leaves any it cannot read back exactly as they
were. Either way, uploading the same file again posts nothing twice.

Journal entries carry the history; the A/R and A/P aging read invoices and
bills, so a company that invoices in QuickBooks brings its open invoices in
from the invoice report below.

**Parent accounts with sub-accounts.** QuickBooks reports a parent account's
own postings in a section it names "<Parent> - Other" (for example "Advertising
and Promotion - Other"). That is not an account in its chart, and the import
posts those lines to the parent account itself.

**One year per file.** Export **one year per file** (your fiscal year, or January 1
to December 31; set From and To to its first and last day, keep All accounts and the default
columns) and import the files one at a time, **oldest year first**. The reason:
in a range that spans more than one year, QuickBooks prints the balance sheet
accounts for the whole range but the income and expense accounts for its last
year only, so every earlier sale and expense would arrive without its other
half and the books would look half-complete. The import reads the period
QuickBooks prints above the columns and, for a file that covers more than 12
months, imports it and warns (#568); the warning names the period and says to
export one year per file, and in a file whose transactions do not balance
the same note sits above the list of them. A July–June fiscal year is 12
months and gets no warning. A transaction's lines share a date, so a
year never splits one, and a line already posted is skipped on the next file.

**Long histories.** QuickBooks' CSV export also stops a report at about
32,768 rows (users report 32,769 lines with the header) with no warning: the last section ends without its Total line (or the file
without the report's TOTAL row), and the income and expense accounts, which
come after the balance sheet accounts, may be missing altogether. The import
says the file may be cut off, and the transactions missing their other side
do not balance. One year per file
keeps most companies under the cap; a year that still does not fit can go in
two files split at a month end.

### Time: the Time by Job Detail report

QuickBooks Desktop does not export timesheets to IIF (it only imports them),
so the way across is a report: **Reports > Jobs, Time & Mileage > Time by
Job Detail**, with the dates covering the time you want, saved as CSV or
Excel (a Time by Name report customized to group by job reads too). Upload
it under Interoperability → Import from Report CSV. Every row becomes an approved
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

### Payroll year to date

Each employee's year-to-date payroll comes across as an **opening balance**
(#433): what the old payroll paid this year, quarter by quarter, through the
day before the first SlowBooks pay run. Enter it the way QuickBooks takes a
YTD adjustment: for each quarter, gross, federal withholding, the Social
Security, Medicare and FUTA wages and tax, deductions and net, and for each
state the wages, withholding, SUTA wages and SUTA tax. The 941s and state
returns the old system filed, and its payroll reports run for each quarter,
have these figures. Each quarter's 941 then carries only that quarter, and the Social
Security, FUTA and SUTA wage bases start from the taxable wages. Enter it on
the employee's page (Opening YTD), or for everyone at once from Payroll →
**Import opening balances** with the template linked there (one row per
employee per quarter). No journal entry is made; the opening balance sheet
carries what was still owed. Once a pay run in the year is processed, a
change is a correction with a reason.

SlowBooks does not read the Payroll Summary report itself yet: every
importer here was built around a real export, and that one is waiting for
one. If you can share a Payroll Summary export (change the names), open an
issue with it.

### What does not come across yet

Individual pay stubs and payroll liabilities as documents (year-to-date
totals do, above), estimates and purchase orders as documents, inventory on
hand and attachments.
Each importer here was built around a real export someone sent; if one of
these is in your way, open an issue with a sample export (change the names)
and it gets built in.

### Memorized transactions

An IIF file does not carry QuickBooks' memorized transaction list, and no
Desktop report prints it with its schedule, so there is nothing to import:
rebuild them under **Memorized Transactions** in the sidebar (#605). A
memorized bill or journal entry takes the how-often choices QuickBooks
offers (daily, weekly, every two weeks, twice a month, monthly, every two
months, quarterly, twice a year, yearly; QuickBooks' "every four weeks" is
every 4 weeks here), or every N days, weeks or months, a next date, an end
date or a number of times, and is either entered automatically on its date
(Auto-enter) or listed as due until you enter it (Remind me), as
QuickBooks' Automate Transaction Entry and Add to my Reminders List do. The quickest way to rebuild one is from the last
document it entered, once that is imported: open the bill on its page and
press **Memorize**, and the same on a journal entry's page; the form opens
filled from the document, and only the schedule is left to set. Recurring
invoices are their own page (Recurring Invoices) and are listed on the
Memorized Transactions page too.

### Other transaction reports

Desktop does not export transactions to IIF natively. Two other options:

- **A third-party IIF transaction exporter** (several exist for QB
  Desktop). The importer understands the same blocks listed under *What
  Desktop can export by itself* above: invoices, payments, sales receipts,
  estimates, bills, deposits, checks, card charges, transfers, bill
  payments and general journal entries. Import lists first, then
  transactions, so customer/item/account names resolve.
- **For sales receipts, deposits, and checks: a report CSV imports
  directly.** Four Desktop report exports upload straight into
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
  that. All four are safe to re-upload; duplicates are skipped.
- **A bank or credit card statement (CSV, OFX, QFX) is not an
  Interoperability import.** It goes in from the account itself, under
  **Banking → Bank Accounts → the account → Import file**. The vendors and accounts on the
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
  5. **Excel → Create New Worksheet → Create a comma separated values
     (.csv) file → Export** (or Print → Print to: File → Comma delimited
     file).
  6. Upload it under **Interoperability → Import from Report CSV**; the
     report type is detected. Each receipt imports as a paid sale + its payment, with
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
4. Excel → Create New Worksheet → Create a comma separated values (.csv)
   file → Export, and upload it under **Interoperability → Import from
   Report CSV**. The report type is
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

- **Customer:Job names** — the colon splits the customer from the job: `Smith:Kitchen Remodel` becomes customer *Smith* with job *Kitchen Remodel*, and the invoice, sales receipt or estimate is tagged to that job (see the Jobs page). If the colon names a sub-customer rather than a job (a property's tenants, a parent company's locations), tick **The Customer:Job names in this file are sub-customers, not jobs** on the Validate report (2.31.1): each then comes in as a sub-customer that can bill with its parent. Names deeper than Parent:Child are kept one level deep, and the result says so.
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

- **The starter accounts stay.** A new company comes with a generic chart
  (Checking, Accounts Receivable, Advertising & Marketing, Office Supplies
  and the rest). An import adds your accounts beside them and never deletes
  a starter account. The QuickBooks imports on this page reuse a starter
  account that has the same name as yours (the IIF import also one with
  the same number) and create the rest; the CSV chart import goes one
  step further and renames a starter control account (Accounts Receivable,
  Accounts Payable, Checking, Sales Tax Payable, Undeposited Funds,
  Inventory, Retained Earnings, Cost of Goods Sold) to your name rather
  than making a twin, so every document keeps posting to it. The starter
  accounts your file does not mention stay on the chart, unused. To clear
  them: **Chart of Accounts
  → Remove unused seeded accounts…** takes every starter account with no
  postings, no sub-accounts, no register and no control role off the chart
  in one step, and names the ones that stay and why (Accounts Receivable
  and the other control accounts always stay; so does any account the
  import posted to). The CSV chart import also offers **Replace the seeded
  chart**, which deactivates (never deletes) every account the file does
  not name that has never been posted to. Either way, an account with
  history is kept: delete nothing by hand that a transaction still uses.
  If your business does not number its accounts, untick **Settings →
  Company Information → Use account numbers** after the import: the
  starter accounts keep their numbers, but nothing prints them.
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
SlowBooks to the matching mode after you import: **Settings → Company
Information → Company Type → Construction**. It changes no data and can be switched back at any time. What
it does:

- Adds the contractor accounts — Retainage Receivable and Payable,
  Construction in Progress, Customer Deposits, Use Tax Payable, Equipment
  Cost and Other Job Costs — each only if your imported chart does not
  already have one by that name, and each keeping its standard number only
  where your chart has not already used it. Your own retainage or deposit
  accounts are left as they are; move their balances by journal entry if you
  want the standard numbers.
- Loads the standard cost-code division titles and a set of common sections
  (the two-level division/section numbering) if you have not built your own. QuickBooks' job "Items" do
  not come across as cost codes — rebuild the ones you use under the matching
  division on the Cost Codes page.
- Shows a subcontractor as a **Sub**, and gives each vendor you mark a
  Subcontractor a trade-license and certificate-of-insurance record. Mark
  your subs after the import; QuickBooks does not export the COI.

The seed chart SlowBooks starts a brand-new company with is already the
Contractor template (Materials, Labor and Subcontractor Costs on the COGS
side), so a fresh contractor file and an imported one converge on the same
accounts. QuickBooks' **Customer:Job** list imports as SlowBooks jobs under
their customers (or as sub-customers, when you tick that on the Validate
report); the name prints as "Customer: Job" here.

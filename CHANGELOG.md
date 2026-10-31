# Changelog

Notable changes between releases. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/). The internal build order
used during development is captured here so the README can stay focused
on what the software does, not on what sprint shipped what.

### Unreleased

**Payment terms beyond Net N** (#664). Terms were free text with one rule:
"Net N", else 30 days, said to no one, so "2% 10 Net 30", "Net 10th" and
"15th of following month" were all quietly due 30 days out. Settings now
keeps a terms list, the QuickBooks shape: a standard term is due a number of
days after the date, a date-driven term on its next day of the month
(the one after, when the document is dated fewer than a set number of days
before that day), and either can offer an early-payment discount. Every form that takes
terms offers the list, and every document's due date (invoices, bills,
recurring invoices, memorized bills, an estimate's or a PO's conversion, a
duplicate) comes from one parser. An invoice, a bill and a recurring or
memorized schedule are tied to the term their terms named when the person
picked them, and keep that term's rule; a term added to the list later
never reaches what was saved before it, even when it is saved again with
exactly the same terms (any other text picked, even one differing only
in case or spacing, ties it anew to the entry it names), and a term an
invoice or bill was saved under keeps its rule and name. A duplicate and a
schedule's next document keep their source's tie; an import ties nothing.
Existing books don't change: Due on Receipt, Net 15, 30, 45 and 60 are on
the list with the rules they always had, an entry is added for any other
number of days the old parser read from text in use, and only the text the
old parser read as exactly those days is tied to them ("net<TAB>45" was 30
days and stays untied). Anything untied is read as the old parser read it,
everywhere (A/P aging included); a document says what its due date is. Term
names are stored with whitespace collapsed and invisible characters taken
out, so a name reads, matches and parses alike. A name the app already
reads ("Net 30", "COD") keeps its meaning. A recurring invoice whose terms
are any of the receipt words (Due on Receipt, due upon receipt, COD, Net 0,
in any case) is now due the day it is made; its generator had its own
parser, which read all of them as 30 days. The discount shows on the invoice and bill and on
their PDFs (a foreign-currency one offers none), and Receive Payments and
Pay Bills suggest it until its date, on what the payment applies to: the
customer's is a credit memo, ours a vendor credit, applied to the document,
posted to the account you pick and voided with the payment. A discount the
terms don't offer (none, after its date, more than the percent, or a second
one) is refused unless the payment says `allow_discount_override`, which is
an administrator's; the forms ask first, and the payment, the memo or
credit and the audit log record who took it and why. A discount the
customer took after a short payment is taken with that payment later
(`POST /api/payments/{id}/discount`, and `/api/bill-payments/{id}/discount`).
A discount is a percent of the document's total, never on a payment dated
before the document, and never on money a payment's remainder paid later.
**The CSV list import changes on purpose:** Terms are no longer limited to
the five the forms offered. A row takes any term on the list (matched as
the list matches, so "net<TAB>45" is the listed Net 45) or any terms the old
parser reads ("Net 99", "COD", "Net 0", "Net 1_0"), kept as typed, so rows
main refused now come in; terms nothing reads, and terms that would be due
before their date ("Net -5"), are still refused, each naming its row. On the cash basis a discount settles its share of the
document as a payment does, and is recomputed with the payments when the
document is edited, so the cash Sales Tax report agrees with its ledger. An invoice or bill
with a discount taken keeps its terms and date. Migration `e5c1a7d3f9b2`
(the list, seeded; nullable columns tying an invoice or bill to its term
and a discount to its payment).

### v2.31.1 — Sub-customers, memorized transactions, and stock that comes back out at its own cost

**Each sign-in is offered only what it may do.** The Memorized Transactions
page's box for entering due schedules when the app opens is a company setting,
so only an administrator can change it; a bill payment's Print Check no longer
saves the check memo first for a read-only sign-in (it prints the check as it
stands); and a bookkeeper sees Settings' payroll posting accounts locked, as the
rest of the administrator's settings are.

**A bank feed imports into any bank account in the chart** (from a support
email). The "Imports into" list beside a SimpleFIN or Mercury feed offered only
the bank accounts added with + New Bank Account, so a company whose bank
accounts were already in its chart saw an empty list, and SimpleFIN's section
did not say why. Both lists now offer every bank account in the chart (and,
for SimpleFIN, every credit-card account), and choosing one that has never been
set up on the Banking page sets it up when the mapping is saved. A chart with no
bank account at all gets a sentence saying how to add one. API: the map routes
take `ledger_mapping`, a feed's account to a chart account.

**A WebView2 runtime too old to run SlowBooks says so** (from a support
email). On Windows the app's window is drawn by Microsoft's Edge WebView2
runtime, and the app only checked that one was installed. A runtime older
than 101.0.1210.39 opened a window that stayed white, with nothing to click
and no message, and reinstalling did not help, because the installer set the
runtime up only when there was none. Now the app checks the version before
it opens the window: on an older runtime it says which version the computer
has and which SlowBooks needs, gives Microsoft's download page, and offers to
open SlowBooks in your web browser meanwhile. The installer installs the
runtime when it is missing and updates it when it is older than that. And if
the app's scripts ever cannot start in the window, the page says so, with
the error and how to reach support, in place of a blank screen.

**The Invoices list sorts by any column** (#650, from the gate). Press a
heading (number, customer, date, due date, status, total, balance) to sort by
it, and again to reverse; numbers sort as numbers. A sortable heading on any
list is now a button the keyboard reaches, and it tells a screen reader how
the column is sorted. The order chosen is kept when you come back to the list.
Dates sort by the day: the Invoices list's Date and Due Date, and the date
columns of Bills and Fixed Assets, which share the list helper, had sorted by
the year alone.

**The server's cron script runs again.** Since 2.30.0, `scripts/run_recurring.py`
stopped at the first due recurring invoice with "could not find table
'pay_applications'": that table was known only to the web app, so a server's
cron entered nothing, recurring or memorized. Every table is registered with
the rest of the models now, and the script runs in a process of its own.

**The wide dialogs use the window's width, and the splash scrolls on a
short window.** A dialog with a wide line table (Enter Bill, an estimate, a
job cost entry) took 90% of the window, so since 2.31.0's move column the
bill's lines scrolled sideways at a 1264px window with Amount out of view,
and a construction company's bill, with its Cost Code and Cost Type
columns, scrolled at any window size. Such a dialog now takes the window's
width less a margin, up to 1280px, the line cells sit closer, and each
line's move controls fold two by two where the table is tight, so the bill
shows every column at a 1264px window; and at 900px or less, where Enter
Bill's vendor panel goes under the form, the lines' table scrolls inside
the dialog instead of the whole dialog scrolling sideways.
With the terms and What's New showing on a window 700px tall, the splash ran
past the bottom of the screen with its button out of reach; it scrolls now.

**The top bar fits a window down to 900px.** It ran 1,265px wide at any
width, so at 900 Sign Out sat past the edge. Its quick-entry buttons now
stay wherever they fit and step aside only as the window narrows: all five
at Windows' own default window and wider, Quick Entry and Bill Entry going
below 1250px and New Customer, Create Invoice and Receive Payment below
1110px, while Back, the company, Home, Reports, search, About, Contact
support and Sign Out stay in view. Invoice Quick Entry has its own sidebar
link now, and the bar's buttons name their shortcuts (Home Alt+H, Create
Invoice Alt+N, Receive Payment Alt+P, Quick Entry Alt+Q) as the pages'
buttons do.

**One shape for every refused field.** A field an edit sends as null that may
not be empty (a schedule's mode, a bill's vendor) answered a bare sentence;
it is refused now as every other field is, a list with a plain `message` for
each field the page shows. An occurrence asked for before a schedule starts
says so, rather than that it was just entered.

**An expense keeps the memo it was given** (#648, from the 2.31.0 gate). An
expense's memo was read back off its bank line, which holds the memo or,
when none was typed, the payee. So a memo typed the same as the payee came
back blank, and an expense entered for a vendor with a different payee typed
and no memo came back with that payee as its memo. The expense now stores
the memo it was given and returns exactly that, blank when none was typed.
Expenses saved before this release are read as before. The bank line is
unchanged, so the register shows what it always did.

**A bill's void or edit takes its stock back out at the cost it came in at**
(#646, #647, from the 2.31.0 gate). A stock line on a taxed bill is received
at the line's rate with its share of the tax, the figure the bill puts on
Inventory, but voiding the bill took the stock out at the rate alone, and an
edit of a bill with the same item on two lines took the whole quantity out at
the last line's rate; either way the ledger's Inventory no longer equalled
quantity x average cost, and selling out left a figure in Inventory with
nothing on hand. Each receipt now comes back out by itself at the cost it
carried, one reversal movement per receipt naming it, the way a sale's void
walks its postings; a bill edited on an earlier release and voided now
reverses only the receipt the edit left standing. Two edges of the
re-averaging that came with 2.31.0 are closed as well (#647): a reversal that
lands the count on zero, or is worth more than the stock holds, left the
difference sitting in Inventory. That difference is now posted to the item's
cost of goods account (its own, else the chart's) under the void or edit that
caused it, so Inventory reads quantity x average cost afterwards, 0.00 with
nothing on hand. The receiving side is covered as well: stock that comes in
while the count is below zero (sold ahead of its receipt) first fills that
hole, and the difference between what the units sold ahead were costed at and
what they cost now goes to the same cost of goods account under the bill, so
bill 5 at 10.00, invoice 8, bill 3 at 14.00 leaves 0 on hand, Inventory 0.00
and the 12.00 difference in cost of goods, where it used to leave 12.00 in
Inventory with nothing on the shelf; a receipt bigger than the hole is stock
at its own price past it, and a sale's void, a credit memo's return and a
count adjustment up fill the hole the same way. A bill's void or edit that
takes stock already sold below zero now says so in the response's `warnings`
("Widget: 2 on hand; this void takes it to -3."), the way a sale past the
stock does, and the Bills page shows it as a toast; with Refuse a sale of
more than is on hand turned on, the void or edit is refused naming the item
and the shortfall, with nothing posted. Each reversal movement names the
receipt it reverses ("VOID Bill B-2 line 2") and the movements API carries
`reverses_movement_id`, so the two reversals of a two-line bill no longer
read the same. Vendor credits now keep Inventory at quantity x average cost
too, and a credit that returns more of an item than is on hand says so in
`warnings` ("Widget: 2 on hand; this credit takes it to -3.") or, with that
setting on, is refused.

**Sub-customers with parent billing** (#638, from Discussion #633). A customer
can now sit under another, one level deep, the way QuickBooks spells
"Parent:Sub": a property manager keeps each property as a customer and each
tenant as a sub-customer under it. A tenant is made inactive on move-out
without touching the property (making a parent inactive asks about its active
sub-customers first, and can take them along), and everything rolls up to the
property: its page lists its sub-customers with their balances and the total.
Every customer picker shows a sub-customer as "Parent: Sub" under its parent,
and searching the property's name finds its tenants. Bill with parent, on the
sub-customer, puts its invoices on the parent's A/R: the printed invoice bills
the parent and names the sub-customer under the address, the parent's
statement and A/R Aging row carry the sub-customer's open items, overdue
statements and collection letters go to the parent, and the parent's payments
apply to the sub-customer's invoices. Without it a sub-customer is billed and
aged on its own. The IIF import asks whether a file's Customer:Job names are
sub-customers or jobs (jobs, as before, unless told), and the QuickBooks
Online export's "Parent:Sub" customers come in as parent and sub-customer. A
name in an imported file is read the way QuickBooks spells it: a bare name is
a top-level customer, never a tenant that happens to share it, so a file's
"Smith" does not post to "Oak St:Smith" and "Smith:Unit 2" never makes a
third level; a three-level name is imported one level deep and the import
says so. A statement's payment and credit lines are what relieved the
invoices on it, whoever paid, so a tenant's own statement shows the
property's payment that paid its rent and reads what the tenant owes, and
while the property's money is on a tenant's open invoices, Bill with parent
stays on. A property is not made inactive while a tenant billed with it
still owes (the 409 says what is owed and what to do), the question about
active tenants names each one's open balance, and a tenant is not made
active again under an inactive property. A credit memo of the property
applies to a tenant billed with it, as a payment does. The IIF export spells
a sub-customer "Parent:Sub" on every row, every invoice response names a
sub-customer the same way, the printed invoice prints the billed customer's
country, and a second property's Unit 1 is not a duplicate of the first's.
Tenants do move: once every invoice the property's money is on is settled, a
tenant may be billed on its own or moved to another property, and the
statements keep the history. A customer's statement lists its own payments
and credits that relieved another customer's invoices as a pair, the payment
and the invoice it paid ("for Unit 1: #1001"), so the former property's
statement still shows what it paid and its balance is unchanged, and the new
property's statement carries neither the old property's payments nor the
invoices they relieved. The printed invoice keeps the Bill To it was made
with: the name and country are frozen on the invoice with the street, so a
move or a flag change afterwards does not alter a printed invoice, and
neither does renaming the customer, the way its street never did (books from
before take the name and country of the customer each invoice is billed to
at the upgrade). An invoice moved to another customer by an edit, a
duplicate and an estimate converted to an invoice are billed as of that
moment: the whole Bill To, street, name and country, is the payer's then
(the property's for a tenant billed with it) and the ship-to the
customer's. Recurring invoices and a pay application's invoice, which used
to carry no street and printed a name alone, are billed the same way. An
edit can correct the invoice's addresses, and a duplicate is checked against
the credit limit as a new invoice is. The printed Ship To keeps its country
the same way: the country is kept on the invoice with the ship-to street,
the customer's own, so an old invoice no longer prints the country a
customer has moved to since under the street it was sent to (books from
before take each invoice's customer's ship country at the upgrade), and an
edit can correct it as it corrects the street (`ship_to_country`). An
address part an edit sends empty is now left empty, also when the edit moves
the invoice to another customer, where it used to take the new customer's
anyway; a new invoice still fills a part left empty from the customer. An
invoice with a payment or credit memo applied to it stays with its customer:
moving it to another customer is refused with a sentence naming each payment
and credit, whose it is and for how much, and saying to void it and enter it
again where it belongs first (409 `money_applied`), since the moved invoice
put the payment on both customers' statements. Every customer now
answers `billed_balance`, what sits on its A/R, its own open documents plus
those of the tenants billed with it, the figure its A/R Aging row shows; the
Customer Center shows it as what a property owes, beside its own balance and
the roll-up, and the customer CSV names a tenant in full, "Parent: Sub". The
credit limit checked when an invoice is saved is the payer's: an invoice to
a tenant billed with its property is checked against the property's limit
and what sits on its A/R, tenants included. Every tenant listed under its
property shows its own name, not only the first, and among the tenants of
one property only the same name is a duplicate, so Unit 2 may follow Unit 1.

**Memorized bills and journal entries, entered on a schedule** (#605, asked
by two people moving from QuickBooks). A bill or a journal entry can now be
kept whole with a schedule: the vendor, terms, bill number and every line
with its account, memo, job, cost code and class for a bill; the lines,
reference, basis and adjusting mark for a journal entry. The schedule takes
the choices QuickBooks offers (daily, weekly, every two weeks, twice a
month, monthly, every two months, quarterly, twice a year, yearly, or every
N days, weeks or months), a next date, and an end date or a number of times;
a monthly schedule begun on the 31st lands on the last day of a shorter
month and on the 31st again after it, and twice a month is the next date's
day and fifteen days later. A recurring invoice now keeps the day of its
start date the same way: one begun on the 29th, 30th or 31st used to slip
to an earlier day for good after the first shorter month (the 28th, after
February), and a catch-up could make one invoice too many in the month it
ran. One that has already slipped goes back to its day at its next
installment, and a re-activated one resumes on that day. A frequency that
carries its own count (every
two weeks, twice a month, quarterly, twice a year) takes no interval: sent
with none, or 1, it is that count, and with another it is refused, naming
the word; daily, weekly, monthly and yearly take one. In auto-enter mode the
app posts the document on its date: it enters every due auto-enter memorized
bill and journal entry when it opens and once a day while it runs, every
missed date in order. When the company has a password, those runs wait until
someone has signed in since the app started (a sign-in the app remembers
counts, an API token does not), so nothing is entered behind the sign-in
screen; the audit log names the person signed in for what a run enters (an
administrator or bookkeeper), or "scheduled run" when nobody signs in (a
company with no password yet, the cron script) or only a read-only or an
accountant's sign-in has. The Memorized Transactions page says what was
entered while
you were away (or, when a run entered nothing and had to skip something,
what could not be entered and why), and a box under its list (on by default)
turns the automatic runs off, for a server that runs
`scripts/run_recurring.py` from cron instead; the script catches up both
kinds the way Enter All Due does, every missed installment and occurrence,
where it used to make one installment of each recurring invoice per run.
Recurring invoices are not entered on their own: as before, they wait for
Generate, Enter All Due or the server's cron, so a company upgrading with an
overdue schedule finds no invoices it did not ask for, and the Memorized
Transactions page lists their mode as Generated, with a note naming those
three. Enter All Due, pressed by you, enters every due schedule of both
kinds, every missed date in order however far behind; when another window
generates one of those installments first, it keeps going, as the cron
script does, so the installments after it are still made in the same run.
In remind mode it waits on the due list until
Enter now posts it, dated the scheduled date or today, and moves the
schedule on. Enter now names the occurrence the page showed: two windows
pressing it for the same occurrence post one document, and a page left open
after that occurrence was entered elsewhere (another window, Enter All Due
or the app's own run) is told so, with the schedule's next date, and posts
nothing, rather than entering the next one early; dated today, it still
enters that occurrence. Asked for an occurrence later than the schedule's
next one, Enter now says that occurrence is not the next one, naming both
dates, and posts nothing. The schedule is moved on in one guarded statement
before anything posts, and a recurring invoice's installment is claimed the
same way, so two generations at once (two windows, Generate, Enter All Due
or the cron script) invoice a period once: Enter now on an installment just
generated says so and makes nothing, and a run leaves it to the other. When
the company file is busy saving another change, Enter now says so and asks
you to try again in a moment, and a run names what it could not enter.
Enter now on an ended schedule says it has ended, paused or not, and so do
Resume on one and ticking Active again on a recurring invoice past its end
date ("move or clear the end date first"), which names the end date the
schedule has, and an end date sent with it that is still too early; an
edit that moves the end date past the next date, or clears it, in the same
request goes through. A schedule that ended by its number of times says to
raise the number of times instead. A memorized
bill's number follows one rule: left blank, each bill is numbered the date
and the vendor's initials, as Enter Bill numbers one; a number on the
template is used as it is on the first bill and -2, -3 after that, since a
vendor's bill number cannot repeat, which is why Memorize from a bill leaves
the number blank. Schedules pause and resume (resuming skips the dates
missed). The new Memorized Transactions page, beside Recurring Invoices in
the sidebar, lists every schedule in one place, recurring invoices included,
with type, next date, how often, mode, last entered, status and amount, a
Due filter, and Enter now, Pause, Edit and Delete on each; Memorize on a
bill's page or a journal entry's page starts a memorized one from it. API:
`/api/memorized` (GET, POST, PUT, DELETE; a recurring invoice's row has
the mode `generate`), `/api/memorized/due`,
`/api/memorized/{id}/enter`, `/pause`, `/resume`,
`/api/memorized/enter-due`, `/api/memorized/entered-recently`, and
`/api/recurring/{id}/generate` to enter one recurring invoice now (both
Enter now routes take the `occurrence` the page showed); the setting is
`auto_enter_on_open`. IIF carries no memorized transactions, so the
QuickBooks guide says to rebuild them here. Migration `c9e5a7b3d1f2` adds
the two tables.

### v2.31.0 — Split expenses, lines that move, and stock that comes back

**From the release gate.** Voiding a credit memo's return took the stock out at
the return's own cost without re-averaging what was left, so once a dearer
receipt had come in the ledger's Inventory and the stock valuation (quantity
times average cost) parted ways, and a residue stayed in Inventory with
nothing on hand; the average now follows the value that leaves, and the two
figures stay one figure. An expense's `memo` over the API read as its first
line's memo once lines carried their own; it is the memo typed on the expense
again. Typed Cc addresses plus the always-copy settings over fifty together
were refused as a failed send blamed on the SMTP settings; the limit's own
sentence answers (400) before anything is rendered. Insert line below left
the keyboard on the page, because the new line's item picker becomes a
type-ahead box a moment after the line lands; the focus now goes to that box.

**A sale's reversal comes off each posting's own accounts at its own cost**
(#622, @ContractorKeith). Voiding a sale, or editing its quantity down,
reversed the cost at the item's current average cost against the item's
current accounts, so a reversal after the item's cost or accounts had
changed left a residue in Cost of Goods Sold and Inventory. The reversal now
walks the sale's own postings, newest first, and takes each off the accounts
it went to at the cost it carried, one reversal movement per posting naming
the one it reverses (`inventory_movements.reverses_movement_id`, migration
`b8d4f2a6c1e3`); a reversal of a sale posted before this release is charged
to the oldest posting, and a piece with no account to post to is logged
rather than dropped. The credit memo return in this release (#641) walks the
same postings.

**The sidebar's bottom link now calls for artists.** The link under the left menu pointed at the discussion asking who uses SlowBooks for real work; it now opens the artists' call to action (discussion #636), which is how the community art repo finds its contributors.

**Reinstalling the same version replaces the program file** (#643). Running
the Windows Setup over an install of the same version replaced every file
except SlowBooksPro.exe, because every build of a version carries the same
version number and the installer took the program file already there for the
one it was shipping. The installer now replaces the whole program folder
whatever version is already there.

**Job reports read a timestamp on the local day** (#644). A cost-to-complete
forecast and a change order approval are stored with a UTC timestamp, and the
construction reports compared that timestamp's UTC date with the local "as of"
day. On a US evening, once it was already the next day in UTC, a forecast
typed that night made the WIP Schedule as of the same day warn that it was
entered the next day (on the screen, in the CSV and in the PDF), and a sub
change order approved that night dropped out of Commitment Status as of that
day; the change order log and the WIP revised contract read an owner change
order's approval the same way. The timestamp is now read as the local calendar
date before the comparison. Also, a commitment that is partly billed now reads
as sent on an as-of day before its first bill, the way a commitment closed by
a later bill already read as open before that bill.

**The About dialog credits the artists** (#635). The community art repo,
github.com/VonHoltenCodes/Slowbooks-Pro-Art, promises everyone whose art
ships in the app a credit in About, and About now keeps it: an Artists
section with each artist by name, the pieces they made and where the app
uses them, and a link to them when they gave one. The credits are a small
file shipped with the app, app/static/artists.json, so adding an artist is
one line in that file and not a code change. Until the first piece lands the
section says the turtle is waiting for its first artist and links to the art
repo.

**A credit memo's lines take their own class and job** (#640, from
Discussion #633). A memo's lines all posted with the header's class, so a
credit against work in two divisions had to be two memos. Each line now
takes a class and a job of its own, as an invoice line does: the form draws
the Class cell on every line once the company has a class to choose, the
reversal of that line's income posts with the line's class and job, the tax
and receivable lines with the memo's, and a line left without one takes the
memo's. P&L by Class and P&L by Job net the credit against the division or
job that earned the sale, and a void nets each back to zero. The memo's
entry now carries the memo's own job as well (it carried the class only), so
a memo with a job counts on P&L by Job, and a job on a memo line alone keeps
the job with its customer the way a job on an invoice line does. Over the API
a line's `class_id` and `job_id` are optional, and a line's job must be the
customer's, as the memo's is.

**A sale past what is on hand says so** (#642). An invoice or a sales
receipt that sold more of a stock item than was on hand posted without a
word, and the count went below zero where only the Items page's To reorder
list showed it. The sale still posts as before (some businesses enter the
receiving bill after the sale), and now the response carries a `warnings`
list in plain words ("Widget: 2 on hand; this sale takes it to -4."), which
the invoice and sales receipt forms show as a toast after the save; a line
shows "N on hand" under its quantity once a stock item is picked. A new
setting, Refuse a sale of more than is on hand (`inventory_refuse_oversell`,
off by default, under Invoice Defaults), turns the warning into a refusal
naming the item and how many short, with nothing posted. The Items page
shows a count below zero in the warning colour with a note saying what it
means, and the item's form, Adjust and History say it in words.

**Hide zero balances on the statements** (#625, from Discussion #354). The
Profit & Loss and the Balance Sheet already left out an account with no
activity, but one that had activity and nets to $0.00 for the period, or a
Balance Sheet account that ends at $0.00, still printed. The Profit & Loss,
the Balance Sheet, the Trial Balance and the P&L Detail now have a Hide zero
balances checkbox (`hide_zero=true` on the report, its PDF and its CSV) that
leaves those accounts out, a parent whose every sub-account is zero with
them. The totals are the same either way, and the choice rides on the
address, the saved report and the PDF and CSV links the way the Basis choice
does. On the Trial Balance a row goes when both of its columns are empty, so
each column still adds up to its total. Off unless asked for.

**An item of your own can be a discount** (#639, from Discussion #633). A
negative line on an invoice or sales receipt was allowed only on the item a
QuickBooks Online discount came in on; a discount item made here was refused
with "rate must be non-negative; use a credit memo for refunds". The item
form (product and service items) now has "Discount or concession: may carry
a negative price". A line on such an item takes a negative price, and the
amount comes off the item's income account on the accrual and the cash basis
both. Every other item, and a line with no item, is refused as before, in the
same words. The item a QuickBooks Online discount came in on is marked the
same way by the migration, so one rule covers both; the items CSV export and
import carry a Negative Price column, and an IIF item of type DISC imports
marked. API: `allows_negative_price` on the item create, update and response,
with `is_discount` saying whether a line on the item may go negative.

**Choose where each piece of a pay run posts** (#620). Settings → Payroll
Accounts names the account for net pay, gross wages, employer payroll taxes
and each payable (federal and state withholding, Social Security, Medicare,
FUTA, SUTA, other deductions). Each defaults to the seeded account payroll
always posted to, so a company that chooses nothing posts exactly as before.
Net pay can go to a bank account or to a liability such as 2300 Payroll
Liabilities, for wages paid later: the run then credits 2300 instead of the
bank, the Balance Sheet carries the wages until they are paid, and the
payment is recorded against 2300 when it goes out (a check or register entry,
a bill payment, or a journal entry). The save refuses an account of the wrong
type, an inactive one, a credit card or a control liability, in a sentence
saying what the piece takes; a chosen account that stops working refuses the
pay run rather than posting somewhere else; and voiding a run reverses the
accounts it posted to, whatever the settings say by then.
`GET /api/settings/payroll-accounts` lists the pieces with their chosen and
default accounts.

**A credit memo that takes stock back puts its cost back too** (#641, from
Discussion #633). A credit memo for a returned stock item put the quantity
back on the shelf but posted nothing for its cost, so Inventory and Cost of
Goods Sold kept the sale's cost after the goods were back. The return now
posts the cost of what came back, Dr Inventory / Cr Cost of Goods Sold, in
one entry per memo that the stock movement links to. When the memo names the
invoice it credits, the cost is the original sale's, taken off that invoice's
sale postings newest first and put back on the accounts those postings used,
even if the item's cost or accounts have changed since; a memo naming no
invoice uses the item's average cost and the item's accounts. Voiding the memo
reverses the entry and the movement together. The Profit & Loss's cost of
goods and Sales by Item's COGS column both net the return, so the margin on a
sale that was partly returned is what it should be. Returns posted before this
release stay as they are: one journal entry moving the cost from Cost of Goods
Sold to Inventory corrects a past return.

**Cc and Bcc addresses are read the way a mail program reads them** (#611,
@ContractorKeith). A pasted `"Smith, Bob" <bob@x.com>` is one address and its
display name is dropped, so the Cc header carries bare addresses; an entry
that is not an address (non-ASCII, malformed, or text left over from a
header-injection attempt) refuses the send (400) or the settings save (422)
naming it, rather than sending to a guess; an address both typed and in the
always-copy settings is copied once; the always-copy settings are stored
normalised, at most 500 characters of addresses; and the log row is cut to
its column so a long list can never fail the send's own log entry.

**Move lines and insert one in the middle of a document** (#604, from
Discussion #595). On an invoice a line could only be added at the end or
removed, so a missed line meant removing and re-keying everything below it.
Every line form (invoices, estimates, sales receipts, credit memos, recurring
invoices, bills, purchase orders and vendor credits) now has Move line up and
Move line down buttons on each line, a handle to drag a line with the mouse,
and an Insert line below button that opens a blank line right under that one.
The totals follow, and the order is saved with the document and used on the
screen, the PDF and the print view. Saving a document whose lines only
changed order now keeps the new order, and the order is always the order the
lines were sent in.

**Enter Bill shows the vendor beside the form** (#601, from Discussion #593).
Pick a vendor on Enter Bill (or open a bill to edit it) and a panel beside the
form shows what you owe them, their last five bills and payments, and their
notes, with a link to the vendor's page. The owed figure is the vendor page's and the A/P aging's.
It fills in while you work, never holds the form up, clears when the vendor is
cleared, and sits below the form on a narrow window.

**Receipts on register entries** (#600, from Discussion #593). A check, a
deposit or a card charge typed straight into a bank or card register can now
take attachments, the same Attachments panel a journal entry has. Open the
entry with Edit and attach the receipt, the scanned check or the deposit
slip; it stays with the entry, even if the entry is later voided. A firm that
keeps a paper trail for every transaction no longer has to re-enter register
entries as Expenses just to hold the file.

**An expense can be split across several accounts** (#599, from Discussion
#592). A lump sum on a bank or card statement often covers several kinds of
cost, and the Expense form could only record it against one account. The form
now has a Lines grid like a bill's: each line takes an account, a memo, a job,
a cost code and cost type, a class and function, and a billable mark of its
own, and a running total shows the amount paid against what the lines add up
to. It starts with one line, so a plain receipt is entered as before; Scan
Receipt fills that first line. The lines must add up to the amount paid, and
a line of 0.00 is refused. The ledger takes one debit per line and a single
credit to the account it was paid from, so reports by account, job costing and
the printed check's stub (which lists every line) see each share; voiding
reverses all of them. Through the API, `POST /api/expenses` takes `lines`, and
the older single `expense_account_id` still works as one line.

### v2.30.0 — Construction from estimate to pay application, and reports on a cash basis

**Stripe payments record again.** On the current Stripe library a paid
checkout's webhook was answered with an error and the payment was never
recorded, and Check payment status failed the same way; the library's
objects are no longer dictionaries, and the provider read them as one. Both
work again, and with the clearing account on the processing fee is booked
from the payment as #583 intends.

**Sales Tax on the cash basis ties to Sales Tax Payable on a pay
application's invoice.** Such an invoice holds retainage back, so its amount
due is less than its total; the report prorated each payment's tax on the
total while the payment's own cash lines prorated on the amount due, and the
two disagreed. The report now prorates the way the cash lines do, to the
cent.

**The desktop window's zoom level comes back after a relaunch, and Ctrl +
wheel zooms on a Mac.** On Windows the level was kept but not applied again
at the next start; on a Mac only the keys zoomed. Ctrl + wheel (Cmd on a
Mac) now steps the same remembered level on both.

**Contact support's Send opens the mail program on a Mac.** The desktop
window on macOS dropped the mailto link; it now hands it to the launcher,
which opens the default mail program with the message written.

**A wide report's scrollbar stays in view.** A report wider than its window
(the WIP Schedule) scrolled sideways only from a bar at the table's foot; a
bar at the bottom of the window now scrolls it while the table is in view.

**Smaller:** the note that lists an entity type's equity accounts names them
the way the company does (no numbers when account numbers are off); the
Commitment Status report as of a date shows a commitment's status on that
date, not today's "closed" on a row that was still open then; the refusal to
edit a locked contract sum says an *approved* change order locks it; at 1280
wide the top bar keeps the company name beside Contact support (the clock
and the brand's year give way below 1366).

**A lien waiver per payment and job** (#576). A payment applied to two jobs'
invoices used to get one waiver, for whichever invoice came first, and a
payment whose first invoice carried no job waived the whole payment. A waiver
is now for one payment and one job: its amount is what the payment applied to
that job's invoices, `POST /api/lien-waivers` takes a `job_id` (needed only
when the payment pays more than one job; refused with 400 `job_required`
naming the jobs otherwise), the Waivers screen makes the waiver for its own
job, and one of each type is allowed per payment and job.

**Voiding a pay application is all or nothing** (#576). The application's
deposit payment, its invoice and the application itself are now voided in one
transaction. Before, the deposit was voided and saved first, so when the
invoice's void was refused (a payment still applied, the closing date) the
deposit stayed voided under an application and invoice that still stood. A
refusal now leaves all three as they were.

**Sales Tax report on the cash basis; retainage on bills; payroll stays on
both** (#483, step 4). Reports > Sales Tax takes a Basis (Company default /
Accrual / Cash), like the Profit & Loss does. On the cash basis it lists the
tax collected on the money received in the dates, each payment taking its
invoice's sales, taxable amount and tax in the proportion paid, and checks
that against Sales Tax Payable on the cash basis; credit memos are not
counted (no cash came for them). The accrual report is unchanged, and
paying the tax is the same either way. A bill that holds retainage is
expensed on the cash basis only as it is paid: paying the net recognizes
that much of each expense line, and the held part is recognized when it is
released (so the Balance Sheet carries no Retainage Payable on the cash
basis until then). Pay runs read the same on both bases: payroll
liabilities stay on the cash Balance Sheet, as other current liabilities do.

**Cash basis: editing a paid invoice or bill, and a basis for journal
entries** (#483). Changing the lines of an invoice or bill that has been paid
now moves what its payments counted on the cash basis to match: if half of an
invoice was paid and you move its amount from one income account to another,
the cash Profit & Loss moves the paid half too, on the day of each payment.
(A payment on or before the closing date keeps what it has; void and re-enter
the payment to change that.) An invoice brought in from QuickBooks Online and
then edited now shows its income on its payments' dates on the cash basis
instead of dropping out of it. The journal entry form has a new Basis choice,
Both (as before), Accrual only or Cash only: enter an opening Accounts
Receivable or Accounts Payable balance as Accrual only and it stays on the
accrual reports without sitting on the cash Balance Sheet. Editing or voiding
the entry keeps its basis.

**Reports on a cash basis, step 2** (#483, @ContractorKeith). The Trial Balance,
General Ledger, Profit & Loss Detail, an account's drill-down (and a P&L
total's), P&L by Class, by Job and by month, Job Profitability and the
nonprofit Statement of Activities, Statement of Financial Position and
Statement of Functional Expenses now take the same Basis choice the Profit &
Loss and Balance Sheet have (Company default / Accrual / Cash, beside the
dates; `?basis=cash` on their API, PDF and CSV, which say "cash basis" when they
are). On the cash basis a ledger line is the payment's: income on the day the
money came in, in the paid proportion, and the balance brought forward is
read the same way, so each account still foots. A drill-down opened from a
cash-basis statement stays on the cash basis, so its lines add up to the
figure clicked. Without a choice they follow Settings → Company → Report
Basis, and on the accrual basis they print what they always did. The
Accountant's Package now sends its General Ledger and Trial Balance on the
company's basis too, and its README says so.

**Trial Balance: balance-sheet accounts at their balance as of the end date**
(#597). For a date range the Trial Balance showed only that range's activity
on every account, bank and card accounts included, so it never matched a
QuickBooks Trial Balance side by side. Asset, liability and equity accounts
now show their balance as of the end date, on the debit or credit side; income,
cost and expense accounts still show their activity from the start date. The
earlier periods' net income, which the balance-sheet rows carry and the
income and expense rows leave out, prints as one "Net Income (prior periods)"
row so the debit and credit totals still agree. The screen, the PDF and the CSV
(the accountant's package included) all read the same way.

**A retainage set by hand on a converted bill survives a re-post.** Editing
the lines, tax or date of a bill converted from a commitment re-computed the
retainage held at the commitment's rate, wiping an amount that had been set
by hand through the API. A hand-set retainage now stays; one that was just the
commitment's rate on the old lines still follows the rate to the new lines.
The bill records which it is (`retainage_manual`), so a derived retainage
keeps following the commitment's rate after that rate is changed, instead
of being mistaken for a hand-set figure and frozen at the old percent.

**The desktop window zooms.** Ctrl +, Ctrl - and Ctrl 0 (Cmd on a Mac) now
make the desktop app's window larger, smaller or normal again, and the level
holds across pages and launches; Ctrl + wheel zooms the window and its document
windows. They did nothing before. (#597)

**Void can record a refunded processing fee.** Voiding an online payment
leaves the fee its provider kept booked as an expense, and the Void button
gave no way to say the provider had given the fee back. The Void confirm on
an online payment with a booked fee now has a "The provider refunded its
processing fee" checkbox, unticked by default; tick it and the fee is
reversed with the payment. Other payments' Void confirm is unchanged.

**An inventory sale's cost posts to the item's COGS account** (#622, from
Discussion #621, @Financials911). Selling a stocked item posted the cost side
to the first cost-of-goods account in the chart (5000, the parent) whatever
the item's *Expense or COGS Account* said, so the Profit & Loss showed it as
"Direct postings to Cost of Goods Sold" instead of under the sub-account. The
sale, its void, and a QuickBooks Online document that becomes ours now post to
the item's account when it is a cost-of-goods account; an item with no account
or an expense-typed one posts to 5000 as before. Entries already posted stay
where they are: one journal entry moving them from 5000 to the right
sub-account cleans up history.

**CC and BCC on email** (#611, from Discussion #607, @PeterRichardsonGolf).
The Email Invoice dialog now has CC and BCC boxes, comma-separated. Settings →
Email (SMTP) gains *Always CC* and *Always BCC*: addresses copied on every email
the program sends, so statements, collection letters and acknowledgments carry
them too, with no dialog. Bcc is never written into the message, only the
envelope. Recent sends shows the copies under the recipient. API: `POST
/api/invoices/{id}/email` accepts `cc` and `bcc`.

**One bank deposit, several donors** (#606). A deposit that is really two
gifts, $750 each from two donors, can now be booked as one entry with a line
per donor. In the register's + Entry (and in the bank feed's add call, which takes
the same lines), every line has a Customer (Donor in a nonprofit) and, when you use
classes or funds, its own Class or Fund; "+ New Customer" in the list adds
one on the spot. The deposit matches the bank statement, and each donor's
year-end giving statement and gift count include their own line; voiding the
deposit takes them all back out. The register shows the donor's name on a
one-donor deposit and "Split (2 names)" on several. An entry you booked
earlier can have a donor added from its Edit form, a reconciled one included,
since a name changes no amount, and each donor line can print or email the
same thank-you letter a donation gets. Check images on a deposit are not part of
this.

**Print the checks Pay Bills wrote in one go** (#603, from Discussion #594).
Each check used to print on its own from its payment. Pay Bills by check with
Check # left blank now leaves the checks waiting, and **Bills → Print Checks**
(offered right after Pay Bills, too) lists them for a bank account with a tick
box on each. The starting check number is filled from the next number on the
account and numbers the ticked checks in order; any row's number can be
adjusted. Print Checks makes one PDF with a check to a page on the usual
voucher stock, gives each check the number it printed with, and takes it off
the list. A number the account already has, a void payment and a payment
dated in a closed period are refused. For API users:
`GET /api/checks/unprinted` and `POST /api/checks/print-batch`. The list
opened after Pay Bills ticks only the checks it just wrote, opened from the
Bills page it ticks none (Select all is there), a payment dated on or before
the closing date is not listed at all, and a number the account already has
asks the same keep-or-edit question a single check does, under the same
warning setting (`?allow_duplicate_check_number=true` keeps it).

**A bill's memo prints on the check that pays it** (#602, from Discussion
#594). A check printed from a bill payment had a blank memo line, though the
bill carried the account or invoice number the vendor wants on it. Paying
one bill now carries that bill's memo onto the payment and its check; paying
several bills leaves the memo for you to type, and a memo you enter yourself
is never replaced. The payment's view has a **Check memo** field you can edit,
and Print Check saves it before the check opens.
**A register line opens over the register.** Clicking a line in a bank or
card register used to go to the posting's own address, which drew that
document's whole list first: for a journal entry, every journal entry in the
company, which took minutes on a file with years of imported history. Closing
it then left you on Journal Entries instead of the register. The line's
posting now opens over the register, with nothing else loaded, and Close
comes back to the register where you were; voiding or saving it there brings
you back to the register too. Opening a document by its address
(a bookmark, a report's drill-down) is unchanged; Ctrl-click still opens it
in a new tab.

**Balance Sheet: sub-accounts beneath their parents, a total per family**
(#565, from Discussion #562, @ContractorKeith). The Balance Sheet now prints a
parent's family together, as the P&L has since #416: the parent as a heading,
its sub-accounts indented beneath it, the parent's own postings as "Direct
postings to …" and a "Total …" for the family, inside each Current / Fixed /
Other Assets and Current / Long Term Liabilities group and in Equity — on the
screen, in the PDF (the accountant's package included) and in the CSV, with
and without the prior-year comparison, where each family total has its
Current, Prior Year, $ Change and % Change. A parent gathers only the
sub-accounts that print in its own group; a sub-account placed in another
group by its detail type prints there, flat, by its full path. Every line
prints once, no figure or subtotal moves, and a chart with no sub-accounts
prints exactly as before. An account line still opens its transactions; a
family's total has no drill-down yet (the #479 set drill-down reads a
period's lines, not a balance to date). The API's `groups[].tree` and
`tree.equity` carry the rows; the flat lists stay for the drill-down.

**Vendors: search the list, and a payment history per vendor with CSV export**
(#569, @ContractorKeith). A vendor's page has a **Payment history** section
in place of the two payment lists: every bill payment, direct expense (Enter
Expenses) and bank-feed entry to the vendor in one list, oldest first with a
running total, over a date range that starts as year to date (From / To /
Show), the paid-in-range and all-time figures above it, voided rows listed,
marked and left out of the totals, and **Export CSV** for the same range
(Date, Type, Method, Check # / Ref, Amount, Running total, Paid from, Notes,
Voided, a Total paid line last), now built by the CSV export service like the
other exports. `GET /api/vendors/{id}/payment-history` answers the page
(`rows`, `totals` by kind, `total`, `all_time`; no dates is year to date, one
date leaves the other side open); `GET /api/vendors/{id}/payments/csv` keeps
its address and its all-time default. The bill-payment subtotal is summed by
the 1099 query itself, so for a 1099 vendor over a year it is the vendor's
1099-NEC figure before the register entries that name the vendor (#590, which
count by their expense lines from bank accounts only); direct expenses, which
the 1099 leaves out, are subtotalled beside it rather
than folded in. The list's search box
(#335) stays as it was — name, company, phone, email — and the list route's
`search` parameter now covers the same four fields instead of the name alone.

**Record a provider payout and match it to the bank feed** (#584,
@ContractorKeith). Step 2 of the clearing-account feature (#583, merged). When a
provider's net payout lands in the bank, the deposit line in the bank
review queue offers a **Payout…** button for any deposit whose payee names
Stripe, PayPal or Square and whose provider has clearing on. It posts a
split that credits the provider's clearing account for the gross, debits
Merchant Account Fees for any fee the payout still carries, and leaves the
net in the bank — through the normal Add path, so the statement line links
and voids/reconciliation behave like any bank entry. A payout that arrives
without a feed is recorded from the provider's settings card with **Record
payout** (date, bank account, net, fee), which posts the same split. Each
provider's card shows the clearing balance awaiting payout with a link to
its register; it returns to zero once every payout is in.

The **Payout…** modal prefills the gross with the deposit's net and shows the
clearing balance for information (clearing already holds the net of every
capture not yet paid out), and both the review-queue and the Record payout
routes refuse a gross larger than the clearing balance. A deposit that
already matches an entry in the books, such as a payout recorded by hand
whose feed line arrives more than five days later, offers Match instead of
Payout… and the payout route refuses to post it twice. Refunds and disputes
(a void of an online payment) reverse against the provider's clearing
account, never the bank, and a void leaves the fee the provider kept booked
as an expense unless it is marked `fee_refunded`. Voiding a payment that
looks already paid out warns (`warning` on the void response). Turning
clearing off while the account holds money warns and keeps payouts available
until the balance is zero; new payments then go to Undeposited Funds. The
manual route rounds its amounts and names the provider "PayPal", not
"Paypal".

**A clearing account per payment provider, with the processing fee** (#583,
@ContractorKeith). Each online-payment provider (Stripe, PayPal, Square) gets
an optional clearing-account setting, off by default. With it off, a captured
payment keeps posting DR Undeposited Funds / CR A/R exactly as before. With it
on, the payment posts DR the provider's clearing account (an asset created on
demand — "Stripe Clearing" / "PayPal Clearing" / "Square Clearing", or an
account you choose) / CR A/R gross, and the processor's fee posts DR Merchant
Account Fees / CR clearing in the same entry — so the clearing balance is the
net payout you'll later see land in the bank. The fee comes from the Stripe
balance transaction, the PayPal capture's `seller_receivable_breakdown`, and
the Square payment's `processing_fee`; when it isn't known yet (Square can
report it late) only the gross posts. A clearing payment doesn't appear in
Make Deposits, because it isn't in Undeposited Funds. Settings → Online
Payments carries the toggle and the two account pickers on each provider's
card. (Recording the payout and matching it to the bank feed is step 2, #584.)

**Account numbers optional on the chart** (#567, @ContractorKeith). From
support: many businesses no longer number their accounts. Settings → Company
has a new box, **Use account numbers**, ticked by default so a company that
leaves it alone sees no change; the setup wizard asks a new company. Unticked,
every picker, report and PDF names an account by its name alone instead of
"6100 - Name", the Chart of Accounts and the Trial Balance hide the Number
column, accounts list by name within their type, the quick-add panel leaves
the number off, and a chart import brings a row with no number in unnumbered
instead of handing it the next free one. A number is optional on the account
form either way (as since 2.23.5), an account keeps the number it has, and
ticking the box again with unnumbered accounts in the chart is allowed: those
simply show no number until given one. The seeded chart keeps its numbers, and
the control accounts (1100, 2000 and the rest) are still found by theirs, so
nothing posts differently. One label helper on each side decides
(`app/services/account_label.py`, `accountLabel()` in `utils.js`); the pages
that built "6100 - Name" by hand now go through it. Every PDF and message follows
it too: the drill-down and adjusting-entries PDFs (and the drill-down's file
name), the nonprofit functional-expenses PDF, the bank reconciliation PDF, the
closing-date exceptions report, the control-account refusals, the pay-run void
notes, the duplicate-check-number warning, the IIF, QuickBooks and chart
imports' notes and the accountant's copy preview name an account by its name
alone when numbers are off. A control account's note no longer says it is "found
by its number"; it says the software relies on the account. The CSVs keep their
number column. docs/migrate-from-quickbooks.md
answers what happens to the starter accounts after a real chart is imported.

**The QuickBooks ledger import warns about a report over 12 months** (#568,
@ContractorKeith). A Transaction Detail by Account export whose period — read
from the title QuickBooks prints above the columns — runs more than 12 months
imports as it always has, with a warning in the result: for a range over a
year QuickBooks prints the balance sheet accounts for the whole range but the
income and expense accounts for its last year only, so the earlier years can
come across half-complete; export one year per file and import them oldest
first. A July–June fiscal year is 12 months and says nothing, and the
adjusting-entries import never warns. If such a file also fails the "does not
balance" check, the first of those errors carries the same explanation. The
migration guide says the same under *One year per file*.

**The register keeps the last entry's date, and the date keys move it**
(#566, from discussion #561, @ContractorKeith). After a post, the register's
entry row and the + Entry dialog open on the date of the entry just posted
rather than snapping back to today — each register keeps its own, for the
page's life (until a reload or sign-in); no preference. In any date box, + and −
step the date a day, T is today, M and H are the first and last day of its
month, Y and R the first and last day of its year — QuickBooks-style keys,
one handler for every date box in the app. The "date far from today"
question (#481) is asked when the date is typed or moved, not again for a
date carried over from the last entry.

**The register's payee box suggests vendors and links the entry to the one
named** (#590, from discussion #561, @ContractorKeith). Typing in the payee
box — the entry row or + Entry — lists the vendors; picking one, or typing a
name that is exactly a vendor's, puts that vendor on the entry (`vendor_id`,
as a bank-feed line has, #491), so the entry shows on the vendor's payment
history (#569). `POST /api/banking/transactions` and `PUT
/api/banking/entries/{id}` take `vendor_id`, or match the payee by name when
it is left out, and the entry comes back with `vendor_id` and `vendor_name`.
A name that is not a vendor stays text and is still offered "Add as a
vendor?". A linked entry counts toward the vendor's 1099-NEC and the 1099
Summary with its bill payments, under the same rules (1099 vendor, NEC type,
$600, voided left out; a linked bank-feed line counts too); a transfer or an
unlinked payee does not. Only entries paid from a bank account count, never a
credit card (the card processor reports those on a 1099-K), and only the
entry's expense and cost-of-goods lines, not the bank amount, so a payment to
Accounts Payable or Sales Tax Payable adds nothing and a split counts its
expense part. A reprinted 1099-NEC or 1099 Summary for an earlier year can
differ from the one filed when register entries were linked to the vendor
since.

**Reports on a cash basis, step 1** (#483, @ContractorKeith). The Profit &
Loss and the Balance Sheet run on the cash basis: income when the customer's
payment lands, a bill's expense when it is paid, each prorated over the
document's lines (and their items' accounts, classes, jobs and sales tax)
by the part paid, with Accounts Receivable and Accounts Payable at zero and
a payment taken before its invoice as Unapplied Cash Payment Income (a bill
payment's remainder as Unapplied Cash Bill Payment Expense) until applied.
Settings → Company has a Report Basis default (accrual, as before), and
each of the two statements a Basis choice beside Sort by (`?basis=cash` on
their API, PDF and CSV, which say "cash basis" when they are). Every ledger
line now carries a basis; a file from an earlier build is tagged and its
payments' cash lines written once at the next start, in batches of 200
with a savepoint per payment (one that fails is logged by id and skipped,
and start-up warns with the count), on a new index of
`transactions(source_type, source_id)` that took the backfill from minutes
to seconds on a big book. The IIF, QuickBooks report and QuickBooks Online
imports write their payments' cash lines at the end of the import. Online card payments with a clearing account write their cash lines too, as does the payment of a QuickBooks Online sales receipt or invoice once an edit makes it ours; an invoice the old QuickBooks Online connector posted counts once on cash, on its posting's date. An
account, job or cost code that has only cash-basis lines (Unapplied Cash
Payment Income, say) counts as in use and cannot be deleted. When A/R or
A/P is not zero on a cash-basis report (history that came in as journal
entries), a note under the report says so, on screen and in the PDF. The
Accountant's Package follows the company's report basis for its Profit &
Loss and Balance Sheet, and its README says which. Every other report,
the registers, agings and exports stay accrual until a later step. Design:
`docs/design/cash-basis.md`.

**A legal entity type on the company** (#488, @ContractorKeith). Settings →
Company now asks for the company's legal form — sole proprietor, general
partnership, limited partnership, limited liability partnership, LLC (with its
tax election), C corporation or S corporation — kept separate from the company
type, which stays a vocabulary mode. It is unset by default, so an existing
company sees no change until it chooses. The entity type decides which federal
return the company files, and Schedule C is now offered only to the ones that
file it: a sole proprietor and a single-member LLC taxed as one. A company set
to anything else sees one sentence on the Tax Reports page saying which return
applies (Form 1065 for the partnerships and an LLC taxed as one, Form 1120-S
for an S corp, Form 1120 for a C corp, Form 990 for a nonprofit), and the
Schedule C API answers 409 with the same sentence. An unset entity type leaves
Schedule C exactly as before. A nonprofit no longer sees Schedule C; it files
Form 990, whatever entity type it has chosen. The books never change; this is a
tax-form matter only.

**Entity-aware equity accounts, and the 1065 / 1120 / 1120-S returns** (#488
step 2, @ContractorKeith; answers #557). The setup wizard now asks the legal
entity in one "Legal entity" picker (an LLC's tax election folded in), and
the equity accounts follow the entity type while the chart is still the
untouched seed and nothing has posted: a sole proprietor keeps 3000 Owner's
Equity, 3100 Owner's Draw and 3200 Retained Earnings; a partnership (or an
LLC taxed as one) gets Partner 1 / Partner 2 Capital and Distributions; a C or
S corporation (or an LLC taxed as either) gets Common Stock, Additional
Paid-in Capital and Shareholder Distributions in place of Owner's Draw. 3000
and 3100 are renamed in place, 3200 and 3900 Opening Balance Equity are never
touched, nonprofit mode keeps its net-asset seed, and the chart stays yours to
edit. Once the books have a posting, or an equity account has been renamed or
added, changing the entity type reseeds nothing: the save says which accounts
to rename or add under Chart of Accounts. The Tax Reports page now draws the
return the entity files — page 1 of Form 1065, Form 1120 or Form 1120-S, from
the P&L the way Schedule C is, with each form's own lines and summary rows,
exported as CSV under the form's title (`GET /api/tax/return`, `/csv`);
Schedule C is exactly as before for the entities that file it, and a
nonprofit's Form 990 is not drawn. An S corporation sees one note under the
entity picker, on Tax Reports and on Payroll: an active shareholder-employee
takes reasonable compensation as W-2 wages through Payroll before any
distribution, and a distribution is equity, never P&L.

**Contact support from inside the app** (@ContractorKeith). A Help → Contact
support form (beside About, on the About dialog, and on the desktop launcher's
error dialog) lets a user describe what went wrong and press Send. The app
opens the user's own mail program with the message already written to
support@slowbookspro.com — it makes no outbound network call of its own and
ships no credentials, so the no-telemetry promise holds. The email carries an
environment block (version, commit, platform, Python, deployment) and the last
~50 lines of the log, redacted before they are placed: emails, personal and
company names, dollar amounts, account and routing numbers, addresses, phone
numbers and tax IDs become bracketed tokens, while timestamps and stack traces
stay readable. A read-only `GET /api/support/diagnostics` endpoint builds the
block and tail (never 500s on a missing log). Copy diagnostics puts the full
block on the clipboard; long logs are trimmed in the mailto because some mail
clients truncate long bodies; Reveal log file opens the log's folder on native
desktop installs only. Security reports still go through GitHub Security
Advisories. See docs/support.md.

**Construction cost codes by division and section, trade packages, and a
cost type on every line** (#550, @ContractorKeith). The Construction company
type (#431) keeps its contractor accounts, its Sub vendor type with the
1099-NEC transition rule, its light vocabulary (Subcontractor → Sub) and its
Report Center ordering (Job Profitability, Job Budget vs Actual, P&L by Job).
What changes is the cost-code chart. Codes now use the two-level
division/section numbering — a **division** ("03") and a **section** ("03 30 00", with an optional ".16"
extension) — and a section sits under its division automatically. What you
type is normalized on save and import (`033000`, `03-3000`, `03.30.00` all
become `03 30 00`); a code that numbers its own way (`L-100`, `EQ`, a numeric
`1000`/`2000` chart) is left exactly as typed and never rewritten on
re-import. The switch (and the Cost Codes page's **Load standard divisions
and common sections** button) seeds the standard division titles plus a
modest set of common sections — the full numbered section list is far too
large to ship, so you import your own through **Import CSV** (a few hundred
sections with just `code,name` land normalized and auto-parented in one
call). The accounts, cost types and the standard catalog still seed only into
a company that has no cost codes of its own yet, so a custom chart is left
alone.

New **Trade Packages** (Concrete, Drywall and Ceilings, HVAC, Electrical …):
a bid package is what a sub bids and gets awarded, mapped to the cost-code
sections its costs post to. A package can span divisions; a division can be
split across packages. Construction setup seeds the defaults, mapped to the
sections it has. And a **cost type** now rides each budget, estimate,
commitment and bill line — not fixed per code — so a line can carry a type
that differs from its code's default; left blank, it falls back to the code's
type, so existing books read exactly as before. In construction mode the cost
type picker appears beside the cost code on bills, expenses, journal entries,
purchase orders and estimates (Business and Nonprofit forms keep their
columns and post the code's type); job-cost reports roll a section's cost up
under its division.

Not yet: no first-run "what kind of company is this?" prompt (company_type
still defaults to Business; Construction is chosen in Settings, as Nonprofit
is), no new accounting, no change orders / draws / pay applications screens,
no COI-expiry warning in Pay Bills, and nothing references a trade package
yet (bids and commitments are later steps). A prompt to collect a Sub's W-9
is a follow-up. See [docs/contractor-mode.md](docs/contractor-mode.md).

**Construction estimating** (#551, @ContractorKeith). The construction
estimate becomes a commercial bid. Its lines now belong to the trade packages
(#550) a general contractor awards, each line is a plain base line, an
**allowance**, an add/deduct **alternate**, or a **unit price** (a rate card,
not part of the total), and each carries a unit of measure. The **lines stay
at cost** — a base or alternate line with a unit cost is priced at cost, one
with no unit cost is priced direct and never marked up — and the four markups
are their own figures **under the subtotal**, **compounding in order**:
general conditions, then insurance, then fee, then bond, each figured on the
running total before it. On a $100,000 cost of work at 8 / 1.5 / 5 / 1 percent
that is $8,000 GC, $1,620 insurance, $5,481 fee and $1,151.01 bond, a
**$116,252.01 bid total**. The bid total is pre-tax; tax covers the lines and
the markups together (nothing when the customer is exempt), and the Total is
subtotal + markups + tax. An alternate with a unit cost is shown at its
marked-up price, and accepting it adds its cost to the markup base; an
allowance budgets its amount as cost. The customer PDF groups the lines by
package with the section code, then the allowances, alternates (marked-up
price) and unit prices in their own blocks, and foots Subtotal, a row per
non-zero markup, Bid total, Tax and Total. Converting carries the markups onto
the invoice as their own lines, so it foots to the bid.

The GC decides where markup goes: **markup on allowances is off by default**,
a per-estimate flag the form, view, totals panel and PDF show as On or Off;
on, the allowances join the markup base. **General conditions are both**: the
GC percent stays markup revenue and its amount is also budgeted as a cost (a
"GC" General Conditions budget row) when the estimate is accepted. A code
budgeted by hand keeps its own share of the markups instead of pushing it onto
the other rows.

Accepting an estimate **locks the job budget to it**: the estimate seeds the
budget from the bought scope (a row you edited by hand still wins), and those
rows are then locked — the lock is per row, so a manual budget row on a code
the estimate never touched still saves afterward, but a locked row's own key,
or re-seeding the whole budget from another estimate, is refused. There is no
unlock: a new estimate or, later, a change order moves a locked budget, and a
construction estimate that locked a budget refuses changes to its lines,
markups, customer, job, tax and status (moving it back to pending would
re-seed the locked budget); a save that resends the same values still goes
through, and Business and Nonprofit estimates are never frozen.
Converting an accepted estimate to an invoice drops the unit-price lines and
any un-accepted alternate, and now carries each line's cost type and unit cost
onto the invoice so the cost basis isn't lost. A Business or Nonprofit
company's estimate form and PDF are exactly as they were. See
[docs/contractor-mode.md](docs/contractor-mode.md).

**The contract on the job** (#552, @ContractorKeith). Review fixes: the
contract sum and contingency edit freely until the job has billing on the
schedule of values or an approved owner change order, then lock (409 `contract_sum_locked` /
`contingency_locked`; a change order moves them from there), so a sum typed
before the bid is just corrected; the original follows the sum until then;
building from an estimate stamps the original even when the job form set the
sum first, and never gives a cleared sum a new one; contract amounts are
rounded to the cent, schedule values too, the money fields are bounded to what the
database stores, and the fee percent is limited to 0-100; a fixed fee shows
no `fee_period`; and building defaults the job's fee percent from the
estimate's when the job has none. A construction job can
now carry a contract — a **lump sum**, a **GMP** (guaranteed maximum price) or
**cost-plus** — with a fee (a percent of cost or a fixed amount, the fixed fee
winning), a cap (the GMP maximum, or a cost-plus not-to-exceed), owner
contingency and a retainage percent held on each billing. A new **Contract**
tab on the job (construction mode only) shows the terms, summary tiles
(contract sum, changes, fee, cost to date, cap remaining, contingency
remaining, billed to date and %) and the owner's **schedule of values** — the
contract sum broken into billable lines (item no, description, package,
section, kind, scheduled value), which must balance to the contract sum; the
balance stays highlighted until it does. The contract sum is **pre-tax** —
tax is added on each invoice. **Build from accepted estimate**
(`POST /api/jobs/{id}/contract/from-estimate/{estimate_id}`) lays the schedule
out from the estimate — one work line per trade package (its base lines plus
any accepted alternates, **at cost**), an allowance line per allowance, one
line per non-zero markup (general conditions, insurance, fee, bond), and a
contingency line when one is set — and defaults the contract
sum to the estimate's pre-tax bid plus the contingency, so a freshly built
schedule balances to zero. `GET`/`PUT /api/jobs/{id}/contract` read and set the
terms, `PUT /api/jobs/{id}/sov` edits the schedule (a line may be negative — a
deduct alternate nets the sum down), and the Job Profitability report gains the
contract type and, for cost-plus/GMP, the period fee (`fee_period`, the fee on
that period's cost — the job-to-date contract fee lives on the contract
summary). The original contract amount is stamped once and kept while the
current sum moves. Later issues move the contract with change orders (#554)
and bill it as owner pay applications with retainage held and released (#555).
A Business or Nonprofit job is exactly as it was. See
[docs/contractor-mode.md](docs/contractor-mode.md).

**Buyout and commitments** (#553, @ContractorKeith). A commitment is a
purchase order: in construction mode a PO is either a **purchase order** or a
**subcontract**, its lines are its schedule of values, and it bills in parts.
The PO form grows a Type select, a Package, a Retainage % and scope notes
(construction only; a business company's PO is unchanged), and a subcontract
to a vendor not marked a Sub is allowed with a one-line note that its license
and insurance aren't tracked. `POST /api/purchase-orders/{id}/convert-to-bill`
now takes an optional `lines: [{line_id, amount}]` so a commitment bills a
period at a time: each line tracks billed-to-date, the PO goes **partial**
while anything remains and **closed** when fully billed. A commitment with a
**retainage %** holds that percent of each bill's subtotal on **Retainage
Payable** (2050) instead of A/P, so Pay Bills pays the net; a hand-entered bill
can hold retainage the same way. Committed cost now means the **remaining**
(unbilled) open commitment, with **committed total** and **billed to date**
alongside it in the job overview and cost tree. A new **Buyout** tab on the job
(and `GET /api/jobs/{id}/buyout`) pairs each trade package's bid against what
has been bought — variance, committed-remaining, billed-to-date and the
commitments behind it. Bills and Pay Bills carry each Sub vendor's compliance
(`vendor_compliance`: W-9 on file, COI expiry); a missing W-9 or COI warns,
while a Sub whose COI has **expired** is not paid without an override (Pay Bills
confirms and says why; `coi_override` on `POST /api/bill-payments`, recorded on
the payment). Pay Bills also offers a per-bill lien-waiver status saved through
`PUT /api/bills/{id}`, which works on closed-period bills since it posts
nothing. The To Bill dialog takes a **bill date** (default today; `date` on
`convert-to-bill`) used for the bill, its journal and the closing-date check,
editing a converted bill's lines keeps the commitment link and re-tallies
billed-to-date, and the 2050 retainage line carries the job. Editing a converted bill's lines re-computes its retainage at the commitment's rate (an explicit `retainage_held` still wins), and a fully billed commitment refuses a higher amount with a 409 pointing to a sub change order.
Sub change orders (#554), progress billing with retainage released (#555) and
the buyout report / job cost by vendor (#556) build on this. A Business or
Nonprofit company sees none of it. One behaviour change for everyone: a $0
(description-only) PO line is no longer copied onto the bill a PO becomes —
the converted bill carries only the lines with money on them. See
[docs/contractor-mode.md](docs/contractor-mode.md).

**Change orders** (#554, @ContractorKeith). A change order is how a locked
construction budget, its contract and its commitments move, with an audit
trail. It comes in three kinds: a **potential change order** prices a change
the owner hasn't approved yet (and **promotes** to an owner change order,
copying its lines, when they say yes); an **owner change order** revises the
contract; a **sub change order** appends scope to a commitment. Material and
labor are priced separately, each line carrying its own markup — the line's,
else the change order's, else the job's new **Markup on change orders**
contract term (kept between -100% and 1000%) — and allowance- and
contingency-funded work carries **none by default**, with an **Apply markup**
flag on the change order to turn it on (the record, form and PDF show whether
markup was applied). An owner change order's **funding** decides the
effect on approve: *contract* adds its sell total to the contract amount,
appends a read-only `change_order` line to the schedule of values and writes
locked `source=change` budget rows with the sell as revenue (the job-cost
tree's Changes column); *contingency* draws the owner's contingency down (409
`contingency_exceeded`), and *allowance* spends an allowance line at its sell price, markup included, as contingency does (409
`allowance_exceeded`), both writing the cost with no revenue (the contract
already carries that money). A sub change order appends cost-only lines to an
open commitment (a sub change order linked to an owner change order approves only while the cost of the approved ones under it, this one included, stays within the owner change order's cost: 409 `owner_co_budget_exceeded`; the lines a change order added keep their job, cost code, type, quantity and rate on a plain purchase-order edit) on the same job (409 when it is on another job or not open),
and the Buyout tab's *bought* grows. The CO's figures freeze at approve, so
changing a contract term afterward never moves an approved CO. Every effect
reverses on **void** — except a commitment line already billed against, which
refuses the void (409 `co_billed`). **Numbers run per job**: the job number,
then `CO-001`, `PCO-001`, `SCO-001` (a job with no number uses `J<id>`; older
company-wide numbers keep theirs). A **PCO can be revised** (`POST
/api/change-orders/{id}/revise`): the new revision (`…-R2`) supersedes the
prior one, which stays as history, the list shows the latest, and only the
latest promotes. **Several PCOs roll up into one owner CO** (`POST
/{id}/promote` with `pco_ids`): the owner CO carries all their lines and each
PCO links to it. The commitment and budget can no longer drift apart: a sub CO
made from an owner CO approves only once that owner CO is approved, an owner CO
with an approved sub CO under it cannot be rejected or voided (void the sub CO
first), the allowance line must be an allowance on the same job, and a plain
purchase-order edit cannot drop or reprice the lines a sub CO added (409
`commitment_co_line`). A sub CO on a fully billed commitment is still refused
(409 `commitment_not_open`). The budget key widens to include
`source` so a change row sits beside an estimate-locked one. A **Change
orders** tab (construction only, after Buyout) holds the log, the line form
with live cost and sell per cost type, and Submit / Approve / Reject / Void /
Promote / Make sub order / PDF; the Contract tab's Changes tile goes live, its
terms gain **Markup on change orders**, and the schedule of values shows
change-order lines read-only with each allowance's used and remaining. An
owner CO / PCO prints as a document showing the price per line and cost type
(`GET /api/change-orders/{id}/pdf`; cost and markup stay in the app). Progress
billing against the
revised schedule (#555) and the change-order log report (#556) build on this;
a Business or Nonprofit company sees none of it. See
[docs/contractor-mode.md](docs/contractor-mode.md).

**Progress billing and retainage** (#555, @ContractorKeith). A construction job
now bills its owner from the schedule of values with a **pay application**:
each SOV line carries previous, this period, materials presently stored,
completed-to-date, percent complete, balance to finish and retainage, and the
application totals the way the owner's payment application reads — completed and stored
to date, retainage to date, total earned less retainage, less previous
payment applications, the current payment due, and the balance to finish. A new
application pre-fills every SOV line (change-order, allowance and contingency
lines bill like work); only one may be open per job (409). **Approve** creates
the invoice for the job's customer — one line per SOV line billed this period
(this period plus the change in stored materials, so stored is billed when
stored and reversed when it is installed) — and posts a retainage journal: DR
A/R net of retainage, DR **Retainage Receivable** (1150) for retainage held
this period, CR 1150 for any retainage released, CR income per line, CR sales
tax (charged on the billed work at the application's tax rate, defaulted from
the job's accepted estimate). A dropped retainage percent makes the retainage
this period negative and the 1150 leg a credit, and the entry still balances.
The invoice carries `retainage_held`, `retainage_released` and its
`pay_application_id`, and its balance due is the net of retainage. **Owner
deposits** (`POST /api/jobs/{id}/deposits`) hold money on **Customer Deposits**
(2210); a pay application's *deposit applied* draws one down as a payment to
2210 on approve. **Void** reverses the invoice (and 1150), restores an applied
deposit, and leaves later applications' figures correct (they read history
live). On the **sub** side, a commitment's schedule drives a **Sub pay app**
(the existing partial convert) and **Release retainage** writes a bill DR
**Retainage Payable** (2050) / CR A/P, payable through Pay Bills; 422 over what
is held, and voiding it restores the figure. Lien waivers ride on both sides:
an application warns about sub bills still missing a waiver for the period. The
first pay application locks the contract sum and contingency (409
`contract_sum_locked` / `contingency_locked` until it is voided). The
schedule can no longer be rebuilt once billed (409 `sov_has_billings`), a line
cannot shrink below what it has billed (422), and an owner change order whose
SOV line has been billed cannot be voided (409 `co_billed`). A **Pay apps** tab
(construction only, after Change orders) holds the application grid, the totals
block, the deposits block and Save / Submit / Approve / Void / PDF
(`GET /api/pay-applications/{id}/pdf` prints the pay application and its
line detail); the invoice view and PDF show the retainage lines. The
WIP schedule, cost-to-complete, the change-order log, the buyout report and job
cost by vendor (#556) build on this; a Business or Nonprofit company sees none
of it. Retainage to date is net of releases, so the application, its invoice and
account 1150 agree after a release, and it cannot go below zero (422): dropping
the retainage percent and releasing on the same application is refused when the
release is more than what is held. An invoice made from a pay application only
takes note edits (409 otherwise) and is voided with the application, not from
the invoice screen (409 `invoice_belongs_to_pay_application`); only the latest
approved application can be voided (409 `later_application`). Sales tax is on
by default with a per-application *Charge tax* switch, taxes at the estimate's
rate to six decimals (8.875% stays 8.875%), shows on the application so its
current due matches the invoice, and is saved on edit. Stored materials move
into the work on a later application, and an application that only moves stored
material is approved with no invoice. The PDF is titled "Application for
Payment" with a "Line detail" sheet and prints the contract sums as of approval.
Each payment can carry conditional or unconditional, progress or final lien
waivers for the part applied to the job (`POST /api/lien-waivers`, PDF per
waiver). A deposit payment applied to an application can't be voided from
Payments (409), and a negative this-period or stored that would take completed
to date below zero is refused (422). See
[docs/contractor-mode.md](docs/contractor-mode.md).

**Construction job reports** (#556, @ContractorKeith). Five reports lead the
Report Center in a Construction group — the three that always existed (Job
Profitability, Job Budget vs Actual, P&L by Job) and then these five, which show
in construction mode only. Each is a plain grid with a JSON route, a PDF and a
CSV like P&L by Job, and takes an `as_of` date (or `start_date`/`end_date` where
a period applies) with optional `job_id`, `customer_id`, `status` and
`include_closed`; closed and not-awarded jobs are left out by default. The **WIP
Schedule** (`/reports/wip-schedule`) lists each job with a contract — contract
sum, approved changes, revised contract, estimated cost and gross profit, cost to
date, percent complete, earned revenue, billed to date, over and under billing,
retainage outstanding, cost to complete and projected gross profit — on the
**cost-to-cost** method: percent complete is cost ÷ estimated cost (capped at
100%), earned is the revised contract times that percent, over billing (billed −
earned) is a liability and under billing (earned − billed) an asset. Every figure
reads **as of the date** — cost and posted revenue to that day, billing and
retainage from the applications approved by then, the revised contract with the
change orders approved by then. Billing reads
from the approved pay applications' completed-to-date when the job has one by the
date, else the posted revenue carrying the job (`billed_basis` says which). The
report **posts nothing** (see the WIP additions below for the adjusting entry
it computes). **Cost to Complete**
(`/reports/cost-to-complete`) is per job and cost code (division › section) —
budget, committed, actual, cost to complete (remaining budget plus open
commitments), projected final, variance and percent — a tree in JSON, flattened
with the code path in PDF/CSV, the totals footing the clamped rows. **Change
Order Log** (`/reports/change-order-log`) lists every change order per job with
the running approved total (as of the date) and the contract it builds, the
pending total and counts by status. **Buyout Report** (`/reports/buyout`) foots
the per-job buyout across jobs (bid vs bought, variance, committed-remaining,
billed, Unassigned kept), as of the date. **Job Cost by Vendor**
(`/reports/job-cost-by-vendor`) groups posted job cost by the transaction's
vendor (a No vendor bucket for journals and job cost entries) with bills, paid to
date, retainage held outstanding and open A/P; cost is period-filtered, the bill
figures job-to-date and prorated by the job's share of each bill's line amounts
(a split bill splits). Job Profitability and Job Budget vs Actual gained the PDF
and CSV routes they lacked (honouring `customer_id` / `include_inactive`). A
Business or Nonprofit company sees none of these reports. See
[docs/contractor-mode.md](docs/contractor-mode.md).

**Job reports, WIP additions** (#556). The WIP schedule now shows
the **adjusting entry with amounts**, per job and net: under-billings (asset),
over-billings (liability), the revenue offset and any loss provision, balanced,
in the JSON, a second PDF grid, a CSV block and on screen (computed, never
posted). A **cost-to-complete forecast** per job, section and cost type
(`job_cost_to_complete`, migration `b4d6f8a0c2e3`; `GET`/`PUT
/api/jobs/{id}/cost-to-complete-forecast`; report `cost-to-complete-forecast`
with PDF and CSV) defaults to budget − actual floored at zero, and the WIP
schedule's estimated cost is now actual + cost to complete, so a job past budget
with work left reads under 100% instead of 100%. A **projected loss** is booked in
full when projected cost passes the contract: the provision is loss × (1 −
percent complete), added to the entry. A **commitment status** report
(`commitment-status`, PDF and CSV) lists each PO and subcontract with committed,
billed, retainage held and remaining, totals per job, read as of the date
(sub change orders approved later are left out; a closed commitment is open on
a date only if a bill against it is dated after that date). A job with no budget
and no forecast earns nothing: it is listed with a warning and left out of the
totals and the adjusting entry until its cost reaches the contract, when it
reads 100% complete and the loss is recognised once, not twice. The WIP
warnings print under the report on screen, in the PDF and in the CSV.

### v2.29.2 — A register line opens over the register

**A register line opens over the register.** Clicking a line in a bank or
card register used to go to the posting's own address, which drew that
document's whole list first: for a journal entry, every journal entry in the
company, which took minutes on a file with years of imported history. Closing
it then left you on Journal Entries instead of the register. The line's
posting now opens over the register, with nothing else loaded, and Close
comes back to the register where you were; voiding or saving it there brings
you back to the register too. Opening a document by its address
(a bookmark, a report's drill-down) is unchanged; Ctrl-click still opens it
in a new tab.

### v2.29.1 — Profit & Loss Detail, Bill Quick Entry, and pre-printed checks

**Profit & Loss Detail** (#477). A new report on the Report Center with every
transaction under each income, cost of goods and expense account for the
period, in the P&L's order and QuickBooks Desktop's columns (Type, Date, Num,
Name, Memo, Split, Amount, Balance). Each account has a running balance and a
total, sub-accounts sit beneath their parents with a total per family, and
Total Income, Total Cost of Goods Sold, Gross Profit, Total Expenses, Other
Income / Other Expense (when there are any) and Net Income are the Profit &
Loss's for the same dates. It narrows to one class or one job, sorts by
number or name and can leave the adjusting entries out; a line's Type opens
its document. Saves as PDF (landscape) and CSV, and as a saved report; the
CSV's Split column reads -SPLIT- in a spreadsheet as on the screen.

**The Profit & Loss's totals open the transactions behind them** (#479).
Total Income, Total Expenses, Gross Profit, Other Income / Other Expense, Net
Income and each sub-account family's "Total …" are links on the Profit &
Loss and on one class's P&L, and so are the Total column's subtotals on P&L
by Class and P&L by Job (when no columns are left out) and each job's
subtotals on P&L by Job. They open the drill-down for every account behind
the total, on the statement's dates, class, job and adjusting-entries
choice: each line with its account, costs counting against Gross Profit and
Net Income, and a running total that ends on the statement's figure. The
drill-down's address takes `section=` or `family=` in place of
`account_id`, and saves, prints and exports the same way.

**The Profit & Loss by month or quarter** (#478). A Columns choice on the
Profit & Loss (Total only, Month, Quarter) puts one column per month, or per
quarter of the fiscal year, across the period with a Total column, on the
P&L by Class grid: the Account column stays put while the months scroll,
Go to / ‹ Prev / Next › and the arrow keys move through them. Each column is
the Profit & Loss for its own dates and the Total column the P&L for the
whole period; sub-account families and their totals show in every column.
An amount opens that account's lines for that month, a subtotal or family
total the lines behind it. The choice rides on the address and a saved
report; the PDF prints the grid landscape and the CSV is the grid (a
sub-account indented beneath its parent) or one row per account and month. The
prior-year comparison is off while the columns are on. Switching to Month or
Quarter widens the dialog with the sidebar kept beneath it, so the title, the
Dates control and the account names stay in reach. A period too long for 60
columns is refused with a sentence that suggests quarters only to a request by
month.

**The Profit & Loss CSV carries the sub-account families** (#489). The
export was flat, one row per account by its full path, so a spreadsheet lost
the grouping the screen shows. It now prints the screen's tree: the parent's
row, its sub-accounts beneath it (the name indented two spaces a level, still
the full path), "Direct postings to …" and the family's "Total …", with the
prior-year columns when the comparison is on, so the file adds up the way
the screen does. The columns are where they were (Section, Account number,
Account name, then the figures). One class's P&L exports the same way; P&L by
Class and P&L by Job have no families on the screen and stay as they were.

**Bill Quick Entry** (#487). A stack of vendor bills can be keyed one after
another, as paper invoices can on Quick Entry: Vendors & Payables → Bill Quick
Entry, the top bar's Bill Entry, or Quick Entry on the Bills page. Pick the
vendor (or add one; its terms and default account follow), the date and the
vendor's bill number, then each line's account, words and amount, with no
quantity or rate. Save & Next (or Ctrl+Enter) saves the bill, lists it in this
batch's log under the form with a running count and total, and starts the
next bill with the vendor, date and terms kept and the focus back on the
vendor. A line's Account can add a new account and its Item a new item, as on
Enter Bill; the item's "Track quantity on hand (stock)" box shows only for a
product or material, not a service or labor item. A read-only or accountant
sign-in is not offered the page.

**A new account from the bill line that needs it** (#485). The Account cell on
an Enter Bill line has an Add button beside the picker, as the bank register's
category picker has had since 2.27.0. It opens a small panel for the account's
name, its type (Expense or Cost of Goods Sold), an optional parent account and
an optional number; Save adds it to the chart, picks it on that line and
offers it on the others, and the bill stays as it was. The same Add is on
vendor credit lines, on each line when a purchase order is turned into a bill,
and beside the expense's account. Control accounts (Cost of Goods Sold 5000,
Payroll Tax Expense and the others the software posts to by number) are never
offered as a parent. Cancel or Escape closes the panel, and the keyboard goes
back to the picker either way. A read-only or accountant sign-in is not
offered it. Items keep their own quick add, which records an item in a cost
of goods account.

**A new account from the bank-feed review and the Bank Rule form** (#492). The
"Pick a category" picker on a To review line and the Bank Rule form's Category
Account have the same Add button. On a review line the new account is picked
and kept on the line, as a category you choose is, and every other line can
choose it too; on the rule form it becomes the rule's category. Reviewing a
feed with a new kind of expense no longer means leaving the review for the
Chart of Accounts.

**Invoice Quick Entry can add a customer** (#484). Its customer picker has the
New Invoice form's "+ New Customer": type a name the books don't have and the
quick add opens with it filled in, Save adds the customer and picks it, and the
date, terms, PO number and lines you already typed stay as they were. The
focus goes back to the customer picker. Before, the only way was the top bar's
New Customer, which cleared the half-entered screen and left nobody picked.
Read-only and accountant sign-ins are not offered it.

**A payee from the bank feed reaches the vendor list** (#491). Adding a line
from To review (Add, or Add all categorised) posted a bank entry that carried
the payee as text only: no vendor was made or found, and the "Add X as a
vendor?" offer the register's quick row and + Entry form make never came, so
after adding "NT Suites" from the feed the vendor list was empty. Now the entry
names the vendor whose name is the payee (capitals and surrounding spaces
ignored), and a money-out payee no vendor has gets the register's toast — one
toast for all of them after Add all — which makes the vendor and names it on
the entry. The entry stores its vendor (`transactions.vendor_id`, migration
`d7e8f9a0b1c2`), so the vendor's page lists its bank and card entries under
Payments beside its bill payments, its paid this year and all time count them,
and its payments CSV has them as "Bank entry" rows. A transfer names no
vendor, and a line added as a transfer (or with no payee) is not offered as
one.

**A bank-feed rule from the vendor form** (#493). A bank rule could be made
from the feed or on the Bank Rules page, but not from the vendor, and the rule
form had no field for the vendor its model already carried; the rules engine
matched on the account only. The vendor form now has "Bank feed: lines from
this payee post to the default account": ticked, it makes the vendor's bank
rule (its name as the pattern, Contains, any case, the default expense or COGS
account as the category) or updates the one it has; unticked, it turns it off.
A rule that names a vendor also matches the vendor's own name, and a line it
categorises is added for that vendor. The Bank Rule form has a Vendor field,
and the Bank Rules list names each rule's account and vendor (it showed the
account's id).

**Journal entries and deposits open for editing from the register** (#388, @ContractorKeith).
The bank-entry edit shipped in 2.27.0 left two register rows still opening
only their own view: a hand-entered journal entry and a deposit. Now each
has **Edit** too. A journal entry opens the journal form filled in, and
Save rewrites it in place (`GET` and `PUT /api/journal/{id}`) — the same
journal entry, so its attachments, statement match and audit trail stay
with it, its lines replaced the way a bill's are on edit (each old line a
DELETE in the audit log, each new one an INSERT). A deposit opens the
deposit form with its date, slip reference and the payments it took, plus
any still waiting; Save reposts it through the posting helpers (`PUT
/api/deposits/{id}`), moving the payments it no longer takes back onto the
Make Deposits list and stamping the new ones with it, the register, the GL
and the Undeposited Funds balance tying.

Both follow the rules the bank-entry edit set. An entry a completed
reconciliation holds keeps its amount and date — a change is refused with
the same sentence ("…so its amount and date cannot be changed, just as it
cannot be voided. Correct the difference with a new entry.") — while its
memo, reference, class and job can still be corrected. For a journal entry,
a change that moves an amount to a different account in a reconciled period
warns first, with **Change it anyway** in the form, and the audit log
records that the edit moved past that reconciliation. A line whose account
and amount did not change keeps its cleared mark; one that changed loses
it. A void entry is not edited, and only a hand-entered journal entry opens
this way (an imported one keeps to its own correction path).

A deposit imported from IIF or a QuickBooks report, posted with its own
splits to income or other accounts (with their classes and memos), keeps
every line when its date or reference is fixed; changing the payments it
takes is refused with a reason, since reposting it as bank / Undeposited
Funds would take its income off the books. A journal entry edited from a
company with no line classes or cost codes keeps each line's own class and
cost code (the form sends them only where it shows a picker for them). A
deposit whose new total takes the cleared mark off its bank line returns the
same note a journal entry's edit does.

**Pre-printed check stock** (#440, @ContractorKeith, from @KWBsupport's note in discussion
#354). Check stock that already carries "Pay to the order of", the memo and
signature lines and the amount box no longer gets SlowBooks' own labels and
lines printed on top of it. Settings → Check Printing adds a per-company
choice: plain / voucher stock (the default, unchanged) or pre-printed stock,
which prints only the values — payee, amount, amount in words, date and memo
— in their places. A horizontal and a vertical alignment nudge (−0.5 to +0.5
inches, in 0.01 steps) shift the check portion only, not the stubs, to line
the values up with the pre-printed boxes, and a "Print a test check" button
produces a sample check so the nudge can be set on plain paper held against
the stock. The choice applies to every check print path — a bill payment,
Pay Bills, a register check and an expense paid from a bank account — through
one helper, so none of them drift (`check_stock`, `check_nudge_x_in`,
`check_nudge_y_in`; `GET /api/checks/test-print`).

On pre-printed stock the labels, signature rule, company name and address and
check number are left blank in place rather than taken out, so every value
sits where it does on plain stock and one nudge lines them all up; the test
print uses the form's unsaved stock and nudges.

**Recent sends under Settings → Email (SMTP)** (#447, @ContractorKeith, from discussion #422). A
new user's test email failed with "see the email log for the reason" and there
was no email log anywhere in the screens, though every send has always written
one. Settings → Email (SMTP) now shows a Recent sends list: the last 50 emails,
newest first, with when each went out, who it went to, its subject, what sent
it (invoice, statement, test, collection letter, portal), and whether it sent.
A failure shows the reason in a sentence a person can act on, with the raw SMTP
error underneath; there is a "failed only" filter and a "Show more" for older
rows. The list is the administrator's, like backups and API tokens
(`GET /api/email-log?limit=&offset=&status=`, bounded to 200 a page).

A failed test email now says why itself instead of pointing at a log — the
server rejection ("535 Username and Password not accepted") in the response,
plus a short hint for the mistakes new users actually hit: Gmail and Google
Workspace need an app password, and a server that cannot be reached means the
host, port, or Use TLS setting is wrong. The three "see the email log"
messages (the test email, an emailed invoice, a collection letter) now point
at Recent sends, and the collection-letter one names the customer whose send
failed. Recent sends is an administrator's section, so anyone else is told
"An administrator can see why under Settings → Recent sends." with no link to a
page they cannot open, and a failed invoice email's toast is an error toast
(an action toast can now be one, kept at least the six seconds an error stays).
The failure sentences live in one place on the server; the list shows the
sentence the API returns with each failed row (`failure`).

Every saved secret field on Settings — the SMTP and closing-date passwords,
the payment-provider keys and the ACH account — now says under it "Saved.
Shown as a placeholder; type a new value only to change it." The placeholder
stays eight characters so no password length leaks; the same new user had read
it as a truncated password.

**An optional sound when a transaction posts** (#476, @ContractorKeith, asked for by a
QuickBooks Desktop user). QuickBooks Desktop played a short beep when a
transaction saved, and the toast is easy to miss when you're keying a stack.
Settings → Sounds now has a per-user toggle, "Play a sound when a transaction
posts" — off by default, kept per sign-in so it follows you to another
machine. When it's on, a short original chime plays when a transaction
posts — an invoice, bill, payment, sales receipt, deposit, check or expense,
journal entry, transfer, pay run, credit memo or a register row; a delete, a
void, a copy, a saved setting, an error or a warning stays silent. The sound
is generated in the browser (Web Audio, no asset) and guarded, so a browser or
desktop WebView without it carries on quietly; its audio is started inside a
click or key press so the macOS app's WebView does not leave it muted. Every
signed-in role has the toggle, read-only and accountant included; an API
token has no preferences to set.

**A company that charges no sales tax can stop unticking the Tax box on every
line** (#486). Settings → Invoice Defaults has a new choice, "New lines and
items are taxable". It stays Yes, as before, unless you change it. Set it to
No and every new invoice, estimate, sales receipt, credit memo, recurring
invoice and Quick Entry line starts untaxed, and so does every new item, on
the Items form and in the line's quick add. You can still tick Tax on any
line, an item marked taxable still taxes the line it is picked on, and a
tax-exempt customer is still never charged. Through the API, a line or item
sent without `is_taxable` follows the same setting.

**Three races between two sign-ins on PostgreSQL** (#501). Each was a
read-then-write two people could both pass at once; SQLite, with one writer
at a time, never saw them, and behaves as before.

- **One receipt deposited twice.** Make Deposits locked the payment lines it
  took, but read back the copies it had loaded a moment earlier, so a deposit
  that committed while it waited for the lock went unseen and the payment was
  deposited a second time. The locked read now refreshes them: the second
  deposit is told the payment was deposited a moment ago (409).
- **A write-off without a lock.** Writing off an invoice read it plainly, so a
  payment landing at the same moment was lost: the write-off saved the balance
  it had read, and A/R was off by the payment. The write-off (one invoice, or
  the small-balances batch) now locks the invoice the way a payment does, so
  each waits for the other and reads the balance it left.
- **Duplicate bill numbers.** Enter Bill checked a vendor's invoice number with
  a read, then saved, so two sign-ins could both save the same one. A unique
  index on (vendor, bill number) now refuses the second, with the same 409 the
  check gives, on a new bill and on a renumbered one. The upgrade (migration
  `f2e3d4c5b6a7`) first renames any pair already in the books: the earliest
  bill keeps its number and the next becomes "111 (2)", then "(3)". A purchase
  order turned into a bill whose "BILL-…" number the vendor already has takes
  "BILL-…-2".

**A typed deposit can't exceed Undeposited Funds** (#494, @ContractorKeith). Make Deposits with
no payments ticked took the typed total as given and posted it, so a $5,000
figure went into the bank against a single $100 payment waiting to be
deposited. The ticked-payments path already refused a total that disagreed
with what was waiting; the no-ticket path now caps the typed total at the
Undeposited Funds balance the same way and answers 400 with what is actually
waiting when it is asked for more. The cap is the live Undeposited Funds
balance: after a partial typed deposit ($60 of a $100 payment) only the $40
left can be deposited, where the payment used to count in full and a second
$100 deposit drove Undeposited Funds to -$60. Ticked payments, and a deposit
edited from the register to take more of them, are held to the same live
balance, so a payment an earlier typed deposit took part of can't be
deposited in full on top of it.

**The Payables card nets vendor credits** (#495, @ContractorKeith). The dashboard's Payables
card summed open bill balances by itself, so a $100 bill with a $30 unapplied
vendor credit read $100 against an A/P Aging total of $70. The card now reads
the A/P Aging report's TOTAL row, the same source the report page shows and
the way the Receivables card already reads A/R Aging, so the two can no longer
disagree.

**Emailing an invoice with no recipient is a clean 400** (#496, @ContractorKeith). The Email
Invoice request let the recipient default to nothing, but EmailLog.recipient
is NOT NULL, so a send with no address crashed on its own log row — and the
route's own error handler crashed a second time logging the same empty
address — leaving the operator an unhandled 500. The route now settles the
recipient first (the typed address, else the customer's email on file) and,
with neither, answers 400 asking for one before anything is logged. The
statement and collection-letter sends already skipped a customer with no
email, so they were left as they were. The email preview now shows the same
address the send will use (it showed the typed one, empty, while the send
went to the customer's email on file).

**A reconciliation already open, and the typed statement kept** (#497, @ContractorKeith). Starting a reconciliation while one is already open for the account no longer
quietly throws away the statement date and ending balance you just typed
(#497). The server still answers that only one session can be open at a time,
but the screen now says which statement is open and offers to move it onto the
date and balance you just entered; confirm and it does, cancel and the open
session is reopened unchanged.

**A re-run migration import no longer posts a long-referenced journal twice** (#498, @ContractorKeith). The dedupe key was cut to the
reference column's width while the journal was stored with the full reference,
so the two never matched and a second import booked the entry again. The
import now stores the same cut value the key uses, so a re-import recognises
what it already posted.
Books imported before this fix, which hold the full reference, are recognised
too: the already-imported set is cut to the same width.

**A no-charge invoice no longer leaves an empty journal entry behind** (#499, @ContractorKeith). A
$0.00 invoice saved with the no-charge say-so used to post a journal whose
A/R debit and every line were zero, so the invoice pointed at a transaction
header that held no splits — an entry that balanced only because it was
empty. A $0.00 invoice now posts no journal at all and keeps no transaction;
voiding it still works, and an edit that gives it a real total posts the
journal then. An invoice edited down to $0.00 drops its old header the same
way instead of leaving the husk. Editing the amounts of an invoice the
QuickBooks Online import created still posts it exactly once, and the QBO
import paths that post invoices handle a $0.00 one too.

**A late fee now carries the invoice's job, and voiding the invoice takes the
fee with it** (#500, @ContractorKeith). A late fee posts its own A/R debit on top of the
invoice; it used to carry no job even on a job-costed invoice, so the job's
A/R came up short, and a void reversed only the invoice and left the fee's
A/R debit stranded with nothing to apply it to. The fee's lines and its
journal entry now take the invoice's job the same way the invoice's own
posting and void reversal do, and the invoice void reverses every late-fee
posting for the invoice in the same transaction, so the customer balance,
A/R and the job's A/R all return to zero. A fee already voided in the
journal is not reversed a second time, and each fee line comes off with its
own job and class, so a fee posted before this release (with no job) or
before the invoice's job changed never credits the job's A/R.

**A QuickBooks Desktop ledger import checks each total against its own
section.** An account the report lists as two sections no longer trips the
cut-short check: each "Total" row is measured against its own section's lines
(sub-accounts included) rather than every line posted to that account path.

**Enter can move between fields, and the decimal point can place itself** (#480).
Two QuickBooks Desktop data-entry preferences, under Settings → My Preferences,
each person's own and both off unless they are turned on:

- *Enter key moves between fields*: Enter goes to the next field, and only
  Enter in the last field, or on Save, saves. The register's new-entry row
  works the same way: Enter walks Date, Payee, Memo, Ref, Category and the two
  amounts, and posts from the last amount. A type-ahead picker keeps Enter
  only while its list is open, to take the highlighted name; closed, with a
  name chosen or empty, it moves on, so Enter walks a bill's or an invoice's
  lines past their Item and Account pickers. A memo box keeps its new line.
  Off, Enter saves a form and posts the register row from any of its fields,
  as before.
- *Automatically place the decimal point*: 12345 typed in an amount is
  $123.45, and 5 is $0.05. A decimal point you type yourself is kept, and an
  amount already on a saved document is never divided. It covers the amounts
  on every transaction form, Bill Quick Entry and the register row.

My Preferences sits above the company settings and saves each box as it
changes. Every sign-in sets its own, read-only and accountant sign-ins
included; an API token sets none.

**Three QuickBooks Desktop warnings** (#481), available under Settings →
Transaction Warnings for the whole company, each off by default: turned on,
each asks before a save and is answered "save it anyway" when the entry is
right:

- *A date far from today.* A transaction dated more than 90 days in the past
  or 30 days in the future (both numbers yours to change) asks before it saves,
  on every transaction form, Bill Quick Entry and the register's new-entry
  row. A register entry dated 2028-06-30 used to post without a word. January, with the books still
  in last year, is when it catches most. The closing date keeps its own lock.
- *A duplicate check number.* A check, register payment or bill payment whose
  number is already used on that bank account asks: keep the number, or go
  back and edit it. Two entries numbered 5001 used to post silently. The
  number is looked up across register entries, expenses and bill payments on
  the same account; a deposit's number, a void's and anything but digits
  ("ACH", "DEBIT", "rcpt 1187") don't count. Through the API, with the
  warning on, it is a 409 (`duplicate_check_number`) that the request sent
  again with `allow_duplicate_check_number=true` passes.
- *Editing a transaction.* Saving changes to a transaction already saved asks
  first, a journal entry or deposit opened from the register included (a void
  and a delete already asked).

All three are off after the update, in new and existing books alike, so
nothing asks until an administrator ticks one under Settings → Transaction
Warnings (and changes the number of days, if 90 and 30 don't suit) and
Saves.

**An open report redraws after a change, and has a Refresh button** (#482).
With the Profit & Loss open, a $1,000 journal entry left it at the old total
until you went away and came Back. Now an open report runs again by itself
after a change to the books made in this window, in another window or tab of
the same books, or when you come back to the window after a while away. The
figures change in place: the report keeps its scroll position, the keyboard
stays where it was, and a note beside the buttons says when it last ran. Every
report also has a Refresh button beside Close.

Running reports again by itself is a personal preference, "Refresh reports
automatically", under Settings → My Preferences. It is on unless you turn it
off; with it off, the report changes only when you press Refresh. The
Financial Statements Pack, which only opens its PDF, is made again only when
you change its dates or press Refresh, never on coming back to the window.

Two schema changes, run in every company file one after the other:
`transactions.vendor_id` (`d7e8f9a0b1c2`) and one bill per vendor invoice
number (`f2e3d4c5b6a7`), which renames a duplicate already in the books to
"111 (2)"; the head is `f2e3d4c5b6a7`. 641 API operations.
### v2.29.0 — A mid-year payroll switch, the fiscal year, and reports by item

**A company whose fiscal year does not start in January can say so** (#444, @ContractorKeith).
Settings → Company Information has a "First month of fiscal year" choice. Set
it to July or October and the reports' new This / Last Fiscal Year presets and
their default period, the dashboard's profit card and nonprofit mode's year to
date all run from that month instead of January 1. A January start is the
calendar year and nothing changes. Payroll forms (W-2, 941) stay on the
calendar year, as the law requires.

Every report screen opens on the fiscal year to date: the Profit & Loss,
Balance Sheet, Trial Balance, General Ledger, Income by Customer, the cash
flow statement and the rest, the Classes page and the dashboard's "Full P&L"
link with them. Custom starts on the fiscal year's first day, and a Balance
Sheet line's drill-down on the first day of its date's fiscal year. New
Previous / Next period buttons beside the period step by the period shown:
a fiscal year on a fiscal preset (This Fiscal Year back one is Last Fiscal
Year), a month or a quarter on those, and a Custom range by its own length.
The Schedule C and the 1099 Summary stay on the calendar year. The Possible
Duplicates report, asked with no dates, opens on the fiscal year to date too.

The Balance Sheet's Net Income line is still every period's to date: the app
has never posted a year-end close into retained earnings, and this release
does not add one.

**Pulling past customers by service address and last job date** (#436,
@ContractorKeith), the thing a trade asked for as what would move it off
QuickBooks — a ready-made mail list built from what is already in the books,
nothing re-entered. A **Past Customers** view, on the Customers page and in
the Report Center, puts one row per customer with their name, contact, the
service address (the job's `site_address`, else the ship-to, else the
bill-to), the first and last job dates, the job count, lifetime sales (net of credit
memos; a write-off is not a sale given back), and the last job's type and
name. A site address typed on one line ("123 Main St, Austin, TX 78701")
splits into its street, city, state and ZIP like a multi-line one, a ZIP+4
is kept whole, and a third street line goes into Address 2 rather than
being dropped. Filter by last job date, city or zip, state, job type,
customer type, lifetime sales, and active or inactive; a customer whose
notes say "do not contact" ("don't contact", "do-not-contact", "do not
mail", DNC) is marked and left out of the export, and "No contact person on
site" is not read as one. Export a mail-merge **CSV** (address parts in
their own columns, the usual formula guard and BOM) or a **labels PDF** on
Avery 5160 (30 up) with the service address, where a full five-line label
(company, name, two street lines, city/state/ZIP) prints its last line
inside the label. A row opens the customer's page, and the view carries its
filter in the address so it can be bookmarked and saved as a report. The
figures are summed per customer in the database, so a very large customer
list does not run past SQLite's query parameter limit.

**Sales by Item and Purchases by Item** (#435, @ContractorKeith). Two reports
QuickBooks users look for and did not find: Sales by Item, one row per item
for a period with quantity, amount, average price and — for an item whose
stock is tracked — COGS, gross margin and margin % from the item's own
inventory movements, built from posted invoices and sales receipts less credit
memos with voids left out; and Purchases by Item, the same shape from bills
less vendor credits with an average cost (expense lines carry no item, so
purchases are bills and vendor credits only). A line without an item is one
"No item" row so the sales total ties to the P&L's income for the same
accounts. Both narrow by customer or vendor, class and job (a line's own class
or job wins, else the header's), open the documents behind a row, and save as
PDF, CSV and a saved report.

Sales by Item counts only the lines that post to an income account, the
item's own or the default income account, so an item pointed at a liability
(a deposit held, say) is not a sale and the total ties to the P&L's income
from sales; a write-off credit memo, which posts to bad debt, is not a
return. COGS follows an edited invoice: sell 5 and edit it to 3 and the
COGS is the cost of 3, the P&L's figure. A credit memo's return puts stock
back without a COGS entry, so it leaves the COGS column alone as it leaves
the P&L's. Foreign-currency lines count at their invoice's rate, rounded per
line as the invoice's journal is. Both reports open on the fiscal year to
date, and an item's lines open under its name as it is ("Nuts & Bolts").

**Items: quick-add from any line, and an item code** (#445, @ContractorKeith).
An item can be added from the line being typed. The item picker on
an invoice, estimate, sales receipt, credit memo, recurring invoice,
purchase order, vendor credit, bill or Quick Entry line offers **+ Add
item**, which opens a small form in the line: name, code, type, and on a
sale a rate and an income account that follows the type (4000 to 4300 in
the seeded chart). On a bill, purchase order or vendor credit it asks
instead for what the item costs and the cost of goods account it is
recorded in (5000 to 5300 by type; only cost of goods accounts are
offered, since an item bought for a job or resale is never an operating
expense), and whether a product or material is
stock (the box and its label on one line inside the panel, however narrow
the cell), so the new item posts that line where it was asked to and a stock
item is received into Inventory. Save creates the item and picks it on
that line, filled in as a pick would be, and nothing else on the document
changes; a name typed in the picker before taking **+ Add item** comes
along. The form opens only when **+ Add item** is chosen (arrowed to and
taken, or clicked): Enter or Tab on a name that matches no item opens
nothing, and Tab goes on to the next cell. Cancel, Escape and Save put the
focus back on the line's item picker, Save with the new item in it. A
read-only or accountant sign-in is not offered **+ Add item**, since the
server would refuse the save. A picker value that
is not an item (`--`, `+ Add item`) saves as no item. Items also take an
optional code (`items.code`, migration `c4e5d6f7a8b9`), unique when set
without regard to capitals (409 "Item code X is already used by ..."),
shown before the name in every line picker so typing either finds it, on
the Items page with a search box over name and code, in
`GET /api/items?q=` (a typed `%` or `_` is that character, not a
wildcard), and as a Code column in the items CSV export and
import (a code already used is left off that row, with a note). The IIF
export is unchanged: QuickBooks 2003's `!INVITEM` header has no field for
a code.

**A processed pay run can be voided** (#432, @ContractorKeith). `PayRunStatus.VOID` and the
refusal to process a void run were already there, but nothing set the status:
there was no route and no button. Void now reverses the payroll journal entry
the way every other void does — a dated reversing entry, both staying in the
register — reverses the burden Job Cost Entry when the run wrote one, backs the
run's amounts out of each employee's YTD and out of `BenefitYTD` (the stub
snapshots stay, as the rebuild expects), and puts the time entries the run paid
back to approved-unpaid so the next run can take them. The stubs read VOID, and
a void stub's PDF says so the way a void deposit slip does. A run whose pay date
is in a closed period is refused; a run whose NACHA file was already made is
voided only after a second look: the void asks first, naming the file's date
and total, because the bank may already have it and the void does not stop the
money.

A run's posting is reversed once. The Journal page no longer voids a payroll
entry (it says to void the pay run instead), and voiding a run whose entry was
already reversed there — a company file from before this may hold one — is
refused and names that reversal, so wages, liabilities and cash never come
off twice. A run whose posting has a line in a
completed reconciliation (net pay that cleared the bank) is refused with the
account and statement date, so a reconciled balance never moves; otherwise the
void gives its matched bank-feed lines back to review and un-clears its bank
lines, as every other void does. Besides the NACHA file, two more things ask
first: liabilities the run accrued that have had payments out of them since
the pay date (a 941 deposit out of 2310 Federal Withholding, a benefit
remittance), since the void would leave what was paid as a balance the agency
owes back; and a Form 941 for the run's quarter, or W-2s for its employees, a
W-3 or a 940 for its year, generated after the run posted, since the run drops
off them (file a 941-X or W-2c if they were filed). The page lists the reasons
and voids only on OK; `POST /api/payroll/{id}/void` answers 409 with the
warnings and changes nothing until `?confirm=true`. A loan's balance goes back
to the enrollment the run deducted from — the stub records which one — so with
an ended loan and a new one on the same code, the new one gets it back.

**A mid-year switch carries the year's payroll over** (#433, @ContractorKeith). Year-to-date
pay was summed from SlowBooks' own stubs only, so a company that switched in
May had every W-2, 941 and wage-base cutoff (Social Security, FUTA, SUTA)
wrong for the rest of the year. Each employee now has an opening balance per
year, entered quarter by quarter the way QuickBooks takes year-to-date
adjustments, through the day before the first SlowBooks pay run: gross,
federal withholding, Social Security, Medicare and FUTA wages and tax (both
sides), deductions, garnishments and net for each quarter; per state and
quarter the state wages, withholding, SUTA wages and tax and other state
taxes; benefit amounts by code for the year. Enter it on the employee's page
(Opening YTD, where each quarter's state table fits the dialog instead of
running past its edge) or import it for everyone from Payroll → Import opening
balances, using the template there (a Quarter column, one row per employee
per quarter, a further state on a row with only the state columns); the
import's Apply button appears only after a Preview says what there is to
write.

- **Each quarter's 941 carries only that quarter.** The old system filed the
  941 for every quarter it finished, so a May switch's Q1 941 is exactly the
  Q1 figures, and the Q2 941 is April and May plus SlowBooks' June runs. The
  tax liability schedule carries the same figures, so its 941 line agrees
  with line 12, and names the old payroll's share (`prior_system`) apart
  from what is left to deposit (`due`).
- **The wage bases come from the wage fields.** The paycheck's Social
  Security cap, the Additional Medicare threshold, the FUTA $7,000 and each
  state's SUTA base start from the old system's own taxable wages, not its
  gross, and the 941, 940 and W-2 measure the same way, so the runs and the
  forms agree. The SUTA base is a state's own: wages paid in another state
  do not fill it, and the state SUI report counts a stub in the state it was
  worked in.
- **Every form reads it.** The 941 by quarter, the 940 (the FUTA base is not
  handed out again), the W-2 and W-3 (every box, 3 and 5 from the taxable
  wages, 16 and 17 from the state rows), the state SUI report for each
  quarter, the employee's YTD and the stub's YTD.
- **Locked once a pay run in the year is processed:** a change or a delete
  is then a correction with a reason, kept in the audit trail, and the as-of
  date stays before that run.
- **A draft run figured from the old figures says so.** Changing or deleting
  an opening marks the employee's draft runs for the year; processing one
  asks first, and offers to discard it instead.
- **The Social Security wage base is the year's own.** A 2025 opening,
  paycheck, 941 and W-2 are measured against 2025's $176,100, not 2026's
  $184,500 (2023 through 2026 are known). A year SlowBooks has no base for
  is measured against the nearest one, and the opening and the pay run say
  so rather than refusing.

It is not a pay run and not a journal entry: the old system booked that
payroll and the opening balance sheet carries what it still owed, so benefit
remittances, the pay-run list, direct-deposit files and the payroll journal
never see it. A reader for QuickBooks Desktop's Payroll Summary report waits
for a real export to build against.

**Discard draft.** A draft pay run can now be discarded, from the Payroll
list or the run's view (`DELETE /api/payroll/{id}`, admin only like the rest
of payroll). Its stubs go, and so does everything creating it changed: the
benefit year-to-date its stubs added (a row only it made is removed), the
loan balances it paid down, the time entries it took, which the next run can
pay. A later draft figured while it counted toward the year-to-date is
flagged, as an opening change flags one. A processed run is never discarded.

**Paychecks count their taxable wages toward the caps.** Each stub counted
its gross toward the Social Security base, the Additional Medicare
threshold, FUTA and SUTA, so an employee with a Section 125 plan reached
them early and the next check was short-taxed. A stub now counts what it
was taxed on, gross less Section 125 and HSA (a 401(k) still counts), the
same way the openings do, and the 941, 940 and state SUI report measure the
same way. The W-2 follows each tax's treatment too: box 1 and 16 drop the
pre-tax amounts that reduce income tax, boxes 3 and 5 those that reduce
FICA (they were gross).

**A W-2 line per state.** Boxes 15 to 17 were one total under the
employee's work state. They are now one line per state the employee had
wages or state withholding in (stubs by the state worked in, the opening's
state rows by theirs), two to a form; a third state prints on an additional
W-2 that carries only the state lines, as the IRS instructions say. The
W-3's boxes 16 and 17 still total every line. Form history keeps every
earlier record: a one-state W-2 has the shape (and hash) it always had, and
Check re-figures a form generated before this release the way it was
figured then, so it still matches when the pay runs behind it have not
changed, and says when today's figures differ.

**API tokens: never an administrator, a backups permission, and revoking
that sticks** (#434, #443, @ContractorKeith). The API tokens from 2.11.1
already give a program a role and a key (`Authorization: Bearer sbp_…`): a
name, a prefix to match it by, the last time it was used, revoke, a SHA-256
at rest, and audit rows in the token's name. Now:

- A new token can no longer be made with the admin role: bookkeeper,
  accountant or read-only. Settings, users, tokens and restoring a backup
  stay with a signed-in administrator.
- **Can make backups**, a box an administrator ticks on a token (when making
  it, or later in the token list, which shows it): the token may make, list
  and download this company's backups on top of its role, so an import
  tool's safety net works without an admin token. The download is in the
  audit log too, as the token.
- No token restores a backup, whatever its role or permission. Restoring
  replaces the whole company; an administrator does it signed in.
- Revoking is final. Switching a revoked token back on is refused (the
  Reactivate button is gone); make a new token instead. A request with a
  revoked token is answered with a sentence naming the token and where to
  get a new one, not "Not authenticated", which sent an unattended client's
  operator to the sign-in page. A secret that matches nothing is still
  refused without a word.
- An admin token made before 2.29.0 keeps working, except that it cannot
  restore a backup and stays revoked once revoked. A tool that restored with
  one (resetting the books between runs) takes its backups with a token
  allowed them and leaves the restore to an administrator. See
  [docs/server-edition.md](docs/server-edition.md#api-tokens-programs-not-people)
  for what a token can and cannot do.

**Mercury bank feed** (PR #356, @yapishu). Connect a Mercury account with a
read-only API token (Banking → Bank Feed (Mercury)), map each Mercury account
to a bank account, and **Sync Mercury** brings posted deposits and withdrawals
into the account's *To review* list, deduplicated by Mercury's transaction id
and matched like any other feed. Optional signed balance webhooks keep the
displayed balances current. The token and the webhook secret are encrypted at
rest; connecting, disconnecting, mapping and webhook settings are an
administrator's; disconnecting keeps imported transactions. A bookkeeper's
card, connected or not, says an administrator does those, as SimpleFIN's does.
SlowBooks Pro is not affiliated with Mercury; see `docs/setup-mercury.md`.

**A Recent dropdown instead of the Open list** (#462). Since 2.24.0 every
screen opened added a line with an × at the top of the sidebar, up to
twelve, pushing the menu down. It is one line now, "Recent: <the screen
showing> ▾", a button that opens the last seven screens, most recent first,
the one showing marked, each a link back with its report period, register
account or document title as before; a screen opened again moves to the
top rather than listing twice, and Clear at the foot empties the list. The
button says whether it is open to a screen reader, the arrow keys, Home and
End move through the list, Esc closes it and returns to the button, and Tab
moves on. The list is still kept for the session, per sign-in and company,
and survives a reload; it fits a 320 px window in both themes.

**Hidden menu items stay hidden** (#457, reported by tithetaker). Settings →
Sidebar dropped every item already hidden from its list, so it could not be
shown again, and the next Save wrote only the items on the page, which
brought the first batch back. The list now always has every item the
company can hide, and a box now means "show": everything is ticked to start
with, untick an item to hide it, tick it again to bring it back, one at a
time or in batches. An item hidden for another reason (a nonprofit page on a
business company, an administrator's page for another role) keeps the choice
made for it, and a nonprofit page unticked on a nonprofit company no longer
comes back on the next reload. The stored setting is
unchanged (`hidden_menu` lists the hidden items), so a list saved by an
earlier version keeps working. A toast no longer takes the click meant for
what is under it: "Settings saved" sat on Save Settings for its few seconds,
and a second save in that time saved nothing.

**Chart import preview: the detail type and parent each row will get**
(#461). The dry run of a chart CSV showed each row's base type only
(`4960 Rebates Earned · income`) though the import creates it as Other
Income, and the same for Fixed Asset and Long Term Liability; the parent a
row would be linked under was not shown, and the change notes sat under a
column titled "Detail". Each preview row now shows the detail type it will
be written with (the file's, the parent's for a sub-account, the number and
name rule for an account given none, the chart's own for a control account)
and the parent's number and name, in their own "Detail type" and "Parent"
columns; the notes column is titled "Notes". The preview and the import read
the same plan, so what the preview shows is what the import writes. An
account the file retypes is written with the detail type the preview shows
(a "Truck Loan" moved to liability is a Long Term Liability, and so are its
sub-accounts), an account the file marks as a bank shows Bank, the import's
answer reads every row back from the chart, and a replace shows a kept
account's parent under the name the file gives it. A sub-account the file
lists unchanged keeps its own detail type; when the Balance Sheet prints it
in its parent's group (2.28.0, display only) its row says so.

**A bookkeeper can sync SimpleFIN again.** Since 2.26.0 saving the SimpleFIN
mapping is an administrator's, but Sync Now and Fetch older history saved the
mapping first for every role, so a bookkeeper's sync stopped at "Admin role
required". A bookkeeper now sees the mapping and the Reverse signs boxes
locked, with a sentence saying an administrator chooses them, and Sync uses
the saved mapping. Connect and Disconnect, also an administrator's, are no
longer offered to other roles.

**The register tells a voided payment from an unrelated posting** (#448,
@ContractorKeith). The bank register marks a posting void when a `<type>_void` reversal points
at it. It did that by matching the reversal's `source_id` against the
transaction id, which is right only for reversals that store the voided
transaction's id (an expense, a journal entry, a transfer). A payment's and
a bill payment's reversal stores the document's id instead, whose posting is
a different transaction, so a customer payment received straight to the bank
and then voided still showed live, while an unrelated posting whose
transaction id happened to equal the document id was marked void. One module
now owns which of a void's two conventions applies (`app/services/voids.py`),
the register and reclassify route through it, and a test keeps every void
type classified.

**Reclassify updates the stored account balances** (#490). Reclassify
moved a line to its new account and left the running total the Chart of
Accounts shows on the old one, so the Balance column, the accounts CSV
export and the AI tools' totals kept the amount on the account the line
had left while the P&L and General Ledger, which read the lines, showed
it where it went. The move now carries each line's amount off the account
it leaves and onto the one it lands on, in each account's own direction,
and moving the lines back restores both. Books already touched are
repaired on upgrade: the stored balances are recomputed from the ledger
once at start, and an administrator can run the same repair any time
(`POST /api/accounts/rebuild-balances` answers with the accounts it
corrected, none when the books agree).

**A bank entry's row says what its link opens.** In the register, a bank
entry (posted with + Entry, the quick-entry row or Add from the bank feed)
showed its memo as a link to `#/journal/…`, which read as "this is a
journal entry", the kind that cannot be edited, when Edit on the same row
is where it changes. The memo is now words, with a small Posting link
beside it that says what it is (the journal posting behind this bank
entry; use Edit to change it), and the posting's own page says "Posted
from the bank register of <account>; edit it there" with the account
linked. A void bank entry keeps the plain link. `GET /api/journal/{id}`
carries `register_account_id` and `register_account_name` for a bank
entry, empty for everything else.

**The IIF export guards the contact fields too.** The CUST and VEND rows'
phone, mobile or fax, email, terms and tax id went out as typed, so a
vendor whose email was `=HYPERLINK(...)` would run in Excel, the documented
way to fix an IIF file before import (noted on #410). A value starting with
`=` or `@` now takes a leading apostrophe; one starting with `+` or `-` does
not, because QuickBooks Desktop keeps the apostrophe, so an E.164 phone like
`+15551234567` or `-0001` terms reach it as typed. The terms on an invoice or
bill row follow the same rule. Names keep the full guard from #410, and the
importer takes the apostrophe off again (#449, @ContractorKeith).

**What a bank import needs first, written down.** `docs/banking.md` says a
CSV or OFX import needs only the bank account you import into, linked to its
ledger account: no vendors, no expense accounts (#446, @ContractorKeith).

Three schema changes, run in every company file one after the other: the
payroll opening balances (`a4330b1c2d3e`), `api_tokens.allow_backups`
(`c5e8a1d47b20`) and `items.code` (`c4e5d6f7a8b9`); the head is
`c4e5d6f7a8b9`. 632 API operations.

### v2.28.0 — Account types and sub-accounts

**Account detail types** (#390, from Kim Morden Bookkeeping's notes). Every
account now carries one of QuickBooks' fifteen account types beside its
type: Bank, Accounts Receivable, Other Current Asset, Fixed Asset, Other
Asset; Accounts Payable, Credit Card, Other Current Liability, Long Term
Liability; Equity; Income, Other Income; Cost of Goods Sold; Expense, Other
Expense. The account form has one picker, grouped by type, and the Chart of
Accounts lists each type's accounts under a heading per detail type, so a
loan sitting among the current liabilities shows in the first scroll. The
detail type cannot contradict the type, a bank or card account is Bank or
Credit Card, Accounts Receivable and Payable stay on 1100 and 2000, the
sixteen control accounts keep theirs. A sub-account shares its parent's
detail type, except under a bank, card, Accounts Receivable or Accounts
Payable parent, where it is set on its own — QuickBooks' rule. So a Savings
account (bank kind bank) under an Other Current Asset folder is a 400 here
where it was allowed before; an existing one is kept and logged
(`detail_type` on `POST` / `PUT /api/accounts`, a 400 naming the reason;
`GET /api/accounts?detail_type=`; `GET /api/accounts/detail-types`). A bank
or card account with reconciliations, feed rows or register entries keeps
its bank kind: changing it would take the register away.

The upgrade fills the detail type in from where the cash flow statement
already puts each account: the fixed-asset register's accounts, accumulated
depreciation and equipment-like 1500–1999 assets are Fixed Assets, other
1500–1999 assets Other Assets, loans, notes, mortgages and lines of credit
(the seeded 2400 Loan Payable among them) Long Term Liabilities, 4900 Other
Income Other Income, and the 6999 exchange and 7999 disposal gain/loss
accounts Other Expense. A sub-account left at the default shares its
parent's only where that keeps it current or non-current as the cash flow
had it (a default-typed sub-account of 1500 Equipment or 2400 Loan Payable
stays current), so no figure moves between working capital, investing and
financing; the one exception is 2000 or 2200 renamed like a loan, a control
account whose detail type stays Other Current Liability. The statements below print by it, so after
upgrading, open Chart of Accounts and check the detail type of any loan,
vehicle, equipment or long-term note; a line of credit kept in a loan
account, for one, is current.

The QuickBooks lists import keeps the type each account has in the file
(FIXASSET, LTLIAB, EXINC…) and the IIF export writes it back, CCARD included:
2400 Loan Payable went out as OCLIAB and 2500 Other Current Liabilities as
LTLIAB, guessed from their numbers. Non-posting accounts (estimates,
purchase orders) are skipped with a note rather than made assets, and an
account already in the chart keeps its own detail type, the import naming
any the file disagrees with. The Xero, Wave, Sage 50, Zoho Books, MYOB and
QuickBooks Online imports keep their files' fixed-asset, long-term and
other-income / other-expense types the same way; the chart CSV export has a
Detail type column, and the chart import reads it.

**The statements print by detail type** (#390). The Balance Sheet groups
its assets under Current / Fixed / Other Assets and its liabilities under
Current / Long Term Liabilities, each with its subtotal, on the screen, in
the PDF (and the accountant's package) and in the CSV, whose Section column
is now the group, with the side in a new last column, Type. A group with no
account is left out; accumulated depreciation is a Fixed Asset and nets
inside its group; the prior-year columns carry the subtotals. A
sub-account still at its default detail type prints in its parent's group,
with its family (1520 Trucks under 1500 Equipment among the Fixed Assets,
Accrued Interest under a loan among the Long Term Liabilities); its detail
type, its cash flow line and every figure stay its own, and one given
another detail type keeps its group. The nonprofit
Statement of Financial Position groups the same way. The Profit & Loss
prints Other Income and Other Expense below Net Operating Income, with Net
Other Income; Net Income is the same figure, and a company with nothing in
them sees the P&L it saw, Net Operating Income included only when there is
something below it. The printed Total Income and Total Expenses no longer
include the Other Income / Other Expense accounts (4900 Other Income, 6999
and 7999 among them): the two together are the old figures. The API keeps
`total_income`, `total_expenses` and `gross_profit` as they were (every
income / expense account, Other included) and adds `total_operating_income`,
`total_operating_expenses` and `operating_gross_profit` for the printed
figures (on the grids, also `operating_income` / `operating_expenses` /
`operating_gross_profit` per column and `total_operating_gross_profit`). P&L by Class and by Job
get the same rows, so the three reports tie line for line; the Classes list
counts other income and expense with income and expenses, as before. The
cash flow statement reads the detail type instead of guessing from numbers
and names: Fixed Asset and Other Asset are investing, Long Term Liability
financing; the fixed-asset register's accounts stay investing and
accumulated depreciation an adjustment. The upgrade's detail types put
every account where the old rules did, so an upgraded company's statement
is unchanged.
An account created without a detail type (the register's Add account panel,
the API, the chart import, the QuickBooks and other imports) takes it from 2.27.0's
number and name rule: an asset numbered 1500–1999 or named like equipment
is a Fixed Asset, a liability named like a loan, note or mortgage a Long
Term Liability, so a new Truck Loan is financing as before; a sub-account
still shares its parent's, and a type given always wins.
The QuickBooks Online report import brings Cost of Goods Sold in as cost of
goods sold, above Gross Profit; it came in as an expense.

**Sub-accounts print under their parents** (#416, from Chris Cree). The
Profit & Loss (and one class's P&L) and the nonprofit Statement of
Activities print a parent's family together on the screen and in the PDF:
the sub-accounts indented beneath it, the parent's own postings as "Direct
postings to Travel", and "Total Travel" for the family, a parent heading
its family even with no postings of its own. Each account prints once and
every total is the one the flat list gives; the prior-year columns add up
by family too. The CSV stays flat, each account named by its full path
("Travel: Hotel"), now on the Statement of Activities as well, which
printed the bare "Hotel". An account under a parent of another section, or
in a parent loop, prints at the top level with its path.

**Sub-accounts show and move on the Chart of Accounts** (#416, from Chris
Cree; the chart half — the statements' family subtotals are a separate
change). An imported Travel › Hotel was kept in the books but listed flat,
and the account form had no way to see or change a parent. The chart now
lists each sub-account indented under its parent within its detail type,
says "sub-account of Travel" to a screen reader, gives the full path
("Travel: Hotel") on hover, and writes it beside the name where two accounts
share a leaf name, the parent is listed elsewhere, or the filter has hidden
the parent; the filter matches the path. The form has a **Sub-account of**
picker, opened on the current parent, offering active accounts of the same
type and never the account itself or one under it; a parent other than 1100,
2000, a bank or a card sets the Type to its own. Choosing a type the parent
cannot take lets the parent go in the same save, and choosing one it can
brings it back, so the base-type change the detail types refuse under a
parent is now done from the form, and that 400 says "(Parent account: none,
on its form)". The server's refusals show in the form, where a passing toast
had them. New rules, refused on a move or a type change (a pair saved before
stays editable, and resending the parent it has is no move): under 1100,
2000, a bank or a card a sub-account keeps its own detail type but not
another base type — an expense under a bank account is refused, either side
of the pair; a Bank or Credit Card parent turned plain is refused while a
sub-account does not share its new type; and a control account takes no new
parent. The chart import leaves off a link the API would refuse, the row's
note saying why in the dry run and when applied, and the Xero, Wave, Sage
50, Zoho Books, MYOB, GnuCash and QuickBooks Online imports bring such an
account in unlinked, with a warning. Where the link stands but the file
gives a sub-account a detail type its parent cannot share, the child takes
the parent's instead — the link is right, so the family stays together on
the Profit & Loss — with a note naming the type the file asked for; under a
bank, card, 1100 or 2000 parent it keeps its own.

**Possible Duplicates counts the groups it shows** (#458, from the 2.27.0
release check). With "Show groups marked not a duplicate" ticked, the line
read "0 groups" above the marked group it was showing; the open groups are
counted, and the marked ones apart, "(shown)" when the box is ticked.

**The macOS release staples on any retry** (#459). The 2.27.0 build's
notarization ticket failed to staple once with a message the retry didn't
recognize; once Apple has accepted the app, any stapler failure is retried
within the same bound, and the error names what the stapler said.

One schema change, run in every company file: `accounts.detail_type`
(4ac01efbd72a), filled in so every statement shows the same figures as
before. 603 API operations.

### v2.27.0 — A register you can edit, and a bookkeeper's wish list

**The register adds a payee or a category as it is typed** (#386, from a
bookkeeper's notes). A payee typed into the register's entry row or the +
Entry form that is not already a customer's or a vendor's name gets a toast,
on leaving the box and again when the entry posts: "Add … as a customer?"
for money in (a deposit) and "Add … as a vendor?" for money out — the
categorized import's rule — with Add and Not now. Add makes the customer or
the vendor (`POST /api/customers` or `/api/vendors`, the name alone), so the
entry's payee resolves to it by name; either way the entry posts with the
text as before, and a name answered Not now is not asked about again that
session. The toast moves focus to itself and gives it back to the row only
when it is answered (or still has the focus as it goes), and holds its
auto-dismiss while it has focus or the pointer, so the keyboard can answer
it; a toast left to time out does not take the caret back from wherever the
typing moved on to. In the + Entry dialog, whose Tab is trapped, the toast's
buttons are folded into the dialog's Tab cycle so the keyboard reaches and
answers the offer. A new account is added from an **Add** button
beside the category picker (not an option on the select, which trapped the
keyboard) — name, type, an optional parent of that type and an optional
number, in a panel under the picker; the parent list leaves out the control
accounts the software posts to by number. Added, the account is in every
picker on the page (the split lines' too), chosen, and the cursor moves on
to the amount or the memo. No server change.

**A register deposit splits like a check, with a fee line** (#387, from a
bookkeeper's notes). A card processor pays over the gross it collected less
its fee, and only the net reaches the bank; a check could be split across
lines since 2.23.5, a deposit could not, so it never matched the statement.
A split line may now go the other way from its entry: the dialog's split
table has a **Taken out** box per line, so the fee is typed as the positive
number the statement shows, and a 97.00 deposit is Income 100.00, Bank
Charges & Fees 3.00 taken out, posted as one entry (CR income 100, DR fees
3, DR bank 97). A ticked line always runs opposite the entry, whichever way
the entry goes, so the same box on a check is a refund netted against it (a
−500 check paying Insurance 620 less a 120 refund), and the check's printed
stub lists that refund as a negative line so the stub still totals the
check. On the API each split line is signed on its own, and a line of 0.00
is refused rather than dropped.

**A register entry opens for editing** (part of #388, from a bookkeeper's
notes). A typo in an entry made from the bank register meant voiding it and
entering it again; now the row has **Edit**, which opens the + Entry form
filled in (the split lines included), and Save rewrites the entry in place
— the same journal entry, so its links, statement match and audit trail
stay with it (`GET` and `PUT /api/banking/entries/{id}`). A fix that leaves
every line's account and amount alone (payee, memo, check number, date,
class, job) updates the entry where it is, and saving it unchanged writes
nothing; a new amount or category replaces the lines the way a bill's are
on edit, each old line a DELETE in the audit log and each new one an
INSERT. A field the PUT leaves out keeps what the entry has, and the form
carries back what it has no box for: the entry's job, a split line's own
class or job, and a class archived since. A line with its own class (an
imported check's lines can have one) keeps it through an edit. A memo-only
entry (a memo, no payee) now reopens with the memo in Memo, not in Payee: no
payee carries the sentinel "Bank entry" on the entry's header instead of the
memo, so a memo-only entry is told apart from a payee-only one, and the
register shows its Payee column blank rather than the memo or the sentinel.
This needs no migration and changes only new entries; a memo-only entry
posted before this release was stored the same as a payee-only one, so it
still reopens with its text in Payee.

An entry a completed reconciliation holds says so as it opens, and its
amount and date stay as the statement has them: a change to either is
refused, as voiding it is ("so its amount and date cannot be changed, just
as it cannot be voided. Correct the difference with a new entry."). Its
payee, memo, check number, class and job can be fixed without a word; a
new category or split asks first — "Changing its category moves its amount
between accounts in a period already reconciled." — with **Change it
anyway**, which saves the form as it is then (`confirm_reconciled`, and the
audit log says the edit moved past that reconciliation). On an entry that
is only cleared, a changed amount loses the tick and the answer says so.
An entry added from a statement line keeps that link through an edit (on
PostgreSQL too, where the edit failed); one whose amount changed sends the
statement line back to review without its category, so **Add all
categorised** cannot post it a second time. A void entry is not edited, an
edit that would turn the entry into a transfer is refused (void it and post
the transfer), the closing date holds for the old date and the new one, and
a read-only or accountant's sign-in is not offered Edit. Journal entries
and deposits still open their own views from the row; editing them in
place is still to come.

**A bill line can be an amount alone** (#391, from a bookkeeper's notes).
A utility bill or a pest-control bill has an amount, not a quantity and a
rate, and Enter Bill asked for both on every line. Qty and Rate now start
blank and the Amount cell is a box: type what the bill says and the line
is that amount; type a quantity or a rate and the amount is worked from
them as before. Typing a quantity on an amount-alone line seeds the rate
from that amount, so the line prices at quantity x amount (and clearing the
quantity brings the amount back) rather than multiplying a blank rate to
zero. Typing the amount after a quantity reads the amount as the line total,
so the rate is amount / quantity — Qty 3 then Amount 30 gives Rate 10 and
posts 3 x 10, instead of dropping the quantity and posting 1 x 30. A stock
item is shown with the quantity 1 it is received as, both when it is drawn
from a stored line and as soon as it is picked on a new line, not as a bare
amount or a blank Qty box. On the API a line may send `amount` by itself; it
is stored as
quantity 1 at that rate, so the journal, job costing, the PDF and the
exports read it as they always have, and a bill opened for editing draws
such a line as an amount. An `amount` sent with a quantity and rate must
agree to the cent or the line is refused (422); the strict model used to
refuse any `amount` field outright, also with a 422.

**The statement's ending balance can be corrected while reconciling**
(#389, from a bookkeeper's notes). The ending balance was typed once, at
the start; a transposed digit meant abandoning the reconciliation and
ticking every line again. The reconcile screen's Statement Balance card
has a **Change…** button now, for the balance and the statement date
(`PUT /api/banking/reconciliations/{id}`, either field optional). The
ticks stay and the difference is refigured from them. A date moved later
brings the lines up to it onto the session; one moved earlier leaves the
lines after it off the session with their ticks kept, as Abandon keeps
them. The ticks an earlier move leaves behind — lines that were in reach
of the old date but fall after the new one — are reported back as a count,
with a note that they carry to the next reconciliation still ticked, in the
change response and on the reconcile screen, since completing this session
won't stamp them. The count is only those lines: a deposit cleared by a
bank-feed match or a register clear, dated after a date that never moved,
is not among them, so a fresh reconciliation of a feed-linked account
reports none. A date on or before the last completed
statement is refused as a fresh start is, and a completed reconciliation
refuses the change. The change leaves an `UPDATE` row in the audit trail.

**Reclassify Transactions filters by payee** (#394, from a bookkeeper's
notes). An unrecognized vendor is parked in Other expenses until the
client says what it was, and the tool found lines by account and period
only, so moving that vendor's meant picking them out of everything else
parked there. A Payee text box and a vendor select beside the account and
period list one payee's lines, ready to tick and move in one pass, both
riding on the address like the account does. Each row now names its payee
the way the bank register does (reusing `payees_for`): a bill's or
expense's vendor, else the payee read off a bank entry, check or card
charge — so an IIF card charge with no "CC Charge:" prefix reads the same
in both places; a journal entry has none. `GET /api/reclassify/lines`
takes `vendor_id`, matched as whole words anywhere in a row's payee,
ignoring case (so bank-feed spellings like `HOME DEPOT #4521 JOLIET IL` and check
memos like `Check 1042 - Home Depot` come along with vendor "Home Depot", while vendor "Ace" doesn't take
"Space Coast Supply"),
and `payee`, a case-insensitive part of the name; each row carries
`vendor_id` and `payee`.

**Save CSV on the A/R and A/P Aging reports** (from a bookkeeper's notes,
#393). A client who asks for the aging as a spreadsheet could only have it
from the Accountant's Package, so one file meant building the whole zip.
Each aging report has a **Save CSV** button now (`GET
/api/reports/ar-aging/csv` and `/ap-aging/csv`, `as_of_date` as the
screen's) that writes the file the package carries — the same preamble,
columns and totals row, made by the one function both use, so the two
cannot drift. It is always the whole company, as the screen is, even when
the report was opened from a customer's or vendor's page (which only
highlights their row).

**A Reconciliation Reports card, and pinned cards first on the Report
Center** (#395, from a bookkeeper's notes). CPAs ask for bank reconciliation
reports; each completed one was in the Accountant's Package but had no card
of its own. Reports → Reconciliation Reports lists every completed
reconciliation by account and statement date, with the statement balance,
the cleared total and the day it was finished, each to the on-screen report
or its PDF (`GET /api/banking/reconciliations?status=completed`, which now
takes `status` and carries `account_name`; nothing new is generated). A
reconciliation from before 2.10 whose ledger account is gone has no account
to name and no report, so it is left off the list, as the Accountant's
Package already leaves it out. Close on an on-screen report opened from the
card returns to that list, not past it to the Report Center. And the cards
can be arranged: each has a star, a toggle button reached by Tab after the
card, and pinned cards lead the grid in the order they were pinned, the rest
in their fixed order. The pins are the preference `report_center_pins`
(`{"pinned": [view, …]}`), kept per company file and, once the company has
users, per user, as the dashboard's layout is; the star is hidden from a
read-only sign-in, whose preference writes the server refuses. The saved
reports strip is as it was.

**Saved invoice messages, one the default, chosen on the invoice** (#392,
from a bookkeeper's notes). The note that says how to pay — Zelle to this
address, ask for a card link — was typed into every invoice's Notes by
hand. Settings → Invoice Messages keeps a short list of them now (name,
text, a Default radio; add, edit, delete, an administrator's writes like
the settings around it), and the invoice form offers the list in a
**Message** select above Notes, "(none)" first: choosing one copies its
text into Notes, where it can still be edited — appended on a new line
when Notes already hold something typed by hand, so nothing is thrown
away, and replacing them only when they are empty or still hold a saved
message's text; "(none)" clears Notes only in that same last case. A new
invoice starts with the default message's text (with no default, the
Default Invoice Notes setting as before; an estimate converted with no
notes of its own starts the same way). The invoice keeps the text, not
the choice, so a message edited later leaves old invoices alone, and the
PDF and email print it where they printed Notes.
`GET/POST /api/invoice-messages`, `PUT/DELETE /api/invoice-messages/{id}`;
setting one message's `is_default` clears the others in the same
transaction, each through the ORM so the old default turning off is
audited. The email templates are unchanged.

**A ministry's own message and footer on the giving statement** (#415, a
bookkeeper for several ministries; stacked on #392's saved messages). The
year-end statement ended with a fixed "Generated by Slowbooks Pro 2026"
footer and had no place for the ministry's words, so statements were merged
in a third-party program to add each ministry's header and footer. The saved
messages (#392) gained a **kind** so one list holds both: invoice messages
fill an invoice's Notes as before, and **giving-statement messages** open the
year-end statement. Settings → Giving Statement (nonprofit mode) manages the
giving-statement messages with the same saved-message UI — the default one
prints above the gift table (a thank-you from the pastor or the board) — plus
a **Footer text** setting (`giving_statement_footer`) that replaces the
product footer (the ministry's address, website, EIN, a verse). Both are
plain text with line breaks, printed as typed and escaped as text (no HTML
injection), an administrator's to write and audited like the settings around
them. The default is **per kind**: one default invoice message and one
default giving-statement message, never crossed; the invoice picker and list
still show only invoice messages (`/api/invoice-messages?kind=` filters, the
invoice behaviour unchanged). The same words ride on all three outputs — the
single PDF, the all-donors stack and the emailed copy (one code path). An
empty message or footer prints what printed before, and the product-name
footer is gone either way so a donor's statement carries no product name; the
IRS acknowledgment sentence is always printed and is not editable. The footer
is a **running element** in each page's bottom margin box (`@bottom-center`),
so a six-line footer on a multi-page statement no longer overprints the gift
table the way the old `position: fixed` footer did (verified with a real
WeasyPrint render). Length caps hold a pasted block on the page: a
giving-statement message is at most **1500 characters** and the footer at most
**500** characters and **six lines** (a line over 90 characters counts as two),
enforced server-side (422), so the footer always fits above the page's edge. **Preview one donor's statement**
(`GET /api/donors/giving-statement/preview`) renders the first donor's with
the saved words (a sample donor on a file with no gifts yet). A new migration
(`f9a0b1c2d3e4`, stacked on `e4f5a6b7c8d9`) adds the `kind` column with a
server default of `invoice`, so a file upgraded from #392 keeps every saved
message as an invoice message.

**Possible Duplicates, for bank and card accounts** (#414). A migration
brings the old books across as they are, so a QuickBooks register line the
bank feed added on top of one someone had keyed, or a statement entered
twice, arrives as two real entries. Reports → Possible Duplicates lists,
for one bank or card account and a period, groups of entries with the same
amount on the same side and the same or a similar payee at most three days
apart (two equal monthly payments are not grouped). A group is bounded by
the window measured from its first entry, so a run of like charges a few
days apart breaks into small groups instead of chaining into one, and a
daily charge never becomes one giant group. A payee that is only a number
(a store or pump number) matches nothing, and a one-word payee must be the
start of a longer name, not sit anywhere inside it. Each entry shows its
date, number, payee, memo, source (imported, keyed or migrated) and whether
it is reconciled, opens, or voids through its own void route — including an
imported QuickBooks Desktop check, which voids through the journal route —
so a reconciled entry is not offered and a closed period asks for the
closing-date password. **Not a duplicate** keeps a group off the report for
everyone, stored in a new `duplicate_dismissals` table (migration
`b7d2e9a41c63`), keyed by a fixed-length hash of the group so a long group's
key never overflows on PostgreSQL; a new copy joining the group brings it
back. Save PDF and
Save CSV as the other reports. Read-only and accountant sign-ins read it;
dismissing is refused to them. The QuickBooks migration guide's *After the
import* suggests running it once. It never voids anything by itself.

**The Interoperability page says where a bank statement goes** (#396, from
a bookkeeper's notes). Its CSV imports take lists and reports; a bank or
credit card statement (CSV, OFX, QFX) is imported from the account itself,
under Banking → the account → Import file, and the Report CSV section now
says so with a link, as does the QuickBooks migration guide. The guide also
answers whether the vendors and accounts on a statement must be entered
first: they need not — each line comes in as a statement line with its
payee as text, and waits in the account's To review list to match or
categorize unless it matches one entry already in the books, which it is
matched to on import.

An inventory item created with a starting quantity on hand posts that stock
now, as QuickBooks does (found in PNWImport's fork,
SlowBooks-Pro-Testing#101). Ten on hand at $4.00 used to write 10 onto the
item and nothing else: no movement, no journal entry, so the $40 never
reached the balance sheet and the first sale took its cost of goods out of
an Inventory account that had never been given it. The item's opening
quantity is a movement at its cost and one entry, DR Inventory / CR 3900
Opening Balance Equity, under its own source type (`item_opening`), so a
fixed asset that happens to share the item's id is no longer mistaken for
its purchase. The entry is dated the day the item is created, or an `as_of`
date you give (books set up as of a prior year-end); the form notes that an
opening trial balance's 1300 already counts this stock, and the entry is
refused on a closed date. Only a stock item posts — a service item with
tracking forced on through the API does not. A zero cost moves the stock
with no entry; a negative starting quantity is refused. The entry is listed
on the Journal page but not voidable there (reversing it would leave the
stock without its value); correct it with an inventory adjustment. Opening
Balance Equity is matched by account number 3900, so a chart that already
holds it under another name — a renamed account, or QuickBooks' "Opening Bal
Equity" — is reused rather than grown a second one. Only an **equity** 3900
counts, both for that resolution and as the control account the renumber and
delete guards now protect; the registry matches on number **and** the type the
posting code expects, so an imported chart's non-equity 3900 (an expense "3900
Miscellaneous Expense", say) is an ordinary account you can still renumber or
delete, and the same holds for any other control number carrying the wrong
type. A later change to
on-hand still posts as an inventory adjustment, as the item form already
sends it; `PUT /api/items/{id}` never sets it. The Balance Sheet now rounds
its current-period net income and total equity to the cent the same way the
Profit & Loss does, so the two reports agree and no 777.4000000000001 sits
beside the P&L's 777.40.

A recurring invoice that has made invoices can no longer be deleted. Deleting
one answered 200 and left its invoices pointing at a template that no longer
existed; now the delete is refused (409). Nothing ends on the delete — it
keeps billing until the user acts — so the sentence counts the invoices it
made and says to set an End Date to stop it for good or clear Active to pause
it, and the edit form has the Active box that sentence names. Re-ticking
Active now rolls next_due forward to the first occurrence on or after today:
a template paused in January and re-activated in July no longer back-dates an
invoice for every missed month, and a run no longer refuses every schedule
because one back-dated installment fell inside the closing date. A schedule
skipped for a closed period is reported with advice the operator can follow —
move the closing date, or clear and re-tick Active to roll the due date
forward past it — not "set a later start", which the edit form has no field
for. `scripts/run_recurring.py` now prints the skipped schedules (each with
its reason) so the cron log shows the ones it passed over, instead of
skipping them silently every day. A schedule that has made nothing still
deletes (found in PNWImport's fork, SlowBooks-Pro-Testing#101).

**A negative number in a CSV stays a number, an identifier stays text**
(found in PNWImport's fork, SlowBooks-Pro-Testing#101; reviewer follow-up,
#410). The formula guard every CSV export shares put an apostrophe in front
of any cell starting with `-`, so a Schedule C loss (`-1234.56`) opened as
text and would not sum. A cell that is only a signed amount (`-1234.56`,
`-0.5`, the reconciliation's `-$1,234.56`) is now written as is; `=1+1`,
`-1+1`, `-SUM(A1)`, `@x` and the like keep the apostrophe. A value that only
looks like a number but is really an identifier keeps its apostrophe too, so
a spreadsheet shows it verbatim: a phone like `+15551234567` or
`+447911123456` (the Phone columns on the customer and vendor exports are
marked identifiers), a leading-`+` code with no decimal point, and a
leading-zero reference like `-0001`. Nothing that goes through the IIF
export's text path is an amount, so its names, memos and descriptions always
stay text; the audit log's and closing-date-exceptions' before/after columns
stay text as well.

**A file the bank import can't read is said so, not a 500** (found in
PNWImport's fork, SlowBooks-Pro-Testing#101). Random bytes, a PDF, a CSV, a
truncated statement, an OFX with broken markup or a nonsense amount
(`NaN`, `Infinity`, `1e999999999`), or a CSV with an absurd date all raised
an error as a 500. The OFX/QFX and CSV preview and import now answer "That
file isn't a bank statement this can read: an OFX, QFX or CSV export from
the bank." for any file they can't parse (an empty one says it is empty),
and write nothing; an unrecognised CSV still goes to the column-mapping
step. A UTF-16 export (either byte order) is decoded by its byte-order mark
and read, and a complete statement that is only missing its closing
`</OFX>` or carries trailing NUL padding still imports. A file that is
nothing but a byte-order mark (UTF-8 or UTF-16) decodes to no content, so it
answers the empty-file sentence like any other empty file rather than a 200
or an "unreadable".

**An account can't become a sub-account of its own sub-account** (found in
PNWImport's fork, SlowBooks-Pro-Testing#101). Only an account named as its
own parent was refused: Vehicles under Truck with Truck already under
Vehicles was saved, and the IIF export's full names recursed until they
failed. The API that sets a parent refuses it now ("That would make Vehicles
a sub-account of itself (through Truck).") at any depth — including a loop
longer than the walk's safety cap, which used to slip through — a parent id
that names no account is refused on create and edit, and a chart file whose
rows name each other as parents leaves the closing link off with that
sentence as the row's note, shown in the dry-run preview and when applied
alike. The report paths and IIF names stop at a loop saved before this
release. The same one walk now guards the cost-code tree too.

`POST /api/accounts`, `PUT /api/accounts/{id}` and the CSV chart import
(`/api/csv/import/accounts`) now refuse the account numbers the posting code
resolves control accounts by. A new expense numbered 5900 used to become where
every inventory adjustment posts, because the software finds that account by
its number; the same slots (1100, 1200, 2000, 2200, 3200, 5900, …) are now
reserved, and creating or renumbering an ordinary account onto one — or a chart
import row that lands a new account on one — is refused with a message naming
what the number is for (the import shows it in the dry run). The one exception
is restoring the control account itself: the registry's exact name AND its
expected type restore an empty slot, which is what the "account missing"
message tells you to do — a right name with the wrong type (an "Inventory
Adjustments" booked as income) is refused and names the type to set. A restored
account that the standard chart seeds comes back `is_system`, so it matches a
fresh company's. The internal paths that create the control accounts directly —
seeding, the chart import's match-and-rename of an existing account, the
IIF/QuickBooks/Xero/MYOB migrations, and on-demand ensure_account creation —
do not go through these guards and are unaffected.

- The vendor payments CSV (`/api/vendors/{id}/payments/csv`) now goes through
  the shared export helpers: a vendor, method, check number, account or note
  that begins with `=`, `+`, `-` or `@` is written as text with the same
  leading apostrophe as every other export (it was written raw, so a vendor
  named `=HYPERLINK(...)` was a live formula), and the file starts with the
  UTF-8 byte-order mark so Excel reads accented names correctly. Amounts stay
  numbers. The download's file name goes through the shared
  `content_disposition` helper, so a vendor whose name Latin-1 can't carry
  (`Łódź Supply`) no longer fails the download with a 500 in the
  Content-Disposition header: the header sends an ASCII fallback
  (`Payments_Lodz-Supply.csv`) plus `filename*=UTF-8''…` with the exact name.
  The desktop shim's inline header is honored too.

**A deposit QuickBooks prints under several numbers imports** (discussion #349). In a
Transaction Detail by Account export, a bank deposit of several customer checks
shows its bank line with no number and each income line with that check's own
number. The ledger import grouped by number, so the deposit came apart into
halves that only balance together and the file was refused. An unnumbered half
and the same-day numbered halves that add up to it now post as one journal entry.

**The macOS build's Pillow moves to 13.0.0.** Dependabot had ten open alerts
on the 12.3.0 pin in `packaging/macos/requirements-build.txt`, two of them
high (a JPEG comment heap overflow and a libopenjp2 heap overflow), all
fixed in 13.0.0. The HarfBuzz rule in the mac spec (#141) excludes Pillow's
font module by name, so it holds unchanged; nothing in the app draws text
with Pillow.

**macOS release tooling (testing-repo #104).** `release.py` now waits for
Apple's notarization ticket before giving up. Right after notarization is
accepted, the ticket may not have reached Apple's CDN yet: `stapler staple`
exits 0 and `stapler validate` fails a second later, or staple itself fails
with "Could not find base64 encoded ticket" or "Record not found" (three of
the four 2.26.0 sign jobs; each passed on rerun). The staple and validate pair
is retried, up to six attempts over about five minutes, and the final error
names the attempts. Any other staple error stops the run on the first try.

Four schema changes, each run in every company file: `reconciliations.max_statement_date`
(a1f2e3d4c5b6), `invoice_messages` (e4f5a6b7c8d9) and its `kind` (f9a0b1c2d3e4), and
`duplicate_dismissals` (b7d2e9a41c63). 602 API operations.
### v2.26.0 — Direct deposit from the page, and last year beside this one

The sentence a Server Edition server answers for a company it cannot open
now is always one of its own, looked up by the server rather than read off
the exception that carried it (two code-scanning alerts on 2.25.0 read that
path as a possible traceback; it never was one, and now cannot be).

**A new Docker install no longer signs people out at random.** The image runs
two server processes, and on a first start each made its own session key
when none was on disk yet, so a request reaching the other process was
refused as not signed in (12 of 30 in one session on a fresh stack) until
the container restarted. The first process to write the key now wins and
the other reads it. `SESSION_SECRET_KEY` in the environment still overrides
the file.

**A Postgres backup restores again** (Discussion #417, @teich). On the Docker and
server installs that keep the books in PostgreSQL, every restore from the
Backups page or `POST /api/backups/restore` was refused as "made under another
key" and asked for a recovery key, since 2.24.0: the check meant for an
encrypted company file took any backup that wasn't plain SQLite for one, and
a pg_dump backup isn't SQLite. Only a `.db` backup is checked for a key now,
and the backup lists no longer mark Postgres backups encrypted.

**A processed pay run's direct deposit file, from the page** (discussion
#211, @ContractorKeith). The NACHA file could only be made by an API client
that sent the bank's numbers in the request body; the page had no button,
and nothing kept the numbers. Settings → Direct Deposit (ACH) keeps them
now: the bank's routing number and name, the company account the pay comes
from (encrypted at rest) and the two identifiers the bank assigns, "1" +
EIN from the company tax id when blank. A processed run's view has a
**Direct deposit file** button (`GET /api/payroll/{id}/nacha`), the file
named for the pay date, and says who it pays and who is paid by check
(`direct_deposit` on each stub, a Paid by column). A run nobody on has an
account, or a company with the bank details blank, is refused with the
sentence that says what to do. `POST .../nacha` takes each field as
optional now, outranking the saved one, and its `effective_date` reaches
the batch header, which always carried the pay date. The file header holds
a 10-character immediate origin whole: a space and nine digits dropped the
leading "1" of "1" + EIN. The builder itself refuses a run with no
credits rather than returning a well-formed empty file (from PNWImport's
fork, 5f522667). Settings refuse a routing number that is not one and an
identifier longer than its field. Each file made leaves an `EXPORT` row in
the audit trail (who, which run, how many it pays, the total and the
effective date, never an account number), and the Audit Log page lists and
filters it, as it now does a backup `RESTORE`.

**Docker build context and compose hardening**. `.dockerignore` now
excludes the auto-generated encryption/signing keys (`.slowbooks-master.key`,
`.slowbooks-session.key`), SQLite company files and their WAL/shm/journal
sidecars, `app/static/uploads/`, and log/coverage artifacts, so `COPY . .`
can't bake a developer's secrets or data into a shared image — while still
copying everything the container needs at runtime (alembic migrations,
`app/static` assets, `index.html`, the entrypoint). `docker-compose.yml` now
binds both published ports to `127.0.0.1` by default (`POSTGRES_BIND` /
`APP_BIND` to override, `POSTGRES_PORT` still sets the host port), so the dev
database — which keeps its convenient default `bookkeeper` password — is no
longer reachable from the LAN on a routable host. `docker-compose.prod.yml`
already published neither service; unchanged. The image's HEALTHCHECK moved to
a stdlib `scripts/docker_healthcheck.py` that no longer follows redirects, so
it stays healthy when `FORCE_HTTPS=true` answers `/health` with a 307. New
tests parse `.dockerignore` (secrets excluded, runtime paths kept), assert the
compose ports are loopback-bound, and exercise the healthcheck probe.

**Time-entry approval integrity.** Approving a time entry records the
signed-in user as the approver — the same name the audit log carries — instead
of taking it from the request body, so no caller can stamp an approval in
someone else's name. The `approved_by` field is still accepted on
`POST /api/time-entries/{id}/approve` for backward compatibility but is ignored.
`PUT /api/time-entries/{id}` no longer changes an entry's status: it still
accepts `status` so a client can round-trip the whole entry, but only to resend
the current value — a real change (e.g. straight to `approved`) is rejected with
a 400, so status moves only through `/submit`, `/approve` and `/reject`. PUT and
DELETE now also refuse an entry already costed to a job (`job_cost_id` set),
mirroring the existing pay-run lock, so a job's posted labor cost can't be left
orphaned. QuickBooks-imported entries keep the approval name from their import.

**API change.** Callers of `/api/time-entries/{id}/approve` that relied on
`approved_by` from the body no longer control the recorded approver. Callers of
`PUT /api/time-entries/{id}` can no longer change `status`.

**The uploads folder is refused whatever the address says.** The guard that
keeps pre-2.18 files in `app/static/uploads/` (logos, attachments, W-4s) off
the web matched only the plain `/static/uploads/…` path; `/static//uploads/…`
and `/static/css/%2e%2e/uploads/…` reached the static mount and were served
without signing in. The mount now resolves each request and refuses anything
inside the uploads folder, however it is spelled, symlinks and case included.
Only installs upgraded from before 2.18 that still have files there, served
over the network, were exposed. Found in MorrisAEGIS's fork (arkcpa-slowbooks).

**A permit's Copy buttons copy the text, never run it.** The reseller permit
Verify dialog built each Copy button as an inline handler with the value
inside a JavaScript string. `escapeHtml` turns a quote into an entity, but
the browser decodes that before the script runs, so a permit number or tax
ID with a quote broke the button, and a crafted one ran script for whoever
opened the permit. The buttons now carry the value in a `data-` attribute,
read as text. The same dialog showed a customer's or vendor's name as
`[object Object]`; it shows the name. A test now fails on any escaped value
inside a quoted inline-handler string. Found in LayoverLogic's fork.

**Sessions end when access changes.** A signed-in session is now
re-checked against the users table on every request instead of being trusted
from the signed cookie alone. Sessions end on the next request when a user is
deactivated, their role changes, or their password changes or is reset; the
role is read from the user row, so a role change takes effect at once. A
user's own password change keeps the session that made it and ends their
others. A switch to another company (`POST /api/auth/company`) re-checks the
session the same way before the cookie's role opens anything, so a demoted or
deactivated administrator cannot set up a new company from a stale session. A
sign-in past its end date is signed out on its next request, and setting an
end date in the past ends the sessions at once. Sign out ends the user's other
sessions too, so a cookie copied before it stops working. Single-user desktop
sign-in and API-token requests are unchanged; an API token outlives its
maker's deactivation by design, until it is revoked itself. Adds
`users.session_version` (one Alembic migration, run in each company file).
Found in MorrisAEGIS's fork (arkcpa-slowbooks).

**Account balances update atomically under concurrency.** Posting a
journal entry used to read each account into memory and add the line's amount
in Python (`account.balance += delta`). On PostgreSQL, two sessions posting to
the same account at once could both read the same starting balance and one
overwrite the other, so `accounts.balance` drifted. The journal lines were
always correct — only the stored balance was affected — but anything reading
`Account.balance` saw the drift. Posting now aggregates each account's change
and applies it as a single `UPDATE accounts SET balance = balance + delta`, so
the database performs the addition and no concurrent update is lost; the same
atomic path now backs the invoice/bill journal rebuild. Per-account updates are
applied in ascending account-id order, so a single posting cannot deadlock with
another; an edit's reversal followed by its repost in one transaction can still
contend with a concurrent edit of the same accounts, which the database resolves
by failing one of them. NULL balances are coalesced to zero, and in-session
Account objects are refreshed so a response built in the same request reflects
the new balance. Behaviour is unchanged on SQLite. Found in MorrisAEGIS's fork (arkcpa-slowbooks).
The Audit Log no longer lists an account row for each balance a posting
moves (a change of `balance` alone); the posting's own lines, which are what
moved it, are recorded as before.

**Reverse-proxy client IP and shared rate-limit counters** (fix). Behind a
TLS-terminating reverse proxy the app saw the proxy's address on every request
instead of the caller's, because uvicorn trusts `X-Forwarded-For` only from
`127.0.0.1` by default and nothing widened that trust. That collapsed the login
rate limiter into one shared bucket for all users (5 attempts/minute across
everyone, not per client) and recorded the proxy's IP in the `login_attempts` /
`portal_accesses` audit tables. Separately, rate-limit counters lived in
per-worker memory, so `APP_WORKERS=4` made every configured limit 4x looser.
Set `FORWARDED_ALLOW_IPS` to the proxy's address or CIDR: uvicorn reads it from
the environment itself, so the Docker entrypoint passes no flag. The client
address is now decided in that one place — `client_ip()` (login and portal
audit rows) reads what uvicorn reports and no longer parses `X-Forwarded-For`
itself, and the `TRUST_PROXY_HEADERS` knob is retired (removed from
`.env.example`, `docker-compose.prod.yml` and the docs). The limiter honours
`RATE_LIMIT_STORAGE_URI` (e.g. `redis://…`) for shared counters, defaulting to
in-memory as before; the `redis` client now ships in `requirements.txt` (a
`redis://` URI used to fail at import without it), and when the URI is set the
storage is health-checked once at startup, so an unreachable Redis stops the
app with a clear message instead of making every login a 500. The app logs a
startup warning when a proxy is implied (`FORCE_HTTPS` on) but the trust list
is empty, or when more than one worker runs with in-memory counters.
`docker-compose.prod.yml` passes `FORWARDED_ALLOW_IPS` through; left blank, it
starts as before with the warning. Desktop and single-worker installs are
unaffected. See `docs/tls-proxy-setup.md`.

**CORS and the security headers now sit outside the login gate.** Starlette
makes the last-registered middleware the outermost layer, so registering CORS
and the security-header middleware before the auth gate had made them the
inner layers — the gate wrapped them. Two consequences, both fixed by
registering the gate inside CORS and the headers: an unauthenticated CORS
preflight (browsers send it without credentials) was answered 401 with no
`Access-Control-Allow-*` headers, so a deployment with a configured
`CORS_ALLOW_ORIGINS` allow-list could sign in and then fail every cross-origin
API call; and the gate's own 401/403/503 refusals (unauthenticated, role
refusal, a company that cannot be opened) carried no CSP, `X-Frame-Options`,
nosniff or Referrer-Policy. The allow-list and credential handling are
unchanged, and a route that chose a stricter header of its own (the portal's
`Referrer-Policy: no-referrer`) still wins.

**Validators reject a trailing newline.** Several input validators built a
Python `re` pattern ending in `$` and checked it with `.match()`/`.search()`.
In Python `$` also matches just before a trailing `"\n"`, so a value like
`"evil.db\n"` passed. The backup and company `.db` filename allow-lists, the
Postgres database-name allow-list, the chart account number, the username, the
preference key, the idempotency-request key, the Cloudflare account id and the
AI worker-url host check now use `re.fullmatch` (anchored to the whole string),
which rejects the trailing newline. HTTP entry points that already `strip()`
their input were not reachable, but the regexes are hardened for any caller.
Pydantic `pattern=` constraints are unaffected: they use the Rust regex engine,
where `$` is end-of-text.

**Bank-feed connection management is admin-only.** Connecting a SimpleFIN
bank feed, remapping its accounts and disconnecting it change a stored
credential and the books' wiring, so they now require the administrator
role — the same boundary as Settings and the AI provider key. Running a
sync or reading status on a feed the administrator has already connected
stays available to the bookkeeper as daily books work; read-only is
unchanged. Previously a non-admin sign-in or token could reach these.

**Hostile input is a 422, not a 500.** Four malformed-input classes that
passed schema validation and then crashed downstream are now refused at the
request boundary, centrally in the shared request base model
(`app/schemas/common.StrictModel`) rather than field by field:

- A non-finite float (`NaN` / `Infinity`) in any float field — a bill line
  quantity/rate hit `decimal.InvalidOperation`, a bill-payment amount became
  a NULL insert — is rejected (`allow_inf_nan=False`); the ~200 float fields
  are covered in one place. Decimal money fields already refused these.
- A NUL byte (`\x00`) in any string, which SQLite stored silently and
  PostgreSQL rejected with a 500, is refused everywhere a request carries text.
- A document/transaction date outside 1900–2199 is refused: an invoice dated
  `9999-12-31` overflowed computing its Net-30 due date. The window still fits
  every real document, hire date and birth date, and file importers
  (QuickBooks/Xero/IIF) build rows directly, so historical imports are
  unaffected.
- A PUT that explicitly sets a NOT NULL column to `null` (e.g.
  `PUT /api/customers/{id}` or `/api/vendors/{id}` with `{"name": null}`) is
  refused with a 422 naming the field, via a generic helper
  (`reject_null_updates`) applied to every all-optional Update route, instead
  of the IntegrityError 500 it caused. An empty `{}` PUT stays a no-op 200.

The NUL check also walks `list[str]` fields, and `BudgetUpdate` now shares
the strict request base, so an unknown field on it is a 422 like everywhere else.

The four above were reported in PNWImport's fork as still present upstream,
and each was checked against 2.25.0 before it was fixed.

**An import's whole error list, and a file of it** (discussion #349,
@ContractorKeith). The Transaction Detail by Account and categorized-sheet
imports cut their list at twenty and said "… and 14,070 more", and the
person asked whether there was a way to see the whole report. There was
not. Both lists come whole now, as the IIF import's already did; the box
on the Interoperability page says how many problems there are and has
**Save the list**, a text file of every line (the desktop app saves it to
Documents/SlowBooks Pro/Reports and says where).

A QuickBooks Desktop ledger export whose parent accounts have sub-accounts
imports: QuickBooks reports a parent's own postings in a section it names
"<Parent> - Other", which is not an account, and the import refused the
whole file as "accounts not in the chart" for each of them (discussion
#349). Those lines post to the parent account itself now.

**Profit & Loss and Balance Sheet compare to last year** (#347, PR #367, @JohnH-code). "Compare to
same period last year" on the P&L, and "Compare to same date last year"
on the Balance Sheet, add a Prior Year column on screen and in the PDF
and CSV (`compare=prior_year` on `/api/reports/profit-loss`,
`/api/reports/balance-sheet` and their exports), with $ Change and
% Change columns that "Show $ and % change" turns off (`hide_changes=1`
on the exports). The prior period is the same dates one year earlier, a
month's last day mapping to that month's last day, so a full February
compares with a full February across a leap year. An account with
activity in only one of the two years shows 0 in the other; % Change is
"—" against a zero. The rows keep the chosen order, and "Without
adjusting entries" applies to both years. A prior-year amount drills into
that year's transactions and "Back to …" returns to the comparison; both
choices ride on the address and in saved reports. The Balance Sheet
screen also shows the Liabilities + Equity total its PDF already had.

A Balance Sheet line's drill-down for an as-of date in an earlier year
started on January 1 of the current year, after its own end date, so the
dates were refused and this year's transactions shown instead. It starts
on January 1 of the as-of date's year (#347).

**The served sign-in reaches the company chosen** (#369, #370, PR #372,
@ContractorKeith). A session signed in to a company whose file then went
missing chose another company on the sign-in screen and got a blank page;
it switches there signed in now. The setup form on a served host offers the
host's other companies a choice away.

**The Mac app needs macOS 15 or later** (PR #376, @ContractorKeith). GitHub
retires its macOS 14 build machines on November 2, so the Mac app is built
on macOS 15. The PDF libraries it carries are built for the macOS they are
built on, so 2.26.0 opens on macOS 15 (Sequoia) or later, and says so
(`LSMinimumSystemVersion` 15.0) rather than closing at launch on macOS 14;
every Apple Silicon Mac can update to 15. The build refuses a bundle whose
libraries need a newer macOS than the app declares. latest.json names the
lowest macOS (`macos_minimum`), and from this release the update check does
not offer a Mac an update it can't open.

One schema change: `users.session_version`, run in each company file.
587 API operations (`GET /api/payroll/{id}/nacha`).

### v2.25.0 — Several companies on one server

**Server Edition serves every company file** (#358, PR #363, @ContractorKeith). The sign-in screen asks
which company, then who; a session works in the company it chose, so two
people work in two companies at once. Each company keeps its own users and
roles. Signing in checks the same username and password against the other
companies, and the topbar's company switch opens each one that matches in one
click, with that company's role. The rest ask for their own password. Audit
log, backups, bank feeds and settings stay per company. A company made under
Companies is set up by the administrator who opens it. An API token's request
names its company with `X-SlowBooks-Company`. Starting with `--serve-lan` brings
every listed company up to this version first. The desktop window still opens
one company at a time, and a PostgreSQL server still serves one database.
A company the server cannot open now (its file missing, locked or from another
version) answers with one sentence, from the gate as well as from a page, and
the sign-in screen still lists the companies so another can be chosen. The
one-click switch looks at the person as they are in the other company now:
a password changed or reset there since the sign-in, or a one-time password
waiting to be replaced, asks for that company's password instead. The setup
wizard's encrypt box on a company served with the others leaves the started
company's key alone (a served company is encrypted under the host's
`COMPANY_FILE_ENCRYPTION_SECRET`). A company taken off the served list gives
its engine up at once. Merged as contributed, with these on top.

No schema change. 586 API operations (`POST /api/auth/company`).

### v2.24.1 — Checks from the register, and a ledger export caught

**Print Check for a check written in the bank register or as an expense**
(PR #362, @ContractorKeith; discussion #354). Print Check sat on bill
payments alone. A check typed straight into the register (+ Entry, the
quick-entry row, a split) or entered as an Expense paid from a bank account
now prints from its register row and from the expense's view, on the same
3-per-page voucher as a bill payment's check: the payee from the vendor or
the entry, the reference as the check number, the entry's memo, and each
split line on the stub. A deposit, a card charge, a transfer and a void are
refused with the reason, and a bill payment's check prints from its bill.
Merged as contributed.

**A QuickBooks ledger export with an added Account column is refused, not
read as sales** (#359, PR #360, @ContractorKeith; discussion #349). A
Transaction Detail by Account export with an Account column added matched
the sales-report signature and posted sales receipts and payments instead
of ledger history. The account-grouped layout is now recognized by its
section headings before the sales check, and such a file is refused with
the sentence that says to export again with the default columns and the
All accounts filter. The default export is read exactly as before. Merged
as contributed.

No schema change. 585 API operations.

- Refuse account-grouped QuickBooks ledger CSVs with an added Account column
  before they can be mistaken for sales documents. The error explains the
  default-column export and All accounts filter (#359, discussion #349).
- Print Check for a check written straight into the bank register or
  entered as an Expense paid from a bank account, from the register row
  and the expense's view: the same 3-per-page voucher as a bill payment's
  check, each split line on the stub (`GET
  /api/checks/print?transaction_id=`). A deposit, card charge, transfer or
  void is refused with the reason (discussion #354).
### v2.24.0 — Working with your accountant

**An Accountant role** (#340). Settings → Users offers Accountant: a sign-in
that reads everything, the reports, every list and the audit log included,
posts journal entries, voids the ones it posted and attaches the support
behind one, and nothing else; every other write is refused with a sentence
that says what the role is for. Payroll and HR stay the administrator's.
Settings → Users also **invites** an accountant (#345): a one-time password
shown once with the address, each line with a Copy button of its own and
the password free of look-alike characters (no 0/O, 1/l/I), an end date if
the engagement has one, and their own password set at the first sign-in;
an ended sign-in is refused with the date, and the Users list shows each
person's last sign-in.

**Adjusting entries, marked and reported** (#341). A journal entry can be
marked as an adjusting entry (the accountant's are, always), carries who
posted it, and the Journal Entries list filters to them. Reports →
Adjusting Journal Entries lists them for a period as PDF or CSV, and the
Profit & Loss and Trial Balance run without them on request, so a
before-and-after is one checkbox.

**Closing Date Exceptions** (#342). Every change made into a closed period
with the closing-date password is written to the audit log as such, and
Reports → Closing Date Exceptions lists each use: who, when, the closing
date at the time, and every row it changed before and after, as PDF or CSV.

**The Accountant's Package** (#343). Reports → Accountant's Package is one
zip for the period: the Financial Statements Pack, the General Ledger and
Trial Balance as CSV, the A/R and A/P agings, each bank reconciliation
completed in the period, the Adjusting Journal Entries and Closing Date
Exceptions reports, and the audit log since the closing date; the dialog
lists the files before the download.

**The QuickBooks Desktop round trip, both ways** (#344). The IIF export
carries checks, card charges, transfers, bill payments and journal entries
(CHECK, CREDIT CARD, TRANSFER, BILLPMT, GENERAL JOURNAL), so the whole
ledger reaches QuickBooks Desktop; the IIF import takes the same five
blocks back as journal entries, a block already in the books posting
nothing twice, and GENERAL JOURNAL blocks post as adjusting entries on
request (tick *This is an accountant's file*). The Transaction Detail by
Account import takes an *Adjusting entries only* mode that refuses the
file whole if it holds anything but journal entries.

**SlowBooks' own Accountant's Copy** (#346). Companies → Send to accountant
writes an encrypted copy of the books (a passphrase shown once, your
sign-ins and secrets left out) with a dividing date. The accountant opens
it in their own SlowBooks Pro, where it takes journal entries dated on or
before that date and nothing else, a banner on every page saying so, and
sends one signed change file back. Apply accountant's changes shows every
entry first, posts the new ones as adjusting entries in the accountant's
name, once, and refuses a file from another company or altered on the way.
The guide is docs/working-with-your-accountant.md.

**A categorized bank sheet imports as posted transactions** (#328). The
Transaction Pro / SaasAnt layout (Bank Account, Transaction Date, Memo,
Payee, Account, Amount, by name in any order, the bank named in the
header or per row) imports under Interoperability's report upload: each
row the register entry it is, rows sharing the date, payee and memo one
split entry, a transfer when the account is another bank or card account;
the file is checked whole first, a row already in the books posts nothing
twice, and payees not on the lists are added. A switch takes sheets that
write money out as positive.

**Reclassify Transactions** (#337). Reports → Reclassify Transactions: pick
an account and a period, tick the lines to move, choose the account they
belong on and a class, preview, then post the move as one audited change
naming every line. A bill's own line follows its posting; the bank side of
an entry and a document's item lines stay put and say why; a move dated
into a closed period takes the closing-date password and shows on the
Closing Date Exceptions report. The accountant role may use it.

**Open screens** (#336). The pages, registers, documents and dialog reports
opened in a session are listed under Open at the top of the sidebar, each
a link back and each closable: a report with its period, a register by its
account, a document by its title. The one showing is marked, closing it
goes to the one before, a reload keeps the list and Close all empties it.

**Company file encryption on the desktop** (#290). Settings → Company File
Encryption → Turn on (or the box on first-run setup) makes a recovery key,
shown once with Copy and Save as a text file, encrypts the company file
under it, and wraps the key for your password in a keyring beside the
file. The company picker then unlocks the books with an administrator's
sign-in, or the recovery key; every other administrator unlocks after
their next sign-in, and one removed, demoted or reset no longer does. New
recovery key re-keys the file and retires the old one. The recovery key
opens the file and every backup of it on any machine: a restore of a
backup made under another key asks for the recovery key it was made under.
Server Edition keeps COMPANY_FILE_ENCRYPTION_SECRET; the guide is
docs/company-file-encryption.md.

**Also.** The Companies page on a server install says how a company is
switched there rather than offering the desktop's picker (#352, Docker);
an imported journal entry (the lists IIF's opening balances, a Transaction
Detail by Account journal, a migration's) voids from the page like a
hand-typed one (#355); and the IIF import's unsupported-type note names
the types it takes now.

**Fixed.** On macOS and Linux, a plain (unencrypted) company file lost the
entries made after Create Backup, or after Send to accountant, when the
app quit or switched company: the backup read the file through a second
SQLite library, which deleted the write-ahead log under the one the app
writes with. Every release from 2.23.2 to 2.23.5 had it; an encrypted
file never did. Every read of a company file goes through the one library
now. The audit log no longer records a password hash or a secret setting's
value (an UPDATE copied them before and after), and the Audit Log page,
the Accountant's Package and the Accountant's Copy redact what earlier
releases wrote. Closing Date Exceptions works on the page's clock, so an
evening override is in the day it was made and times show in local time
with the zone named; accounts on it are named, not numbered. First-run
setup refused for a company name another file already has shows that
sentence and stays on setup (it switched to a sign-in form for a company
with no password).

Schema: migration `c2d3e4f5a6b7` (an entry's adjusting flag and who posted
it, a user's one-time-password flag and end date, the accountants_copies
table). 585 API operations (the Adjusting Journal Entries, Closing Date
Exceptions and Accountant's Package reports with their files, the four IIF
exports, `POST /api/users/invite`, `POST /api/auth/password`, the seven
`/api/accountants-copy/*` calls, `/api/reclassify/lines` and `POST
/api/reclassify`, and `POST /api/company-file/encryption/recovery-key`).

### v2.23.5 — A bookkeeper's first hour

**A SimpleFIN feed that reports every sign the other way round imports
right** (PR #327, @DefiAlan). Some bank connections present a checking
account as a liability: a negative balance, deposits negative, withdrawals
positive, so withdrawals imported as deposits and nothing auto-matched.
The protocol gives no field to tell by, so the person says so: a Reverse
signs checkbox per feed on Banking's bank-feed list, kept as a setting and
applied before the rows reach matching, rules and dedup. Claim and
disconnect reset it. Lines already imported the wrong way round are not
rewritten; they dedup by their SimpleFIN id on the next sync.

**The bank register you can type into** (#329). A bank or card account's
register reads oldest to newest, as a ledger does, with the running balance
down the right, and its last row is an entry row: date, payee, memo, ref,
category, the amount out or in; Enter or Post posts it through the same
call as + Entry, and the next row is ready. The dialog stays for anyone
who prefers it.

**A register entry split across accounts** (#330). A check that pays several
things, or a loan payment that is principal and interest, is one entry
with several lines: "Split this entry…" on the dialog takes an account and
amount per line, says what is left to place, and posts one entry. On the
API, `splits` on `POST /api/banking/transactions`; the lines must add up
to the amount, take its sign, and a bank or card account is a transfer,
never a split line.

**Reconcile shows only the lines through a day** (#331). Several statements
reconciled back to back are started with the last statement's date; "Show
only lines through that date" hides the later lines while a month is worked
and says how many it hid. The session's figures are the session's; Mark
through takes the same day.

**Account numbers are plainly optional, and the seeded chart's unused
accounts go in one step** (#332). The account form says the number is
optional (it always was in the books). "Remove unused seeded accounts…" on
the Chart of Accounts lists every seeded account with no postings, no
sub-accounts, no register or setting naming it and no control role, and
removes them together; the ones that stay are named with why.

**The drill-down saves as CSV and PDF** (#333), the figures on the screen,
the account and range in the file name: what a client asks for when they
want to know what was spent on one account.

**The sidebar hides what a company doesn't use** (#334). Settings → Sidebar
lists every section and link; the ticked ones leave the menu. A hidden page
still opens by its address, and the list is on the Settings page.

**Vendors: a search box and paid-to-date** (#335). The Vendors list filters
as you type (name, company, phone, email) and says how many of how many
show. A vendor's page carries paid this year and all time, and Save CSV
lists the payments with a total, for any date range on the API.

**Time by Job by Name detail imports as QuickBooks writes it.** 2.23.4's
importer took that report with the by-Name layout's groupings swapped,
which is not how QuickBooks Desktop lays it out: a user's real file has
the customer:job as the outer heading, the service item as the inner one
and the person as a Name column in place of Item. The importer read it as
"Unrecognized report layout"; it reads both now, and the result names the
report. (The fixture for it is invented timesheets in that exact layout;
the real file stays private.)

No schema change. 561 API operations (the drill-down's CSV and PDF, the
vendor's payments CSV, the seeded chart's dry run and removal).

### v2.23.4 — Every QuickBooks Desktop transaction, as journal entries

**QuickBooks Desktop's Transaction Detail by Account imports.** The report a
bookkeeper exports for everything (Reports > Accountant & Taxes >
Transaction Detail by Account) lists every deposit, check, transfer, general
journal entry and opening balance, grouped by account, with the other side
of each in Split. Import from Report CSV refused it as an unrecognized
layout, so a Desktop company's transfers and journal entries had no way in.
It now posts each transaction as one balanced journal entry with its date,
number, name and memo, on the accounts the lists IIF made, sub-accounts
included. Before anything is written the whole file is checked: every
account in the chart, every transaction balanced, and every account's lines
adding up to the total QuickBooks printed for it; a file that fails is
refused whole, with the reason. Same-day transactions with no number are
told apart by name, and a journal entry whose number QuickBooks printed on
one line only is still one entry. A second upload posts nothing twice. A
register-style report (no account above its rows) is told which report to
export instead. Built against a real company's report, a year of a small
practice's books in which every account matched QuickBooks to the cent; the
fixture is invented books in that layout, and its sample company, Morden
Bookkeeping, is named for Kim Morden Bookkeeping, who brought the report.

**Accounts in order on the Profit & Loss and Balance Sheet** (#326,
@TigerCoder27). Both listed accounts in the order they were created (the
query grouped by account with no ORDER BY), so a chart added to after
seeding read 6000, 6200, 6600, 6900, 6250, 6410. They read by number, then
name, as the Trial Balance and General Ledger do, with an account that has
no number after the numbered ones and a sub-account right after its parent;
"Sort by" on either report lists them by name instead, and the choice rides
into the PDF, the CSV, the address and a saved report (`sort=name` on the
API). A statement row names a sub-account by its path ("Automobile:
Insurance") with its number first, so two accounts of one name tell apart
and a sub-account whose parent has no amount still says where it sits; the
Trial Balance and General Ledger put the accounts with no number last too
(they came first, in creation order). A company-wide default for the order
can follow if it is wanted.

**QuickBooks Desktop's timesheets come across as time entries** (#323,
@rcheetham). Desktop does not export timesheets to IIF, so a company has a
report: Reports > Jobs, Time & Mileage > Time by Name by Job detail (or
Time by Job by Name detail), saved as CSV or Excel, with a heading row per
person and per customer:job, one row per entry (Date, Item, Billing Status,
Duration, Notes) and QuickBooks' own Total rows. Import from Report CSV now
takes it, Excel included: each row becomes an approved time entry under its
employee, with the hours, the notes, the service item, the billing status
(not billable, unbilled, billed) and the customer or job label as QuickBooks
printed it, linked to the job when the label is one on the Jobs list. A
person not on the Employees list is added from the name and named in the
result, for their pay to be set. Imported time is history: it is marked as
QuickBooks' on HR > Time Entries, and a pay run never picks it up. Before
anything is written the whole file is checked, every group's hours against
the total QuickBooks printed for it; a file that fails is refused whole. A
second upload posts nothing twice; a row QuickBooks holds twice is two
entries. Durations read as 2.25 or 2:15. Built against a real consultancy's
half-year of timesheets (6,277 entries, every total matched); the fixture
is invented timesheets in that layout.

**The migration guide says what does not come across yet** (#323): payroll
history, estimates and purchase orders as documents, inventory on hand,
memorized transactions and attachments; each gets built around a real
export someone sends.

**From the gate's first round** (Windows W-1 to W-4; macOS NEW-122 to
NEW-124): the imported entries list on Journal Entries, and their Type reads
"QuickBooks Desktop import"; the result counts them ("Imported 8 records",
not 0); the Import from Report CSV panel names Transaction Detail by
Account; and a lists IIF's bank and credit card accounts come in as
registers, so Banking lists them (they came in as plain accounts).

**From the gate's second round** (macOS NEW-125, NEW-126): a lists IIF's
credit card accounts (CCARD) come in as liabilities; they fell back to
expense accounts, which the register flag then put on Banking as cards. A
journal entry's view no longer prints "- " before an account with no
number.

**From the gate's third round** (Windows W-5, macOS NEW-127, both older):
the lists IIF import matched an account by its leaf name alone, or by its
number alone, whatever its type, and merged in silence: an income
sub-account "Maintenance & Repairs" landed in the expense account of that
name, "Retained Earnings" (3100 in the file) in the chart's 3100 Owner's
Draw. An account is its whole path now, matched under the same parents and
of the same type; a number alone never matches, and a number the chart
already uses is dropped with a note; QuickBooks' own names the chart knows
("Inventory Asset" is 1300 Inventory) still match, named in the result; an
account of another type with the same name is created beside it, with a
note. And a second upload of the same lists posted every opening balance
again, doubling them all: a balance an earlier import posted is not posted
again, and the result says how many were left alone.

**From the gate's fourth round** (macOS NEW-128): lists first imported on
an older version, whose matching merged accounts wrongly, and imported
again on this one made the right accounts beside the wrong ones and posted
their balances a second time, the same debt twice. Opening balances come
with a company's first import of the lists; a later import posts none, and
names the accounts it created that carry a balance in the file, for the
person to enter any that are real by hand.

**From the gate's fifth round** (Windows W-6; macOS NEW-129, older): that
note names every account it created with a balance in the file, however
many (it stopped at twelve, "and 15 more", and the rest appeared nowhere);
the list of accounts matched to the chart by a name it knows is whole too.
On the dashboard's A/R Aging bar, the light theme's 31-60 slice printed its
figure in black on a bright orange, which WCAG 2's arithmetic clears and
the eye does not; the slice is a deeper orange now and its figure prints
white.

**From the gate's seventh round** (macOS NEW-130 to NEW-135; Windows W-7
to W-11): a timesheet in QuickBooks' Minutes format imports (0:20 three
times under a total of 1:00 was refused as short: totals are compared in
whole minutes now, and a decimal that is minutes shown to two places counts
as those minutes); a Total the Excel export writes as a formula reads from
the value Excel computed for it, and a Total that cannot be read refuses
the file instead of being passed over; the New Pay Run form's time column
leaves imported time out, and a run with nothing else to pay says the
approved time there came from QuickBooks; the report file input takes
.xlsx; the result names the report it read (by Name by Job, or by Job by
Name); a seam between the aging bar's slices keeps two bands of one
lightness apart; and What's new says the statements changed their order.

**From the gate's eighth round** (Windows W-12, macOS NEW-137, NEW-138):
the Trial Balance and General Ledger name a sub-account by its path and
number too, on the page, in the CSV and in the PDF (they took the
statements' order and printed "6210 Fuel" after a parent with no balance);
and a Minutes-format timesheet opened in Excel or Numbers and saved as
.xlsx, whose durations became times of day (00:20:00), imports.

Schema: five nullable columns on time entries (the import's source and
row fingerprint, billing status, service item, customer or job label),
migration `b1c2d3e4f5a6`. 556 API operations.

### v2.23.3 — Voids that always post, and imported invoices paid once

**Voiding never fails on a long line memo** (#318, PR #319, @Justin-Rezamp).
A reversal writes "VOID: " before each line's memo, so a memo of 295 to 300
characters came out over the column's 300 and PostgreSQL refused the row:
the void answered 500 and the entry could never be voided. The reversal's
copy of the memo is cut to fit (the original keeps its full text), on every
void path, and an in-kind gift's posted memo the same. A journal entry whose
reversal still cannot post is left as it was with a sentence (400 or 422),
and a value the database refuses anywhere answers 422 with a fixed sentence
instead of a 500; the driver's text goes to the log only. Merged as
contributed.

**Invoices the QuickBooks Online import counted as paid twice are
repaired** (#317, @tim-getthehayout). The connector removed in 2.23.2 set an
imported invoice's amount paid from QuickBooks Online, then its payment
import added each payment again: a paid invoice read paid twice with a
negative balance, a partly paid one owed too little, and the aging,
statements and open lists were off (the ledger was right). On the first
start, an invoice the import created whose amount paid, less credits
applied here, is exactly twice what its payments applied gets its paid
amount, balance and status back. Any other invoice is left alone.

**The Interoperability page says QuickBooks Desktop** (#316): its notes
named QuickBooks 2003 Pro, as if IIF worked with that version only.

No schema change. 556 API operations.

### v2.23.2 — QuickBooks Online without the API, and the company file encrypted

**Migrate from QuickBooks Online with its own export.** The QuickBooks
Online path needed each user's own app in Intuit's developer portal, and
keys that reach a real company sit behind Intuit's app questionnaire, an
hour of vendor questions a bookkeeper connecting their own books cannot
answer (@sutehk gave up after two, and said so; the sample company in the
tests is named for them). QuickBooks Online's Export data (the
gear, Tools, Export data) hands every user a zip of Excel workbooks, and
Migrate Data takes that zip as downloaded, or its workbooks, or those saved
as CSV, with no Intuit app at all. The chart comes from the Balance Sheet
and the Profit and Loss, typed by the section each account sits under,
sub-accounts named Parent:Child as QuickBooks writes them and placed under
their parents, banks and cards marked as such. Every transaction in the
Journal, invoices, payments, bills, checks, deposits, transfers and journal
entries alike, posts as a journal entry with its date, type, number, name
and memo, each block checked against the totals the file carries. The
Customer and Vendor lists come across with their emails, phones and
addresses. The General Ledger repeats the Journal by account and is left
alone, as is the Employee list, which the export carries without payroll.
A Trial Balance exported on the cash basis is set aside with a note rather
than read as opening balances, since the Journal is accrual; exported on
the accrual basis it verifies the import. Built against a real company's
export, twenty-four years and 9,510 transactions, every block balanced to
the cent; the repository's fixtures are invented books in that layout.

**The QuickBooks Online connector is gone.** Intuit OAuth, the import and
the export over Intuit's API, the QuickBooks Online page in the sidebar,
the Intuit app credentials under Settings, the eleven `/api/qbo` routes,
the setup guide and the two SDKs (python-quickbooks, intuit-oauth) are
removed. The connector never served an end user (the paragraph above
says why), and the product touted it as working; a dead end is not
shipped. A company file that was connected while it existed is cleaned of
the connection's credentials and tokens on the first start after
upgrading, and keeps what the import made: the documents it created still
void and edit as they did, the item a discount came in on still takes a
negative line, and the mappings table stays (no schema change). On the
Migrate Data page the journals' Type reads "QuickBooks Online export".

**Also.** Migrate Data accepts a zip or an .xlsx for every source (a sheet
is read as CSV), the migration engine reads a figure written as formula
text ("=23683.16"), and a source may name an account's bank kind and
parent. One new dependency, openpyxl.

**Company-file encryption, the plumbing** (#290, PR #311, @grungenut).
The first of the pull requests behind #290, encrypting the company file at
rest. Nothing changes for a company that does not turn it on.

- **The company file can be a SQLCipher file.** The app opens one when its
  key is in hand: the launcher has the key before the "already running"
  check, before the migrations and before the server starts, and hands it
  to each. A migration or a start without the key stops with one sentence,
  never a traceback or SQLCipher's own log lines.
- **Server Edition unlocks from an operator-set credential.** Set
  `COMPANY_FILE_ENCRYPTION_SECRET` where SlowBooks Pro starts, the way
  `PAYROLL_ENCRYPTION_SECRET` is set; the key derives from it the way the
  payroll key does, under its own salt.
- **Settings → Company File Encryption**, administrators only: whether the
  file is encrypted and where the key comes from, and the switch, which
  asks for the company's name typed before it rewrites the file in place
  (SQLCipher's own export). A PostgreSQL company is told that encryption at
  rest is the database host's job.
- **Backups of an encrypted company are encrypted** under the same key,
  and a restore leaves the books in the state they are in, whichever state
  the backup was in.
- The desktop's own key management (a key per installation, wrapped for
  each administrator and under a recovery key) is the next pull request;
  until then a desktop company turns encryption on with the same
  operator-set secret.
- `sqlcipher3>=0.6.3,<0.7` is a new dependency (Windows, macOS and Linux
  wheels, SQLCipher 4.12 linked in). Both installers' smoke tests read its
  version through the frozen bundle, then write and read back an encrypted
  scratch file.

**Also in this release.** The 2.23.1 entry below now names the compose fix
it shipped with (#307). The reader that decides whether a desktop's shared
uploads folder is still needed leaves an encrypted company file alone: it
cannot read one, and the plain-SQLite read it made would have written
sidecar files beside the encrypted file for nothing.

**From the gate's first round** (Windows W-1, W-2; macOS NEW-108 to
NEW-110): a Turn off that could not replace the company file (another
program had it open) left a plain copy beside the encrypted one; it now
tries again for a moment, then says so in one sentence and leaves nothing
behind, and the next start sweeps what an interrupted rewrite left. "The
key comes from COMPANY_FILE_ENCRYPTION_SECRET" where Settings said
"launcher". A wrong key is refused before anything opens the file (it
used to write to it first). The dates of a user's last sign-in and a
token's last use carry their time zone, so an evening in Central time is
no longer tomorrow's date.

**From the gate's second round** (Windows W-3, W-4; macOS NEW-111 to
NEW-117): the Migrate Data page counts the customers and vendors with the
accounts and journals ("Imported 13 accounts, 13 journals, 2 customers and
4 vendors"), says "1 journal" rather than "1 journals", and a dry run
counts what an import would make, so a second upload of the same export
reads "0 accounts, 0 journals, 0 customers, 0 vendors ready to import"
above the note that says why. The engine's "No trial balance file
supplied" note stays out when the dialect set a cash-basis Trial Balance
aside with its own. Chart of Accounts orders accounts without a number by
name, so a sub-account sits with its parent. The guide says how
receivables and payables cross over: the export's A/R and A/P come in as
their own accounts, and the open documents at the cut-over are entered
afterwards against them.

**From the gate's third round** (Windows W-5; macOS NEW-114, NEW-118):
the shell page still carried the connector's sidebar link, which opened
"Page not found", and its script tag, a 404 on every page load; both are
gone. The bank registers, the General Ledger's drill-down and a journal
entry's view name a posting's source in words ("QuickBooks Online
export", "Wave import") instead of its key. Journal Entries lists the
journals a migration posted, every Migrate Data source's and the IIF
opening entry; before, the 13 the export route posted were on no list,
and a drill-down's link opened one over an empty page.

**From the gate's fourth round** (the owner, NEW-119 and NEW-120): the
sidebar's "QuickBooks Interop" entry reads "Interoperability", and so do the
page's heading and its status line; the guides say the same. And the
invoices still open come across: upload QuickBooks Online's A/R Aging Detail
report (Reports → A/R Aging Detail → Export to Excel) beside the zip, and
each open invoice arrives as an invoice with its number, date, due date and
open balance, and each payment not yet applied as an unapplied payment for
what is left of it, so the A/R aging and every customer's balance are right
from the first day. Their postings move the open balance from the export's
own receivable account, where the history left it, to 1100 Accounts
Receivable, and touch no income; that account ends at zero. The report is
checked against the Journal before anything is written, and a difference is
named. Built against a real company's report, every row matched to its
Journal block; the sample's report is invented books in the same layout.
Unpaid bills are still entered after the import.

No schema change. 556 API operations (the two company-file ones added,
the eleven QuickBooks Online ones removed).

### v2.23.1 — Entities keep their class

A QuickBooks file that keeps its entities apart by class (a nonprofit and
two LLCs in one file is the usual shape) lost that coding on every
migration path, so the new books showed income under Uncategorized and
lines under another entity's class (#308, raised the morning after 2.23.0
by an accountant's advice to diff the trial balance and open AR/AP per
entity before switching).

**The IIF import carries CLASS on every block type.** It read the class
for BILL and DEPOSIT blocks only, so an invoice, sales receipt or
estimate whose rows all said North LLC arrived with no class on the
document, its lines or its posting. Every block now takes the TRNS row's
class as the document's and each split row's own class on its line and
its journal line (an estimate's lines carry none, so an estimate takes
the document's), with the header's as the fallback; a block whose lines
belong to two entities imports with each line under its own (it was
refused); and a class the file names that the list did not carry is
created on first sight with a note, as a customer or a job is.

**The report CSV import reads the Class column.** On all four reports it
takes (Transaction Detail for invoices and sales receipts, Deposit
Detail, Check Detail), the header row's class is the document's and each
line's is its own, created on first sight with a note. The column was
indexed and never read.

**The IIF export writes each line's own class.** It wrote the document's
header class on every split row, so a document with a North LLC line and
a South LLC line left with both under North LLC; line-level classes
exist since 2.22.0. The header's class is the fallback for a line without
one. Deposits too, and a deposit's MEMO goes out as it was typed,
without the "IIF Import — " an import wrote in front of its description
(each round trip added another).

**Contributed fixes** (@lEWFkRAD). Migration dry runs refuse a chart, ledger or supplied trial-balance file with no
recognised records, instead of reporting a successful empty import or skipping balance verification (PR #304).
IIF sales receipts keep the deposit account of their own allocated payment when the customer has other payments
on the same day; the latest same-day payment could supply another receipt's account (PR #305). The Sage 50 US
migration accepts the numeric account types the Chart of Accounts List export carries, checked against a Sage 50
2026 sample-company export; a real export rejected all 156 accounts (PR #306).

**Smaller things the gate found.** In the dark theme a green or red amount
read as plain text (the theme's plain-amount rule outranked the colour by
loading later), and the success toast kept 2.23.0's green; both carry the
leaf green now, and so do bold and amber figures (the aging tables' totals
and 30+ and 60+ columns, the cash forecast's net). The shared per-line
Class box has a minimum width, so "North LLC" no longer reads "North LL",
and the bill form's line table, the widest, has room for Item, Account
and Cost Code: the dialog opens as wide as the window allows, like the
estimate's and the job cost entry's, so every column shows at rest where
there is room, and the table scrolls sideways in its container where
there is not. Under every scrolling line table there is room for a
classic scroll bar, so "+ Add Line" sits below the bar on a Mac rather
than half under it (the job cost entry had that since 2.18.0). The IIF
import's result ends in one sentence on a line of its own, "3 transaction
blocks in the file: 3 imported, 0 already here, 0 not imported, 0 failed"
(as a label beside a count it wrapped, the count reading as a superscript),
and its toast counts what the panel counts, "Imported 1 class and 3
transactions", rather than a total the panel never shows. The migration dry
run no longer says a ledger had "no records recognised" when its rows
were read and refused one by one for accounts an empty chart does not
list; that sentence names a file only when its parser read nothing. And
docker-compose.prod.yml parses again under Compose 2.40 (#307,
@bracycpa): its two required-secret lines carried ": " inside unquoted
messages, which that Compose read as a mapping; they are quoted, with the
colon gone, and a test parses every compose file in the repo. The
Windows CI skip budget counts the bill form's browser measurement (118).

**A leaf green of our own.** The green that amounts, import results and
the success toast use was the teal-leaning one the QuickBooks palette gave
it, named for that heritage; it is a leaf green now (#257321 in the light
theme, #6cc46a in the dark), named SlowBooks' own, at 5.91:1 on white,
4.86 on a hovered totals row and 7.54 on the dark panel, white reading on
it at 5.91 for the toast.

No schema change. 565 API operations.

### v2.23.0 — Both ways with QuickBooks

Every issue that was open after 2.22.3, in one release.

**Invoices from a QuickBooks Desktop report** (#291). Desktop's own IIF
export is lists only, so a Desktop user's invoices never arrived by IIF,
and the report-CSV path took sales receipts, deposits and checks but not
invoices. The same Transaction Detail report filtered to Invoice now
imports each invoice with its lines (item, quantity, price, tax), its
Customer:Job and its number; open, partly paid or paid by the report's
Open Balance column, with a payment for what was paid; without that column
every invoice comes in open and the import says how many did, and
uploading the report again with the column marks the open ones here paid or partly paid
from it. An invoice is already here when it is the same customer's under
the report's number, as it was numbered here or as the import renumbered
it when that number was taken (QB-1031) — not when any invoice here
carries the number. An invoice's header row posts to Accounts Receivable
and a receipt's to the deposit account, so one file can hold both.

**The IIF export keeps an invoice's job, items and quantities** (#199).
It wrote the customer without the job and split rows with no item,
quantity or price. Per Intuit's own sample import files: the TRNS row
names the full Customer:Job; INVITEM, QNTY, PRICE and TAXABLE sit on the
split rows only, the quantity negative and the price positive on a sale;
the import reads TAXABLE back. On a document in another currency PRICE
is the line's booked home-currency amount divided by its quantity, to the
places the quantity needs, so QNTY × PRICE gives the file's AMOUNT to the
cent at any quantity. Invoices and sales receipts.

**Quantities keep four decimal places.** Every sale and purchase line's
quantity was stored to two, so the IIF import and the report import kept
2.125 as 2.12 (or 2.13), the edit form showed the rounded quantity with a
total cents short, and the export wrote it beside the line's real amount.
Invoice, estimate, credit memo, recurring invoice, bill, purchase order
and vendor credit lines now hold four places (migration e8f9a0b1c2d3), as
unit prices have since 2.17.3; the API and the documents show a quantity
as entered (2.125; 3.00 as before), the forms take four places, a finer
one is refused, and the imports round one with a note, setting its unit
price so that quantity × price still gives the line's amount. **The IIF import names what it does not
take** (#288): a GENERAL JOURNAL, CHECK or other block outside the
dispatch was skipped without a word; Validate now lists each with its
number, type, date, document number and name, the import counts them,
and every parsed block is reported as imported, already here, not
imported or failed.

**Retry-safe payment recording** (#292). A POST that commits and then
loses its response was retried and booked twice. An Idempotency-Key
header on POST /api/payments or /api/bill-payments names the intended
payment: the same key with the same body answers with the first
response, the same key with another body is refused (bodies compared by
their values, so 100 and 100.00 are one amount), a first delivery still
running is refused until it finishes, and every keyed answer names its
key in an Idempotency-Key header. The Receive Payment and Pay Bills
forms send one per submit. One new table, request_keys (migration
d6f7a8b9c0e1).

**Line replacement is audited per line** (#289). Editing a posted bill's
or invoice's lines removed the old ones past the audit hooks; every
replacement now writes a DELETE event for each old bill line, invoice
line and journal line, with its account and amounts, in the same
transaction as the edit — the QuickBooks Online re-import of a changed
sale and an estimate edit included — and each replaced row leaves the
session as it goes.

**Two things said plainly.** The invoice form says that creating an
invoice posts it to the books, Draft meaning not yet marked sent, and the
Create button names that note for a screen reader (#293).
The AI guide on the site, the README and the docs say what stays on your
machine — the company file and the API — and what a cloud-backed CLI's
model sees (#294).

No new API operations: 565. Two migrations (request_keys; line quantities to four places).

### v2.22.3 — The ledger behind the dashboard

Two gaps from a user bookkeeping a bank-fed, journal-entry-heavy company
(#281, @sgwyther-gif).

**Analytics and the AI brief read the ledger.** Expenses by category
summed bill lines on paid bills, and the revenue trend paid invoices, so a
company that reconciles through a bank feed and records the rest as
journal entries saw an empty doughnut and an AI Insights brief with nothing
in it — silently wrong, not merely incomplete. Both now read the ledger:
everything posted to expense and cost-of-goods accounts, and to income
accounts, in the period — bills when dated (paid or not), expenses,
register entries, card charges, payroll and journal entries — as the Profit
& Loss counts them; a voided posting and its reversal net to zero. The AI
tool behind "expenses by category" questions uses the same query. Revenue
by customer stays what it was, invoices and sales receipts dated in the
period and paid in full, because ledger lines carry no customer; the
screen, the PDF and the KPI labels say which basis is which.

**One basis for the headline figures.** The four KPI cards, the PDF's and
the AI brief's revenue, net income and margin are the ledger's for the
period — the Profit & Loss's figures — so the margin is net income over
revenue (the gate found it dividing paid-invoice revenue into ledger
expenses: a company with journal-entry income and no invoices read "Margin
0.0%" and a loss in a profitable month). The margin shows a dash when there
is no revenue. The dashboard payload carries `totals` (`revenue`,
`expenses`, `net_income`, `margin_pct`, `basis`), the CSV export too.
Expense categories carry the account's name, "6100 - Auto & Truck Expense",
on the page, the PDF, the CSV, in the brief and in the AI tool (it was the
number alone). The AI dropdown's **Expenses by category** says its figures
are the ledger's and sits under Financial Reports (it said "vendor bill
expenses"). The PDF heads the revenue trend "Revenue (ledger)". A chart's
figure is drawn on a slice or segment only where ink or white reaches
4.5:1; where neither does, the table has it (the doughnut's red, blue and
purple drew figures at 4.2–4.4:1).

Also from the gate: revenue by customer counts invoices and sales receipts
before sales tax, in home currency — what the ledger booked to income for
each (it included the tax, so it could exceed the ledger's revenue);
DSO is a dash, "n/a" in the brief, when no invoice or sales receipt dated
in the last 30 days has been paid in full — the card's hover and the
brief say exactly that, since a payment received today on an older
invoice is not in the figure (0.0 read as instant collection beside open
invoices); an expense
account that nets to a credit for the period (a refund larger than the
spending) keeps its row and its place in the totals but gets no slice,
which Chart.js would have sized by the absolute value; the revenue trend's
card has a month-by-month table, and a point on the chart's edge or top
keeps its figure, sliding in from the edge or going below the point — and
never on its own line: a figure its line would run through moves to a
corner on its own side, beside the point, or to the other side, and one
with no such place is left off, the table having it.

**A source run's server stops when the launcher is quit** (#283,
@jacilynh). Quitting the desktop app run from source (`python
desktop_launcher.py`) with Command-Q left its server running: the fix for
#52 gave the installed apps' server a watch on the window that started
it, but a source run started its server another way, without that watch,
and the next launch was refused as "already running" until the leftover
process was ended by hand. A source run now starts its server the same way
the installed apps do. And on Windows the server watches its launcher too
(the gate's W-7): a launcher ended from Task Manager, or crashed, left the
server on its port, since the watcher ran on macOS and Linux only and the
window-close cleanup never saw it go; the server now holds a handle on
its launcher and leaves with it, installed or from source.

**Four findings from a static review of the source** (#284–#287,
@johncarterlives), each reproduced in a test before it was fixed. A bill
payment that names no bank now resolves Checking (1000) before anything
is written — a chart without it gets a 409 naming the account, with no
payment, allocations or bill balances left behind (the default lookup
answered None and the payment committed without a journal). A/P aging as
of a date leaves out bills dated after it, as A/R aging always did; a bill
paid since that date is not rebuilt as owed on it — the report is the
current ledger aged, not a reconstruction. A manual journal takes cents:
the API refuses a sub-cent amount, and the posting service rounds every
line to cents before it checks the balance, so a journal that balanced on
paper and not in the ledger's two decimals is refused rather than stored
a cent out. The 1099 summary and the 1099-NEC / 1096 totals leave voided
bill payments out, as the A/P aging already did.

**A receipt attaches to a journal entry.** `/api/attachments` whitelisted
eight record types and not `journal_entry`, though a journal entry is
sometimes the only right record of a real transaction — an owner paying a
business expense personally, credited to equity, which an Expense cannot be
(it is paid from an asset or liability). The journal entry's view carries
the same Attachments block as an expense's.

No schema change. 565 API operations.

### v2.22.2 — Thirteen quick wins

Thirteen enhancements from Grok's feature exploration of 2.22.1 on
2026-10-11 — read-only, against the code and a sandbox company — filed
as #254–#266 and built one branch and PR each. A patch, not a minor: each
is a small turn on something already there.

**AI Insights lists each provider's models from the provider** (#254). The
app no longer carries a list of models to keep current: with a key
entered, the picker asks the provider (Anthropic, OpenAI, Groq, Gemini,
xAI, Cloudflare, a worker or a custom endpoint) and offers what it
answers, chat models only; Refresh models asks again, and a model is
kept with the key it was chosen for. A request with no model chosen says
so rather than guessing one.

**Write off small balances** (#255). On the A/R Aging report, "Write off
balances under…" lists the open invoices whose balance is under an amount
as of the report's date; one click writes them all off, each to its own
credit memo, and says which were skipped and why (void, already paid, the
balance changed since it was listed). Write Off on an invoice — in its
row and its view — no longer needs a setting.

**The credit limit holds through the API** (#256). An invoice, or an
estimate converted to one, that would take a customer past the limit is
refused with a 409 naming the limit and what would be owed, as the screen
already did; the same request sent again with `allow_over_credit_limit`
goes through, and the forms do that after asking. A sales receipt is paid
on the spot and never refused. An edit is checked only when it raises
what is owed or changes the customer.

**W-9 on file** (#257). A vendor says whether its W-9 is on file; the
vendor's page shows it, the 1099 report carries a column and names the
vendors still missing one.

**Reconcile faster** (#258). Mark all, Mark through a date and Clear all
tick or untick every line of a reconciliation at once, lines after the
statement date never among them. **A rule from a statement line**
(#259): on a line in To review, Rule… opens the bank-rule form with the
payee as the pattern and the chosen category filled in; saved, the rules
run over the queue.

**A deposit slip** (#260). Save PDF and Print on a deposit: the account,
the date, each payment with who it was from and its check number, and
the total; a void deposit's slip says VOID, and a void deposit's view no
longer offers one. **Items to reorder** (#261): a filter on the Items page
showing what is at or under its reorder point, with the reorder point
and how short.

**Cancel a time-off request** (#262). A pending or approved request can
be cancelled from the HR page; an approved one puts back exactly what the
approval took — the hours that were in the bank (an approval can overdraw
it to zero) and the relief's dollars, through a reversing journal — and
stays on the list as cancelled. A request whose dates have passed asks
first. The approval now records what it drew (`hours_drawn`,
`dollars_relieved`); a request approved by an earlier build has neither,
and its cancel assumes the request's own hours. On the portal an employee
withdraws their own request while it is still pending; an approved one is
the employer's to cancel, and the page says so.

**Form history** (#263). Tax Forms gains a history of every W-2, W-3, 940
and 941 generated — what, when, by whom, its hash. Check re-figures the
form from today's books through the same hash the form wrote and answers
in a sentence: still matches, or no longer matches because the pay runs
behind it changed; a voided pay run in its period is said. A W-2 whose
employee is gone stays listed and says so. The hash printed in a form's
footer can be looked up. `document_audits` records who generated a form
from here on; `/api/document-audits` is admin-only, like the rest of
payroll.

**Alt+B for a bill, Alt+E for an expense** (#264), under the rules the
other shortcuts follow — by the key's place, so Option-B works on a Mac
though it types "∫"; never while typing in a field or over a form being
filled in. The toolbar button each entry shortcut stands for announces
it: "Enter Bill (Alt+B)" on hover, "(⌥B)" on a Mac, and
`aria-keyshortcuts` for a screen reader.

**The figure on the bar** (#265). Every bar, slice and point on the
dashboard's and Analytics' charts carries its figure as text on or beside
the mark — past a bar's end, in a slice, above a line's point — so the
colour is not the only way from a mark to its amount; a label that would
land on another is left off, and the table under each chart has every
figure. The dashboard's own aging bar and monthly revenue bars say theirs
too.

**Landmarks and skip links** (#266). The invoice, bill and journal-entry
forms are form landmarks named by the dialog's title, each opening with
"Skip to the line items" as its first stop, which lands the keyboard on
the first line with the focus ring showing; a dialog still opens on its
first field, never on Void or Delete. The chart of accounts is a
navigation landmark with "Skip to the account filter" first. The
accessibility statement's open list is down to one item.

Two schema migrations: `b4d6f8a0c2e4` (the PTO request's `cancelled`
status and the draw columns) and `c5e7a9b1d3f5` (`document_audits.username`).
The Windows CI skip budget counts the deposit slip's real-PDF test (116).

### v2.22.1 — Claude's current models

**Claude's request follows Anthropic's current API** (#251). The request
SlowBooks sent carried a temperature of 0.3, and Anthropic's Claude Sonnet
5.5 migration guide says "on Claude Sonnet 5.5, a non-default value returns
a 400 error". Sonnet 5.5 has been SlowBooks' default Claude model since
2.18.2 and Claude the default provider, so on that model every Claude
request was refused, the Test button's included. Claude requests now send
no temperature. Claude from the 5 line on thinks before it answers, out of the
answer's token budget, so those models get the 8,192-token ceiling OpenAI's
reasoning models have, billed only as used, and any model that thinks first
gets three minutes to answer instead of one. A reply that stops at the limit
says so, and a refusal says the model declined. In a tool-calling analysis,
Claude's turn goes back exactly as it came, its thinking included, and every
tool call is answered by the id Claude gave it.

Run against a live key on the release build: Claude Fable 5.1, Opus 5.5,
Sonnet 5.5 and Haiku 5.5, Anthropic's current models and now the four the
menu offers, each pass the Test button and AI Insights, as does the legacy
Haiku 4.5, and on Sonnet 5.5 a predefined analysis and a tool-calling
question answer correctly. The request 2.22.0 sent is refused by Sonnet 5.5
with "`temperature` is deprecated for this model".

**A bill's class, job, tax rate, vendor, number or currency can be changed on
its own** (#250). `PUT /api/bills/{id}` with only those fields, such as
`{"class_id": 3}`, failed with a 500: an edit that re-posts the journal
without resending the lines handed the stored lines to code that reads
request lines. The stored lines now re-post as they are, each keeping its
fund's default function or its own. The Mac QA agent found it tagging
existing bills to a class through the API.

**Docs** (#252). `docs/features.md` says which AI providers have run against
a live key: Groq at v2.0.0; OpenAI corrected in v2.17.1; Claude brought up to
date and run live here; the others built to each vendor's API and
unit-tested. It replaces a "verified April 2026" that meant the endpoints
were looked up, not run.
Expenses are void-only by design, voided and entered again, and the docs now
say so beside Bills, with the Expenses API.

**From the release gate**, five older things the Mac QA agent found:

- **A saved AI key goes only to the provider it was entered for** (NEW-46).
  One key was stored whatever the provider, so picking another provider and
  pressing Test sent the first provider's key to the second: OpenAI was sent
  an Anthropic key. The key now carries its provider; another provider has
  no key until one is entered for it, and switching back finds the saved key
  again. A key saved before 2.22.1 belongs to the provider that was chosen
  when it was saved, read from the audit log, so a key the old behaviour
  left under another provider is not sent there either. Where the log has
  no such save, a vendor's own key prefix (sk-ant-, xai-, gsk_, AIza) says
  whose it is; failing that, the key goes to no provider and Settings asks
  for it once. AI Insights and the analyses now say which provider has no
  key, as Test does, and Test's save updates the page's "(saved ✓)" at
  once (the gate's second round).
- **Edit Bill shows a taxed bill's tax and its own total** (NEW-47): a
  $35.42 bill ($33.73 + 5% tax) opened as $33.72.
- **The purchase forms round a half cent up**, as the server does
  (NEW-48): 2.5 × $13.37 showed $33.42 and saved $33.43. Bills, purchase
  orders and vendor credits.
- **Claude's menu offers Anthropic's current models** (NEW-49): Fable 5.1,
  Opus 5.5, Sonnet 5.5 and Haiku 5.5, in place of the legacy Haiku 4.5.
  Sonnet 5.5 stays the default; a saved Haiku 4.5 is kept, shown as Custom.
- **A green amount passes AA on every row** (NEW-50): Analytics' A/P aging
  TOTAL was 4.36:1 on the totals row's tint in the light theme.

No schema change. 555 operations.

### v2.22.0 — Connected

The last three releases each built one edge of the same graph — classes,
reports, drill-downs and the pages behind them — and left the rest of it
unbuilt. A survey of the code found fourteen missing links; this release
builds them all, deep links first.

**Every report view has an address** (#237). `#/reports/profit-loss?start_date=…&end_date=…`,
`#/reports/account-transactions?account_id=…&class_id=…&from=general-ledger`,
a class's own P&L, the by-class grid: a reload, a bookmark or Back
reproduces the view with its period and filters. A change inside a view
(period, filter) rewrites the address in place; opening another view (a
drill-down, a class column, a document from a drill-down) adds a history
entry. Browser Back returns to the view left and closes the one being
left; every "Back to …" button goes back through history when that is where
it came from, however deep the chain (each pushed entry records where it
was pushed from), and otherwise opens the view. A drill-down from a class's
P&L says "Back to Profit & Loss — <class>". The dashboard's "Full P&L" and
"Full Balance Sheet" open the dated report; saved reports open through the
same addresses. A date or period in a pasted address that isn't one is
ignored and said so; names `Object.prototype` has aren't reports.
`docs/dev/report-views.md` says how a view gets an address.

**P&L by Class you can scroll, jump, choose, save and export** (#231, #232,
#233). The grid opens in the wide dialog and scrolls sideways with the
Account column and the header frozen; "Go to class" (type to find it),
‹ Prev / Next ›, ←/→, Home/End and Enter move through the columns, and a
live status line names the column. It saves as PDF (landscape, paged past
eight classes) and CSV (the grid, or one row per account and class for a
pivot) and as a saved report; the Profit & Loss PDF and CSV take `class_id`.
`class_ids`, `show=active` and `include_empty` choose the columns; a subset
says "Filtered: n of m classes shown — these totals are for the classes
shown, not the company", with the company's Net Income beside it, and a
subset that names no column says so with "Show every class".

**A class's P&L is a report** (#235): period picker, a class picker with
‹ Prev / Next › that walk the grid's columns for the dates, Save and export,
"Back to P&L by Class" on the report's period; its drill-downs come back to
it. The by-class subtotal cells open it. A "P&L Unclassified" card on the
Report Center is this report pointed at Uncategorized.

**P&L by Job** (#242). `GET /api/reports/profit-loss-by-job` (+ PDF, CSV):
every P&L account down the side, a column per job with "No job" first,
`customer_id` to narrow to one customer's jobs, the same chooser and the
same filtered contract; column totals equal the plain P&L. An amount opens
the lines behind it (the drill-down and the register take `job_id`, 0 = no
job); a job's heading opens the job page on the report's dates with a way
back. Job Profitability's rows do the same now, and its "No job" row opens
P&L by Job on that column (#245).

**Classes have a page** (#234). `#/classes` lists every class with its
income, cost of goods, expenses and net for a period (Active / Archived /
All; every class together is the P&L, and a filtered list says what it
totals). `#/classes/<id>`: Overview with the class's P&L account by account,
its net income and share of the company's, and links into its P&L and into
the by-class grid landing on its column; Transactions with every posted line
tagged to the class across every account, each opening its document; the
fund fields in nonprofit mode. Uncategorized and archived classes have
pages; rename and archive stay in Settings. Classes are in the global
search. New: `GET /api/classes/activity`, `/api/classes/{id}`,
`/{id}/summary`, `/{id}/transactions`.

**Class on the General Ledger and the register** (#236). The ledger shows
the Class column its CSV already had and takes Account and Class pickers;
`class_id` on the route applies to the lines and to the balance brought
forward under the same test, on the CSV and PDF, the drill-down and saved
reports; a filtered ledger says so. The bank and card register takes
`start_date`, `end_date` and `class_id`, shows the balance brought forward
when dated, and keeps the account's whole balance beside a class-filtered
running balance.

**The Chart of Accounts opens the register** (#240): bank and card
accounts to their register, every other account to its drill-down for this
year; a filter box on the chart (its count announced); accounts in the
global search by number or name.

**Every report row goes somewhere** (#239). A/R Aging, Income by Customer
and the Job Profitability customer cell open the customer's page; A/P Aging
and the 1099 Summary open the vendor's; Trial Balance and Cash Flow accounts
open the drill-down for the report's dates; a Fund Balances row opens the
fund's own P&L. The drill-down says "Back to <report>" for every report that
opened it, and Back puts the keyboard back on the row it left.

**Entity pages open their reports** (#238). The customer page gets a
Reports row — Statement (PDF) in business mode, Income by Customer, A/R
Aging, Job Profitability for that customer's jobs — and the vendor page A/P
Aging, the 1099 Summary (for a 1099 vendor) and the default expense
account's register, each on this year to date with the row picked out. Job
Profitability for one customer says so and labels its total as that
customer's. The customer page's invoice rows open the invoice (they opened
the Invoices list). The customer and vendor pages have addresses,
`#/customers/<id>` and `#/vendors/<id>`, so Back from one of those reports
returns to the page.

**A drill-down you can re-date and re-scope** (#241). It runs through the
period shell: dates change in place, a Class and an Account select in its
toolbar, Previous / Next through the accounts of the report it came from;
Payee, the void badge, cleared (✓) and reconciled (R), a "Balance brought
forward" line and a period total; a line with no document opens its journal
entry; "Add to Saved Reports…" works. Every `source_type` the app posts
links to its document — job costs, pay runs, credit memos, in-kind gifts,
releases, allocations, the void and edit postings, adjustments,
depreciation — and a test enumerates the posting sites so a new one can't
ship unlinked.

**Class on every line, and a warning** (#243). The per-line Class cell is
drawn for every company that has a class of its own (it was nonprofit-only),
and invoice lines get it in both modes, so one bill can split across two
classes. A company setting, *Warn when a transaction is saved without a
class* (`class_warn_blank`, off by default): on, every document form with a
class picker starts with no class chosen and asks before saving with none
on the header or a line.

**Not built, by decision: a Balance Sheet by Class.** Customer payments,
bill payments, transfers, payroll runs, sales-tax payments and opening
balances carry no class, so every balance-sheet column but a class's own
net income would read Uncategorized and the report would mislead, as
QuickBooks' does. The class page's net income is the honest figure;
nonprofit mode's Fund Balances already covers funds. In the same spirit,
`BillPaymentCreate` no longer accepts `class_id` or `job_id`: both were
accepted and silently dropped (a bill payment has no class column and Pay
Bills offers none); a body carrying them is refused.

**Also:** "Job Cost Entry" is "Grant Cost Entry" in nonprofit mode (the
new route's label); a job in the by-job chooser, picker and headings has one
name; every amount in the plain and per-class P&L CSVs has two decimals.

**From the release gate's first round** (macOS NEW-23–NEW-41, Windows W-1, W-2
— every one fixed, the older ones included):

- **Back, within the app** (NEW-30). The desktop window has no Back of
  its own, and some hops (a report's row to a customer's page, a class's
  page to its P&L, the chart to a register) have no "Back to …" button. A
  ← button in the toolbar, with ⌘[ on a Mac and Alt+← elsewhere, goes back
  through the app's history, and is lit only while an app page is behind
  (the router's own record, not `history.length`). Over a plain form (New
  Invoice, Edit Customer) it is inert, so no half-typed form is lost to
  one key; over a report, a document or a page it is live.
- **Dialogs and the keyboard** (NEW-25, NEW-32, NEW-33). Tab inside a
  dialog reaches every control, buttons and links included, on every
  browser and whatever the Mac's keyboard setting: the dialog moves focus
  itself in the order the browser would. Escape in a date field leaves the
  field (after its calendar), and the next Escape closes the dialog, so an
  unsaved invoice is not lost to one key (on Chromium and Windows the
  native picker swallows the first Escape, so a date field takes two). No
  dialog opens with focus on Void or Delete; one whose first control undoes
  something takes focus itself and reads its title.
- **The Alt shortcuts work from a Mac keyboard** (NEW-41; W-6 from the
  second round): Option-D, N, P, Q and H go by the key, not the "∂", "˜",
  "œ", "˙" Option types — and no Alt letter fires while typing in a field,
  on any computer (on Windows the plain letter was let through, and Alt+N in
  a bill's Description opened New Invoice in its place), nor do N, P, Q and
  H over a form being filled in, from any of its controls, so a half-typed
  bill stays; ⌘K finds, as Ctrl+K does, from the search box too.
- **A closed report gives its address back** (NEW-23). Closing a report
  (Close, ×, Escape) leaves the page under it on the bar, so the app's
  refreshes, a reload or Back no longer reopen it; the same for a
  customer's or vendor's page. The app's own moves keep the address: a
  customer's page comes back after an edit or a new invoice from it, and
  an invoice opened by its address comes back marked sent.
- **Dates in an address** (NEW-24, NEW-35; W-3, W-4, W-5 from the second
  round). A date that is not one shows the date the report used in its box,
  and a day the month does not have (2026-02-30) is not one; From after To,
  typed or in an address, is refused with a note and the dates before it are
  kept — typed into the Classes list, a class page, a register or the job
  page as into a report ("From (…) is after To (…) — kept …", the boxes put
  back, the address untouched); a register's and a job page's address drop a
  refused date, and a job opened from the list starts on Job to date, not on
  the last job's period.
- **The grid's chooser** (NEW-26, NEW-28, W-1). A chosen class or job with
  no activity in the period is no longer drawn as a column of zeros unless
  "Show … with no activity" is on; when nothing chosen has activity the
  grid says so, with "Show every class"; the note names the empty chosen
  ones (a few, then "and n more") and the chooser's count is of its ticks.
- **Saved Reports** (NEW-27) lists each report by its own title and the
  class, job or account it was saved on.
- **The exports say what they are** (NEW-29, NEW-34): a class's P&L CSV
  reads "Class: X only, not the company total", and a class's General
  Ledger CSV names the class on its preamble and its brought-forward rows.
- **A class's share** (NEW-31) is a percentage only when both nets are in
  the black; otherwise the company's figure is said beside the class's.
- **Drill-down lines** (NEW-40) read "Expense #2220", "Bill payment #3",
  "Journal entry #2224": the app's words, numbered by the document the
  link opens, not by its vendor.
- **Named for its line** (NEW-37): a line's Class (and a nonprofit's
  Function) cell is "Class, line 2", as the cells beside it. **Reachable
  rows** (NEW-38): the customer page's invoice numbers and payment dates,
  and the vendor page's bills and payments, are links. **Announced**
  (NEW-39): the drill-down's "3 of 23: 1000 - Checking" is a status.
- **A job is its customer's** (NEW-36, older than this release). The Job
  picker narrowed its list only when the hidden select got focus, which
  the type-ahead never gives it, so an invoice could carry another
  customer's job and P&L by Job counted it there. The picker lists the
  customer's jobs the moment the customer is chosen and clears a job that
  stops matching; the API refuses another customer's job on every customer
  document (invoice, sales receipt, credit memo, estimate, recurring
  schedule, in-kind gift; the header's and each line's) with 400 "Job X
  belongs to a different customer than this invoice — clear the Job field
  or pick one of this customer's", and a job that does not exist with 404;
  an edit that touches neither customer, job nor lines saves as it is, so
  old books are not blocked; a copy (convert, duplicate, a recurring run)
  carries only the customer's jobs and says what it left off; a job
  carried by documents can't move to another customer; a converted
  estimate posts each line's job as create does. The QA fixture seeds each
  invoice's job from its own customer's.
- Tests: the bakery's books fixture lives in `conftest.py`, so the six
  report-link tests run on Windows and macOS CI (W-2).

**From the gate's second round** (macOS NEW-42–NEW-44, Windows W-3–W-7; the
dates and shortcuts ones are in the lines above):

- **Back looks like a button** (NEW-42). The toolbar's ← was a bare grey
  glyph that got its border on hover alone, and dimmed it looked the same, so
  with a card open it read as unusable. It is a bordered gold button reading
  "← Back" in both themes while there is somewhere to go, muted and
  borderless when there is not; its edge clears 3:1 against the bar.
- **The keyboard's place is visible** (NEW-43). Links and buttons had no
  focus style of their own, and WebKit draws none on a link with the Mac's
  keyboard navigation off. Every link, button, checkbox, radio and file
  picker draws a 2px gold ring on keyboard focus, in both themes, the gold on
  a halo of the theme's own ground so it clears 3:1 against whatever it meets
  — the toolbar (where the gold is deeper and clears the bar by itself), a
  dialog's title bar, a table's header row, a line of running text — read off
  the screen as the macOS gate reads it; a mouse click draws none. A sidebar
  link draws it inside its edge; a table's last row draws it whole; the
  grid's and the search results' own rings win; the P&L by Class grid brings a
  cell out from under its frozen header and Account column; Settings scrolls
  a control clear of its save bar; the skip link moves focus to the page's
  content instead of showing "Page not found". A dialog's title bar is 4px
  taller, to give the × room (WCAG 2.4.7, 1.4.11).
- **Every field keeps a field's own focus style.** A list's search box and
  filters, a line item, a type-ahead box, Budgets' cells, Settings' lists, a
  page's Notes and a report's pickers and dates look and focus like a form's
  fields, in both themes: none is white in the dark theme, none draws the
  browser's ring, a dark date field's calendar icon is light, and with the
  system's high-contrast colours a focused field draws the system highlight.
- **Cards open from the keyboard.** The Report Center's cards, Banking's
  account cards and the dashboard's bank cards were a click alone; each is
  a link now: Tab reaches it, Enter or Space opens it, and Back returns to it.
- **A page's Notes box saves only a change** (NEW-44). The customer's and
  the vendor's Notes saved on every blur, so a Tab through the page wrote the
  company file and an audit entry each time and turned a note never written
  into "". Only a change is saved, once — leaving the box again before the
  save is back sends nothing more; a space added alone is no change, and a
  note of spaces is none.
- **Back puts focus back on the row left** (W-7). Chart of Accounts → a
  register or a drill-down, a drill-down's line → its document, every report
  or page row that opens something, and a customer's or vendor's report links
  come back with the keyboard on the row or link left — after a mouse click
  too, which on a Mac does not focus a link; a ⌘/Ctrl-click that opens a new
  tab notes nothing.
- **Wide tables scroll sideways** in their box instead of being cut off (at
  the desktop app's 900px minimum a list's last column could hide its
  buttons); every table, the line-item tables and the pay run included, has
  8px of room below it (and at its right end where it scrolls) for a
  focused control's ring; a wide table with no controls in it (Benefits'
  rates) is a Tab stop the arrow keys scroll.

**From the gate's third round** (macOS NEW-43 again, NEW-45):

- **The ring shows on a Mac after a click too** (NEW-43). WebKit counts
  focus moved in script as the keyboard's — and draws the ring — only when
  the focus before it was the keyboard's, and a dialog's Tab is moved in
  script; so in a dialog opened with a click, Tab moved with no ring to
  see. Focus the app moves because of the keyboard now asks for the ring
  outright: Tab inside a dialog, Back's return to the row left, a disabled
  Previous/Next handing over.
- **⌘K waits for the dialog** (NEW-45). Over an open dialog, ⌘K (Ctrl+K)
  moved focus to the search box under its overlay, and the hits opened
  behind the dialog where they could be neither read nor clicked. Over a
  dialog it does nothing now; close it, then search.

No schema migration. 555 operations.

### v2.21.0 — Three things you'd reach for

A user who knows QuickBooks clicked a vendor, an account in the General
Ledger and a past bill, and nothing happened. None of the three was broken;
none of the three existed. Now they do.

**A vendor's own page** (#223). Click a vendor in the Vendors list for the
mirror of a customer's page: contact, terms, 1099 status, the default
expense or COGS account, notes you can edit in place, what's owed, its bills
with status and balance, its bill payments, and any credits not yet applied.
A bill, payment or credit opens from it; Enter Bill, Pay Bills and Edit are
on it. The row's own Edit and Make Inactive keep working. Nothing new on the
server: the page reads what the API already served.

**Edit a posted bill** (#225), the way an invoice is edited: the header
(vendor, dates, terms, number, memo) and the lines (item, account,
description, quantity, rate, job, class, cost code, billable). The journal
keeps its identity and is re-posted, so Accounts Payable, the vendor's
balance, the aging and the trial balance follow. The rules: a bill can't be
edited below what's already been paid (void the payment first); a voided
bill can't be edited; the closing date applies; a posting in a completed
reconciliation takes words but not amounts. An inventory line's receipt is
reversed at its original cost and the new quantity received, so stock on
hand and average cost stay right. `PUT /api/bills/{id}` is the 546th
operation. Edit is on the bill's row and in its view, not on voided bills.

**The General Ledger drills down** (#224). An account's name in the report
opens that account's register for the report's dates, as the P&L and
Balance Sheet already did, with a way back to the ledger.

**Also:** a contrast-sweep fixture dated a permit to 2026-10-06 so it would
read "Expires soon"; from 2026-10-07 it read "Expired" and the sweep failed
on main. The date is worked out from today now (#229).

No schema change. 546 operations.

### v2.20.1 — A quieter start

Both from @stevenmirabito.

**The release notes show once per version** (#220). The start-up splash
opened on every page load: the licence terms recorded their acknowledgement,
once per licence version per browser, but the what's-new panel never did, so
the whole splash came back each time, with "OK" instead of "I understand".
The notes now record the version they showed, the same way the terms do, and
the splash opens at start only while something on it is new. With both seen
it's hidden before first paint. About still shows the whole splash any time.
Nothing moves to the server and the licence's acknowledgement is untouched.
Steven reported it, reproduced it on Server Edition and mapped out the fix;
this is his "keep localStorage, add a second key" alternative.

**The sign-in and setup screens no longer link to each other** (#219, by
Steven). The app already opens the right one from the server's own status,
and each link only led somewhere that bounced straight back: the sign-in
screen's "First time? Set up Slowbooks" opened a wizard whose only possible
answer was "Setup is already complete on this server", and the wizard's
"Already set up? Sign in" wanted a password that does not exist yet. Each
screen's 409 already hands the reader over to the other when it turns out
they belong there.

No schema change. 545 operations.

### v2.20.0 — Tidy lists, classes in detail

**Inactive records out of the way** (#210, asked for by Ryan of Cimarron Site
Services). Customers, Vendors, Items & Services, Employees and Jobs showed
every record, inactive ones dimmed, so a list was just as long after a
cleanup, and making a record inactive meant Edit, untick, Save.
- Each list has **Show: Active / Inactive / All**, Active by default, and
  remembers the choice.
- **Make Inactive / Make Active** sits beside Edit on every row. An inactive
  record keeps its history and leaves the pickers, as before.
- It asks first where hiding could hide money: a customer or vendor with an
  open balance (it names the amount), or an employee, who isn't included
  in new pay runs while inactive.
- A read-only sign-in doesn't get the buttons; the employee ones are the
  administrator's. On Jobs, Status now covers job status only.
- API: `inactive_only=true` on the five list endpoints.

**P&L by Class in detail** (#213). It was one row per class with five
totals, and you couldn't see what made them up.
- It now lists every income, COGS and expense account down the side with a
  column per class and a total, and still ties to the plain P&L.
- Click an amount for the transactions behind it, in that class; click a
  class's heading for its own Profit & Loss. Both have a way back.
- API: `class_id` on the P&L and on the account drill-down; P&L by Class
  adds `accounts`. The General Ledger carries each line's class, and its CSV
  gains a Class column, last.

**Expense or COGS** (#214, from D#212). The vendor's "Default Expense
Account" read as expense-only, though it has taken cost of goods accounts
since 2.18.0. The vendor's and item's fields now say "Expense or COGS", and
every purchase account list groups Cost of Goods Sold first, then Expenses.

No schema change. 545 operations.

### v2.19.0 — Type to find it

**The pickers search as you type**, as QuickBooks' do. Type part of a name
into a customer, vendor, item, account, employee, job, class or fund picker,
or any list of 15 or more, and the list narrows to what matches: "harb"
finds Harbor Light Bakery, "6500" finds 6500 Rent or Lease. Every word typed
has to appear in the name; names that start with it come first, and the
matching part is in bold. Enter, Tab or a click takes the highlighted name,
Escape backs out, and a name typed in full is taken when you leave the box
(#207).
- "+ New Customer" and "+ New Vendor" stay in the list. Chosen after typing
  a name that isn't there, the quick add opens with that name filled in.
- A required picker left empty stops the save with "Choose one from the
  list." A read-only sign-in sees the pickers locked, as before.
- Screen readers hear each picker by its label (#198), how many names match
  as you type, and the highlighted one. The list follows the WAI-ARIA 1.2
  combobox pattern, and its text meets AA contrast in both themes.
- Behind the box the picker is still the same `<select>`: every form reads it
  as before, and whatever changes it shows in the box.

**Windows builds pin pythonnet 3.2.0** (#208), the library pywebview's window
runs on. pywebview asks for it unpinned, so 2.18.1 was built on 3.1.0 and
2.18.2 on 3.2.0; every build now gets the version the gate ran.

No schema change. 545 operations.

### v2.18.2 — Every field says its name

**A screen reader says what each field is.** Most forms put a label beside its
field without tying the two, so a screen reader said "combo box" where it
should have said "Customer", and a grid of inputs (a budget, a batch of
payments, opening balances) had no names at all: in 2.18.1, 495 fields on 22
of 53 pages and 625 in 91 of 125 dialogs had no name a screen reader could
say (#198). Every field has one now, wherever a page or dialog draws it:
- a form's label is tied to its field, so clicking the label also puts the
  cursor in the field, and a required field is read as required rather than
  as "star";
- a label written just before its field ("Deposit To:", From and To) is tied
  to it;
- a field in a table is named from its column and its row: the row's
  first words where it has some ("Jan, 6500 Rent or Lease", "Payment,
  1001"), else its line ("Qty, line 2");
- a checkbox that starts a row says what ticking it does ("Pay invoice
  1001", "Pay Lena Ortiz", and on the reconcile screen "Cleared", with the
  line's date, payee and amount);
- fields with the same label in different parts of a screen are in groups
  named after their headings, so a screen reader can tell them apart: New
  Customer's Billing Address and Shipping Address, each Settings section
  (PayPal, Square and QuickBooks Online each have an Environment), and the
  four tax forms' Year;
- the few fields with nothing beside them to borrow from (the status and
  other filters, file choosers, the job page's period) are named where
  they're drawn;
- a field whose only words are its placeholder, like the search boxes and
  a quick add's Email and Phone, takes them as its name. Chromium read
  placeholders as names already, but WebKit, and so VoiceOver on the Mac,
  doesn't.

A browser test sweeps every page, every dialog, the reconcile screen and a
nonprofit's own pages for a field with no name, a field named only by its
placeholder, and two fields with one name in one group.

**An import that hit errors says so.** The QuickBooks Interop page ended every
import with a green "Imported 0 records" and "Import complete", even when the
only result was an error in the red box below. An import that comes back
with errors now shows a red "Imported N records, M errors: see the list
below", and the status bar says "Import finished with errors" (#197). The
report-CSV import on the same page did the same, and is fixed too.

**Claude and Grok come first in the AI provider list** (Settings → AI
Insights) and in the sentence above it, side by side, as the docs and the
website list them (#200). An install that hasn't chosen a provider starts on
Claude; a saved choice is kept. Claude's models move to the current
generation: Opus 5.5, Sonnet 5.5 and Haiku 4.5, with Sonnet 5.5 the default
(it was Sonnet 4.6). A model you saved before is kept, shown as Custom.

**Docs.** The accessibility statement (`docs/accessibility.md`) describes
2.18's contrast work and how it's checked; `docs/development.md` counts the
app's 64 page routes.

No schema change. 545 operations.

### v2.18.1 — Names from QuickBooks come across clean

**A name QuickBooks had put in quotes kept them.** QuickBooks writes a field
that holds a comma inside double quotes (`"JONES, BOB"`, `"99,250.02"`) and
a quote mark within one twice. The IIF import kept the quotes as part of the
value, so a customer or vendor whose name has a comma was listed with quote
marks around it. They come off now (#195, @TheLocalW).

**ALL-CAPS names can come in as normal capitalization.** Many QuickBooks
users typed every name in capitals. When a file has names in ALL CAPS,
**Validate** now shows a few of them as they would import and offers
**Change ALL-CAPS names to normal capitalization**: `BOB JONES` becomes
`Bob Jones` and `ACME TOOLING, INC.` becomes `ACME Tooling, Inc.`, with
initials, legal forms and web addresses kept as they are written
(`CVS Pharmacy`, `Wells Fargo Bank, N.A.`, `Amazon.com`). It is off unless
you tick it, because no word list knows every initialism a business uses,
and the examples are your own names, so you can see first. It covers
customer, vendor and account names; item names are kept as typed, since
they are often part numbers, and nothing already in your books is renamed.
From @TheLocalW (#195), who also added `tools/clean_iif.py`: for source
installs, it writes a cleaned copy of an export with the same rules.

Fixed in review, before it shipped:

- The renaming reached the list rows but not the transactions that name
  them: a bill for `ACME TOOLING, INC.` could not find the vendor imported as
  `ACME Tooling, Inc.`, and an invoice or payment for `BOB JONES` made a
  second customer. A name is now rewritten everywhere the file uses it.
- Re-importing a list that an earlier version had imported made a second copy
  of every ALL-CAPS name. **A name in a file now matches the customer, vendor
  or account already in your books whatever its case**, as QuickBooks treats
  names, box ticked or not; `ACME CO` beside an existing `Acme Co` used to
  make a second one. So a transactions file imported after its lists finds
  every name, however it spells it. Item names still match exactly.
- The list of initials held ordinary words ("BANK OF AMERICA" read "BANK of
  America", "NEW YORK LIFE" read "New YORK Life") and read a bank's `NA`
  (National Association) as "N/A". Initials beside a full stop, `&` or `/`
  keep their capitals (`N.A.`, `AT&T`, `A/R`), and `MCDONALD'S` reads
  `McDonald's`.
- `tools/clean_iif.py` keeps a file's Windows line endings and a Windows-1252
  file's encoding.

**A line posted to a sub-account imports.** QuickBooks names a sub-account
by its path (`Automobile Expense:Gasoline`) on a transaction, while the list
import keeps it under its own name with its parent linked, and the path was
never looked up: every bill, invoice or deposit line posted to a sub-account
was refused as "account ... not found". The path finds it now, in any case.
One under a different parent is not taken: a line for `Automobile:Gas` is
still refused rather than posted to `Utilities:Gas`. Re-importing a list also
counted each job already there as a customer imported; it doesn't. Both
turned up while testing the samples for this release, and predate it.

**A second import says what it skipped.** Importing the same file twice adds
nothing, rightly, but the result counted only the bills, deposits and sales
receipts it skipped, and labelled the count "imported": "Duplicates skipped:
1 imported" for a bill, an invoice and a payment (both QA agents, 2.18.1
gate). Every document type counts what it skips now, shown as "Already here,
skipped". A payment for a customer who isn't in the books, and an invoice or
estimate with no customer name, vanished without a word; each is an error
naming the document now.

**The permit form's format note covered the boxes above it** (#194,
@cnbarry1). On Add Reseller Permit, the state's note under State and Permit
number (for Washington, "9 digits…") was drawn over both boxes, and a click
on their lower part landed on the note. It has a row of its own now, and a
longer note wraps within it.

**API.** `POST /api/iif/import` takes a form field `retitle_names` (default
false). `POST /api/iif/validate` reports `caps_names` and up to six
`caps_name_examples` (`{name, becomes}`), and the import result reports
`names_changed`. 545 operations, unchanged.

No schema change.

### v2.18.0 — Around the ledger

Two of the QA agents each started a brand-new company and ran it for a day
as its owner would, through the screens: skytech on Windows (a sign shop,
771 recorded steps) and macbase1 on macOS (a bakery). Every figure was
checked by hand against the ledger. The ledger held — every trial balance
balanced and every account they rebuilt matched to the cent. What they found
was around it: figures that were never kept up to date, postings a person
couldn't see, flows that couldn't be finished from the screen, and tax forms
mapped wrong. Seventy-four findings between them, sixty-eight once the
overlaps were merged, and this release fixes every one — along with
twenty-nine more that fixing them turned up. The release gate's own run
found more, fixed here too — the largest, that companies on one desktop
shared their uploaded files. And @Sciumo's QuickBooks Online work (#192)
and a longer reach for SimpleFIN bank feeds are in it.

#### Money that was wrong

**Pay Sales Tax never worked.** Every attempt was refused with "date: Input
should be None" — a field named `date` hid the date type. It records the
payment now, and only from a bank or credit card account (the list used to
offer Accounts Receivable and Inventory).

**Tax paid to a supplier reduced the sales tax owed to the state.** A
purchase order started at the company's *selling* tax rate, and turning it
into a bill debited that tax to Sales Tax Payable, netting it against the
tax collected from customers. macbase1's bakery collected $59.57 and Pay
Sales Tax offered $0.33. Tax on a purchase is now part of what the purchase
cost: it is spread over the bill's lines, to the cent, and posts with them.
Purchase orders start at no tax. *If your books were entered on an earlier
version,* the Sales Tax report now names the supplier tax sitting in Sales
Tax Payable and gives the one journal entry that moves it.

**Purchases with no account were booked as advertising.** A bill line with
no account fell back to account 6000, which the standard chart names
Advertising & Marketing — a bakery's flour and a sign shop's panels. A line
now posts where someone said: the account on the line, the item's, or the
vendor's default; a line none of them names is refused with a sentence
saying what to choose. Enter Bill has an Account column, To Bill asks for an
account per line, and cost-of-goods accounts can be chosen for vendors,
expenses and card charges.

**Schedule C counted expenses as income.** Lines 10, 13, 15, 17 and 18
contain "Line 1", and a substring test moved office expense, depreciation,
insurance and more into gross income. Lines are matched exactly now, the
mapping follows the standard chart, and cost of goods lands on line 4. Net
profit equals the P&L.

**Customer and vendor balances always read $0.00** — on the Customer Center,
the customer page, the Vendor list and both CSV exports. They are worked out
from the open documents and unapplied credits whenever they're shown.

**Money a customer paid could disappear from view.** Receive Payment didn't
apply a payment to anything unless each amount was typed by hand, and the
leftover could never be applied later; A/R Aging ignored it and disagreed
with the balance sheet. Typing the amount now fills the invoices oldest
first; leaving money unapplied is a choice with its own box; a customer's
credits are listed on Receive Payment, the customer page and the payment,
with Apply; and A/R Aging, Income by Customer and the dashboard all tie to
account 1100. Foreign-currency invoices count at the amount the ledger
booked.

**Foreign-currency money is counted at what the ledger booked.** A/R Aging,
A/P Aging, customer and vendor balances, statements, Income by Customer, the
analytics charts and the assistant all count a EUR invoice at its booked
dollars, and every aging report now equals its control account. A
foreign-currency invoice can be paid from Receive Payment, which offers the
currency and asks for the rate on the payment date (it could only be paid
through the API).

**A deposited payment could be voided out from under its deposit,** driving
Undeposited Funds negative — even after the deposit was reconciled. A
deposit now records the payments it took; a payment in a deposit can't be
voided until the deposit is, and never once it is reconciled. Deposits can
be voided from Make Deposits.

**Adding a bank feed could count the opening balance twice.** The statement
balance was posted even when the account already had it. It is now compared
with the books: equal posts nothing, different asks before posting only the
difference.

**Email All Overdue said "Sent 2 statements" when nothing went out,** and
counted draft invoices as overdue. It counts what was sent and names who
didn't get one. Collection letters had the same fault.

**Time tracking couldn't be used.** Entries showed 0.00 hours, saving landed
on "Page not found", draft entries could never be approved, and a pay run
from approved time paid an hourly employee $0.00 without a word. All fixed;
a pay run that would pay someone nothing is refused and names them, and the
W-3, 940 and 941 count only employees who were paid.

**The Cash Flow statement put customer receipts under Investing** and
supplier and payroll payments under Financing. It is built the standard way
now, from net income, and its net change equals the change in cash.

**A pay stub didn't add up.** An Oregon employee's stub listed the state
income tax twice, the company's own share of some state premiums appeared
as the employee's deductions, and the year-to-date column was guessed from
the labels: Total Deductions read $548.53 where $404.06 was withheld. Every
line now adds up to what was withheld, each line has its own year to date,
and the stub says "OR Income Tax".

**A typed exchange rate could be replaced.** Choosing a currency looks up
the day's rate; a rate typed before that answer arrived was overwritten a
few seconds later, and the invoice booked at the looked-up rate without a
word (850 EUR at a typed 1.10 posted as $968.80). A typed rate is kept.

**Tax forms produced nothing in the Mac app.** W-2, W-3, 940, 941 and the
New-Hire Report open in the viewer as invoices do. The 941 works lines 5a–5d
from the rates and puts the rounding difference on line 7.

#### Each company's files, in its own company file

**Companies on one desktop shared their files.** Every company wrote its
logo, attachments, employee documents and waiting receipt scans into one
folder, and nothing in a file's name said whose it was. A second company's
logo printed on the first company's invoices; its invoice 1 "receipt.pdf"
replaced the first company's, and deleting it left the first with nothing;
its employee #1's W-4 opened from the first company's employee #1; an
updated W-4.pdf replaced the original; a deleted document stayed on disk;
every company's dashboard listed every company's pending scans; and a
backup carried none of it. A company's files are now kept in its own
database: a backup carries them, a second file with the same name is a
second document, and deleting one deletes its bytes (overwritten, not left
in the file's free space).

- Upgrading copies each company's files in from the shared folder the first
  time the company opens on 2.18. Nothing there recorded whose a file was,
  so a copied logo or attachment says it came from the folder earlier
  versions shared (upload it again if it isn't yours), and a file that
  wasn't there is named, without a download or a size. The shared folder
  is left where it was until every company on the install has been opened
  on 2.18; then an administrator can remove it from Settings → Files from
  earlier versions, and a document deleted in the app leaves no old copy
  behind.
- **Security.** The shared folder was published at `/static/uploads/`,
  which needs no sign-in: on a Server Edition, `--serve-lan` or Docker
  install, anyone who could reach the server could fetch a company's logo,
  attachments and employee documents (W-4s, I-9s) at addresses that were
  easy to guess. Nothing is served from it now; a company's files come
  from signed-in routes. A read-only sign-in could also download an
  employee's documents through the attachment routes, and a bookkeeper
  could delete one; employee documents are now reached only through the HR
  routes, which are the administrator's. And any sign-in could download a
  backup, which is the whole company, sign-in password hashes included:
  only an administrator can now.
- **Employees' portal links** were kept in the company file as issued, so a
  copy of it, or a backup, held every employee's working link, and a link
  signs in as that employee, the bank account their pay goes to included.
  A link is now kept as a digest the portal checks and a copy encrypted
  with the payroll key, which is kept outside the company file. Links
  already sent keep working, and Copy Link still shows them.

#### Purchases

- Bills and purchase orders have Save PDF and Print; purchase orders have a
  View; a bill lists its payments, each with View, Print Check and Void.
- A bill takes its vendor's terms and a due date from them; bills made from
  a PO before this release get the due date their terms give.
- The PO, bill and vendor credit forms show line amounts and totals as you
  type, fill an item's cost when it's picked, and won't save at $0.00.
- Prices to four places on bills, purchase orders, vendor credits and items
  ($0.045 a box).
- A vendor can be made inactive. An expense, a bill payment or a pay run
  that would overdraw a bank account asks first.
- A/P Aging is in home currency, nets vendor credits and bill-payment money
  not yet applied, and equals account 2000.

#### Sales documents

- Credit memos and recurring schedules fill an item's price and show a
  total; credit memos start at the company's tax rate (or the credited
  invoice's) and have View, Save PDF and Print.
- A document that adds up to $0.00 asks before it saves, and a no-charge
  invoice starts Paid (a recurring schedule for $0.00 is refused).
- Duplicating an invoice keeps its currency, rate and job; changing an
  invoice's or estimate's customer or rate re-totals its tax.
- Foreign-currency invoices say which currency they're in, on screen, in
  lists and on the PDF.
- Settings' invoice prefix, next invoice number and invoice footer are used.
- Converting an estimate makes today's invoice, due by the customer's terms,
  addressed to the customer.
- Addresses print without a dangling comma, and with the country abroad; the
  invoice header no longer wraps dates and terms, a statement's dates and
  totals stay on one line, and a negative amount prints "-$10.00", not
  "$-10.00".
- Email Invoice fills in the customer's email and thanks them once.
- A due date before the invoice date, or a schedule ending before it starts,
  is refused.
- A counter sale needs no customer (Walk-in Customer). Unit prices take four
  decimal places. Saving past a customer's credit limit asks first.
- **Tax rates take four decimal places** — New York City's 8.875%, or
  7.0625% — on every document and in Settings, and are kept exactly: the
  forms took two, and a rate was stored to four places of the fraction, so
  8.875% became 8.88% ($88.80 on $1,000 instead of $88.75). The forms now
  work tax out to the cent exactly as the server saves it (a purchase
  order at 8.25% on $102.00 showed $8.41 and saved $8.42), and a purchase
  order whose only change is its rate re-totals. A default saved earlier
  with more decimals shows rounded to four instead of holding back Save
  Settings.

#### Customers, payments and statements

- Receive Payment lists draft invoices too, and no longer offers Print Check
  for money received. An invoice shows the customer's credit with Apply
  Credit.
- The customer statement is one list in date order, each line describing its
  document.
- Income by Customer shows sales before tax, with tax in its own column.
- Customers get the company's default terms, a Tax exempt box and an Active
  box; a negative credit limit is refused.
- Make Deposits names each sales receipt and check.
- Batch Payments, Receive Payment, Pay Bills, the credit screens and the
  credit-limit check see every open invoice and bill; they read only the
  newest 500, so once a company had more, an older unpaid one never
  appeared (#191). Every list page that shows only the newest (Invoices,
  Bills, Estimates, Sales Receipts, Credit Memos, Purchase Orders,
  Payments, Vendor Credits, Payroll) says so and offers Show all, and a
  bank account's review queue shows every line waiting.
- Receive Payment and Apply Credit name an Apply amount that is more than
  its invoice's balance, instead of reading "Fully allocated" while the
  save is refused.
- The dashboard's A/R Aging card shows the report's own figures — the
  buckets, credits not yet applied, and a total equal to Total Receivables
  — and an invoice counts as overdue only while something is owed on it.

#### Banking and the books

- Reconciliations go forward only; Finish says what's out of balance; a
  completed reconciliation has a report and PDF.
- A category picked in the bank review list is kept; Add all categorised
  uses it.
- Bank CSV import reads any file with a date, a description and an amount,
  and asks which column is which when it can't tell.
- The register shows bill payments' check numbers, keeps its place on
  refresh, and every line opens its document (deposits and bill payments
  have views of their own).
- Registering a fixed asset posts its purchase (paid from an account, on a
  bill already entered, or owned before the books began); salvage above cost
  is refused.
- An unbalanced journal entry says by how much, in dollars, and a journal
  entry can no longer be voided twice.

#### Payroll and tax forms

- Each employee on a pay run has a Stub PDF naming the company and the
  employee; the Employee column stays in view while the table scrolls, and
  an Other column makes every row add up to Net.
- Tax forms, pay stubs and the New-Hire Report save under names that say
  what they are and whose (W-2_2026_Lena-Ortiz.pdf, 941_2026_Q3.pdf,
  Pay-Stub_2026-10-01_Lena-Ortiz.pdf). Onboarding offers the New-Hire
  Report as its PDF, not raw data.
- A vendor marked "1099 Vendor: Yes" reaches the 1099-NEC and 1096, which
  the Tax Forms page now prints.
- The Sales Tax report nets credit memos and checks itself against Sales Tax
  Payable, and says how much old supplier tax lowered that balance.
- SSN last 4, pay rate and work state are checked, in words.
- A garnishment order is ended, not deleted: End order stops it being
  withheld and keeps its record.

#### Settings, sign-in and backups

- Settings refuses a tax rate outside 0–100%, a next number that isn't a
  whole number, and the like, in words; Save Settings stays in reach and
  leaving with unsaved changes asks first; the closing date shows whether
  one is set, clears in one click (on the Mac too), and says what is saved
  rather than what is typed.
- The closing-date override password is asked for and works; five wrong
  passwords lock it for ten minutes, and the fifth answer says so.
- A new company opens on setup with its name filled in; the unlock screen
  names the company and, in the desktop app, offers "Choose a different
  company →" from the moment the app starts (the Mac app's first screen
  never had it).
- Backups are named for their company, listed per company, and can be
  restored from Settings — with a safety copy first and a second question
  for another company's backup. After a rename, Restore names the company
  as it is now, and restoring an older backup renames the company list's
  entry at once.
- An opt-in setting asks for the password each time SlowBooks Pro starts.
- A sign-in belongs to the company it was made in. A session signed in to
  one company opened another signed in, that company's own password never
  asked, when the app was pointed at it from outside or a Switch company…
  failed to sign out; it now asks for that company's password, and Switch
  company… stops if signing out fails.
- A refused form says what to fix in a sentence, not validator text, and a
  message stays long enough to read (three seconds for a few words, more
  for more, at least six for an error; hovering holds it, a click closes
  it).
- A read-only sign-in isn't offered what it can't do. Edit, Mark Sent,
  Void, Duplicate, file choosers and every other write are hidden on pages
  and in dialogs, where the server refused them after the form was filled
  in; Settings, Quick Entry and Batch Payments show locked, with a sentence
  saying why; Payroll and HR opened by their address say they are the
  administrator's, and the Audit Log that it isn't open to a read-only
  sign-in; Alt+N, Alt+P and Alt+Q say it is read-only. It can read
  an invoice's payment link but no longer makes one, and leaves Settings
  without being asked to save.
- The company logo is the administrator's to change, like every other
  setting, and Settings can remove it.
- A bookkeeper isn't offered the administrator's controls either, which
  the server refused after the form was filled in: Settings shows the
  company settings locked, with "Company settings are changed by an
  administrator."; backups, new company files, and connecting to,
  importing from and disconnecting from QuickBooks Online say they are the
  administrator's; Migrate Data says so from the sidebar on. The AI
  Insights settings are the administrator's now too: the key is a
  company-wide credential, and the endpoint receives the dashboard's
  figures.
- Opening an email template in Settings no longer saves the whole page:
  its Edit button submitted the Settings form, saving an administrator's
  unfinished edits behind the editor.
- **Docker: saved passwords survive an upgrade.** With no settings key
  configured, the key for saved passwords and API keys lived inside the
  container, so recreating it for an upgrade made a new one: every saved
  secret stopped decrypting, and every page that reads the settings failed.
  The key now comes from `PAYROLL_ENCRYPTION_SECRET`, which a Docker install
  keeps in `.env`. A secret that can't be decrypted reads as not set (what
  uses it stops, and nothing is let through without it), and Settings names
  each one and where to enter it again.
- **Docker: Create Backup works.** Every backup on a Docker install failed
  with "Permission denied", and so did every logo and attachment upload,
  since 2.0: the volumes docker compose mounts were created owned by root,
  and the app runs as a user of its own. The image now gives them to that
  user, and an existing install's volumes, left empty by those failures,
  take the right owner when 2.18 starts.
- A disabled button looks disabled (it drew at full colour and did
  nothing), and the QuickBooks Online page says why Import is unavailable.

#### Import, export, lists and search

- Every CSV export opens correctly in Excel (UTF-8 byte-order mark) and
  writes money to the cent ("-20.00", not "-20.0"); the IIF export is
  written for QuickBooks (Windows-1252), at home-currency amounts, a sales
  receipt goes across once, and a blank state or ZIP is left out rather
  than written as "None".
- Re-importing our own export no longer creates `'=HYPERLINK…` duplicates; a
  CSV row is checked like the form, and blank terms take the company
  default.
- One active item per name; items can be made inactive; the item form offers
  only income accounts, and no nonprofit accounts in a business company —
  whose chart no longer starts with 4400 In-Kind Contributions (it is added
  when a company becomes a nonprofit).
- Account numbers are digits. Search finds documents by amount. Read-only
  sign-ins see no "+ New" buttons.
- Report PDFs print the company name as written; Save PDF files documents
  under Documents and reports under Reports, named once, and a download
  named after a customer keeps its accents.
- The toolbar's Home, Quick Entry and Reports move the address with the
  page, so Back and the sidebar link of the page you left work; a form
  dialog keeps every field in view beside a very long customer name, and
  the Estimate form's line table fits the dialog at 1280 wide.
- Desktop app: the PDF window has **Open in** your PDF app and **Show in
  folder**; the IIF export and file attachments save instead of failing or
  opening as text, and an attachment you open is saved with the documents,
  not the reports; upload and import refusals read as sentences.

#### QuickBooks Online (#192, @Sciumo)

- **Journal entries and posted ledger activity import.** Import from QBO
  now brings journal entries, and the posted activity of QBO's accrual
  General Ledger (purchases, deposits, transfers, invoices, payments and
  journals), each validated as a balanced entry before a batch posts and
  rolled back whole if any fails. The two paths share their mappings, so
  nothing posts twice, and an invoice or payment that is already a posted
  SlowBooks document is not posted again. A foreign-currency journal
  balances in its own currency and converts the way the rest of the books
  do. Imported journals are listed on Journal Entries and linked from the
  bank registers. Sales receipts keep each line's item, quantity and rate
  (they arrived as one amount at rate 0.00, in 2.17 too), and invoices and
  receipts bring QBO's sales tax: each line's taxable flag and the rate, or
  QBO's tax amount where no single rate reproduces it, kept through an
  edit.
- **Changes on either side.** Anything the import brought in voids and edits
  here like any other document: SlowBooks reverses the import's entry, an
  edited invoice posts its own (and brings its imported payments with it),
  and a later import leaves it as it is here. A transaction edited or voided
  in QuickBooks Online (or a journal deleted there) is brought up to date on
  the next import; one that can't be — a closed period, a reconciled line,
  an invoice paid here past QBO's new total — is named in the log and
  skipped, and never stops the rest.
- **A live import log.** An import runs in the background with a log under
  the controls — every query, check, posting, skip and error, with the
  document and account it concerns, an Errors filter, elapsed time and
  counts — that survives leaving the page. One import runs per company;
  a restore waits for it; the books stay writable between its steps; and
  it works when the Docker image runs several worker processes. An idle
  page with no import reads as ready, not "Connection interrupted".
- **Connecting from the desktop app** opens Intuit's sign-in in your
  browser. **When the redirect can't reach SlowBooks,** an administrator
  can complete the connection by pasting Intuit's callback address (or the
  code and Realm ID from Intuit's OAuth Playground). Starting an import or
  a connection is administrator-only.
- **Discounts and bundles.** A QBO discount comes across on its invoice or
  sales receipt as a line on a Discount item that posts to QBO's discount
  account, with tax worked out before or after it as QBO had it; a bundle
  comes across as the lines of its items. Exporting such a document back
  sends QBO a discount, not a negative sale, and a Discount item's line
  takes a negative price on the invoice form.
- **Exporting keeps QuickBooks Online up to date.** A document sent from
  here that changes here is updated in QBO on the next export, and one
  voided here is voided there; sales receipts go as QBO sales receipts;
  each line carries its tax code, and several discounts go as QBO's one
  discount with a note. Export no longer sends back QBO's own documents
  (after an import it sent QBO's sales receipts back as new invoices and
  payments, counting each sale twice) or anything voided here before it
  went. Records an earlier release sent are left as they went.
- **Paging and inactive accounts.** Every entity imports all its pages;
  inactive QBO accounts come in inactive; QBO bank and card accounts get a
  Banking identity; a subcustomer mapped to a job resolves through its
  parent.

#### Invoices and bank feeds

- **Show company logo on invoices** — in Settings and on the invoice form,
  on by default; it governs the invoice PDF, Print and the emailed PDF
  alike (#192). Only an administrator can change it.
- **Fetch older history** reaches a SimpleFIN feed back 3, 6 or 12 months —
  as far as the provider keeps (the SimpleFIN Bridge about 90 days,
  BankSync a year) — in 85-day slices the Bridge accepts, skipping what is
  already imported (#181, tested with BankSync by @cnbarry1).
- The update notice stays on for desktop installs; `SLOWBOOKS_UPDATE_CHECK=0`
  in `.env` turns it off. Stripe's own SDK telemetry is off.

#### Accessibility

- Text meets WCAG AA contrast in both themes everywhere it is drawn: every
  page, 142 dialogs, the sign-in and setup screens, pop-up messages and the
  PDF window. Muted text, the status bar, buttons, badges, notes and the
  colours pages wrote in by hand now come from theme colours that pass; the
  look is the same, a shade darker or lighter only as far as AA needs.
  Semi-transparent text is measured as it is painted, and charts' lines,
  bars and colour keys meet 3:1 in both themes (the light theme had the dark
  theme's bright colours, as faint as 1.5:1). This clears the contrast list
  the macOS release gate had carried for several releases.

#### For API clients and agents

- `POST /api/bills`, `POST /api/vendor-credits` and PO convert-to-bill: a
  line with an amount and no account (none on the line, the item or the
  vendor) is a 400; convert-to-bill takes an optional `lines: [{line_id,
  account_id}]`.
- Tax on a bill, PO→bill or vendor credit no longer touches 2200.
- An invoice, credit memo, duplicate or estimate conversion that adds up to
  $0.00 is a 409 (`code: zero_total`) unless the request sends
  `allow_zero_total: true`; a recurring template for $0.00 is a 400.
- 422 responses carry a plain `message` on each error.
- A payment, batch line or credit-memo application reaches only its own
  customer's invoices; a batch payment is in the home currency.
- New: `GET /api/bills/{id}/pdf`, `/api/purchase-orders/{id}/pdf`,
  `/api/credit-memos/{id}/pdf` (and `/print-preview`); `POST
  /api/payments/{id}/apply`; `GET /api/customers/{id}/credits`; `GET
  /api/deposits`, `POST /api/deposits/{id}/void`; `GET
  /api/banking/ledger-balance`; `PATCH /api/banking/transactions/{id}`;
  `POST /api/fixed-assets/{id}/post-purchase`; `GET
  /api/banking/reconciliations/{id}/report` (and `/pdf`); `GET
  /api/deposits/{id}`, `GET /api/bill-payments/{id}`; `POST
  /api/backups/restore` now reachable from Settings; `GET` on the
  W-2/W-3/940/941 PDFs; `POST /api/deductions/garnishments/{id}/end` (DELETE
  is a 405); `POST /api/qbo/import-runs`, `GET
  /api/qbo/import-runs/latest`, `POST /api/qbo/connect-manual`
  (administrators); `POST /api/simplefin/sync` takes an optional
  `{"history_months": 1-24}`; `GET` and `DELETE /api/uploads/logo`, `GET
  /api/uploads/logo/{id}`, `GET /portal/logo`, `GET
  /api/settings/unreadable-secrets`, `GET`/`DELETE /api/uploads/legacy`.
  545 operations.
- A company's files are served from its database: an attachment's
  `file_path` is `stored_files/<id>`, attachments and employee documents
  carry `from_shared_folder` and `missing`, and `POST /api/uploads/logo`
  answers with the logo's address (`/api/uploads/logo/<id>`).
  `/static/uploads/` is a 404. The generic attachment routes answer 404 for
  an employee document and refuse a record type that takes no attachments.
- Administrator-only (403 otherwise): downloading a backup, uploading or
  removing the logo, `PUT /api/analytics/ai-config` and its `/test`, and
  `POST /api/qbo/disconnect`. `GET /api/payments/payment-link/{id}` from a
  read-only sign-in is a 403 for an invoice with no payment link yet.
- A session signed in to another company is a 401 (`/api/auth/status`
  answers `authenticated: false`); sign in to this one. An attachment or
  employee document the upgrade found missing has `file_size: null`.
- New: `GET /api/settings/unreadable-secrets` names the saved secrets no
  key on the install decrypts. `GET /api/uploads/legacy` (administrators)
  says what the folder earlier versions shared still holds and which
  companies still need it; `DELETE /api/uploads/legacy` removes its files,
  or answers 409 naming those companies. `GET
  /api/employees/{id}/portal-token` answers `portal_token: null` with a
  `note` when the link's stored copy can't be decrypted (the link still
  works).
- Income by Customer `total_sales` excludes tax (new `total_tax`);
  `/api/checks/print` takes `bill_payment_id` only.
- A document `tax_rate` is a fraction kept to six places (8.875% is
  0.08875) and comes back that way; a NaN or Infinity anywhere in a request
  is a 422, not a 500.
- `GET /api/invoices` and `GET /api/bills` take `open_only=true` (what can
  still be paid or credited, filtered on the server; page with `skip`);
  items carry `is_discount`.
- `GET /api/auth/status` has `desktop`; `GET /api/system` has
  `update_check_enabled`; CSV money columns are written to the cent;
  tax-form, pay-stub and New-Hire Report PDFs have descriptive file names.
- The QBO per-entity import routes (`POST /api/qbo/import`,
  `/api/qbo/import/{entity}`) are for API clients; the page uses the
  background import.

#### What you'll notice after upgrading

- A bill line with no account (and none on its item or vendor) is refused
  instead of booked to Advertising — give your vendors a default expense
  account.
- A pay run that would pay someone $0.00 is refused and names them; approve
  their time or leave them off.
- Account numbers are digits; a customer CSV's terms must be ones the form
  offers.
- **If your books were entered on an earlier version:** the Sales Tax report
  shows any supplier tax an older release posted to Sales Tax Payable, and
  the entry that corrects it; bills made from a purchase order get their due
  dates; deposits made earlier are matched to the oldest waiting payments,
  so Make Deposits may list different waiting lines for a company with a
  partly deposited batch.
- Total Receivables on the dashboard is the A/R Aging total as of today, so
  an invoice dated in the future isn't in it until its date.
- A tax rate with more than two decimals sent through the API before 2.18
  was stored rounded to four places of the fraction on PostgreSQL; those
  documents keep the rounded rate (SQLite files kept the full rate, and it
  now reads back as sent).
- A company's logo, attachments and employee documents move into its
  company file the first time it opens on 2.18, so the file and its backups
  grow by their size. A logo or attachment marked as copied from the shared
  folder may be another company's: upload yours again. A receipt scan
  waiting to be attached when you upgrade isn't carried over (scans expire
  after a day); scan it again.
- **Docker:** keep both volumes mounted when 2.18 first starts. Any files
  in `slowbooks_uploads` are copied into the database (your database
  backups carry them from then on), and `slowbooks_backups` becomes
  writable, so Create Backup works. If you saved an email password, payment
  keys, a QuickBooks Online connection or a bank feed, copy the settings key
  out of the running container before upgrading
  (`docker compose exec slowbooks cat /app/.slowbooks-master.key`) and put
  it in `.env` as `SETTINGS_ENCRYPTION_KEY=...`; otherwise Settings asks for
  them again after the upgrade.
- A bookkeeper can no longer change the logo or the AI Insights settings,
  disconnect QuickBooks Online, or download a backup.

#### For developers

- The test suite runs in a data folder of its own: it never reads the
  machine's companies or the checkout's `.env`, and writes nothing into
  `app/static`. #192's node test suites run under pytest.
- A browser test signs in read-only and visits every page and dialog
  beside an administrator; any write it is offered fails the test.
- A write control a read-only sign-in can't use is marked `data-write`
  where it is built.
- `python -m app.services.encryption rewrap` also re-encrypts the saved
  settings when their key is derived from `PAYROLL_ENCRYPTION_SECRET`.

#### Schema

Six migrations: sales line prices to four places, deposits remember their
payments, purchase and item prices to four places (which also gives old
PO-made bills their due dates), document tax rates to four places of a
percent, each company's files in its own database, and employees' portal
links kept as a digest and an encrypted copy. An existing company file
upgrades when it opens.

### v2.17.3 — Payments land on the right account

**Pay Bills paid one vendor's bills with another vendor's payment.** The
screen lists every vendor's open bills, and sent all the ticked bills as one
payment to the first bill's vendor. On the QA company, ticking a CPA's bill
and a supplier's recorded one 5,638.26 payment to the CPA that also marked
the supplier's bill paid; the vendor balances, the check register and the
1099 figures were all wrong from then on. Pay Bills now makes one payment
per vendor, and asks you to pay one vendor at a time when you enter a check
number, since one check cannot pay two vendors.

**A payment pays down its own customer's or vendor's documents only.** A
customer payment could be applied to another customer's invoice (#189,
@Bit-Sage), and the same was true of applying a credit memo, a batch payment
line, and a bill payment to another vendor's bill. Each returned success and
reduced the other party's balance. All four now refuse with a message naming
the document and write nothing; a batch with one wrong line is refused whole.
Vendor credits already checked this.

If you paid several vendors at once from Pay Bills in an earlier version,
look at those bill payments themselves (Vendor Balances look right, because
every bill was marked paid): a payment to the first vendor may cover bills
that belonged to others. Void it and pay each vendor separately.

No schema change.

### v2.17.2 — Editing an invoice keeps its job costing

**Saving an invoice from the edit screen stripped its job costing.** An edit
rebuilt the invoice's ledger entry by a separate route from creating one,
and that route dropped the job, class and cost code from every line of the
entry; the invoice form, which has no cells for them, never sent the
per-line values back either. Revenue quietly left the job-cost reports each
time an invoice was saved. On the QA company, re-saving all 960 invoices
unchanged took their ledger lines from 565 job and 875 cost-code tags to
none. Both halves are fixed: an edit now posts through the same code as a
new invoice (#187, @Bit-Sage), and the form sends each line's job, class
and cost code back. The same 960 re-saves now keep all 565 and 875, and the
trial balance does not move.

Where it showed: **Job Cost Detail**, which splits a job's revenue by cost
code — one of the QA company's jobs went from seven cost codes to a single
*uncoded* line on 2.17.1. Job Profitability, which files a line under the
invoice's own job when the line has none, kept reading correctly for
invoices that carry their job on the header, which is why the loss was easy
to miss.

**An edited invoice posts the way a new one does** (#187). A foreign-currency
invoice's edit posts at its exchange rate — it posted document-currency
amounts to the home ledger before; changing the invoice date moves its
ledger entry with it, subject to the closing date; and a total can no
longer be edited below what has already been paid. Paid and partly-paid
follow the payments.

**API.** `PUT /api/invoices/{id}` with `status: "void"` is refused with a
message naming `POST /api/invoices/{id}/void`; an empty `status` is refused;
a requested `paid` or `partial` on an invoice whose payments say otherwise
is not applied.

No schema change.

### v2.17.1 — OpenAI works again

**AI analysis with OpenAI failed on current models** (#185, @Sciumo). OpenAI's
reasoning models — the gpt-5 line, including SlowBooks' default
`gpt-5.4-mini`, and the o-series — refuse `max_tokens` and any temperature
but their default, so the request was rejected before it ran, the Test button
included. OpenAI is now sent `max_completion_tokens`, and no temperature for
its reasoning models. Grok, Groq, Cloudflare and custom endpoints send what
they always sent.

**Room to think.** A reasoning model spends hidden reasoning out of the same
token budget as its answer, so the 1,024 tokens that suit every other
provider could run out before the answer began. OpenAI's reasoning models get
a ceiling of 8,192 — billed only as used — and a reply that stops at the limit
with nothing written now says so, instead of "empty response (body shape
unexpected)".

No schema change.

### v2.17.0 — Your ledger, in a spreadsheet

**Trial Balance and General Ledger save as a spreadsheet and a printable
file** (#179, @cnbarry1); Profit & Loss and Balance Sheet, which already
printed, gain the spreadsheet. Each report has *Save CSV* and *Save PDF*
buttons. The files hold the figures on the screen: the trial
balance with debit, credit and net per account and totals that agree; the
general ledger with every posted line, a balance brought forward, a running
balance, the kind of document each line came from, and a period total per
account whose net equals that account's trial balance line. Balances read
in each account's natural sign, the way the balance sheet shows them: an
asset or expense is debit minus credit, a liability, equity or income account
credit minus debit, so a payable you owe and income you earned read positive.
Amounts are
written as plain numbers, so a spreadsheet sums them without a conversion
step; text cells keep the formula guard every other export has. The on-screen
general ledger gains the same running balance and brought-forward row.
Exporting reads the books and writes nothing.

**Bank feeds from any SimpleFIN provider** (#181, @cnbarry1). SlowBooks has
always followed the claim URL inside a setup token, wherever it points, as
long as it is HTTPS and not a private or local address; the Banking page and
the setup guide named only bridge.simplefin.org. They now say any SimpleFIN
provider works.

No schema change. An existing company file opens with no upgrade step.

### v2.16.3 — The Docker image starts again

**A freshly built Docker image failed to start.** SQLAlchemy 2.1.0, released
this week, makes a plain `postgresql://` address use the psycopg 3 driver; the
image installs psycopg2, so the app could not import and the container exited.
Anyone building the server or Docker install from scratch since the release
was affected, on 2.16.1 and 2.16.2 alike. SQLAlchemy is pinned below 2.1, the
version every release has been tested on; moving to 2.1 will be its own
release. v2.16.2 was tagged past the red check that showed this — a miss in
the release process, and the Linux QA lane now builds the image from nothing
every time.

The desktop apps use SQLite and are unaffected by the Docker fault.

**The What's New box on the splash reads in the dark theme.** It had the
light panel the licence block had before 2.15.0 and was never given a dark
one: 2.34 : 1 in dark, now 11.95 to 15.23, measured in a rendered browser.
Found by the macOS lane and reproduced at the window by the owner.

No schema change.

### v2.16.2 — Tax-exempt customers, wider amounts, and two forms put right

**The estimate's line items line up with their headings** (#176, @cnbarry1).
The header read Item, Description, Cost code, Cost, Qty while the cells
underneath ran Qty, Cost code, Cost — so the quantity sat under *Cost code*
and the cost code under *Cost*. The cells follow the header now; picking an
item fills its standard cost as the line's cost, blank if it has none and
yours to overwrite; the item select keeps a readable width; and the form
opens wide, its table scrolling sideways when the window is narrower still.

**The Job Cost Entry dialog shows all of its cost lines** (#174, @cnbarry1).
Eleven columns in a 700-pixel dialog whose table hid its overflow: the
Bill? column and the remove button were cut off with no way to reach them,
and the selects had shrunk to a few characters. The dialog opens as wide as
the window allows, the table scrolls sideways when that is still not enough,
and every control keeps a readable width. Any dialog with a wide table can
ask for the same with `openModal(title, html, { wide: true })`.

**Hosting your own books on a cloud server has a guide**, `docs/cloud-hosting.md`
— one VPS, Docker, a proxy with a real certificate, backups off the box, and
what the setup does and does not give you. On the way, the production
compose file now passes `TRUST_PROXY_HEADERS` into the container, so the
proxy trust the TLS guide describes takes effect under Docker.

**A customer marked non-taxable pays no sales tax on any line.** The
exemption only filled lines that left their tax flag unset, and every sales
form sends each line's Tax box, defaulted from the item — so a reseller or
exempt customer billed from the window was charged tax. Invoices,
estimates, recurring templates and sales receipts all honour the customer
now, and the forms clear and disable the Tax boxes and say why. Found by
the Windows lane on this release's gate. Documents already saved keep the
tax they were saved with; check any open invoice to a reseller.

**A new document built from an old one gets the customer's current tax.**
Converting an estimate, duplicating an invoice and running a recurring
template copied the source's stored tax, so a reseller estimate saved before
this release became a new invoice billing tax and crediting Sales Tax
Payable. All three now compute the tax from the customer as they stand
today. Found by the Windows lane on this release's gate, on an upgraded file.

**The estimate screen shows the tax it will charge.** It taxed the subtotal
on screen instead of the ticked lines, so an unticked Tax box — for any
customer — still showed tax before saving; the saved estimate was always
right. It reads the same as the invoice screen now (macOS lane, this gate).

**The licence link on the splash reads at AA in the light theme** (4.29 : 1
before, 5.44 now, measured in a rendered browser). The dark theme's licence
block, fixed in 2.15.0, measures 7.23 to 15.23.

**Money columns hold up to 9,999,999,999,999.99** (#173, @6lb). Every
amount column widens from 12 to 15 digits, for currencies whose everyday
amounts are large — 9,999,999,999.99 dong is about US$400,000. Rates,
quantities and exchange rates are unchanged.

**Schema change:** one migration widens the money columns. The desktop app
and the Docker image apply it when a company file opens; a self-managed
server runs `alembic upgrade head`. Nothing is converted — the stored
amounts are the same numbers in a wider column.

### v2.16.1 — Wave's full export imports its journals

**Wave's full export imports its journals** (#169, @rcavatar1-debug). Wave's
"Get all transactions" file heads its sides *Debit Amount (Two Column
Approach)* and *Credit Amount (Two Column Approach)*; neither was recognised,
so every line read 0.00, the dry run said "14,372 journals ready to import",
and the import wrote none of them. The same file's *Amount (One column)* is
signed by what it does to the account, not by side, and is no longer read as
"positive = debit" when the file has its own debit and credit columns.

**A ledger whose amounts all read zero is refused, by name.** 0 = 0
balances, so an unrecognised amount column passed the dry run and imported
nothing — the hole behind this report and behind 2.11.1's Wave fix before it,
which fixed the headers and not the hole. The dry run now fails with the
file's own header row in the message, for every migration source; a few
amount-less journals among real ones are a warning and are counted as
skipped in the import result.

**A second click on Import does not double the books.** Nothing stopped a
repeat import from posting every journal again. A journal whose transaction
id an earlier import from the same source already posted is skipped; the dry
run says how many, the result carries `duplicate_journals`, and a later
export with new transactions imports only the new ones. The synthesized
opening-balance journal is likewise posted once.

No schema change. An existing company file opens with no upgrade step.

### v2.16.0 — The year at a glance

**Two new overview cards, both opt-in** (#166, @jarvis4openclaw): *P&L: Year to
Date* — income, expenses and net for the year with cumulative net by month —
and *Balance Sheet Trend* — assets, liabilities and equity at each of the
last twelve month-ends, the current month to date, as a line chart. The trend
folds current net income into equity the way the Balance Sheet report does,
so it balances at every point and its latest point equals the report. Pick
them under Customize; neither is in a default layout. A nonprofit sees
Activities, Statement of Financial Position and Net Assets.

**The chart import dialog offers a CSV template** (#164), the same file the
site hands out, served by the app so an offline install has it.

**A lower-case word takes a wholly lower-case phrase.** A nonprofit's card
description read "liabilities and net Assets": the vocabulary swap lowered only
the first letter of a multi-word replacement. Found on this release's gate; the
server's swap and the page's carry the same rule.

**A theme toggle redraws the charts on screen.** Canvas ink is painted with the
theme that was active when the chart was made, so toggling the theme with the
Balance Sheet Trend on screen left light-mode axis text on a dark panel
(1.06 : 1) until the next visit; the Analytics page had carried the same flaw
since it shipped. Found by the Windows lane on this release's gate.

No schema change. An existing company file opens with no upgrade step.

### v2.15.0 — Your chart, from your file

**Import a chart of accounts** (#139, #161, @tresero). Chart of Accounts →
Import… reads a CSV in the columns the export writes, any spreadsheet with
Number / Name / Type in its header row, or hledger's account list — the real
output of `hledger accounts`, `accounts --types` and `balance -O csv`, which
the tests run against files hledger 1.30.1 wrote. The first pass is a dry
run: every row's fate with a reason, nothing written; the second applies
that plan. Accounts you already have are matched by number, then by name,
and renamed to the file's names; when the file names one of the fifteen
control accounts (Receivable, Payable, Checking, Sales tax payable, a credit
card under liabilities, ...) that control account takes the file's name
instead of gaining a twin, so your chart replaces ours and every document
still finds its posting account. hledger paths keep their hierarchy and
their full path as the description; rows without a number get the next free
one in their type's range. *Replace the seeded chart* deactivates every
unused account the file does not name — control accounts and accounts with
history stay.

**Found by the gate before the tag:** a parent segment the file never lists
as a row (`assets:inventory`, `liabilities:credit card`) was being created
as a second, active *Inventory* beside control 1300 — and again on every
re-import (skytech, Windows lane). A parent named like a control account is
now that account, the user's tree hangs from the one the ledger posts to,
and a re-import of the same file is a no-op.

**Why this took two releases.** #139 asked for exactly this on September 11.
2.12.0 shipped delete / deactivate / rename of the seeded chart and the issue
was closed as released, while docs/features.md claimed a chart CSV import
that did not exist. The reporter came back on the 16th. The docs line is
now true, and the CSV page's result line no longer says "Imported undefined"
(it read a field the import never returned).

### v2.14.0 — In and out of a company, and the terms once

**Opening the app lands in your last company, with the picker one click
away.** The window opens straight to that company's sign-in screen, which
offers *Choose a different company*; the picker itself appears when there is
no last company, or whenever you ask for it.

**Sign out goes back to the company picker.** It used to reload the same
company's password prompt; to open another company, or even to see who the
users were, you closed SlowBooks and opened it again — and the Companies page
said so. In the desktop window, Sign out and the new **Switch company** button
on the Companies page stop that company's server and return to the picker;
the sign-in screen offers *Choose a different company* too. On a multi-user
install the sign-in screen lists the users, so a person picks their name and
types only their own password; names only, never roles, and only before
sign-in.

**The first launch shows the terms.** The splash that already opens on
every launch shows the short form of the license once per install — free
forever for what shipped, the forms are aids and the filer is responsible,
no warranty and no obligation to maintain, Illinois law — and the button
reads *I understand* until it has been pressed for that license version.
After that it is the splash it always was. The Windows installer shows the
full license with an accept step and leaves a copy beside the program. The
license itself moved to version 2.0 at the same time; every earlier release
keeps the license it shipped with.

No schema change. An existing company file opens with no upgrade step.

### v2.13.1 — The words the server sends

**Every sentence the server sends now follows the company type.** The
page's labels went through the terminology dictionary; the sentences the
server sent back never did, so a nonprofit's Pledge screen said "Invoice not
found" under it — 42 such sentences across the routes, three files wrapping
any. Found by walking a running server in both company types, an
instrument that measures the output the page-side guards cannot see (it is
checked in under `scripts/audit/`). Rather than 42 edits, the swap happens
once, at the boundary every HTTP error crosses; the missing-control-account
message goes the same way. Three surfaces that are not errors are wrapped
where they are built: the control-account purpose on the chart, the AI
analysis labels, and the "No job" bucket in job profitability. The analytics
empty state, which the 2.9.1 sweep missed because it is lower-case, is
wrapped on the page and in the PDF. And an all-caps dictionary key was being
treated as a shouted sentence: "P&L analysis" came out "ACTIVITIES analysis".

**What a posting writes for itself is in the document's own words.** Twenty-two
sites composed "Invoice #1081 - Boise Neon Supply" into stored ledger
descriptions, the inventory memo, the void reversal, the late fee, the
write-off, the recurring run, the provider payment record, and the line item
a payer sees on the Stripe, Square or PayPal page. They now say what the
document itself is — the same rule the printed page and the covering email
already used: a flagged pledge posts as "Pledge #1081", a nonprofit's
program-fee invoice stays "Invoice #1081", and the ledger, the email and the
printed document agree. The first cut of this swapped every invoice to Pledge
and the donor's PDF disagreed with the ledger; the macOS QA lane found it.
**History is never rewritten** — the words are chosen once, at posting time.
The integrations never keyed on those words: Stripe resolves by metadata,
PayPal by its custom id, Square by the order id, and the tests pin that every
id, amount and URL is byte-identical while only the text differs. QuickBooks
import keeps QuickBooks' words; a vendor's invoice on a printed check stays an
invoice; CSV export headers are unchanged.

**Default email templates name the document through `{{ doc_label }}`.** One
saved template reads Invoice for an invoice and Pledge for a pledge, instead of
opening with a fixed word; the variable was already in the editor's list. A
template you have already saved is yours and is not touched.

**Connecting QuickBooks Online works again.** Contributed by
@CimarronSiteServices (#151, root cause in #152). Intuit's consent screen
sends the browser back to the app as a cross-site navigation, and a
`SameSite=Strict` session cookie is never attached to one of those — so the
callback answered "Not authenticated" before the token exchange ever ran, on
every attempt, on every install. The callback is now exempt from the session
check the way the Stripe webhook is, and for the same reason: it carries its
own proof. The random `state` token stored when the connection starts must
match, exactly, or the callback is refused.

No schema change. An existing company file opens with no upgrade step.

### v2.13.0 — Bank of America, in a file the bank produced

**Bank of America detail CSV import.** Checking and savings detail exports
with the bank's statement-summary preamble now import into the review queue.
The statement's beginning-balance metadata is skipped because opening balances
are posted separately through the linked ledger account. Contributed by
@Lazyjimpressions, and parked for a day for one reason: no file a bank had
produced. He opened two real exports, reported what they contain, and then
committed a fixture derived from them with its byte shape pinned — CRLF, the
six-row preamble, minus-sign debits, a blank amount on the balance row, a
quoted comma in a description, a thousands separator. Every unknown the park
recorded is answered by that file. The one gap left, a check-number column
neither export has, is carried as a missing field rather than a wrong number.

**Only a sentence written for the user leaves the process.** Four code-scanning
alerts, open since August on the CSV, IIF and QuickBooks Online import routes,
all said the same thing: a caught exception's text reaches a response. The
sites already answered through the shared helper, and the scanner was still
right — for a ValueError it returned the exception's own text, and nothing
told "Missing customer NAME" apart from "invalid literal for int() with base
10: 'abc'". "In our own words" meant "is a ValueError", a convention nobody
could check. The marker is explicit now: a data problem carries the sentence
it was raised with, the helper passes that on and nothing else, and Python's
own wording is logged with its value and answered generically. A missing
control account, which an import used to swallow into "unexpected error",
tells the operator which account to restore. The query run locally against
this tree reports no such sinks; the previous tree reported five.

**A Windows machine without the WebView2 runtime is told what it can do.** The
portable zip carries no runtime installer (the installer does), and the
launcher's check for the runtime already existed. What was wrong was what it
said: the installed application was told to run a Python command it has no
way to run — no Python, no such file — the sixth appearance this month of an
error telling somebody to do something they cannot do. The installed build is
now told the two things it can do, and on Windows is offered the one path that
works without the runtime: the app in the system browser, held open by a box
the user closes to stop it. The tests execute that path against stubs and read
the installer's name out of the packaging script rather than from memory. Not
covered anywhere: the end-to-end on a machine with the runtime genuinely
absent, which needs a scratch machine. Recorded as such rather than claimed.

**A 1 ms timer for the Windows server, available and off.** Issue #107 measured
about half of all requests waiting exactly one 15.625 ms scheduler tick on
2.9.3. The server can now ask Windows for a 1 ms timer while it serves — and
first tell Windows 11 not to ignore that request from a process with no
window, which the server is — logging the resolution before and after. It is
**off unless `SLOWBOOKS_TIMER_RESOLUTION_MS` is set**, because the gate put a
Windows 11 box back at the true default and measured the unfixed build: a
median under 2 ms and not one sample in the tick band. The symptom does not
reproduce there, and a permanent 1 ms timer in a background process costs
power, so it is not imposed on every install for a benefit nobody has
measured. The switch and the log line are the instrument for anyone who does
see the tick.

**The bank-import dialog says it is working, and names Bank of America.** The
preview pane stayed empty for the whole round trip and the button stayed live,
so a second click put a second parse in flight; found by the owner on a
382-byte file. It shows *Reading the file…* first and takes the button away
until the answer is back, and the import button does the same. The file
picker's own label listed Chase and PayPal and not Bank of America — the one
place a user is actually choosing a file described a different product than
the one shipped.

**The test suite builds its database once.** Every test used to build its own
engine and schema; now the schema exists once and each test runs in a
transaction that is rolled back. Peak memory 795 → 606 MB and wall time 6:43
→ 4:00 on the same machine, with 2,199 tests passing in shuffled order. A
sentinel asserts five tables empty at the start of every test, so a test that
ever commits outside its transaction fails the next test by name. Found on
the way: the async library's per-run registry kept every closed event loop
alive through its own root task, one per request the test client made; those
are released after each test now. Memory still climbs across a run, about
170 KB per test from a source not yet named, so the issue stays open with
the measurement rather than closing on the improvement.

No schema change. An existing company file opens with no upgrade step.

### v2.12.1 — See an email template before you save it

**Preview an email template edit against a real invoice, before saving it.**
Contributed by @mdornich. v2.12.0 added a preview to the Email Invoice
dialog; this is the other half of the same idea. An operator editing the
invoice email under Settings still had no way to see an edit except by
saving over a working template and mailing a real customer to find out.

`POST /api/email-templates/preview` renders the text currently in the editor
against an invoice you pick. Nothing saved, nothing sent — asserted by
counting rows either side rather than claimed. The preview and the mail share
one template environment, because a preview rendering under different rules
than the mail is the same class of problem as the template and the send
disagreeing, which is what the previous release was about.

Credentials stay redacted in the preview by construction rather than by
remembering: it builds its context through the same helper the send does, so
the protection added in 2.12.0 covers this endpoint without anyone having to
notice it needed to.

Two things he deliberately left out, both correctly. Validating a template at
save time would mean a bad expression can stop an invoice going out, and
degrading to the built-in body is the better behaviour — the preview is where
an operator should find out instead. And this is its own endpoint rather than
an option on the send preview, because widening a request model that a
sending route shares is how a surface grows a capability nobody audited.

**The refusal to open an older company file now names a command that runs.**
Found by inspecting the published 2.12.0 artifact rather than the source: the
message named `scripts/repair-schema.py`, and that file was not in the
installed application. Files are bundled selectively, and the repair script
was not among them — so the operator most likely to read that message was the
one least likely to have the file. It ships now on both platforms — and,
more to the point, an installed application is told to use its own
`--_repair-schema` option rather than a separate Python command it has no way
to run. Both QA agents found the first attempt at this: the file was in the
bundle and nothing on the machine could execute it, because the program code
lives inside the executable rather than beside it.

That was the fourth time one mistake has appeared in this product — an error
telling somebody to do something they cannot do. The test that was supposed
to close it checked that the named file existed, which is exactly the check
that passes while the instruction still fails. It now takes the command out
of the message and runs it.

**And it tells the two kinds of blank apart.** Something no email template
can ever use is reported differently from something that is available and
simply not set for this invoice. The first version called both "not available
to an email template" — which was wrong about the payment link, and
contradicted the list of usable variables printed two inches above it. Raised
by the Windows QA agent as a suggestion rather than a finding; taken because
one message disagreeing with another message on the same screen is the defect
this release spent its whole review closing.

**A template preview says what came out blank, and the editor's own list of
what you can use is now accurate.** The hint under the editor offered
`{{ amount }}`, which does not exist and rendered as nothing, and offered
`{{ pay_url }}` without saying it is only set when a payment provider is
enabled — where a template using it bare would put the word "None" into a
customer's email. It no longer can: that name is simply absent when there is
no link, so it renders as nothing and is reported like anything else. The
list also now mentions the one variable that makes a single template read
correctly as both an invoice and a sales receipt, which it never advertised.

**A template preview says what came out blank.** Some things an operator
might type — `{{ config }}`, `{{ request }}`, anything the template sandbox
refuses — are simply not available to an email template, so they render as
nothing. The preview showed a working template with a hole in it and no
reason for the hole, which is the one outcome that tells the author nothing
at all.

The preview now lists what resolved to nothing, beside the rendered output.
The output itself is unchanged and still byte-identical to what would be
sent, because a preview that renders under different rules is not a preview.
Raised by the macOS QA agent, who noticed it while running a battery of
deliberately hostile template input and pointed out that silently-empty
deserved a decision rather than being inherited.

**And a repair that cannot finish no longer starts.** The repair used to
clear the tables in its way and only then discover whether it could run the
upgrade at all — so on an installed application, where it could not, the file
was left with nothing to clear and the same problem, and the next attempt
offered a different instruction that also could not work. A loop entered by
following the instructions. It now loads what it needs first and, if that
cannot work, says so while the file is still untouched.

The reason it could not work on an installed application was that the repair
ran before the application had worked out which company file it was talking
about, so it fell back to looking for a database server that is not there.
Found by the Windows QA agent, who first reported a different cause, tested
it, and withdrew that half of their own report.

**Repairing a damaged file no longer gets slower the more tables are
involved.** Each attempt restarted the whole upgrade, so the work doubled
with every table in the way: three tables took seven passes, and five would
have exceeded the limit and given up. Everything blocking is now cleared in
one pass, so the cost is one step per table. Measured by the Windows QA
agent, who noticed the comment in the code claimed one pass per table and the
behaviour did not.

**You can see a template edit before you save it.** Editing `invoice_email`
under Settings -> Email Templates still meant saving over a working template
and mailing a real customer to find out what it looked like. The editor now
renders what you have typed against an invoice you pick — no save, no send,
no record written. It renders through the same sandboxed environment the send
uses, so a preview cannot follow different rules than the mail it previews.

### v2.12.0 — Your chart, your templates, your clipboard

Five reader-reported defects and one privately reported security issue,
closed together. Two of them had been breaking something on every install.

**Security: an editable email template could read stored credentials**
(GHSA-c3v4-f43f-4wqm, reported privately). The settings dictionary handed to
an email template was the decrypted one, so a template containing
`{{ company.smtp_password }}` rendered the live password, and `{{ company }}`
dumped every credential at once — into an email addressed to whoever sent it.
The settings screen has always shown these as asterisks to every role
including the administrator, so the intent was never in doubt; the template
simply walked around it.

It is a privilege escalation: a bookkeeper cannot read or write those values
through the settings API, but could edit a template and send.

Every credential is now replaced before a template ever sees it, at the point
the context is built rather than at each place that sends, so a renderer
added later inherits the protection instead of repeating the mistake. The two
lists of which settings are secret — one used for encryption, one for the
settings screen — were identical and are now literally the same list, because
a credential added to one and not the other would be encrypted at rest and
printed in plaintext by an email.

**This release found the wider half of it.** The acknowledgment letter has
been affected since v2.9.0 on nonprofit installs. Making the invoice template
render, below, would have taken that to every install sending an invoice.
Caught before release; both paths are closed and both have tests.

**A new company arrives with 57 accounts, and not one of them could be
removed or hidden** (#139, @tresero, coming from hledger with a chart of his
own). Delete refused every seeded account, because all 57 are flagged as
created by the software and the rule rejected anything with that flag —
permanently, even on a company with no transactions. Deactivate was not on
the page at all, and our own delete error told people to "deactivate it
instead", which the interface could not do. The only thing an operator could
do to our chart was rename it.

That is the same wrong flag 2.10.2 found hiding the Edit button, in a second
place. The gate is the control-account registry now: fifteen numbers the
posting code resolves literally, where a document that cannot find one is
issue #119. Those rename and deactivate but never delete. The other
forty-two go when you say so.

Deactivate, Reactivate and Delete are on the page, with a button to show
inactive accounts — hiding something with no way back to it is a trap.
Delete is not offered on a control account at all, rather than offered and
refused. And a blocked delete now names what is in the way and how many,
rather than "referenced by other records", which told the operator nothing
about where to go and undo it.

**The email template you save is now the email that gets sent** (#140,
@mdornich). Editing `invoice_email` under Settings saved correctly and
changed nothing: the invoice email was always built from a fixed layout, and
the machinery for rendering a saved template existed with exactly one caller,
the donor acknowledgment. Four releases of a settings page that did nothing.

The template is found by name and never by the document's face — selecting on
the label would have skipped the saved template for every sales receipt,
which is ordinary businesses and not only nonprofit installs. A template with
a mistake in it falls through to the built-in body rather than stopping the
mail.

**There is a preview in the Email Invoice dialog**, rendered by the same
server code that sends, so what an operator reads is what a customer
receives. An operator editing a template previously had no way to see the
result short of mailing a real customer.

**And emailing an invoice failed on every install.** The dialog posts a
`message` field; the route accepted `recipient` and `subject` and rejected
anything else, so every send failed validation before reaching the sending
code. The Message box did not get ignored — it broke the button it sat on.
Nothing caught it because every test called that endpoint with a payload the
endpoint accepts rather than the payload the page sends, which is the shape
of 2.10.3's unreachable attachment route.

The message now appears as a paragraph at the top of the email, always,
rather than as something a saved template has to remember to include. A
template that forgot would have dropped it silently, which is the same defect
wearing a different hat.

**Copy buttons worked on the machine running SlowBooks and silently failed
everywhere else** (#137). Copying to the clipboard requires a secure
connection, and the desktop app gets one only because a loopback address
counts as secure. Anyone reaching SlowBooks over a network address — Server
Edition, Docker on a host address, a tablet on the Wi-Fi — got a button that
appeared to do nothing.

Nobody who could reproduce that was in a position to see it: a developer runs
on loopback, and so does every test machine. It was found by measuring why
the clipboard was permitted rather than being satisfied that it was.

All four copy buttons go through one helper now. It names the cause when it
knows it, and leaves the text selected so recovery is one keystroke. Two of
the four had no fallback at all — the payment link reported a clipboard
refusal as a server error.

**The app refuses to start against a company file older than itself** (#132).
It used to create the new tables without altering the existing ones and
without recording the upgrade, after which the file could never be migrated
again. The damage was silent when it happened and surfaced much later as a
start failure with no obvious cause. Neither the desktop app nor the Docker
image could reach it; a self-managed deployment could.

**And a file it has already happened to can now be repaired.** Refusing to
start protects files going forward and does nothing for one already damaged,
where the ordinary upgrade command fails on a table that already exists —
so the refusal was printing advice that could not work for exactly the
people who hit it. `scripts/repair-schema.py` drops the empty tables left
behind and then upgrades. It will not touch a table with anything in it: the
mechanism that causes this only ever creates, so what it left behind is
empty, and a table with rows was made by something else. The startup message
now tells the two situations apart and names the right remedy for each.

**One HarfBuzz in the macOS bundle** (#141, @mdornich). Two copies of the
same text-shaping library were being collected under one name, so whichever
loaded first won for the whole process — and the pieces did not match, which
killed the first PDF render on a locally built app. The module that brings
the second copy is excluded, and the build now fails loudly if a bundle ever
contains anything other than exactly one.

The first attempt at this removed the duplicate library after it had been
collected, which was wrong in two ways that only appear on a machine that has
the problem. On its own it left no usable copy at all, so an affected builder
could not build. Repairing that produced a bundle that looked complete,
signed and notarized, and still died at the first PDF. Both were found by
deliberately reproducing the fault on a machine that did not have it, rather
than by a build passing.

### v2.11.1 — Wave imports work, and you can copy an API token

Both fixes in this release came from @rchanks, and both were found the way
we most want things found: against a real file and a real workflow.

**Importing from Wave brought in nothing and said it had worked.** Wave's
**Account Transactions** report — the plain export any Wave user can pull,
with no plan restrictions — failed on two independent faults at once.

`parse_gl()` looked for `Account Name`, `Debit Amount` and `Credit Amount`.
The report's real headers are `ACCOUNT NUMBER`, `DEBIT (In Business
Currency)` and `CREDIT (In Business Currency)`. None matched, so every row
parsed as an empty account with zero on both sides — and the dry run then
reported the file **balanced**, because zero equals zero. It failed once, on
a deduplicated message about an account named `''` that never changed no
matter how the source file was reshaped, because the parser had never read
the file's amounts or account names to begin with.

A dry run that passes because everything is zero is worse than one that
fails, and that lesson is not specific to Wave.

The second fault was the filename. The bundle classifier checked the
fragment `account` before `transaction`, and Wave's own export is named
`Account Transactions.csv` — so an unambiguously-ledger file was always
taken for the chart of accounts and collided with the real chart upload.
Ledger fragments are checked first now.

That reorder was measured rather than assumed. Across realistic export
filenames from the four sources that share the classifier, fourteen classify
identically and nine move, **eight of the nine from wrong to right** — so it
corrects files beyond Wave. The ninth, a chart of accounts named something
like `General Account List.csv`, would now be read as a ledger. That is
inherent to first-match-wins rather than to the new order, which is right far
more often than the old one, and it is recorded so the trade stays
deliberate.

Found while migrating a real business's several years of Wave history: a
six-year, 2000-transaction export that now imports with a zero-difference
trial balance.

**An API token is shown exactly once, and had to be copied by hand.** It sat
in a styled block with no button and no shortcut, so getting it meant
triple-clicking and hoping — which failed often enough that one person
recovered their token by screenshotting it. A secret ending up in a camera
roll is a security outcome, not an inconvenience. There is a Copy button now,
with a clear message to fall back on if the browser refuses.

### v2.11.0 — Record a credit from a supplier

**A supplier credit had nowhere correct to go.** Reported by
@CimarronSiteServices (issue #129) while evaluating SlowBooks against
QuickBooks Online. A vendor issues a credit for returned or short-shipped
materials; a bill with a negative line is refused, an expense with a
negative amount is refused, and both refusals point at credit memos — which
only accept a customer. The reporter went looking through bills, expenses,
credit memos, card charges and journal entries, and was right that none of
them was the answer.

**Vendor Credits are the missing document.** A bill posts DR Expense / CR
Accounts Payable; a vendor credit is its mirror. It reduces what you owe
that vendor the moment it is entered, and you apply it to a bill afterwards
— or leave it on the vendor's account until there is a bill to settle.
Applying posts nothing at all, because A/P already moved when the credit was
issued; applying only decides which bill it pays down.

The reporter also named why a manual journal entry against A/P was not good
enough, and he was right: a journal entry moves the general ledger without
moving the vendor sub-ledger, so A/P aging and the vendor's balance stop
agreeing with account 2000. That is the same split as #119, and it is why
this is a document rather than a shortcut.

**Returning stock takes it off the shelf.** A bill receives inventory into
the Inventory asset rather than an expense; a credit for returned goods
takes it back out of Inventory, records a `return_out` movement, and drops
the quantity on hand. Anything else would leave an asset on the balance
sheet for goods that are no longer in the building. A credit for an
inventory item with no asset account is refused rather than posted to an
expense — the same refusal `create_bill` already makes, for the same reason.

**Both aging reports were hiding credits, and now show them.** Found while
building this, and confirmed before it was fixed: an unapplied credit memo
credits A/R at the moment it is issued, but A/R aging summed only invoice
balances. A customer with a 1,000 invoice and a 300 credit showed a ledger
balance of 700 and an aging total of 1,000. The report overstated what
customers owed by every credit not yet applied, and the sub-ledger did not
tie to account 1100. The payable side would have inherited exactly the same
hole, so both are fixed together, and both reports now carry an
`unapplied_credits` column — because "you owe 700" and "you owe 1,000 and
hold a 300 credit" are different facts to someone about to pay a vendor.

`CONTROL` on the badge vocabulary: `.badge-issued` and `.badge-applied` had
no CSS rule and never have. Credit memos have rendered an unstyled badge
since the day they shipped. Vendor credits use the same two words, so both
documents are styled now.

### v2.10.3 — You can see when there is a new version

**The update notice was in the last place anyone would look.** It sat at the
bottom of the sidebar, under every menu item, so it was found only by
someone who scrolled the whole list — which meant people stayed on old
versions without knowing there was a newer one. It is at the **top** of the
sidebar now, directly under the edition line, visible the moment the app
opens. Deliberately a quiet banner rather than a dialog: it never blocks
anything, and it occupies no space at all when you are up to date. The
version you are running is shown beside the edition too, because knowing
that is half of knowing whether there is a newer one.

**Clicking an attachment returned nothing at all.** `GET
/api/attachments/download/{id}` was declared *after*
`GET /{entity_type}/{entity_id}`, and FastAPI matches in declaration order —
so `/download/2` bound the first route as entity type "download", entity 2,
and answered an empty list. The download route was never reached. You could
attach a file and never get it back, and no test caught it because every one
called the handler rather than the URL. Found by macbase1 during the gate
and reproduced by skytech; the route is reordered, a test now walks the URL
a browser walks, and a second test checks **every** route in the application
for the same shadowing, so the class is closed rather than the instance.

**The search results panel never closed.** Not on clicking away, not on
clearing the box, not on changing page — it stayed until a reload. At rest
it painted a two-pixel sliver under the toolbar in every session, which is
the grey edge along the top of the desktop screenshots taken for every
release. The JS was right all along: it adds a `hidden` class in ten places,
and **there was no rule that hid anything**, only two element-specific ones
for the modal and the splash. The generic rule the code has always assumed
now exists.

**The Quick Entry log was unreadable in dark theme** — near-white text on a
hardcoded white block, about 1.1:1 in a product that documents WCAG AA
conformance. Quick Entry exists to enter a batch and read the log back, so
the confirmation was the thing you could not see. It follows the theme now.

Both CSS defects were found by the marketing agent building the training
videos against the installed bundle, by reading what the browser computed
rather than what the stylesheet said.

**An attachment added on Windows could not be found when the same company
file was opened on a Mac or Linux.** `app/routes/attachments.py` stored the
path with the platform's own separator, so a file uploaded on Windows went
into the database as `uploads\attachments\invoice\42\report.pdf`, which
every other platform reads as a single filename. Company files move between
machines routinely — that is the point of a file you own — so this was
reachable rather than theoretical. Stored in portable form now, and read
tolerantly so rows an existing Windows install already wrote keep resolving.

It surfaced as a failing test on Windows that looked like a hardcoded slash
in an assertion. It was the product hardcoding the platform.

**The suite runs on Windows in CI** (issue #121). It ran on Linux only and
the Windows build workflow never ran it at all, which is how a Windows-only
encoding failure reached 2.10.0 with nothing able to catch it — a reader
found it, not us. No extra system libraries were needed once WeasyPrint's
import went lazy in 2.10.2, and the job enforces a skip budget, because a
portability job's worst failure is staying green while covering less.

Getting it green found **eight** Windows-only defects, seven in the tests:
two macOS-only modules needing a platform guard, a literal path compared
against one built with `Path()`, two fixtures writing a shell-script stub
Windows cannot execute, one test measuring the OCR engine check rather than
the PDF path it was written for, and — reached only once the first seven
were fixed — a test that **terminated the test runner**, because
`os.kill(pid, 0)` is a harmless existence check on POSIX and a process kill
on Windows. The launcher itself was already correct.

**A host-dependent test on macOS**, reported by @ContractorKeith in his
v2.10.2 review: the stock-install fallback test emptied `PATH` but left the
real stock locations enabled, so on a Mac with Homebrew Tesseract the
resolver correctly found `/opt/homebrew/bin/tesseract` and the assertion
failed. Same class as the Windows findings — a test that only passed where
the thing was absent. His isolation fix, applied with thanks.

**Test suite memory** (issue #124): peak 1202 MB → 698 MB and 20% faster, by
rebinding one session factory per test instead of building two fresh ones
that each registered an audit listener, and by not running the application's
startup events 2,030 times for something no route reads.

### v2.10.2 — You can edit your chart of accounts again

**2.10.1 told operators "You can rename it", and through the interface they
could not rename anything at all.** Found by skytech at the GUI while
confirming 2.10.1's control-account guard. The Chart of Accounts rendered
its Edit button only for accounts that are not system accounts, and *every*
one of the 57 accounts a new company is seeded with is a system account — so
the Actions column was empty for every row on the page. The release notes,
the changelog and the docs all promised a rename the product did not offer.

The gating predates 2.10.1 and is not a regression from it. What 2.10.1 did
was make the gap visible by publishing the promise.

**Every account has an Edit button now, and the API is the authority on what
may change.** Hiding the control pre-emptively was a reasonable belt when the
API had no opinion; it has a good one since 2.10.1, and it refuses the number
and the type of a control account with a message naming the account and its
purpose. So the button is there, renaming works, and the fifteen ordinary
seeded accounts nobody considers structural are editable again too.

**Control accounts are marked and explained.** A `CONTROL` badge on the row,
and an edit form that says which account it is, what the software posts to it,
that the number and type are fixed because a document that could not find it
would have nowhere to post — and that you can rename it. The number and type
fields are disabled rather than absent, so the reason is visible at the point
of the restriction. `GET /api/accounts` carries `is_control` and
`control_purpose`, derived from the registry, so the page cannot drift from
what the posting code actually reads.

Also resolved: a dead ternary on the accounts row (`is_system ? '' : ''`,
both branches empty) that had been standing in for the badge.

**The test suite runs without WeasyPrint's native stack** (issue #121).
Importing the app pulled in pango/cairo/gobject at module scope, so the suite
could not even be collected on a machine without them — which is how a
Windows-only failure reached 2.10.0: CI runs pytest on Linux only, the
Windows workflow never runs the suite, and the one box that would have caught
it could not import the app. WeasyPrint is imported lazily now; 1,988 of
2,025 tests run with nothing installed and the 37 that genuinely render skip
cleanly. A test skips exactly when it reached for the missing library, so
there is no marker list to forget. Both PyInstaller specs name WeasyPrint
explicitly, because a lazily imported PDF engine is not something to leave to
the scanner's discretion.

### v2.10.1 — A document is never accepted without its journal entry

**A saved invoice that never reached the books is worse than a rejected
one.** wilsons043 reported (issue #119, against the signed 2.10.0 Windows
release) that renumbering Accounts Receivable away from 1100 made every
later invoice, payment, credit memo and estimate post *no journal entry* —
while still returning success and still appearing in the A/R aging. The same
thing happened on the payables side with Accounts Payable and 2000. The
sub-ledger and the general ledger drifted apart with nothing shown to the
operator.

Two independent faults produced it, and both are fixed.

**The chart no longer lets you renumber an account the software posts to.**
Fifteen account numbers are resolved by literal value somewhere in the
code — not the six in the original report, which is why the fix is a
registry rather than a patch at one site. Changing the number or the type of
one of those is refused with a message naming the account and what it is
used for. **Renaming stays allowed**, because only the number is
load-bearing: an accountant relabelling 1100 as "Trade Debtors" was never
the problem. The guard is deliberately *not* keyed on the system-account
flag, since every seeded account carries it and renumbering an ordinary
expense account is a reasonable request.

**A posting that cannot resolve its account now stops.** The resolvers
returned `None` on a miss and their callers read that as "skip the journal
entry" — twenty places did this, covering both receivables and payables and
both manual entry and import. They raise now, and the request answers **409**
naming the missing account, with nothing written. This is the half that
matters: renumbering was only the easiest way to reach a fail-open that the
recurring-invoice and unattended import paths could also have hit.

**Opening a company file now says if its chart is incomplete**, rather than
waiting for the first document to fail. That also closes a second route the
reporter found: a company bootstrapped outside the normal path has no chart
at all and used to accept documents that posted nothing.

*Why nothing caught this.* Through all of it the trial balance **balanced** —
debits equalled credits to the cent, because the ledger stayed internally
consistent and was simply missing an entry. Our three-platform release gate
compares trial balances every release and would have passed this every time.
Only tying a control account to its sub-ledger finds it. The regression test
asserts that tie-out, not the balance.

Thanks to wilsons043 for a report that included a clean reproduction, the
root cause, and the sixteen call sites they had read but not run.

### v2.10.0 — The bank register is the ledger

**A register entry moves the general ledger, and the register shows what the
ledger has.** Discussion #112 (a QuickBooks user) asked why a −500 entered in
the checking register left account 1000 untouched; the answer was that the
register had been a side ledger since day one — its own rows, its own stored
balance, imports that never posted, documents that never appeared in it,
reconciliation ticking rows the ledger had never seen. From 2.10 the ledger
is the only ledger (issue #114).

**What a bank account is now.** A chart account flagged `bank` or
`credit_card` (Checking, Savings and Credit Card come flagged; Chart of
Accounts can flag others). Every "paid from", "deposit to" and "pay from"
picker lists exactly those, and the register, transfers and reconciliation
are keyed by the ledger account. A bank feed (SimpleFIN, OFX/QFX, CSV) is the
account's statement identity and must link to a ledger account.

**One sign rule.** An amount above zero goes into the account (a deposit, a
card payment), below zero comes out (a payment, a card charge) — the same on
a bank and on a card; a card's register shows the amount owed as a positive
number. Statement lines already arrive that way, so nothing is flipped.

**Register entries post** (DR category / CR account for money out, the
reverse for money in; a bank or card category makes it a transfer). **Transfers**
are a document: paying a card is a transfer from the bank to the card. Credit
card charges pick the card (default 2100) and can be voided; so can register
entries and transfers. A void refuses an entry a completed reconciliation
holds and hands any matched statement lines back to the review queue.

**Statement lines are a review queue.** On import each line looks for the one
posting the ledger already has for it (same amount, same side, within five
days; a check number narrows it; two equal candidates stay unmatched — no
guessing). Bank rules suggest a category and never post. What is left waits in
*To review* on the account: **Add** posts it with the category, **Match** links
it to something already entered, **Exclude** drops it, **Add all categorised**
posts the rule-suggested lines in one click that says what it skipped.

**Reconciliation runs over the ledger's lines**: the prior statement's balance
is the beginning balance, ticks clear ledger lines, matched statement lines
arrive cleared, and completing locks the lines. One open reconciliation per
account; Abandon keeps the ticks.

**Upgrading.** The old register balance is not posted for you — a migration
must not write ledger lines you have not reviewed. The Banking page shows the
number once, with **Post as opening balance** (against 3900 Opening Balance
Equity, created on demand) or **Dismiss**. Feeds that were never linked get a
bank account created and linked. Rows ticked in the old reconciliations are
excluded from the queue (Restore brings one back). API changes: `POST
/api/banking/accounts` needs `account_id` and takes `opening_balance` (the old
`balance` is a 422); `POST /api/banking/transactions` needs
`category_account_id` and returns the journal entry; `GET
/api/banking/transactions` lists statement lines only; reconciliations take
`account_id`. The Check Register page is the Banking register (old bookmarks
redirect).

**QA baseline.** The shared NEONpulse fixture posts its bank account's 42,500
opening balance now, so the cross-platform trial balance moves from
3,316,390.46 to 3,358,890.46 — deliberately, once.

**Also in 2.10.0 — PDF receipts scan on Windows and macOS with nothing to
install (issue #116).** Scanning an image on the desktop apps has used the OS
engine since 2.9, but a PDF first has to become an image and only poppler's
`pdftoppm` did that — a tool the installers do not ship, so a PDF on Windows
answered "PDF scanning requires poppler-utils". The Windows app now renders
page 1 with Windows.Data.Pdf and the macOS app with Quartz, both already in
the bundle; poppler-utils stays the Linux path and the fallback anywhere it is
on PATH. When nothing can render, the message names the fix for the platform
you are on. `GET /api/ocr/status` reports `pdf` (`windows` | `macos` |
`poppler` | null) and the Settings OCR row shows it. A renderer's own error text
(a WinRT HRESULT, a Quartz message) stays in the log; the response says the PDF
could not be read.

**Dependency: WeasyPrint 69.0 → 70.0** (CVE-2026-55073, GHSA-jf6q-chmf-3h3v: two
`write_pdf()` channels ignored a document's `url_fetcher`). The app's fetcher
was already data-URI-only and the templates never pass those channels, so no
SlowBooks PDF was exposed; the pin moves because the scanner flags 69.0 and
because 70.0 made the fetcher a class the library enforces everywhere
(`allowed_protocols`). A regression test renders `file://` images, stylesheets
and `@import`s and asserts the file's bytes never reach the PDF.

**Three Windows/launcher gaps from the 2.9.x gates closed.** The Windows exe now
carries a version resource (FileVersion, ProductVersion, ProductName from the
app version — Properties → Details and inventory tools can read it; the build
fails if it is missing; issue #106). A headless `--serve-lan` / `--no-window`
run no longer rewrites the desktop app's `.env` DATABASE_URL or last-opened
company, so it cannot repoint the next windowed launch (issue #110). Two
error strings that interpolated another library's exception text (the stored
scan image reader, the custom AI worker URL parser) now say the problem in the
app's own words (issue #111).

**macOS release tooling (testing-repo #28, macbase1).** `release.py` retries a
codesign step up to three times when Apple's timestamp service answers
"A timestamp was expected but was not found" — that failure alone; any other
signing error stops the run on the first try. The app bundle's
`CFBundleVersion` is now `<version>+<12-char git SHA>` so two builds of one
release can be told apart from the bundle itself (the Windows exe got the same
treatment in #106); the macOS workflow refuses a bundle without it.

**Cash flow statement follows the cash** (PR #117, @jsonmez). The report used to
sum the net change of every account — both sides of every journal, non-cash
accruals such as an unpaid bill, and opening-balance carry-forwards — and could
overstate the period's cash change several times over. It now reads only the
non-cash side of journals that touch a bank account (the chart's `bank` kind),
classifies each cash movement once, and leaves opening-balance entries out;
transfers between bank accounts net to nothing. Investing shows a purchase as
an outflow directly, so the old sign flip is gone.

### v2.9.4 — The company logo on every document; exception text stays in the log

**Every PDF now carries the company logo when one is set.** The logo
helper fed only the analytics PDF and the new-hire report; invoices,
estimates, statements, collection letters, donor acknowledgments, giving
statements and the financial reports never received it, though the
features list said they did (discussion #108). The render helper attaches
it to every document and each header shows it. (The estimate and report
templates now also receive the vocabulary dictionary the other documents had;
nothing in them uses it yet.) Checks print on pre-printed stock and stay
logo-free on purpose.

**Exception text stays in the server log.** The QuickBooks Online import,
export and OAuth callback, the IIF import and the QuickBooks report CSV
importer answered failures with the exception's own words, which can carry
paths and provider responses (CodeQL stack-trace exposure, five alerts).
They now log the traceback and say only that the step failed; data problems
in the CSV importer keep their wording. The donor acknowledgment preview's
reason is a fixed phrase off a typed exception, as it always meant to be.

### v2.9.3 — SimpleFIN request pinned to the address the guard approved

**One security fix, right behind 2.9.2.** The SimpleFIN SSRF guard resolved
the bridge hostname and refused private addresses, then the HTTP client
resolved the name again to connect — a second DNS answer could steer the
socket at a private service (DNS rebinding; CodeQL py/full-ssrf, #104,
raised on the 2.9.2 pull request and wrongly dismissed as guarded). The
request now connects to the address the guard checked, with the hostname
kept for the Host header and TLS (the certificate is still verified against
the hostname), and a response from a non-global peer is discarded.

### v2.9.2 — Security: HR and payroll are admin-only; payment row locks; Server Edition CSP; SimpleFIN on PostgreSQL

**Security (Server Edition).** Four private reports arrived on the same
morning, three from @hongshengy and one from @furkan-arslan-sec, and all four
were right. The role gate treated everything outside six admin prefixes as
"daily books", so a bookkeeper could mint any employee's self-service portal
token (a full login as that employee: W-4, bank accounts, pay stubs), rewrite
any employee's direct-deposit account and export the NACHA file, and a
read-only user could download pay stubs, W-2s and I-9 paperwork. The docs
said HR and payroll were admin functions; the code now agrees: payroll,
tax forms, benefits, garnishments, onboarding, and the credential-bearing
parts of an employee record (portal token, bank accounts, documents,
E-Verify, year-to-date) are refused to bookkeeper and read-only roles for
every method; creating or editing an employee is an admin write; the
employee list stays readable as a directory with pay, tax and address fields
blanked for non-admins. The SPA hides those pages for non-admins. Separately,
batch payments and bill payments now take the same row lock on the invoice
or bill that single payments already did, so two concurrent requests on
PostgreSQL cannot both pass the balance check and over-apply; an
over-application that slips past the check is refused with 409 instead of a
negative balance. Advisories GHSA-rh68-48w8-pj8r, GHSA-rh75-6834-f66j,
GHSA-pwj7-6qq3-h4fj, GHSA-rm5h-555g-vpjj; fixed in 2.9.2.

**Server Edition no longer serves the desktop's relaxed script policy to
LAN browsers.** The launcher marks every server it starts as "desktop",
including headless `--serve-lan`, so the `'unsafe-eval'` allowance the
native web view needs went to the whole office (found by Keith in the
post-release macOS review). The policy is now decided per request: relaxed
only when the launcher flag is set *and* the request arrived over
loopback, which is the only way the web view ever connects. **SimpleFIN
settings on PostgreSQL** — the settings table was created with a 500-
character value column that SQLite ignores and PostgreSQL enforces, and a
bank feed's access URL is longer than that; the column is now text
(contributed by @kycrna). **Bank feeds can target a liability account**,
so a credit card feed lands where the card lives (also @kycrna).

### v2.9.1 — Post-release tidy from the 2.9.0 gate

**A stored AI provider key can be removed.** `PUT /api/analytics/ai-config`
with `"api_key": ""` clears it (omit the field to keep it; a value replaces
it), the spec says so, and the Settings page has a Remove button beside the
saved-key mark — it no longer round-trips a blank field. **Windows releases
publish `SHA256SUMS.windows`** beside the installer and zip, the same
format as the macOS file, so a download can be checked without trusting
the transport. **The migrations now create every table** (`api_tokens` was
the last one only app startup made), so `alembic upgrade head` alone yields
the complete schema. Two nonprofit vocabulary leaks closed: the
Contributions by Donor card described "sales totals", and the analytics
receivables aging header said "Customer". The macOS maintainer runbook
describes the staple-before-DMG order that has shipped since 2.9.0, and the
install guide's table and account counts are current.

### v2.9.0 — Nonprofit mode

**A nonprofit sees its own words in the first minute.** Settings → Company
Type → Nonprofit swaps the vocabulary everywhere it shows: Customer → Donor,
Invoice → Pledge, Sales Receipt → Donation, Class → Fund, Job → Grant, Profit
& Loss → Statement of Activities, Balance Sheet → Statement of Financial
Position, Equity → Net Assets. One dictionary, applied at render, on screens,
in report titles, in PDF filenames and on the dashboard; nothing in the API
or the database changes name, and a business file renders exactly what it
did before. Printed documents are literal, not vocabulary: a donation prints
as DONATION RECEIPT, a pledge as PLEDGE, a program fee still as INVOICE.

**Net assets by restriction, without a closing entry.** A class is a fund
with a restriction (without / with donor restrictions, purpose or permanent)
and a default function. When a restricted fund spends for its purpose, a
**Release from Restriction** moves that much to net assets without donor
restrictions — one document, DR 3400 / CR 3300 tagged to the fund, with the
amount suggested from the fund's unreleased spending. The **Statement of
Financial Position** splits the change in net assets by restriction at
report time, the way the balance sheet already synthesizes net income, so
the two net-asset lines always add up to the balance sheet's equity; the
**Statement of Activities** shows revenue and expenses in two columns with
releases between them and its change in net assets is the P&L net income;
**Fund Balances** shows each restricted fund's beginning, contributions,
spending, releases, ending and unreleased. P&L by Class now groups on the
line's class first (a bill with three line classes and a blank header used
to land whole in Uncategorized), and every void reverses with job, class,
cost code and function carried.

**Every expense knows its function.** Posted lines carry program /
management / fundraising, defaulted from the fund. Shared costs — rent, the
office manager's wages — are posted unassigned and divided by a saved
**allocation rule** (percent, square feet, or hours on grants), either with
**Split** on the entry line or as a month-end **Functional Allocation** that
reclasses whatever is still unassigned on the rule's source account without
moving the P&L by a cent; running a month twice finds nothing to move. The
**Statement of Functional Expenses** puts every expense account in Form 990
Part IX columns, with the program-by-program breakout, as PDF and CSV.

**Donor documents.** A donation receipt prints the IRS Publication 1771
acknowledgment — the date, the amount, and either "no goods or services were
provided" or the fair value of the gala dinner with the deductible portion.
Every gift gets an **acknowledgment letter** (PDF and email) worded by the
editable `donation_acknowledgment` template with `{{ irs.text }}` supplied.
**In-kind gifts** are their own two-sided document (the piano to Musical
Instruments, the credit to In-Kind Contributions) acknowledged without a
stated value. **Year-end giving statements** list every cash gift with the
deductible portion and non-cash gifts without amounts — one donor, every
donor in one PDF with a page break each, or emailed to everyone who has not
opted out. The **pledge report** reads promised, invoiced, received, written
off and outstanding off recurring pledges and their installments (generated
invoices now remember their template and carry its grant), and a pledge that
will never be paid is **written off** through a credit memo to Bad Debt
Expense — credit memos gained the void they never had, which is also the
undo.

**Reports you can find and compare.** In the desktop app, Save PDF now
writes the report to Documents → SlowBooks Pro → Reports (period-stamped, never
overwritten), opens it, and says where it went with a Show-in-folder button —
it used to land in a temp folder. Saved report definitions are a collapsible
list at the top of the Report Center instead of a growing wall of cards. The
Statement of Activities and the Statement of Functional Expenses gained
"Compare to prior year": the same dates a year earlier as two more columns,
on screen, in the PDF and in the CSV.

**Riverbend Community Arts.** The stage's acceptance test is a seeded
nonprofit year — a grant, an endowment, a gala, pledgers, a piano, rent
split 70/20/10, a June release — driven entirely through the API with scoped
tokens the way a bring-your-own-AI agent would, checking that every
statement reconciles to the cent and that a readonly agent cannot write.
Design notes: [docs/design/nonprofit.md](docs/design/nonprofit.md); user
guide: [docs/nonprofit-module.md](docs/nonprofit-module.md).

**A custom AI provider** (contributed by @jarvis4openclaw): an eighth AI
Insights provider that points at any OpenAI-compatible chat endpoint on the
public internet, HTTPS-only and behind the same address guard as the Worker
gateway, with the model ID yours to type. Along the way it fixed the
self-hosted Cloudflare Worker gateway, whose replies had been parsed to an
empty string.

**From the release gate (SlowBooks-Pro-Testing, 2.9.0).** The macOS app is
now notarized and stapled *before* the disk image is built, so the copy a
user drags to Applications carries its own ticket and launches offline;
the bundle declares why it writes to Documents and Downloads, and a refused
folder is explained (the file goes to the app's data folder and the notice
says so) instead of failing like a crash. For agents driving the API: an
unknown request field is a 422 naming the field, never silently dropped;
`tax_rate` is documented as a fraction and a percent-looking value is
rejected with the unit in the message; `pto_type` and `accrual_method` are
enums in the spec; an empty pay run is refused with the roster named;
`DELETE` on a posted document names the `/void` route; a fresh company has
6810 Depreciation Expense and a default Equipment asset type so depreciation
runs first time; a missing `companies.json` is logged with the data
directory that was searched.

**Round 3 of the gate found the macOS desktop bridge dead — since v2.1.0.**
Save PDF, print preview, Save backup, Show in folder and the company picker
all rely on pywebview's `window.pywebview.api`, which pywebview builds with
`new Function`; the app's Content-Security-Policy had no `'unsafe-eval'`,
WebKit enforces that inside the page, and the bridge stayed empty on every
Mac while Chromium on Windows let it through. The policy now allows eval
only under the desktop launcher (a browser install keeps the strict one).
The shell also stops failing in silence: a missing bridge is reported on the
first click and checked at startup, Save CSV goes through the bridge to the
same Reports folder as Save PDF, and every export a desktop fetch receives
is served inline so neither webview swallows it as a download. Two more
from the same round: first-run setup on a file that already holds books now
says whose books they are and prefills the name, and the company name in
Settings keeps the manifest (the picker's name) in step so the two can no
longer diverge; the Windows installer clears `_internal` before an upgrade
so stale package metadata from earlier builds no longer ships.
Round 4 closed the loop on the name reconciliation itself: two company files
can never end up with one name — renaming a company (in Settings or in
first-run setup) to a name another file already carries is refused with the
file named, the same rule creating a company has always applied.
The Linux gate then found that `docker compose up` had been broken since
v2.8.0: no migration ever created the `users` table (the app made it at
startup), and the v2.8.0 preferences migration referenced it, which SQLite
tolerates and PostgreSQL refuses. A migration now creates `users` ahead of
that reference, a test walks the migrated schema for any foreign key whose
target no migration creates, and under PostgreSQL the company list flags
the database the server is connected to as current so an agent can tell
which books it reached.
Behind that lay an older one: the production guards that demand a TLS
database connection and an HTTPS redirect refused the compose stack's own
plaintext bridge-network URL, so the documented one-command install had not
started since those guards landed in v2.1. The compose file now declares
`SLOWBOOKS_PRIVATE_NETWORK=1`, which relaxes exactly those two transport
checks with a logged warning and nothing else; the encryption-key guards are
never relaxed, and the install guide says what to change before exposing
the stack beyond the host.
And a third, once the stack ran: with two uvicorn workers, both raced to
create the tables the migrations do not cover, one lost on a Postgres enum
type, and the container crashed and restarted on every first boot. Table
creation now takes a Postgres advisory lock so the second worker waits.

### v2.8.0 — Benefits, all-state payroll, and an overview you can arrange

### Export parity with import (#70)

**What comes in from QuickBooks can go back out.** IIF export now writes
everything the importer reads: the `!CLASS` list (names verbatim, archived
as HIDDEN=Y), a `CLASS` column on every transaction block so a tag never
falls off on the way out, jobs as `Customer:Job` rows, and three block
types that were import-only since they were added — **bills**, **deposits**
and **sales receipts** (`CASH SALE`). A full export re-imports into the same
books with no errors and no duplicates. CSV export gained bills, deposits,
sales receipts, classes and jobs, with class, job and cost code on every
line. The IIF and Import/Export pages carry buttons for all of it.

### Accessibility

**Striving toward WCAG 2.1 AA.** The audit's six app findings are fixed:
every table header declares its scope, icon-only remove and close buttons
carry labels, toast notifications announce through a live region, modals
are real dialogs (focus moves in, Tab stays inside, Escape closes, focus
returns to what opened them), the reconciliation difference says
"Balanced" or "Out of balance" in words rather than colour alone, and the
muted text colour now clears the AA contrast ratio in both themes. The
bigger gap was PDFs: every PDF the app produces — invoices, statements,
pay stubs, W-2s, 1099s, 940/941, reports — is now **tagged (PDF/UA-1)** with
a declared language and title, so a screen reader gets headings, tables
and reading order instead of a picture of text. See
[docs/accessibility.md](docs/accessibility.md) for the statement and the
contact path; this is a commitment, not a compliance claim.

### Sales tax per line

**A labor line and a taxed part can share one invoice.** The invoice's tax
rate used to apply to the whole subtotal, even though items already carried
a taxable flag. Now every line on an invoice, estimate, sales receipt and
recurring invoice has a **Tax** checkbox: it starts from the item's flag
(and turns off for every line when the customer is marked non-taxable), you
can flip it per line, the totals only tax the checked lines, the flag rides
from an estimate into the invoice it becomes and from a recurring template
onto every invoice it generates, the Sales Tax report's taxable base counts
only taxed lines, and the PDF marks non-taxable lines when the document
carries tax. Field report from an IT shop that repairs customer-owned
devices (untaxed labor) and sells the part with install (taxed) on the
same invoice.

### Customizable overview

**The Company Snapshot is yours to arrange.** A **Customize** button on the
overview lets you hide any card, move cards up or down, and add cards from
a catalog; **Save layout** remembers it for your login (each user on a
Server Edition company gets their own; the single-password operator gets
one shared layout), and **Reset** brings back the standard overview. The
classic cards are all there — receivables, overdue invoices, active
customers, payables, bank balances, A/R aging, monthly revenue, recent
invoices and payments — and five new ones join the catalog:

- **P&L: This Month vs Last** — income, expenses and net side by side.
- **Cash Position** — cash on hand plus a 30-day forecast from receivables
  and payables coming due (assumes customers pay on the due date).
- **Open Purchase Orders** — committed but not yet billed, with the job.
- **Receipts to Review** — scanned receipts waiting to become a bill or
  expense, and how long before they expire.
- **Jobs: Budget vs Actual** — active jobs ranked by projected variance,
  each a click from its job page.

Every card loads independently, so one card with a problem shows its
error in place instead of taking the page down. The overdue-invoices card
now names who owes what and by how many days.

### Benefits engine

**A benefit is a code with a rule.** Payroll evaluates whatever codes are
attached to an employee: kind (deduction, benefit, both), calculation
method, which wage bases the pre-tax side reduces, an explicit sequence
(pre-tax codes apply in order and each changes the taxable base for the
next), and three separate limits — per period, annual, and a wage-base
ceiling. Rates are effective-dated and resolve against the pay-period end
date; processed runs snapshot the rules they used so a later change never
rewrites history. The employer side has its own rate and method including
tiered 401(k) matching, an expense and liability account per code, and a
remittance vendor — the Remittance tab totals what is owed and creates the
vendor bill. Employee groups are templates; an enrollment overrides them.
Loan-style codes carry a balance and stop at zero. PTO banks now carry
dollars and can post the accrued liability. The Deductions page became
Benefits; garnishments have their own page. Existing deduction types and
elections migrate onto codes and enrollments.

### Actual labor burden on jobs

Set the Labor cost type's burden method to **payroll** and the pay run
distributes real employer taxes plus job-routed benefit codes across the
jobs each employee's time entries hit, by hours, P&L-neutral. Time entries
then post base labor only. Replaces the flat percent from v2.7.0.

### State withholding for all 50 states and DC

Every state resolves to a payroll engine (Washington, California, New York
and Oregon keep their dedicated ones). The figures are the 2026 published
values with the source named per state in `docs/state-withholding.md`.
Employees gain the state W-4 inputs: allowances, extra state withholding,
an elected rate (Arizona), and a flat local rate for county and city taxes.
Verify against your state before filing; the table is re-checked every
January.

### Employee portal link

On the desktop the link now opens in the employee's browser instead of
inside SlowBooks, and Copy Link / Email to Employee give a full address.
The Details view says where that address is reachable from.

### Small things

- `GET /api/sales-receipts` lists receipts for API clients.
- The Company Snapshot is titled with your company name, which also sits
  in the toolbar and the window title.
- The splash shows what's new in the version you're running.

### Benefits engine — from the first macOS lap

- A post-tax deduction larger than the check used to leave a negative net
  pay on the stub and an unbalanced payroll entry on processing. Post-tax
  codes now take what is left after taxes and garnishments, in sequence,
  with the shortfall noted on the stub; net pay never goes below zero.
- The remittance report and bill follow a code's current vendor when the
  run was processed before the vendor was assigned.

### v2.7.0 — Jobs, job costing, and receipt intake

The two most-requested features since Server Edition, each field-tested on
Windows (SkyTech / VonHolten308) and macOS (Keith's laps on #73 and #86).
#### Jobs — QuickBooks-style Customer:Job / Projects (milestone 1)

**Every posted line can now carry a job (and a class).** A job is a
customer's project — "Smith: Kitchen Remodel" — and the unit of job
costing. Invoices, bills, expenses, card charges, journal entries, sales
receipts and estimates take a Job on the header; invoice, bill and journal
lines can set their own job and class, and a line without one inherits the
header's. The ledger line is the source of truth, so the new **Jobs** page
(Customers & Sales) and the **Job Profitability** report show income,
costs, net and margin per job straight from posted activity — the "No job"
row holds everything untagged, so the report's totals equal the Profit &
Loss for the same period, the same reconciliation promise P&L by Class
makes. Job detail lists every posted line attributed to it (the job cost
detail). The Customer Center lists a customer's jobs and can create one.

Jobs carry what a contractor tracks: status (pending, awarded, in progress,
closed, not awarded), job number, type, dates, site address and contract
amount, so the detail can show billed-vs-contract. A job with posted
activity is never deleted — mark it inactive and it leaves the pickers.

**The cost model: drill-down, every kind of cost, burden, budgets and
variance (milestone 3).** Feedback from the first lap was that jobs
existed but there was no way to drill down or to get the extra and
edge-case costs onto them. Now:

- **Cost codes nest** (division › code › sub-code, any depth) with roll-ups
  at every level, your own numbering, and a CSV import
  (`code,name,cost_type,parent_code`). **Cost types are yours to edit** —
  add permits, bonding, warranty, split labor — each with a burden % and
  the accounts it posts through.
- **Job Cost Entry**, a new document for costs that aren't a bill:
  internal labor at an employee's loaded rate, owned-equipment hours from
  an Equipment list, mileage, small tools, burden, corrections. It debits
  job cost (tagged to job, code and type) and credits an offset account
  (applied labor, applied equipment, applied overhead — all contra-expense
  accounts on the P&L) — the applied-cost pattern, so the company P&L is
  unchanged while every job carries its share. Settings → Cost Types →
  **Create default offset accounts** sets all of that up in one click,
  pointing each cost type at the chart's own COGS account (Materials,
  Labor, Subcontractor) so the P&L keeps its cost categories. **Allocate a Cost** spreads one amount
  across jobs by labor hours, revenue, costs, equally, or by weights.
- **Time entries post to jobs.** Employees get a job cost rate and a burden
  %; approved time tagged to a job posts as labor cost at that rate
  (overtime at 1.5×, double-time at 2×) with the burden as its own line,
  one click from the time list or the job's Time tab.
- **Budgets and variance.** Each job carries a budget per cost code (or
  per type, or whole-job), seeded from an estimate — estimate lines gained
  a cost code and a unit cost, so cost = qty × unit cost and revenue = the
  line amount — or typed in. The job page shows, at every level, the
  columns contractors read weekly: Original, Changes, Budget, Committed,
  Actual, Projected (actual + committed), Variance (budget − projected),
  % Used, and estimated vs actual revenue.
- **The job page** replaces the modal: Overview (headline figures and a
  by-type table), Cost Detail (the expandable tree — type › division ›
  code › sub-code › posted lines, each line opening its bill, invoice,
  expense, journal entry or job cost), Budget, Transactions and Time tabs,
  with a period filter and a job-to-date default. A **Job Budget vs
  Actual** report lists every job's headline figures.
- Also fixed on the way: the Time Entry form was sending hours under the
  wrong field names, so every entry saved with zero hours.
- From the first macOS lap: the labor offset was a balance-sheet account,
  so labor landed on the P&L twice once payroll ran — it is a P&L contra
  now, like the other offsets. Rejecting a time entry that was already
  posted to a job voids that job cost. Re-seeding a budget from an
  estimate leaves hand-edited rows alone, and an estimate line with no
  unit cost budgets zero cost (unknown) rather than the sale price.

**Cost codes, billable costs and committed cost (milestone 2).** Settings
gained a **Cost Codes** chart — which part of a job a cost belongs to ("03
Concrete", "26 Electrical"), each with a cost type (labor, material,
subcontract, equipment, other) and an optional default account — with a
one-click load of the standard divisions. Bill lines, journal
lines, purchase-order lines and expenses take a cost code, and bill lines
and expenses can be marked **billable** to the job's customer (the
unbilled-costs-to-invoice step arrives with progress billing). The job
detail rolls costs up by code and type, and shows **committed cost**: the
value of sent, partially received and received purchase orders tagged to
the job that has not yet become a bill. Purchase orders take a Job on the
header (and per line); converting one to a bill carries job and cost code
onto every bill line.

**QuickBooks migration keeps the hierarchy.** IIF imports split
`Customer:Job` names into the customer and a job under it (customer list
rows and every invoice, sales receipt and estimate); QBO imports turn
sub-customers ("Projects") into jobs under their parent. A flat customer
that already carries the colon from an earlier import keeps matching, so
re-imports are stable.

The estimate form also gained the Class field that was computed but never
rendered. Design and the rest of the plan (cost codes, committed cost,
change orders, progress billing, time and burden, WIP):
[docs/design/projects.md](docs/design/projects.md).

#### Receipt intake — scan a receipt into the Sales Receipt / Bill form

A new **Scan Receipt** button on both the Enter Sales Receipt and Enter
Bill forms uploads a receipt image or PDF, runs it through local OCR
(Tesseract), and pre-fills the form: date, merchant/vendor hint, and the
grand total as a single line (Qty 1 × Rate = total), with detected tax
split out on the Sales Receipt form (tax rate field) and noted in Bill
Notes (bills have no tax field). The operator always reviews before
saving, and the source image/PDF attaches to the saved document so every
scanned entry keeps its evidence.

Per the design notes, this is **zero new Python dependencies** —
tesseract and poppler-utils are called directly via subprocess and stay
the user's install (never bundled into the signed installers); the
Docker image installs both system packages. When the binary is absent,
the feature degrades gracefully: the button is disabled and the Settings
page shows "install Tesseract to enable scanning." Parsing is
deterministic (regex/anchor extraction for date, total, tax, merchant) —
no AI, no bundled models. Design + API contract:
[docs/design/receipt-intake-spec.md](docs/design/receipt-intake-spec.md).

**Expenses — the form most receipts actually belong on.** A receipt for
something already paid (card, cash, check) is neither a bill (money
still owed) nor a sales receipt (money taken in); entering one used to
mean a bill plus a payment, or a journal entry. The new **Expenses**
page (Vendors & Payables) records it in one step — vendor, expense
account, the bank or credit-card account it was paid from, amount — as
a single balanced posting (DR expense, CR paid-from), with the Scan
Receipt button, box-to-fix canvas, and attachment on save, exactly like
bills. Paid From lists bank/cash assets and credit-card liabilities,
defaulting to Checking.

**Vendor quick-add on the Bill and Expense forms.** A scanned merchant
the books don't know yet no longer dead-ends the form: the vendor
picker gained "+ New Vendor" with an inline name box; a scan that
doesn't match an existing vendor pre-fills it, and the vendor is
created on save (a near-duplicate name resolves to the existing
record instead of a twin).

**Box-to-fix canvas hardening**, from the first hands-on hardware lap:
a box dragged over two figures (tax + tip) is refused with the numbers
it saw rather than silently taking the first; any refused read — no
value, two values, or a value the form won't accept (a "tax" larger
than the subtotal) — leaves no box on the scan and no stale value on
the field buttons, so the next drag starts clean. Field buttons moved
below the canvas, a drag paints immediately, and a box recolors the
moment it's labeled instead of after the read comes back.

**Highlights land on the right words.** The colored boxes the scan
draws over the image are placed by matching the parsed values back to
the recognized words; a receipt that prints the same figure twice (a
line-item price and the grand total, or "CASH" repeating the total)
used to get the box on the first hit, and the Date box went to the
first thing with a slash or dash in it (an invoice number). The boxes
now go to the word on the labeled line — lowest one for totals — and
to the word that actually prints the parsed date in whatever order the
receipt used. The values on the form were already right; only the
highlight moved.

**Merchant template memory** no longer anchors a remembered box on a
word that repeats on the page when a unique label is on the same line
("Inclusive" over "GST"), records which occurrence it meant when every
label repeats, and fails closed — canvas takes over — when a rescan
doesn't repeat the anchor the same way. A remembered amount or date box
that reads only a bare digit run on a new print (a template that landed
on a tax-ID number) is discarded instead of filling the form with it.

**Windows: the second scan of a session no longer crashes the app.** On
Windows the built-in text recognizer was driven from a throwaway thread
per scan; when that thread exited, Windows tore down the component
runtime the recognizer had been created in, and the next scan jumped
through a stale handle — the server process died and the window
reported "SlowBooks isn't responding (network error)" right after the
first bill or expense was saved from a receipt. All recognizer work now
runs on one thread that lives as long as the app does.

**The merchant name gets a highlight box too.** The scan now boxes the
run of words on the image that spells the parsed merchant name (pink),
alongside the totals and date, so it can be corrected by tapping like
the others; a name recalled from a remembered layout that isn't printed
on the page draws nothing.

**Bill numbers come off the receipt, and never block the entry.** The
Bill # is the vendor's own invoice number — that is what stops the same
invoice being entered twice (the check is per vendor, so two vendors
can both send invoice 111). The scan now reads it from the receipt
("Invoice No", "Receipt #", "Check", "Trans No" …) into Bill # and the
expense Reference; a receipt that prints none can be saved with the
field blank and gets `<date>-<vendor initials>` (suffixed if that vendor
already has one that day).

**Expenses can be voided.** A recorded expense booked to the wrong
account (checking instead of the credit card) now has a Void button on
the list and in its detail; like bills and journal entries it posts the
mirror-image reversing entry, keeps the original in the ledger, respects
the closing date, and shows the row as void. Enter it again to correct.

**Scan Receipt field buttons are readable in dark mode.** The Total /
Tax / Subtotal / Date / Merchant buttons under the receipt used pale
fills with dark text regardless of theme — near-invisible on the dark
theme. Dark mode now uses deep opaque fills with light text (8–10:1
contrast); light mode keeps the pale fills with darker text.

**Invoice / Ref # has its own box on the scan.** The picker under the
receipt gained an **Invoice / Ref #** button (teal): draw it around the
vendor's document number and it lands in Bill Number (bills) or
Reference (expenses); the auto-parse also outlines the number it found,
and a taught box is remembered per merchant like the others. The
auto-parse no longer skips an invoice line because a "Pax"/"Table"/"Tel"
word sits after the number on the same line.

**Day-first dates parse.** `14-02-2018`, `14/02/2018`, `14.02.18` — any
numeric date whose US reading is impossible — now resolves day-first
(US ordering still wins when both readings are valid, so `12/02/17` is
December 2), and month-name dates accept a two-digit year or dashes
(`28 Mar 18`, `05-JAN-2017`). A date box that reads something the parser cannot turn into
a date no longer reports "applied to the form" while the date input
stays empty; it says what it read and asks for a redraw or a manual
entry.

**US register tape parses (Walmart, Whole Foods, Costco phone photos).**
`TAX 1 7.000 %` is a tax *rate* line, not a 7.00 tax — a third decimal
or a trailing % never reads as money. A return-policy or promotion line
("purchases made on or after 9/15/2020") can no longer supply the
transaction date. Walmart's `TC#` is the receipt number; the `REF #`
printed beside APPR CODE / NETWORK ID / TERMINAL # is the card
authorization and is ignored. The Invoice / Ref # button is present on
the picker under the receipt (build 47 drew the box but had no button
to assign it to).

**Tilted photos read the right rows.** On a phone photo taken a couple of
degrees off square, the amounts down the right edge of a 2000-pixel
receipt sit a full line below the labels they belong to, and the row
builder for the native engines put them on the neighbouring row — total
survived but subtotal and tax silently took the wrong values (found on
the macOS hardware pass). The engines now report each line's tilt (Apple
Vision from its corner points, Windows OCR from its line grouping) and
the rows are straightened before they are read; verified to 8 degrees.
Also from that pass: the Settings OCR row names the built-in engine
instead of assuming Tesseract everywhere, Apple Vision lists its
recognition languages, and the macOS build's smoke test now proves Vision
survived freezing.

**Desktop launcher:** `--data-dir` (Server Edition scheduled task,
headless test rigs) now relocates the per-user `.env` along with the
data directory; it used to write `DATABASE_URL` into the launching
user's own `%LOCALAPPDATA%` `.env`.

### v2.6.3 — Classes cross over, and report CSVs read ANSI

**Classes now come across from QuickBooks.** A class list export
(File > Utilities > Export > Lists > Class List) previously vanished on
import: the IIF parser only recognized accounts, customers, vendors and
items, so the `!CLASS` section fell through the skip-unknown-sections
path. A transaction IIF never carries the definitions either — CLASS
appears only as a column on split lines — which left no way into the
class list but typing every name into Settings → Classes by hand, and
every transaction citing one failed its document. The list imports now,
ahead of anything that can cite a class, so a single file holding both
the list and the transactions lands in one pass. QuickBooks'
"Parent:Child" subclass paths are kept verbatim (the split lines use
that same path, so this is exactly what makes the two match), inactive
classes arrive archived, and re-importing a list is a no-op. An unknown
class on a transaction still stops that document rather than being
invented — the error now names the list export as the fix. (#69)

**Report-CSV import follow-ups**, from the #62 post-merge review: block
types the Check and Deposit Detail parsers don't handle are now counted
and warned about ("3 'Bill Pmt -Check' block(s) skipped") instead of
being silently dropped, so a full Check Detail export no longer looks
like it imported cleanly when it didn't. CSV uploads also fall back to
Windows-1252 when UTF-8 fails — QuickBooks Desktop's Save-as-CSV
frequently writes ANSI, and a payee like "José" used to 500 the upload.
All four CSV import endpoints got the fix; a file neither encoding can
read returns a guided error. (#67)

### v2.6.2 — Report-CSV imports & field fixes

**Field fixes** (both from a Server Edition user's report, #64/#65):

- Creating a customer or vendor with a blank Email box failed with an
  unexplained "unprocessable entity" — blank email now means "no
  email", and validation errors name the field they're about.
  (Workaround before this release was entering any valid email.)
- The Server Edition install script now copies existing desktop books
  (company files, encryption key, uploads, backups) into the server's
  data home, as the docs always claimed; a wrong run location stops
  with a guided message; and — found reproducing the report on real
  hardware — the startup task could never be registered from the
  normal installed path at all (PowerShell 5.1 mangled the quoting on
  the spaced "Program Files" path and the script printed success over
  the failure). Task creation is fixed and failures now stop the
  script loudly. Field-verified end-to-end on hardware.

### Deposits and checks from QuickBooks report CSVs

The report-CSV path now covers three exports, auto-detected by their
columns on one upload: **Deposit Detail** (each deposit becomes the
journal entry moving its payments from Undeposited Funds to the bank),
**Check Detail** (bank credit + expense debits — including payroll
checks, whose withholding lines credit their liability accounts and
net to the check amount; sign-aware parsing again, proven against a
real customer's export), and the Transaction Detail sales-receipt
report below. Blocks that don't balance or reference missing accounts
error individually with a pointer to import the chart of accounts
first; re-uploads dedup.

### Sales receipts from a QuickBooks report CSV

QuickBooks Desktop can't export transactions to IIF, so the sales-
receipt migration doc pointed Desktop users at a Transaction Detail
report export — which previously had nowhere to land. It does now:
**QuickBooks Interop → Sales Receipts from Report CSV** imports a
"Transaction Detail by Date" export (filtered to Sales Receipt), each
receipt becoming a paid sale + payment with balanced journals.

Shaped by a real customer's export, so the parser handles what real
files contain: applied-deposit contra lines that reduce the total
(sign-aware — an absolute-value parse would inflate them), percentage
tax rows carrying the tax agency's name, deposits-only receipts,
thousands separators, and per-receipt balance checks with clear
errors. Unmatched account names post to default income with a warning;
re-uploads dedup by customer + date + total. Receipt numbers keep the
report's Num where free (SR-prefixed on collision).

### v2.6.1 — The receipt now looks like a receipt

- The printed/saved sales receipt was the unmodified invoice template —
  titled INVOICE, with Due Date, Terms, and Balance Due rows, saved as
  `Invoice_<n>.pdf`. It now renders as a SALES RECEIPT: date only, Sold
  To, Total + Paid (no balance line — nothing is due), filename
  `SalesReceipt_<n>.pdf`, and the same for the email attachment name.
  Found on macOS hardware by the build maintainer during the v2.6.0
  release pass. (#60)
- The PDF's Bill To / Sold To block always prints the customer's name
  now: the template fell back on a `customer_name` attribute only some
  callers stamped onto the invoice, so the direct PDF route printed a
  bare header whenever the customer had no address on file.
- `SHA256SUMS.macos` now also lists the stable-named
  `SlowBooksPro-macos-arm64.dmg`, so the README's direct download can be
  checksum-verified, not just the versioned asset.

### v2.6.0 — Sales receipts: one-screen POS sales + QuickBooks import

For businesses that ring up sales at a counter instead of invoicing:
a QuickBooks sales receipt is an invoice paid at the moment of sale,
and SlowBooks now models it exactly that way — an Invoice flagged
`is_sales_receipt` plus a Payment for the full total, so every
existing report, PDF, export, and void path works unchanged. Schema
migration `c7d8e9f0a1b2` adds the flag (drop-in; new databases need
nothing).

- **Enter Sales Receipts screen** — new sidebar page with a
  one-screen form: customer (with quick-add), payment method,
  check #/reference, deposit-to account (defaults to Undeposited
  Funds), tax, class, currency, and line items. `POST
  /api/sales-receipts` composes the existing invoice and payment
  routes, so numbering, closing-date enforcement, FX, and
  inventory/COGS behave identically to documents entered separately;
  if the payment half fails the invoice half is voided rather than
  left as a stray open balance. Receipts list on their own page and
  no longer clutter the Invoices list (`GET
  /api/invoices?is_sales_receipt=...` filters either way; omitting
  the param returns everything, as before).
- **IIF import: `CASH SALE` blocks** — QuickBooks Desktop's sales
  receipts previously fell into the silently-skipped bucket. They now
  import as paid invoice + payment with balanced journals (deposit
  account from the TRNS header, Undeposited Funds fallback).
  Counter sales with a blank Customer:Job land on an auto-created
  "Walk-In Customer" (reported as a warning); unnumbered receipts get
  the next invoice number, and re-imports dedup by document number or
  customer + date + total.
- **QBO import: SalesReceipt entity** — the QuickBooks Online
  importer pulls sales receipts alongside invoices and payments, with
  the same id-mapping dedup; the QBO page gets a Sales Receipts
  import checkbox (import-only — there is no matching export entity).
- **docs/migrate-from-quickbooks.md** — new guide covering both
  paths, including the fact that Desktop's built-in IIF export is
  lists-only and the clean Transaction Detail report recipe for
  getting sales history out.

### v2.5.3 — API hardening, from a full-surface sweep

Every one of the API's 357 operations was driven end-to-end on Windows
(installer) and Linux (source, PostgreSQL 17); everything that surfaced is
fixed here. No schema migrations; drop-in upgrade from 2.5.x.

**Data-integrity fixes**

- An invalid `pay_type` or `role` on an employee was written as-is and then
  made the row permanently unreadable — one bad `PUT` returned 500 for the
  record *and* for `GET /api/employees` company-wide, with no API-level
  recovery. Both fields are now validated as enums (422 at the edge), the
  same treatment `pay_frequency` and `filing_status` already had. For rows
  corrupted before the fix, `scripts/repair_employee_enums.py` repairs in
  raw SQL (dry-run by default; `--apply` to write).
- The migration dry-run tolerated a one-cent journal imbalance the importer
  then refused, so `ok=true` could precede a mid-import 500. The gate now
  applies the importer's exact-balance rule, including to synthesized
  opening-balance journals.
- Customer and vendor names: blank/whitespace-only names rejected, lengths
  capped to the column width (was: opaque 500 on PostgreSQL, silent
  overflow on SQLite), obviously malformed emails rejected. The CSV and
  IIF importers honor the same rules — an IIF transaction with a blank
  NAME no longer auto-creates an unnamed customer.

**Correctness / API behavior**

- Emailing an invoice (and Settings → "send test email") called
  `send_email()` with a stale signature and could never succeed; both now
  work, report 502 with a pointer to the email log when SMTP fails, and no
  longer double-log.
- Account endpoints return 409 with a real message instead of leaking
  database errors as 500s (delete-with-history, duplicate account number);
  an account can no longer be made its own parent.
- `/openapi.json` no longer requires auth, so the documented agent flow
  ("discover from the spec") works; the spec now declares its bearer
  security scheme, with genuinely public routes exempted.
- API tokens can no longer clear or roll back the closing date, nor set
  the closing-date override password; moving the date forward (tightening)
  is still allowed, and signed-in users are unaffected.
- Machine-originated audit rows (e.g. the token `last_used_at` stamp) are
  attributed to an explicit `system` principal instead of NULL.
- `SlowBooksPro.exe --help` with piped/redirected output hung the frozen
  Windows build forever on an invisible error dialog (UnicodeEncodeError
  under cp1252 inside argparse). Launcher stdio is now total; verified on
  Windows before/after.

**Docs**

- INSTALL.md's native Linux path works as written on PEP 668 distros
  (venv steps, `.env` honored by alembic, APP_DEBUG guidance);
  `.env.example` no longer recommends a FORCE_HTTPS setting the app
  refuses to boot with; README operation count corrected.

**Report display fix**

- Balance-sheet and P&L lines rendered in the app wrapped every amount in
  an absolute value, so a contra-balance account displayed positive while
  the totals summed real signed values — visibly "$100 + $400 + $600 =
  $300" after an unapplied customer payment drove A/R negative (a
  legitimate prepayment). Lines now render signed, matching the totals
  and the PDF output, which were always correct. (#56)

**macOS releases now sign themselves in CI**

- Tag builds sign, notarize, and staple the macOS DMG on the runner using
  the same release tooling the maintainer ran locally, and attach it to
  the release alongside the Windows assets. Starting with this release the
  macOS publisher identity is **Trenton Von Holten** (previously releases
  were signed by the macOS maintainer's own Developer ID).

20 new regression tests (1162 total).

### Native macOS desktop

- Added a signed and Apple-notarized native app for Apple Silicon Macs running
  macOS 14 or newer, distributed as a drag-to-Applications DMG.
- Desktop companies, settings keys, uploads, logs, and backups persist under
  the user's Application Support directory; upgrades never place writable data
  inside the app bundle.
- Bundled PDF libraries and the Cocoa window backend are exercised before a
  build can become a release candidate.

### v2.3.0 — Migration onramps

**Migrate Data** — one page that brings accounting history in from six
systems, each behind the same dry-run-gated engine (nothing is written
until every reconstructed journal balances and, when supplied, the
trial balance reconciles):

- **MYOB** — fully validated against MYOB's own Clearwater sample
  company (101 accounts, 328 journals, ledger balanced to the cent,
  every account reconciling exactly to MYOB's trial balance). Handles
  tab-separated classic exports, dd/mm/yyyy dates, header accounts,
  reused journal IDs, number-only GL rows, duplicate account names,
  and per-type journal file bundles. docs/migrate-from-myob.md walks
  the export flow.
- **GnuCash** — fully validated against real 5.5 exports (multi-split
  transactions, GUID grouping, signed per-split amounts, placeholder
  accounts).
- **Xero** — refactored onto the shared engine (behavior-identical).
- **Zoho Books** — chart validated against a live account's export.
- **Wave** — trial-balance report shape validated against a live
  account's export; supports both debit/credit and signed
  single-amount transaction exports.
- **Sage 50** — account-ID resolution, mm/dd dates, Sage's descriptive
  account types.
- Opening balances: trial-balance residuals that net to zero (the
  source system's account-setup balances, absent from any journal
  export) are detected and imported as one balanced opening journal.

**Dependencies** — refreshed across the board for the release
(FastAPI 0.141, Stripe SDK 15, cryptography 50, argon2-cffi 25,
python-multipart 0.0.32, psycopg2-binary 2.9.12); pip-audit clean.
The route-table introspection tests were taught FastAPI 0.141's new
nested-router shape, with a tripwire so a future shape change can
never silently empty the auth-contract suite again.

### v2.2.0 — The fork-integration release

The largest single release since 2.0: work mined from four community
forks (with per-commit attribution), two new payment processors, and
five major accounting features.

**Online payments — Stripe, PayPal, Square**
- Payment-provider abstraction with one shared, idempotent recorder
  (row-locked; a webhook and a status poll can never double-record).
- PayPal (Checkout Orders v2) and Square (Payment Links) join Stripe;
  enable any combination and the pay page shows a button per processor.
- Desktop installs record payments without webhooks: verified capture
  on the customer's return + a "Check Payment Status" button.
- Fixed: the public Pay button 401'd (checkout route was never
  session-exempt) and the success banner trusted a URL query param —
  it now renders only after provider-verified capture.
- Fixed (found in live sandbox testing): Square leaves paid
  payment-link orders in state OPEN; polling now recognizes them.

**Class tracking**
- QuickBooks-style class dimension on invoices, bills, estimates,
  credit memos, recurring, journals, deposits, and cc charges;
  managed in Settings; immutable "Uncategorized" system default.
- P&L by Class report whose totals reconcile exactly with the plain
  P&L; IIF SPL.CLASS resolves on import.

**Multi-currency**
- Foreign-currency invoices and bills booked at per-document rates
  (Bank of Canada feed prefill, always overridable); the ledger stays
  single-currency so every report and invariant holds exactly.
- Realized FX gain/loss posts automatically on BOTH sides: customer
  payments (A/R) and bill payments (A/P).
- Cross-currency allocations rejected with clear errors; online
  checkout guarded to home-currency invoices.

**Fixed assets**
- Register with per-type account mappings, straight-line and
  declining-balance depreciation runs (one journal per asset, salvage
  floor, idempotent re-runs), disposal with gain/loss, CSV import,
  and a reconciliation report.

**Migration onramps**
- Xero CSV import (chart + general ledger + trial balance) gated by a
  dry-run that verifies every journal balances and cross-checks the
  trial balance before anything is written.
- Opening Balances wizard: guided setup, normal-side posting rules,
  optional auto-balance to equity.

**Banking & interop**
- Bank CSV import for Chase checking/credit and PayPal exports,
  auto-detected by header signature, with content-derived dedup that
  survives re-imports without dropping legitimate same-day duplicates.
- IIF BILL and DEPOSIT transaction blocks (previously silently
  discarded), with strict vendor/account matching and
  duplicates-skipped reporting.

**Reports**
- Printable P&L and Balance Sheet PDFs plus the one-click Financial
  Statements Pack (P&L + Balance Sheet + Trial Balance, page-numbered),
  US Letter or A4 via a new setting.

**Hardening**
- Auth-contract regression suite: every API route proven to 401
  unauthenticated or match a justified-public pattern (295 combos).
- Upload size caps on all import endpoints; CREATE DATABASE identifier
  quoting; decompilation debug strings scrubbed from the DOM with a
  regression scan; version-stable ruff lint gate.

**Community**
Work in this release originates from the forks of Alex Jordan
(@LayoverLogic), Joel Macklow (@joelmacklow), @moshgrossman, and
@amazon1148 — authorship preserved per commit. Thank you.

### v2.1.1 — Windows field fixes (first-machine feedback)

- **Session cookie now reaches every native window and download.**
  pywebview's default `private_mode=True` partitions WebView2 cookie
  storage, so the print-preview/PDF window opened as
  `{"detail":"Not authenticated"}` and CSV/backup downloads failed with
  "Needs authorization" (saving the 401 JSON body as `.json`). The
  desktop shell now uses a persistent shared profile under
  `%LOCALAPPDATA%\SlowBooksPro\data\webview` — PDFs, exports, and
  attachment downloads work, and logins survive app restarts.
- **The installer now installs the WebView2 runtime when missing**
  (silent Evergreen bootstrapper, skipped if already present) instead
  of showing a "runtime is not installed" error on first launch —
  Windows 10 machines without Edge updates hit this.

### Native Windows desktop install (no Docker, no WSL2)

Replaces the WSL2/Docker-Engine Windows setup from PR #1 with a fully
native install: the app runs as a normal Windows process against SQLite,
in its own desktop window (pywebview → WebView2). Desktop-mode groundwork
contributed in PR #14; delivery is a signed Windows installer (see below).

- **Multi-company, QuickBooks-style:** each company is its own SQLite file
  under `%LOCALAPPDATA%\SlowBooksPro\data\companies\`, tracked in a
  `companies.json` manifest. A company picker appears at every launch;
  switching companies = close and reopen. Creating a company runs the real
  `alembic upgrade head` plus the Chart of Accounts seed against a fresh
  file. Company identity/settings already live per-database, so each file
  is fully self-contained.
- **Backups on SQLite:** `backup_service` gains a SQLite branch — backup/
  restore are consistent `.db` snapshots via sqlite3's online backup API.
  Postgres installs keep pg_dump/pg_restore unchanged.
- **Migrations now genuinely run on SQLite:** four ALTER-added FK
  constraints converted to Alembic batch mode and literal `now()` server
  defaults replaced with the dialect-portable `CURRENT_TIMESTAMP`
  (identical semantics on PostgreSQL; these migrations have already run on
  existing Postgres installs and never re-run).
- **Launcher:** `desktop_launcher.py` — env prep with a generated
  `PAYROLL_ENCRYPTION_SECRET`, company picker, uvicorn on 127.0.0.1,
  native window; closing the window stops the server.
- **Delivery is a signed installer**, not setup scripts: the
  `.bat`/`.ps1` bootstrap flow contributed in PR #14 was replaced by
  `SlowBooksPro-Setup-x64.exe` — a PyInstaller bundle (Python and the
  PDF-rendering libraries included, nothing installed system-wide) built
  by CI and code-signed via Azure Trusted Signing, so Windows shows a
  verified publisher instead of a SmartScreen warning.
- The Docker/Postgres multi-company path (separate databases per company)
  is unchanged and still used when `DATABASE_URL` is Postgres.

### Post-merge review fixes (PR #12 follow-up)

A deep review pass after merging the payroll/HR contribution surfaced and
fixed twelve issues plus a round of structural cleanups (commits `af65843`,
`68eb844`).

**Schema / migrations:**

- Six `Employee` columns (`portal_token_last_used`, `portal_token_expires_at`,
  `everify_status`, `everify_submitted_at`, `everify_closed_at`,
  `everify_notes`) and four whole tables (`document_audits`, `login_attempts`,
  `reseller_permits`, `portal_accesses`) existed only in the models — no
  Alembic migration created them. Startup `create_all()` masked the missing
  tables but never ALTERs the existing `employees` table, so employee
  creation, portal access, and E-Verify updates crashed with
  `UndefinedColumn` on any alembic-migrated PostgreSQL.
  `migrations/versions/d0e1f2a3b4c5` adds the columns;
  `migrations/versions/bc3c3c5fd0a6` adds the tables (existence-guarded so it
  works on databases where `create_all` already made them). Verified with a
  full model-vs-schema diff against a scratch Postgres — zero gaps remain.

**Correctness:**

- `app/services/iif_export.py` — payment export filtered with
  `.filter(not Payment.is_voided)`, which Python evaluates to
  `.filter(False)` at query-build time (`WHERE false`), so payment IIF
  exports were always empty. Restored the column comparison.
- `app/routes/invoices.py` — late fees were being applied to DRAFT (unsent)
  invoices: drafts get a terms-derived `due_date` at creation, so they
  qualified as overdue. Filter scoped back to SENT/PARTIAL. Fee rounding
  switched from bare `.quantize()` (banker's rounding) to `_q`
  (ROUND_HALF_UP) to match the rest of the ledger.
- `app/routes/bills.py` — `void_bill` gained the payments-applied guard that
  `void_invoice` already had: voiding a paid bill reversed the full A/P while
  the bill payment's cash JE and allocations stayed on the books,
  double-counting the outflow.
- `app/services/nacha_export.py` — `_split_net_pay` silently dropped
  unallocated net pay when an employee had only PERCENT/FIXED accounts and no
  REMAINDER/FULL account, producing an ACH file that underpaid the employee
  with no error. Now raises `ValueError` (the route maps it to 400).
- `app/services/payroll_service.py` + `app/routes/payroll.py` — the
  $1M/37% supplemental-withholding tier could never fire:
  `supplemental_federal_tax()` implements it but the call site never passed
  `ytd_supplemental`. YTD bonus-run wages are now threaded through
  (`_ytd_supplemental` helper; flat and gross-up paths). Deduction/gross/net
  rounding in the pay-run route unified on `_q` — bare `.quantize(CENT)`
  rounded half-cents the opposite direction from the service.
- `app/static/js/employees.js` + `app/schemas/payroll.py` — the pay-frequency
  dropdown emitted `semimonthly` but the enum value is `semi_monthly`, so
  semi-monthly employees 500'd at flush. JS fixed; `pay_frequency` /
  `filing_status` now typed against the model enums so bad values 422 at the
  edge. Dropped the stale pre-2020 W-4 `allowances` field from the form.
- `app/routes/credit_memos.py` + `app/services/recurring_service.py` — the
  MAX+1 numbering race fix invoices got (retry on `IntegrityError` against
  the UNIQUE constraint) now also covers credit memos and the recurring
  batch. The recurring path retries under a SAVEPOINT so one collision can't
  abort the whole batch; a template that can't get a number is left for the
  next run.
- `app/services/accounting.py` — closing-date enforcement moved inside
  `create_journal_entry()` so every entry point inherits it. Recurring runs,
  IIF/QBO imports, and inventory hooks could previously post into closed
  periods that the UI forbids. Route-level checks kept for earlier, clearer
  errors; `bypass_closing_date` kwarg exists as an operator escape hatch but
  nothing sets it.
- `app/routes/portal.py` — the token-in-URL POST handlers (W-4 elections,
  direct-deposit accounts, PTO requests) resolved the employee without
  writing a `portal_accesses` audit row, while their cookie-based twins
  logged everything. All portal mutations are now audited.
- `app/routes/payroll.py` — the JSON tax-form endpoints (`/forms/w2|w3|940|941`)
  hand-rolled box math that diverged from the PDF path — W-2 box 3 returned
  raw gross with no Social Security wage-base cap. They now delegate to the
  same `compute_w2/w3/940/941` services the PDFs use; the 941 also now counts
  only PROCESSED stubs.

**Hardening / cleanup:**

- ABA check-digit validation (`validate_routing_number`, weights 3-7-1) for
  direct-deposit routing numbers — used by the portal and the employees API;
  previously both accepted any 9 digits, so a typo'd routing number wasn't
  caught until the bank bounced the ACH file.
- `_q`/`CENT` money rounding consolidated onto `app/services/accounting.py`
  (was copy-pasted across ~17 service modules; one divergent copy in
  `inventory_service.py` kept deliberately — it quantizes quantities/costs to
  4 dp).
- Portal token/cookie handler bodies deduped into shared `_save_profile` /
  `_add_bank` / `_request_pto` helpers; `_client_ip` moved to
  `app/services/request_utils.py` (was duplicated in `auth.py` and
  `portal.py`).
- N+1 query fixes: time-entries list + pay-period summary, PTO requests
  list, pay-run deduction loading, and W-2/W-3 generation (one stub fetch
  for the whole year, bucketed in memory, instead of 2+ queries per
  employee).

**Test coverage:**

- `tests/test_closing_date_enforcement.py` — four new service-layer tests
  including the recurring-service path.
- `tests/test_no_nplus1_in_list_endpoints.py` — extended to time-entries and
  PTO list endpoints.
- Routing-number fixtures updated to ABA-valid values (`021000021`).
- Full suite: 458 passed; black/ruff clean.

---

### AP void — `POST /api/bill-payments/{id}/void`

The customer-payment void (`POST /api/payments/{id}/void`) had no AP mirror.
Any voided customer receipt restored A/R cleanly; vendor bill payments could
not be undone at all. This gap is now closed.

**What was added:**

- `app/routes/bill_payments.py` — `void_bill_payment()` endpoint. Acquires a
  `with_for_update()` row-lock on the payment before checking `is_voided`,
  so two concurrent void requests cannot both pass the guard and post
  duplicate reversing JEs. Posts a reversing JE (swaps debit/credit on every
  original JE line). Walks each `BillPaymentAllocation` with a second
  `with_for_update()` lock and restores `amount_paid` / `balance_due` /
  `status` on each bill. Respects the closing-date guard — cannot post a
  reversing JE into a locked period.

- `app/models/bills.py` — `is_voided = Column(Boolean, …)` on `BillPayment`.

- `app/schemas/bills.py` — `is_voided: bool = False` on `BillPaymentResponse`.

- `migrations/versions/c9d0e1f2a3b4_add_is_voided_to_bill_payments.py` —
  Alembic migration adds the column with `server_default=false()`.

- `app/static/js/bills.js` — `BillsPage.voidBillPayment()` wires the new
  endpoint to the UI so the wiring audit passes.

**Test coverage:**

- `tests/test_void_reversal_symmetry.py::test_bill_payment_void_restores_bill_balance`
  — full void cycle: bill paid → void → balance restored, ledger balanced,
  double-void rejects 400.
- `tests/test_closing_date_enforcement.py::test_bill_payment_void_respects_closing_date`
  — reversing JE cannot land in a closed period.

---

### Whole-repo lint & format sweep

`black 24.8` and `ruff 0.6` run against `app/ tests/ scripts/` without an
allowlist — every file is now clean. CI replaced a 30-line per-file allowlist
with a two-line whole-tree gate.

Fixes applied to reach a clean tree:

- **E402** (imports not at top of file) in `app/routes/invoices.py`,
  `app/routes/stripe_payments.py`, `app/routes/reports.py`,
  `app/routes/saved_reports.py`, `app/services/iif_import.py`.
- **E741** (ambiguous `l` variable name) in `app/services/accounting.py`,
  `app/routes/journal.py`, `app/routes/reports.py`,
  `app/services/iif_import.py`, `app/services/tax_export.py`,
  `scripts/repair_rounding_drift.py`.
- **black reformatting** of ~40 files with over-long lines.

---

### Books-balance invariant tests (`test_books_balance_invariants.py`)

Seven cross-feature invariant tests that exercise the entire accounting layer
end-to-end through the API:

1. Every posted JE has `Σ debit == Σ credit`.
2. Full ledger `Σ debit == Σ credit` across all transactions.
3. Balance sheet balances: `A == L + E` (with synthetic Net Income line).
4. A/R aging total matches open invoice balances.
5. A/P aging total matches open bill balances.
6. Analytics A/R widget matches `/api/reports/ar-aging`.
7. P&L net income matches the balance-sheet synthetic equity line.

`_build_scenario()` creates a realistic dataset (3 invoices, 2 bills,
payments at various states) before each invariant check.

---

### Shell-injection AST audit (`test_subprocess_safety_audit.py`)

Four CI-gated static-analysis tests that verify the subprocess/shell call
surface is safe:

1. Zero `subprocess.*` calls in `app/` or `scripts/` use `shell=True`.
2. Zero `os.system` / `os.popen` / `commands.getoutput` in production code.
3. All three subprocess callsites use list-form args (not string
   interpolation).
4. Every bash script in `scripts/` double-quotes all `$VAR` expansions.

Uses `ast.NodeVisitor` for Python files; regex for shell scripts. Runs in
< 1 s. Result: zero vulnerabilities found in the codebase.

---

### Void-reversal symmetry tests (`test_void_reversal_symmetry.py`)

Six property-based invariant tests for void semantics:

1. Full payment void restores invoice balance and keeps ledger balanced.
2. Partial payment void restores only the voided portion.
3. Bill-payment void restores bill balance, keeps ledger balanced, double-void
   rejects 400.
4. Invoice void (no payments applied) → status VOID, balance\_due 0, ledger
   balanced.
5. Invoice void with payments applied rejects 400/409 (would double-reverse
   A/R).
6. Double-void of same payment rejects or is a no-op — never posts a second
   reversing JE.

---

### Closing-date exhaustive sweep (extended `test_closing_date_enforcement.py`)

Expanded from 3 to 13 tests. Added `test_bill_payment_void_respects_closing_date`
(the AP void guard), plus nine exhaustive sweep tests covering every
direct-create route that accepts a user-supplied date and posts a JE:
invoices, bills, payments, bill-payments, credit memos, journal entries,
CC charges, deposits, batch payments.

---

### IIF round-trip tests (`test_iif_round_trip.py`)

Three tests verifying the Intuit Interchange Format export/import pipeline:

1. Chart-of-accounts export → reimport preserves all accounts by number.
2. Customer names with metacharacters (`\t`, `\n`) are sanitized on export;
   the sanitized record can be reimported cleanly.
3. Invoice TRNS + SPL rows sum to zero (double-entry identity): the A/R debit
   plus income credits plus tax credit == 0.

---

### Production-readiness sweep (rounding / races / N+1 / closing-date / secrets)

A 19-commit program-wide audit of every JE-posting path and money
boundary in the codebase. All 452 tests pass; live walkthrough
exercised every flow listed below.

**Money math — rounding drift fixed at the source.**
The class of bugs: `qty * rate` was stored to `Numeric(12, 2)` columns
without being quantized first. SQL rounded each line on the way in,
so `sum(line.amount)` no longer equaled the stored `subtotal` after a
round-trip. Fix: every per-line money expression now goes through
`_q()` (ROUND_HALF_UP at 2 decimals) **before** being assigned. Applied
to invoices, bills, POs, estimates, credit memos, and the recurring
invoice generator. `compute_line_totals()` is the single canonical
helper. `scripts/repair_rounding_drift.py` detects and repairs
pre-fix rows (dry-run by default; `--apply` writes).

**Auto-number races — IntegrityError retry on every doc series.**
`SELECT MAX(num) + 1` followed by `INSERT` has no lock. Two concurrent
creates would both see the same MAX and collide on the UNIQUE
constraint. `create_invoice`, `create_po`, and `create_estimate` now
catch `IntegrityError`, roll back, and retry up to 10 times. Pinned
by `tests/test_invoice_number_race.py`.

**N+1 SELECT storm — eager-loaded every list endpoint.**
`for inv in invoices: inv.customer.name` was firing one SELECT per
row. Added `joinedload(.customer)` + `selectinload(.lines)` to
invoices, bills, POs, estimates, payments. Also clamped `skip`/`limit`
on every list route (1 ≤ limit ≤ 1000; skip ≥ 0). Pinned by
`tests/test_no_nplus1_in_list_endpoints.py`.

**Closing-date enforcement — plugged three bypass paths.**
A code audit found three routes that posted dated JEs without calling
`check_closing_date`:
- `POST /api/purchase-orders/{id}/convert-to-bill`
- `POST /api/estimates/{id}/convert`
- `POST /api/payroll/{id}/process`

Each one let an operator land a JE into a closed period by routing
through a "convert" or "process" verb instead of the direct create.
All three now call the guard. Pinned by
`tests/test_closing_date_enforcement.py`.

**Stripe webhook idempotency under contention.**
Stripe retries with backoff; two webhook deliveries can land
milliseconds apart. The check-then-insert against `Payment.reference`
let both pass the existence guard and create duplicate payments. Fix:
`with_for_update()` on the invoice row before the idempotency check,
so the second arrival serializes behind the first and sees the
already-recorded payment.

**Settings — secret redaction on GET.**
`GET /api/settings` was returning `stripe_secret_key`,
`smtp_password`, `closing_date_password`, and the QBO tokens in
plaintext. Fix: response runs through `_redact_secrets()`, which
replaces any non-empty secret with `"********"`. `PUT` treats the
placeholder as a no-op so a UI round-trip can't overwrite the real
value with `"********"`. Pinned by `tests/test_settings_redaction.py`.

**Input validation at the boundary.**
Schema-level rejection of impossible inputs: zero-line invoices /
bills / POs / estimates (422), negative quantity / rate / hours (422),
zero or negative payment amounts (400), payment allocations exceeding
invoice balance (400), duplicate `(vendor_id, bill_number)` pairs
(409). 17 tests in `tests/test_input_validation.py`.

**Payment void race.**
`void_payment` walked allocations and decremented `invoice.balance_due`
without locking. Concurrent voids could double-credit. Fix:
`with_for_update()` on both the payment and each invoice in the
allocation loop.

**Reconciliation drift.**
`sum(float(t.amount) ...)` over hundreds of cleared transactions
produced sub-cent float drift that made a truly-zero difference
display as `$0.00000001`. Replaced with `Decimal(str(...))`
arithmetic; convert to float only at the JSON boundary.

**Analytics AR aging consistency.**
The dashboard widget bucketed by **days-since-invoiced**; the
`/api/reports/ar-aging` endpoint bucketed by **days-past-due**. Same
data, different bucket → operator confusion. Analytics now matches
the report.

**Balance sheet — synthetic Net Income equity line.**
With no equity accounts holding transactions, the balance sheet
showed `Total Equity = 0` even though the books balanced. Now
computes net income from income/COGS/expense accounts and appends a
synthetic "Net Income (current period)" line to equity. A − L − E = 0.

**AR aging filter — include DRAFT.**
Aging was filtering `[SENT, PARTIAL]` only, hiding draft invoices with
open balances. Now `[DRAFT, SENT, PARTIAL]` at all 10 filter sites.

**Schema response types — Decimal not float.**
`BillResponse`, `BillLineResponse`, `POResponse`, `CreditMemoResponse`
were serializing money as `float`. Now `Decimal`. Round-trip stays
exact through the wire.

**Error handling — 4xx / 5xx mapping.**
`get_1099_pdf` was 500ing on a `ValueError` (not-found case); now
404. `restore_backup` always returned 500 regardless of cause; now
maps to 400 / 404 / 500. `low_stock_items` now surfaces oversold
inventory (`qty < 0`) regardless of reorder_point.

**IIF export — tab/newline sanitization.**
A vendor name with a `\t` in it would split that field into two on
import elsewhere. `_iif_clean()` now strips `\t\r\n` from every
field value before emission.

**Payroll input validation.**
`PayStubInput` schema rejects negative hours / overtime / deductions
/ gross_override at the boundary.

**IIF import — quantize SPL amounts.**
`_import_invoice` and `_import_estimate` now `_q(abs(...))` each SPL
amount before accumulating, matching the rounding semantics of
native invoice creation.

**Decompressed inventory restore audit, SSRF hardening, proxy
correctness** (batch 3 of the earlier enterprise eval) — see commits
`749b96e`, `f0c5816`, `87c0222`.

### Red-team pass on WC3D's Jinja2 XSS fix
WC3D's commit `ca6182f` enabled `autoescape=True` on the two Jinja2
Environments he found (`app/routes/public.py`,
`app/services/pdf_service.py`). A red-team sweep of every other
Jinja2 construction in `app/` turned up **one more spot** missing
the same fix:

- `app/services/email_service.py:139` — `SandboxedEnvironment()`
  (used to render admin-editable email templates with customer-
  supplied data injected as context). Fixed:
  `SandboxedEnvironment(autoescape=True)`.

- Same file, line 156–164 — when the file-based template fails, the
  fallback path was f-string-interpolating `invoice.customer.name`
  directly into an HTML body. Routed through `html.escape()` now.

Added `tests/test_jinja_autoescape_audit.py` — walks every
`Environment(...)` / `SandboxedEnvironment(...)` call in `app/`
(with a proper balanced-paren walker, since `Environment(loader=
FileSystemLoader(...))` defeats a naive `[^)]*` regex) and fails CI
if any one is missing `autoescape=`. The rule can't drift back.

Also verified the JS side: `toast()` uses `textContent`, so all
`toast(\`...${user.name}...\`)` calls are safe by construction;
`openModal()` uses `textContent` for the title (safe) and
`innerHTML` for the body (relies on per-call `escapeHtml()`, which
36 of 40 JS files use — the remainder don't render user-strings).
The broader JS-innerHTML-XSS class is a separate concern already
tracked under the CSP-unsafe-inline-cleanup item in `docs/todo.md`.

### Layout: `alembic/` → `migrations/`
Database migration scripts moved from `alembic/` to the more
conventional `migrations/` at the top level. `script_location` in
`alembic.ini` updated; references in CONTRIBUTING, PR template, and
docs all retargeted. The `alembic` CLI command itself is unchanged
(reads alembic.ini for its script_location), so `alembic upgrade
head` in `docker-entrypoint.sh` keeps working. Git tracked the moves
as renames, so blame history is preserved.

### Schema-wide date-collision fix (the rest of jake-378's pattern)
jake-378 previously fixed the `date: date` field-name-shadows-type
collision in `app/schemas/invoices.py` and `estimates.py` (commits
48cdb79, e12bbb1). A quick reproducer confirmed **pydantic 2.13 still
has the same bug**:

```python
class Update(BaseModel):
    date: Optional[date] = None   # Optional[<the field>] not Optional[date]
                                   # -> "Input should be None" on every value
```

Same pattern existed in **9 more schemas** (banking, bills, cc_charges,
credit_memos, deposits, journal, payments, purchase_orders,
time_entries) — applied jake's `from datetime import date as dt_date`
rename uniformly across all of them. Added
`tests/test_schemas_audit.py` to lock in the rule so the bug can't
drift back in via a new schema file. (296 tests now passing, up from
295.)

### PostgreSQL version doc alignment
Compose files (both dev and prod) already ship `postgres:17-alpine`,
but `README.md`, `INSTALL.md`, `docs/development.md`, and
`docs/operations.md` all said "PostgreSQL 16" or `brew install
postgresql@16`. Same lag-vs-reality pattern as the Python version
fix. Docs now match what's actually deployed (17).

### Dependency upgrade pass
Five hard-pinned (`==`) deps in `requirements.txt` were months behind.
Pins relaxed to floor-and-cap ranges so future patch/minor bumps land
without needing a release. All upgrades are stable 2.x → 2.x or
patch-only; no API churn expected. pip-audit on the new requirements
remains clean (zero known CVEs).

| Dep | Was | Now | Installed (verified) |
|---|---|---|---|
| `alembic` | `==1.13.3` | `>=1.16.0,<2.0` | 1.18.4 |
| `sqlalchemy` | `==2.0.35` | `>=2.0.40,<3.0` | 2.0.49 |
| `pydantic` | `==2.9.2` | `>=2.11.0,<3.0` | 2.13.4 |
| `pydantic-settings` | `==2.5.2` | `>=2.10.0,<3.0` | 2.14.1 |
| `uvicorn[standard]` | `==0.30.6` | `>=0.32.0,<1.0` | 0.47.0 |

Tests: 295 passing on the upgraded set (no source changes needed).

### Python version doc alignment
`README.md`, `INSTALL.md`, and `docs/development.md` all said "Python
3.12" or "3.12+", but the Dockerfile, every CI job, and the CVE
comments in `requirements.txt` reference Python 3.13. Docs now say
3.13 (the actual tested version); INSTALL.md notes that 3.12 may work
but isn't gated by CI.

### CRM-side UX additions
- **Customer Details modal** — clicking a customer row now opens a
  single-screen popout (no sub-tabs) with billing/shipping addresses,
  autosaving notes, attached reseller permits, recent invoices, and
  recent payments. Closes the "where do we put notes for everyone to
  see?" gap.
- **Reseller permits module** — new `#/reseller-permits` page with
  expiring-soon strip, per-state format validation (WA 9-digit, CA
  9-12, TX 11), copy-permit/business-name/tax-ID buttons, and a
  unified Verify workflow that opens the state's official lookup site
  in the default browser (`window.open('_blank', 'noopener,noreferrer')`
  after a confirm dialog) then stamps `last_verified_at` / disables
  with an inactive marker. Backend has CRUD + `/expiring` +
  `/validate-format` + `/mark-verified`. Pure record-keeping — there
  is no fake "API call" to the state; the operator does the lookup,
  we record the verification trail.
- **Admin Sign Out button** — topbar now has a dedicated logout button
  that POSTs `/api/auth/logout` and reloads to the splash page. The
  endpoint was live; only the button was missing.

### Test infrastructure
- **Bidirectional wiring audit** — `tests/test_wiring.py` already
  asserted every JS `API.*` call resolves to a route; it now also
  asserts every backend `/api/*` route has a JS caller (or is on the
  `_INTENTIONAL_BACKEND_ONLY` allowlist). The catch-all collector
  picks up template-literal paths (including paths assigned to a
  variable before `API.post(url, …)`), `href=`/`action=` attributes
  in JS-rendered HTML, and `window.open('/api/…')`. Pre-substitutes
  `${…}` blocks before regex matching so nested `encodeURIComponent`
  expressions don't break the path capture. Each allowlist entry now
  carries a comment explaining *why* the route has no SPA caller
  (admin-only, scheduled job, drill-down endpoint shadowed by the
  bundled `/dashboard` response, future UI tab, etc.).
- **Audit hook coverage in tests** — `conftest.py` was creating a
  fresh per-test session factory but never re-attaching the
  `after_flush` audit hook to it, so the entire audit-log mechanism
  was silently bypassed in every existing test. The fixture now calls
  `register_audit_hooks` on the per-test session factory. A new
  matrix test (`test_audit_log_covers_new_entities_but_skips_audit_tables`)
  asserts a ResellerPermit insert lands in `audit_log` and a
  PortalAccess insert does NOT (it's already an audit-flavored table).
- **`_SKIP_TABLES` curated** — `audit_log` was the only entry; added
  `portal_accesses`, `login_attempts`, `document_audits`, and
  `email_log` (every one is itself an audit/log table, and double-
  logging into `audit_log` would just add noise and create a future
  recursion footgun if any of them ever gains a trigger-set `id`).

### Payroll / HR UI additions
- **Portal-token admin view** — Employee Details > Portal Access now
  shows expires-at (red when <30 days), last-used-at, and a
  collapsible recent-access log pulled from `portal_accesses`.
- **PTO accrual editor** — `#/hr/pto` gained an Employee Accruals
  section with enroll-employee-in-policy form and per-row "Run Accrual"
  prompt. Closes the gap where admins had to enroll employees via curl.
- **E-Verify case tracking** — schema additions (`everify_status`,
  `everify_submitted_at`, `everify_closed_at`, `everify_notes`),
  GET/PUT `/api/employees/{id}/everify` endpoints, and a new section
  in the Employee Details modal with color-coded status. Pure
  record-keeping — the federal E-Verify submission still happens via
  the official portal or a vendor; this stores the case so DHS
  inspections find it in one place.

### Partial CSP tightening
- index.html's 11 inline `onclick=`/`oninput=` handlers moved to a new
  `app/static/js/bootstrap.js` that wires them via `addEventListener`
  after DOMContentLoaded. The static shell page now has zero inline
  handlers — would work under a stricter CSP today.
- `'unsafe-inline'` stays in `script-src` and `style-src` because the
  JS-rendered modal templates across the rest of the app still emit
  inline handlers + styles. Removing those is a multi-file refactor
  documented in `docs/todo.md`. Honest accounting added to
  `docs/security-hardening.md`.

### Polish
- `docs/release-checklist.md` section 4 (TLS) now mentions optionally
  submitting the domain to the HSTS preload list once TLS is locked in.

### Audit + ops automation
- **Portal access audit log** — new `portal_accesses` table records
  every authenticated and unauthenticated portal hit (employee_id, IP,
  truncated UA, path, success). Mirrors `LoginAttempt` and gives
  forensic queries something more granular than `portal_token_last_used`.
- **Encryption key rewrap CLI** — `python -m app.services.encryption
  rewrap` re-encrypts every bank-PII blob under the current key, so
  rotation can actually complete (transparent reads via PREV fallback
  was already shipped). Supports `--dry-run`.
- **Wiring audit as a unit test** — `tests/test_wiring.py` grep-and-
  resolves every JS `API.*` call against the registered FastAPI routes.
  Catches typos and stale paths automatically — CI fails when a JS
  caller goes nowhere.
- **End-to-end portal test** — single test walks the entire portal
  lifecycle (mint → claim → 5 cookieless pages → POST PTO → logout →
  cold-401 → force-expire → rotate → claim again).
- **Weekly `pip-audit` GitHub Action** — Sunday cron, opens a
  security-labeled issue on findings (de-duped), fails the workflow run.

### Frontend polish
- **Drag-and-drop document uploads** on Employee Details > Documents.
- **Portal logout button** in `portal/base.html` nav.
- **Branded portal favicon** — `/portal/favicon.ico` serves the
  employer's company logo so each customer's portal carries their own
  bookmark icon.

### Bug fix
- Pay-stub PDF was rendering accountable-plan reimbursements as
  positive line items in the **Deductions** table. Now they have their
  own **Additions to Net (non-taxable)** table above net pay. Net-pay
  math was always right; the display was confusing.

### Tax forms — PDFs, audit hashes, the works
- **WeasyPrint PDF endpoints** — `POST /api/payroll/forms/{w2,w3,940,941}/.../pdf`
  render real printable forms (Acme Co. branded, masked SSN, full box
  data). The existing JSON endpoints stay for future e-file integration.
- **Document audit hashes** — every tax-form PDF carries a SHA-256
  content hash and an audit ID in the footer. Backed by a new
  `document_audits` table with three lookup endpoints
  (`/api/document-audits`, `.../verify/{hash}`, etc.). An auditor with
  the PDF can recompute the hash and confirm authenticity against
  the trust-anchor row.

### Payroll / HR
- **Time-entry → pay-run auto-population** — the pay-run form now has
  a "Use approved time entries" checkbox + live-preview column showing
  unpaid approved hours per employee. Backend was already wired; only
  the frontend opt-in was missing.
- **PTO year-end carryover automation** — new
  `POST /api/pto/accruals/year-end-carryover?target_year=YYYY` endpoint
  caps every accrual at its policy `max_carryover` and resets YTD
  counters, returning a per-row before/after summary.
- **Portal cookie session** — after the first `/portal/{token}` claim,
  the token moves into a `HttpOnly Secure SameSite=Strict` cookie and
  every subsequent URL is cookieless. No more Referer leak, browser
  history, or shared-bookmark exposure. Backward-compat: emailed
  `/portal/{token}` links still work — they just redirect through the
  claim flow once.
- **Portal branding** — every page renders the employer's company name
  and logo in the header (was generic "Employee Portal").
- **State new-hire report PDF branding** — same treatment.

### Authentication / session hardening
- **Login attempt audit log** — new `login_attempts` table records
  every success and failure (IP, UA, timestamp). Catches the slow
  brute-force attacker who paces under the 5/min rate limit.
- **Session rotation on login** — `request.session.clear()` before
  issuing the auth flag, defense-in-depth against session fixation.
- **Idle session timeout** — sliding window via
  `SESSION_IDLE_TIMEOUT_SECONDS` (default 14400s = 4 hours). Sessions
  past the threshold get 401'd and cleared.

### Security
- **App-level `HTTPSRedirectMiddleware`** + HSTS (2-year, includeSubDomains,
  preload) when `FORCE_HTTPS=true`. Session cookie carries `Secure` flag
  in the same conditional.
- **Content-Security-Policy** — `frame-ancestors none`, `object-src
  none`, `form-action self`, Stripe origins allowlisted.
- **Startup fail-hard checks** (production only): refuses to start if
  `PAYROLL_ENCRYPTION_SECRET` is the dev default, `DATABASE_URL` lacks
  `sslmode`, or `FORCE_HTTPS=false`.
- **Portal token expiry** — 1-year hard + 90-day sliding idle. Expired
  tokens return `410 Gone`.
- **Portal headers** — `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store` on every portal response.
- **Encryption key versioning** — bank PII ciphertext now prefixed with
  `v1:`. `PAYROLL_ENCRYPTION_SECRET_PREV` env var supports
  zero-downtime key rotation; decrypt tries current key first, then
  previous.
- **Per-endpoint rate limiting** — portal at 30/min GET / 10/min POST,
  joining the existing 5/min on login.

### Dependency CVE pass
Bumped requirements.txt to close known CVEs surfaced by `pip-audit`:
- `cryptography` — cap raised from `<44.0` to `<47.0`, floor `46.0.5`
  (closes PYSEC-2026-35, CVE-2024-12797, CVE-2026-26007, etc.)
- `fastapi` — bumped from `0.115.0` to `>=0.121.0,<0.122` to allow
  starlette `0.47+`
- New explicit `starlette>=0.47.2,<0.50` pin (closes CVE-2024-47874,
  CVE-2025-54121)
- New explicit `pyjwt>=2.10.0,<3.0` pin (override intuit-oauth's
  transitive 2.7.0 with known CVE)

`pip-audit -r requirements.txt` now reports **zero known
vulnerabilities**.

### Wiring fixes
Spider-web audit of every `API.*` call against every `@router.*`
handler caught four real breakages:
- 3× `API.delete()` typos (`API.del` is the actual export) — `employees.js`,
  `deductions.js`
- Missing `GET /api/pto/policies/{id}` and `PUT /api/pto/policies/{id}` —
  the policy-edit form was 404'ing
- `/approve` and `/reject` alias routes for the PTO `/decision` endpoint —
  the buttons were hitting non-existent paths

### Docs + repo conventions
- **`CONTRIBUTING.md`**, **`.github/PULL_REQUEST_TEMPLATE.md`**,
  **`.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.{md,yml}`** — the
  standard set this size of repo should have had.
- **`docs/hipaa-compliance.md`** — Security Rule mapping, 8-gap honest
  assessment, deployment recommendations.
- **`docs/security-hardening.md`** + **`docs/wiring-audit.md`** + **`docs/todo.md`** — engineering
  logs for the hardening pass, the wiring audit methodology, and the
  internal TODO scratchpad.
- README de-Phased / de-Tiered — that history now lives in this
  CHANGELOG file instead of cluttering the user-facing readme.

### Cleanup
- **Alembic revision collision fixed** — tier1 was sharing
  `f6a7b8c9d0e1` with the Phase 11 inventory migration. Renamed to
  `f7a8b9c0d1e2`; chain is now linear.
- `test_frontend_pages.py` moved from repo root to
  `scripts/integration_test_frontend.py` (it's a live-HTTP integration
  script, not a unit test).
- `app/templates/invoice_pdf_v2.html` deleted — added 5 weeks ago but
  never wired into `pdf_service.py`.
- `backups/` directory kept tracked (via `.gitkeep`) but contents
  gitignored so dumps don't accidentally land in commits.

### Test coverage
297 tests passing. Up from 119 at the start of this branch's work.
All previously-passing tests still pass.

### Docs reorganization
Root now keeps only `README.md`, `INSTALL.md`, `SECURITY.md`,
`CHANGELOG.md`, `CONTRIBUTING.md` (the conventional set). Everything
else moved into `docs/`.

## [2.0.0] — May 2026

### Added
- **Analytics dashboard** at `#/analytics` — KPI cards plus four charts
  (12-month revenue line, expenses doughnut, A/R+A/P stacked bar,
  90-day cash forecast), MTD/QTD/YTD period selector, CSV/PDF export
  with branded headers.
- **AI Insights** — Optional one-shot executive brief (3 observations /
  3 risks / 3 recommendations) with seven supported providers (xAI Grok,
  Groq, Cloudflare Workers AI, Cloudflare self-hosted gateway, Anthropic
  Claude, OpenAI, Google Gemini). Bring-your-own-key, encrypted at rest.
- **AI Predefined Analyses** — 11 curated actions across 5 categories,
  replacing the earlier free-form chat (more reliable across providers).
- **Inventory ledger** — Perpetual inventory with weighted-average cost,
  automatic COGS journal entries on every sale, reorder points,
  Adjust modal for add/remove/set-to-count.
- **Drill-down reports** — P&L and Balance Sheet rows are click-through
  to source transactions with running balance and source-doc links.
- **Saved Reports** — Name and one-click rerun favorite report configs.
- **Duplicate detection** — Fuzzy matching on customer/vendor names with
  a confirm-and-create-anyway dialog.
- **Setup wizard** collects operator name + email + company name + email
  + password (was password-only).
- **Branded headers** on PDF/CSV exports (SlowBooks Pro 2026 wordmark +
  company logo).

### Changed
- AI provider config moved from a modal to a Settings sub-page with a
  curated model dropdown and Custom escape hatch.
- Items form gained the full inventory toolset (track checkbox, qty,
  reorder point, asset account).
- Customers/Vendors gained the duplicate-warning confirm dialog.

### Security
- **Single-user authentication** — Argon2id-hashed password, session
  cookie (`same_site=strict`, 30-day TTL).
- **Rate limiting** — slowapi at 5 logins/minute per IP.
- **Security headers** — X-Content-Type-Options, X-Frame-Options DENY,
  Referrer-Policy, Permissions-Policy on all responses.
- **CORS lockdown** — explicit origin allowlist, no wildcards.
- **Path traversal protection** — backup and attachment endpoints use
  `Path.is_relative_to()`.
- **Atomic secret writes** — session key uses `mkstemp` + `os.replace()`.
- **Fernet encryption** for AI provider API keys.
- **SSRF protection** — AI provider URLs validated against private IPs
  and metadata endpoints.
- **Constant-time secret compare** in the Cloudflare Worker gateway.
- **Schema-validated AI config payloads.**
- **CSV formula injection protection** — exports neutralize `=`, `+`,
  `-`, `@` cell prefixes.
- **Non-root Docker** — container runs as UID 1000.

### Performance
- Analytics dashboard: 10 SQL queries, ~26 ms engine on 3,000 invoices
  plus 1,500 bills.
- Test suite runs in under 30 seconds with zero network dependencies.

### Fixed
- Dark mode now works on every report subtotal row (missing `--gray-50`
  definition).
- `--text-main` typo fixed.

## Earlier releases

Internal build history before v2.0.0 lived under "Phases" 1-11. A
recap of what each phase covered:

| Phase | Scope |
|-------|-------|
| 1 | Foundation — audit log, full-text search |
| 2 | Accounts Payable — POs, bills, bill payments, credit memos |
| 3 | Productivity — recurring invoices, batch payments |
| 4 | Communication & Export — CSV import/export, uploads |
| 5 | Advanced integration — bank import (OFX/CSV), tax export, backups |
| 6 | Companies, employees, payroll |
| 7 | Online payments (Stripe) |
| 8 | QuickBooks Online sync |
| 9 | Analytics + journal entries + deposits + credit-card charges + checks |
| 9.5 | AI Insights layer |
| 9.7 | Single-user authentication, rate limiting, security audit pass |
| 10 | Bank rules, budgets, attachments, email templates |
| 11 | Inventory ledger, drill-down reports, fuzzy duplicate detection, saved reports |

The payroll/HR module was layered separately:

| Tier | Scope |
|------|-------|
| 1 | Onboarding checklists, time entries, PTO |
| 2 | Deductions, garnishments, gross-up calculator |
| 3 | Tax forms (W-2/W-3/940/941), employee self-service portal |

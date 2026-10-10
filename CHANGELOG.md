# Changelog

Notable changes between releases. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/). The internal build order
used during development is captured here so the README can stay focused
on what the software does, not on what sprint shipped what.

## [Unreleased]

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
one-click load of the CSI MasterFormat divisions. Bill lines, journal
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

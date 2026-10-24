# Banking: the register is the ledger

Since 2.10 there is one ledger. A bank or credit-card account is a chart
account flagged as such; its register is the ledger's lines on that account;
a register entry posts; statement lines from a feed or a file wait until you
match them to a posting or add them as one; reconciliation ticks ledger
lines; a card is paid with a transfer. (Issue #114, from discussion #112.)

## Accounts

**Chart of Accounts → an asset account → kind: Bank**, or a liability →
**Credit card**. Checking (1000), Savings (1010) and Credit Card (2100) come
flagged. Only flagged accounts appear in "Paid from", "Deposit to", "Pay
from", the transfer form and the Banking page. `GET /api/accounts?bank=1`.

A **bank feed** (Banking → + New Bank Account) is the account's statement
identity: bank name, last four, and where SimpleFIN or a file import lands.
It links to exactly one ledger account. An **opening balance** entered there
posts against 3900 Opening Balance Equity (created on demand): for a bank,
the cash in it; for a card, the amount owed. When the ledger already
carries the account on the As-of date, the statement balance is compared
with it instead: equal posts nothing, and a difference posts (as "Opening
balance adjustment") only after you confirm — the form shows what the books
hold (`GET /api/banking/ledger-balance?account_id=&as_of=`); the API
answers 409 `ledger_has_balance` until the request carries
`post_difference: true`.

## One sign rule

| amount | bank account | credit card |
|---|---|---|
| **> 0** | money in — DR bank | a payment to the card — DR card (owe less) |
| **< 0** | money out — CR bank | a charge — CR card (owe more) |

The same rule on both kinds. Display follows the account's natural balance,
so a card register shows the amount owed as a positive number, with
*Charge* and *Payment* columns instead of *Payment* and *Deposit*. Bank
statements (OFX/QFX, CSV, SimpleFIN) already carry their amounts this way;
nothing is flipped on import.

## The register

Banking → an account (its own address, `#/banking/<account id>`, so a
refresh stays on it). Every posting on the account — expenses, deposits,
bill payments, customer payments, payroll, card charges, transfers, register
entries, journal entries — with payee, reference (a bill payment's check
number included), type, a link to the document, the running balance, and a
✓ when a statement line has cleared it or **R** when a reconciliation has
closed it.

**+ Entry** posts a register entry: date, amount (sign per the rule), payee,
check/ref number, **category** (required — the other side of the entry) and
memo. A category that is itself a bank or card account makes it a transfer.
"Split this entry…" takes an account and amount per line and posts one
entry with several lines (a check that pays several things, a loan
payment's principal and interest); a deposit splits the same way, and a
line ticked **Taken out** goes the other way from the entry, so a card
processor's deposit is the gross as income, the fee as an expense, and the
net the bank got (`splits` on the API, each line signed; a line against the
entry's sign is the fee or a refund).
Register entries, transfers and card charges have **Void** (a mirror-image
reversal; the original stays). An entry a completed reconciliation holds
cannot be voided.

A register entry's row has **Edit** (part of #388): the same form, filled in, and
Save rewrites the entry on the same journal entry (its links, statement
match and audit trail stay with it). A fix that leaves every line's account
and amount alone (payee, memo, check number, date, class, job) updates the
entry where it is, and saving it unchanged writes nothing; a new amount or
category replaces the lines (each old line a DELETE in the audit log, each
new one an INSERT). The form carries back what it has no box for: the
entry's job, a split line's own class or job, a class archived since; a
line with a class of its own (an imported check's) keeps it. A memo-only
entry (a memo, no payee) reopens with the memo in Memo, not in Payee: no
payee is stored as the sentinel "Bank entry" on the header, which tells a
memo-only entry from a payee-only one and shows a blank Payee in the
register. No migration; entries posted before this still read their memo as
the payee.

An entry a completed reconciliation holds says so when it opens: its
amount and date stay as the statement has them (refused, as its void is;
correct the difference with a new entry). Its payee, memo, check number,
class and job can change; a new category or split asks first, with
**Change it anyway**. On an entry that is only cleared, a changed amount
loses the tick, and the page says so. An entry added from a statement line
stays linked to it; when its amount changes, the statement line goes back
to review without its category, so **Add all categorised** doesn't post it
again. A void entry is not edited; an edit that would make the entry a
transfer is refused (void it and post the transfer). Read-only and
accountant sign-ins don't see Edit. Journal entries and deposits open
their own views from the row.

The entry row at the foot of the register posts the same way (Enter or
Post). A payee typed there, or in + Entry, that is not already a customer's
or a vendor's name is offered as one — a customer for money in (a deposit),
a vendor for money out, matching the categorized import — with Add / Not
now; the entry posts either way, and Add names the party so the entry's
payee resolves to it. The question's toast takes focus and returns it to the
row only when answered (or if it still holds the focus as it goes), holds its
auto-dismiss while focused or hovered, and — in the + Entry dialog — folds
into the dialog's Tab cycle, so it answers by keyboard; a toast left to time
out leaves the caret where the typing moved on to. An
**Add** button beside the category picker makes a new account in place —
name, type, an optional parent (never a control account) and number — and
selects it.

`POST /api/banking/transactions {account_id, date, amount,
category_account_id, payee?, description?, check_number?, class_id?,
job_id?, splits?: [{category_account_id, amount, description?, class_id?,
job_id?}]}` → the
journal entry (the split lines add up to `amount`; none is zero; no bank or
card account among them; a split line may name its own `class_id` and
`job_id`). `GET /api/banking/entries/{id}` is a register entry in that
shape (plus `class_id`, `class_name`, `job_id`, each split's own class and
job, `reconciled_on` and `reconciled_note`); `PUT
/api/banking/entries/{id}` takes the same fields, any left out keeping what
the entry has, and rewrites it (on a reconciled entry: 400 for a new amount
or date, 409 with the statement date for a new category or split until
`confirm_reconciled` is true; the answer's `uncleared` names the lines that
lost their mark). `GET
/api/banking/check-register?account_id=` is the register
(`start_date`/`end_date` optional; an opening balance is carried in for a
range); its rows say `editable`.

A check written here, or an Expense paid from a bank account, prints from
its register row (Print Check) and from the expense's view: `GET
/api/checks/print?transaction_id=` renders the same 3-per-page voucher
as a bill payment's check, the reference as the check number and each
split line on the stub. The register marks the rows that print
(`printable`): money out of a bank account, not a card, not void. A bill
payment's check prints from its bill.

## Transfers

**Transfer** on the Banking page or a register: date, amount, from, to.
DR to / CR from. Paying a card is a transfer from the bank to the card.
`POST /api/transfers`, `GET /api/transfers`, `POST /api/transfers/{id}/void`.

## Bank feeds and file imports: the review queue

A statement line arriving by SimpleFIN sync, OFX/QFX or CSV import. CSV
files: the Bank of America, Chase and PayPal layouts, or any file whose
header names a date, a description (or payee / memo) and either one signed
amount or money-out / money-in columns. For a file whose header says none
of that, the import dialog shows its columns and a few rows and asks which
is which (and the date format); the answer travels with the preview and the
import as a `mapping` form field (JSON: column indexes for `date`,
`description`, `payee`, `amount` or `debit`/`credit`, `check_number`, plus
`date_format` and `has_header`).

Nothing has to exist before the import: the payee on a line is free text
until it is matched or a rule names a vendor, and a line with no category
arrives uncategorised and is categorised in the review queue. You do not
set up vendors or accounts first.

1. **Duplicates are skipped** (bank transaction id / FITID, or a
   content-derived id for CSV).
2. **Bank rules suggest a category** for lines whose payee matches a rule.
   Rules never post.
3. **Auto-match**: the line looks for the one ledger line on the account with
   its amount on its side within ±5 days; a check number narrows to entries
   carrying it as reference. Exactly one nearest candidate → linked and the
   ledger line is cleared. Two equal candidates → the line waits.
4. Everything else is **To review** on the account:
   - **Add** — posts it as a register entry with the category (a bank or card
     category → a transfer; a positive card line with the paying bank as
     category is a card payment) and links it.
   - **Match** — pick from the ledger lines within 30 days with that amount.
   - **Exclude** — drop it (Restore brings it back).
   - **Add all categorised** — adds every line that carries a category and
     reports what it skipped (a closed period, for instance). A category
     picked in a line's list is saved on the line as it is picked
     (`PATCH /api/banking/transactions/{id} {category_account_id}`), so
     Add all posts it and a reload keeps it.
   - **Find matches** — re-runs auto-match for the feed.

`GET /api/banking/transactions?bank_account_id=&status=unmatched`;
`/transactions/{id}/candidates`, `match {line_id}`, `unmatch`, `add
{category_account_id?…}`, `exclude`, `restore`;
`/accounts/{feed}/feed/add-all`, `/feed/auto-match`.

Voiding a posting that a statement line is linked to sends the line back to
the queue.

## Reconciliation

Banking → an account → **Reconcile**: statement date and ending balance (for
a card, the amount owed). The session lists the ledger lines on the account
up to that date that no completed reconciliation holds; matched statement
lines arrive ticked. **Beginning balance** is the prior completed statement's
balance; **difference** = statement − (beginning + cleared). Finish when the
difference is zero: the cleared lines are stamped with the reconciliation
and locked. **Abandon** drops the session and keeps the ticks. One open
reconciliation per account.

`POST /api/banking/reconciliations {account_id, statement_date,
statement_balance}` (409 with `existing_id` when one is open), `GET
/{id}/transactions`, `POST /{id}/toggle/{line_id}`, `POST /{id}/complete`,
`DELETE /{id}`. `PUT /{id} {statement_balance?, statement_date?}` corrects
the statement while the reconciliation is in progress (the screen's
**Change…** button): the ticks stay and the difference is refigured; a date
moved earlier leaves the lines after it off the session, ticks kept. Those
lines won't be stamped when you finish and carry to the next reconciliation
still ticked, so the change response and the reconcile screen count them —
only the ticks the earlier move left behind, not a deposit a bank-feed match
or a register clear cleared after a date that never moved.

A statement date on or before the last completed one is refused. Finish
names the difference when it isn't $0.00. A completed reconciliation keeps
a report — beginning and ending balances, the items it cleared, what was
still outstanding on the statement date, the register balance then — on
screen (register → **Reconciliations…**) and as a PDF: `GET /{id}/report`,
`GET /{id}/pdf`.

## Upgrading from 2.9 and earlier

- The old register kept its own balance. It is **not posted for you**; the
  Banking page shows it once per feed with **Post as opening balance** (as of
  a date you pick, against 3900) or **Dismiss** (if the ledger already
  carries it through Opening Balances or journal entries).
- A feed that was never linked to a ledger account gets a bank account
  created and linked (named after the feed, next free 10x0 number).
- Rows ticked in the old side-ledger reconciliations are **excluded** from
  the review queue; Restore brings one back. Old reconciliations stay in the
  history.
- Old register rows that were never posted are statement lines now: review
  them like an import.
- API: `POST /api/banking/accounts` needs `account_id` and takes
  `opening_balance` (the old `balance` field is refused); `POST
  /api/banking/transactions` needs `category_account_id` and returns the
  journal entry; `GET /api/banking/transactions` lists statement lines only.

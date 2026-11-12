# Reports on a cash basis — design (#483)

Status: DESIGN + steps 1, 2a, 2b and 4 built (#483, 2026-10-26 / 28): the
reports take a basis, edits of a paid document rewrite its payments' cash
lines, the journal form chooses a basis, the Sales Tax Liability report
reads on a basis, retainage on bills is handled, and payroll stays on both
(see "Step 4" below). The
architecture — a basis on every ledger line, written when the document
posts, never recomputed when a report is read — was decided by Keith on
2026-10-26. Steps 1 and 2a (below) are built; "Decisions" at the end records
what the builds settled that this text had not, and "Rollout in PRs" what
is left. Issue #483, from Trent: every statement is accrual only, nothing in
`app/` knows a basis, and the CPAs who file most small-business returns
file them on the cash basis, so the bookkeeper switches the P&L at year
end. QuickBooks has a company default and a per-report toggle.

## What exists today

**One ledger, one reading.** `Transaction` / `TransactionLine` in
`app/models/transactions.py` are the whole double-entry engine: every
document posts a balanced entry through `create_journal_entry` in
`app/services/accounting.py` (sum of debits equals sum of credits, cents
exact, the closing date checked once for every path), and every report
sums the lines. Nothing on a line says *when* its figure counts.

**A/R and A/P are the only accruals.** An invoice posts Dr 1100 Accounts
Receivable / Cr income accounts (one per line, the item's or 4000) / Cr
2200 Sales Tax Payable (`_build_invoice_journal_lines`,
`app/routes/invoices/helpers.py`). A customer payment posts Dr bank or
1200 Undeposited Funds / Cr 1100, with a realized-FX line when the
currencies differ (`create_payment`, `app/routes/payments.py`); the
unallocated part of a payment stays a credit on 1100 until
`POST /api/payments/{id}/apply` moves it onto invoices, which posts
nothing but FX. A batch payment (`app/routes/batch_payments.py`) and an
online payment (`app/services/payments/recorder.py`) post the same two
lines. A sales receipt is an invoice and a payment on the same day
(`app/routes/sales_receipts.py`). A bill posts Dr each line's expense,
cost of goods or inventory account (tax spread over the lines) / Cr 2000
Accounts Payable (`_post_bill_lines`, `app/routes/bills.py`); a bill
payment posts Dr 2000 / Cr bank (`app/routes/bill_payments.py`). A credit
memo posts Dr income / Cr 1100 and applying it to an invoice posts
nothing; a small-balance write-off is a credit memo with `is_write_off`
whose income side is 6960 Bad Debt Expense (`app/routes/credit_memos.py`).
A vendor credit is the mirror. Everything else — expenses, checks, card
charges, register entries, deposits straight to income, transfers,
payroll runs, journal entries, opening balances, depreciation — is cash
or neither and reads the same under either basis.

**Voids reverse, edits repost.** A void posts the mirror image of the
original lines (`reversing_lines`, or the per-route copy of it) on the
original date, under the closing date, and refuses a line that cleared in
a completed reconciliation (`bank_posting.assert_not_reconciled`). A paid
invoice or bill cannot be voided until its payments are
(`void_invoice`, `void_bill`). An edit of a posted invoice or bill
deletes the entry's lines and reposts them under the same transaction
id (`_reverse_and_delete_journal`, `existing_transaction`), allowed
while the new total is not below what is already paid.

**The stored balance** `Account.balance` is a running total kept beside
the lines (`apply_balance_deltas`), recomputed from them at every start
(`rebuild_stored_balances`); the Chart of Accounts, the accounts export
and the AI tools read it.

**The reports.** `_totals_by_account` in `app/routes/reports/financial.py`
is the one sum behind the P&L (`profit_loss` → `_pl_period`) and the
Balance Sheet (`balance_sheet` → `_bs_period`, which also derives the
"Net Income (current period)" equity line from the same sums). Both take
`sort`, `compare=prior_year`, and the P&L `exclude_adjusting` (#341),
`class_id` and `columns`; the PDF and CSV routes call the same functions
and print the same figures. The Trial Balance, General Ledger, P&L Detail
(`app/services/pl_detail.py`), the grids (`_pl_pivot`), the nonprofit
statements, the dashboard, the registers, the agings and about a hundred
other query sites sum `TransactionLine` directly.

## The proposal

**A basis on every line.** `transaction_lines.basis`, a `String(10)`,
one of:

| `basis` | Meaning | Written by |
|---|---|---|
| `both` | the figure is the same under either basis | the default: every cash expense, deposit, transfer, payroll run, journal entry, opening balance… and the bank and A/R or A/P sides of a payment |
| `accrual` | counts only on the accrual basis | every line of an invoice, bill, credit memo, vendor credit, late fee and their voids and edit adjustments (`ACCRUAL_SOURCE_TYPES` in `app/services/cash_basis.py`) |
| `cash` | counts only on the cash basis | the recognition lines a customer payment or bill payment writes (below) |

**Two readings of one ledger.** The accrual view is `both + accrual`; the
cash view is `both + cash`. Each view must balance on its own, so the
rule `create_journal_entry` enforces is: the whole entry balances (as
today), *and* its `accrual` lines balance among themselves, *and* its
`cash` lines balance among themselves. An entry that breaks it is refused
as an unbalanced entry is.

That rule is why the payment's A/R line is `both`, not `accrual`: a
payment is Dr bank 100 (both) / Cr A/R 100 (both), and its cash
recognition is a self-balancing pair, Dr A/R 100 (cash) / Cr income 100
(cash). In the accrual view A/R is relieved and income was recognized by
the invoice; in the cash view the invoice is invisible, A/R nets to zero
on the payment, and income lands on the payment's date. Bank is `both`,
so bank balances are identical under either basis.

**The cash recognition lines** are written into the payment's own journal
entry, dated the payment:

- For each allocation to an invoice, the invoice's recognition lines —
  every line of its entry except A/R: income per item, the discount
  debit, sales tax payable, a QuickBooks Online balancing line, plus any
  late fee posted on it — prorated by `applied / invoice total` in home
  currency, each share to the cent with the rounding cent on the largest
  share, by the running-total rule (#804, "Partial payments" below): the
  lines prorated to all that has been settled on the invoice, less what
  the earlier settlements recognized, carrying the line's class, job, cost code, cost type and function
  (there is no item column on a ledger line; the item's income account is
  what the line carries). Same side as the original (income stays a
  credit, a discount stays a debit). One balancing Dr A/R (cash) for the
  applied amount.
- For a bill payment, the same from the bill's lines (expense, cost of
  goods, inventory asset, with the tax already inside each) as debits and
  a Cr A/P (cash) for the applied amount.
- For the unallocated part of a payment (a prepayment, an overpayment, a
  payment taken without choosing invoices): Dr A/R (cash) / Cr 4950
  Unapplied Cash Payment Income (cash), QuickBooks' rule. A bill payment's
  remainder: Dr 6990 Unapplied Cash Bill Payment Expense (cash) / Cr A/P
  (cash). Both accounts are created on demand (`ensure_account`, as 3900
  and 6999 are).
- When that remainder is later applied (`POST /api/payments/{id}/apply`),
  cash lines appended to the payment's own entry move it: Dr Unapplied
  Cash Payment Income / Cr the invoice's recognition lines, prorated as
  above. So they sit on the payment's date: the cash was in hand that
  day, the period's cash-basis net income does not change (unapplied
  cash income was income already), only the account it sits in, and
  applying still posts no new transaction. Voiding the payment reverses
  the entry whole.

**Reading a basis.** `ledger_basis` is a session execution option (one
`do_orm_execute` listener on `Session`, beside the model in
`app/models/transactions.py`): every ORM select that touches `TransactionLine` — a column
sum, a join, a subquery, a relationship load — carries
`basis != 'cash'` unless the query asks for `ledger_basis="cash"`
(`basis != 'accrual'`) or `"all"`. So every reader that exists today,
and every one written without thinking about it, keeps the accrual
figures it had: the General Ledger, the registers, the agings, the
dashboard, the IIF export, `rebuild_stored_balances`. Step 1's two
reports opt in; the paths that must see every line (a void copying the
entry it reverses) ask for `"all"` through `all_lines`. The stored
balance never takes a cash line (`apply_balance_deltas` is handed none).

**The switch.** A company default, `report_basis` in `DEFAULT_SETTINGS`
(`accrual` | `cash`, default `accrual`, Settings → Company), and a
`basis` query parameter on the report (`?basis=cash`, `accrual`, or
omitted for the default) — QuickBooks' shape. The response says which
(`"basis"`), the PDF and CSV take the same parameter and print "Cash
basis" in the period line (the accrual line stays as it is, so a file
saved yesterday still matches), and the screen offers it beside Sort by.

### Migration

One Alembic revision after `c8e4a2f1b3d5` (cost codes by division,
2026-10-26): `transaction_lines.basis` NOT NULL, server default `'both'`,
so every existing row and every row an old build inserts is `both`
(SQLite adds the column in one `ALTER TABLE`; PostgreSQL the same). No
index on `basis`: `basis != 'cash'` is not selective. A second revision,
`f2b3c4d5e6a7`, indexes `transactions(source_type, source_id)`: the
backfill finds a payment's invoice and bill entries by that pair, and
without the index it scanned the table once per payment (18 s for 2,000
payments and 189 s for 8,000 in review). It is its own revision so a file
already at the first one gets it.

**The backfill** is `cash_basis.backfill(db)`, run at every start beside
`rebuild_stored_balances` (not inside the migration — a migration must
not import the models, which describe a later schema; the detail-type
migration keeps its own copies for the same reason, and the start-up
pass is what repairs a file a build between the two wrote). It is
idempotent and cheap when there is nothing to do:

1. Every line of an entry whose `source_type` is an accrual one and
   whose basis is still `both` → `accrual`. (A reversal entry is matched
   by its own source type, `invoice_void`, `bill_void`…)
2. Every customer payment and bill payment that is not void, has an
   entry, and has no `cash` line yet, in date order: its allocations are
   prorated and written as above, on the payment's date; the remainder
   as unapplied. An allocation made later through `/apply` cannot be
   told from one made on the day, so all of them land on the payment's
   date. A payment's entry gets its cash lines exactly once.
3. Nothing is written for a voided payment (its entry and its reversal
   both carry no cash lines, and net to zero in either view).

It commits every 200 payments (`BACKFILL_BATCH`), so a big book never
holds one long write transaction. Each payment is written inside its own
savepoint: one that fails is logged by id (`Cash-basis backfill skipped
payment 42`), rolled back alone and counted in the result's `failed`; the
rest are written, and the failed one is tried again at the next start.
Start-up prints the counts and, when `failed` is not zero, a warning with
the number.

After it, the invariant below holds for the file; the test suite proves
it on a seeded one. The importers that create payments (IIF, the QuickBooks report
imports, the QuickBooks Online export) run the same pass at the end of
the import (`backfill_after_import`), so their payments have cash lines
at once, not after a restart; a failure there is logged and never fails
the import. Any other path is caught at the next start, so a payment
entry is never left with a reading only one basis can give.

### The invariant

For any period, in the cash view: cash-basis net income equals the change
in cash (bank, credit card and Undeposited Funds balances) plus the
change in the other balance-sheet accounts that a cash-basis book still
carries, which is what the cash Balance Sheet says:

```
NI_cash(period) = Δ cash
                + Δ other assets         (fixed assets bought, inventory paid for, prepaid, retainage receivable…)
                − Δ liabilities           (loans, cards, sales tax collected not remitted, payroll liabilities, customer deposits)
                − Δ equity ex. net income (owner contributions and draws, opening balance equity)
```

with A/R and A/P at zero. The tests state it as: the cash Balance Sheet
balances; 1100 and 2000 read zero on it; and `net_income` on the cash
P&L equals the change in the cash accounts plus the named non-cash
movements of the scenario, exactly (no sales tax: net income is the
change in cash to the cent).

## The hard cases, and step 1's rule for each

**Partial payments.** Prorated by amount over the document's recognition
lines by a running total (#804; `settle` in `app/services/cash_basis.py`).
A settlement — a payment's allocation, an early-payment discount, an
unapplied remainder applied later — prorates the lines to the running
total, what the document's standing settlements before it recognized plus
what it applies, each line to the cent with the leftover cent on the
largest share (`prorate`), and takes that less what those earlier
settlements recognized, line by line, read from their cash lines through
`recognizes_invoice_id` / `recognizes_bill_id`. Its lines add up to what it
applied exactly; once the running total reaches the document's total every
line is recognized whole, so the settlement that settles the document
carries the rounding and the cash lines equal the accrual lines to the
cent; a part paid never recognizes more tax than its rounded share of what
has been paid. Example: 33.33 of income and 2.42 of tax paid in thirds
(11.92, 11.92, 11.91) recognize tax 0.81, 0.80, 0.81 — rounded on its own,
each third took 0.81, and the cash basis read 33.32 / 2.43 against
33.33 / 2.42. "Before" is every standing settlement when a payment posts
now, and those entered earlier (date, then entry) when the lines are
rewritten; a voided payment or discount is not standing, though its lines
stay in its entry beside their reversal. A payment posting, the backfill,
an apply-later reclass, a discount and the recompute after an edit all go
through `_recognition`, so they share the rule; the recompute rewrites a
document's payments and discounts together, in the order they were
entered, each after those before it. Lines written before the link existed
name nothing and are not counted, so the next settlement after them rounds
as it did before.

**Credits applied, discounts, write-offs.** A credit memo is an accrual
entry (Dr income / Cr A/R); applying it to an invoice posts nothing, and
the invoice's later cash is still prorated over the invoice's *total*,
so the credited part of the income is never recognized on the cash basis
— which is right: no cash came for it. A discount line is a debit on the
item's account and is prorated with the rest. A small-balance write-off
is a credit memo whose income side is Bad Debt Expense, so on the cash
basis neither the written-off income nor the bad debt ever appears.

**Prepayments and unapplied cash.** QuickBooks' rule, above: Unapplied
Cash Payment Income on the payment's date, moved to the invoice's income
when applied — on the payment's date too (decision 6). The vendor side
mirrors it with Unapplied Cash Bill Payment Expense; a vendor prepayment
has no apply-later route yet, so it stays there until step 2.

**Voiding or editing a paid document.** The cash lines belong to the
payment's entry, so voiding the payment reverses them (the void reads
every line, `all_lines`, and `reversing_lines` carries each line's
basis), and the invoice or bill can then be voided as an accrual entry.
A paid or partly paid invoice or bill whose lines are edited has its
payments' cash lines rewritten (step 2b): each cash line names the invoice
or bill it recognizes (`recognizes_invoice_id` / `recognizes_bill_id` on
`transaction_lines`), so after the entry is reposted every non-void
payment's lines for that document are removed from the payment's entry and
written again from the document's new lines, prorated by the same applied
amount (from the allocation rows), on the payment's date. The unapplied
remainder's lines (4950 / 6990) are not touched, and the control A/R or A/P
cash line stays (it is rebalanced if the amounts differ). A payment on or
before the closing date keeps the lines it has, and the server log says so;
to change those, void the payment, edit, receive it again. Lines written
before the link existed name nothing: an edit rewrites them only when the
payment paid that one document in full (every cash line but the control's is
then the document's); a payment on several documents with unnamed lines is
left alone and logged, since its lines cannot be told apart. A payment that
has no cash lines for the document yet gets them (an invoice the QuickBooks
Online ledger import posted, adopted by the edit).

**Sales tax payable.** Prorated with the invoice's lines, so on the cash
basis 2200 carries the tax *collected* and not yet remitted, and the
sales-tax payment (Dr 2200 / Cr bank, `both`) takes it down. Step 4 puts the
Sales Tax Liability report on a basis (below).

**Payroll.** A pay run posts wages and employer taxes as expense against
net pay (bank) and the withholdings and employer taxes (payroll
liabilities), all `both`. Decided in step 4: payroll stays `both` under
either basis. QuickBooks' cash-basis reports do the same (payroll
liabilities stay, like other current liabilities), and the invariant above
already names payroll liabilities among the non-cash movements. The earlier
plan, tagging the liability-side expense `accrual` and writing cash lines on
the liability payment, has nothing to hang on: payroll liabilities are paid
with ordinary checks or journal entries (`_paid_liability_cautions` only
warns), there is no liability-payment document. An accountant who wants
the tax expense on the deposit date adjusts it by journal entry.

**Journal entries that hit A/R or A/P directly.** `both`, as every
journal entry: a hand-posted A/R line counts on both bases and is not
relieved by a payment's cash pair (a payment cannot be applied to a
journal entry). QuickBooks' rule — such a line follows the document it
is "linked" to — needs a customer on the line, which the journal form
does not have. Opening A/R and A/P balances entered as journal entries
sit on the cash Balance Sheet unless the entry says **Accrual only** (step
2b: the journal form's Basis choice and `basis` on `POST/PUT /api/journal`,
`both` | `accrual` | `cash`, applied to every line of the entry, shown on the
entry, kept by an edit that leaves it out and by the void). So nobody reads that history as cash, a cash-basis report
whose A/R or A/P is not zero at its end date carries a short note
(`basis_note` on the data; under the period line on screen, under the
table on the PDF).

**Retainage and progress billing.** A release is a payment, and a payment
writes the cash lines for what it paid or collected.

*Bills (step 4).* A bill holding retainage posts Dr expense lines / Cr A/P
(total less retainage) / Cr 2050 Retainage Payable. Its cash lines leave 2050
out: a payment applied to the bill spreads the amount over the bill's
expense, cost and inventory lines (`applied / sum of those lines`), so the
cash lines balance on A/P as before and, once the net is paid, each line is
recognized in the proportion `net / total`; the held part is not yet an
expense and 2050 reads zero on the cash Balance Sheet (the accrual view shows
it as a liability). A release is a bill of its own (`release-retainage` on
the commitment, #555: Dr 2050 / Cr A/P, accrual), paid through Pay Bills.
Its payment's cash lines recognize the held part of the commitment's bills:
`recognition_pool` gives a release bill the bills held before it, each
prorated to its share of the release, and the payment prorates that pool by
`applied / release total`, Cr A/P as any bill payment. Each release takes
from the bills held before it in proportion to what each still holds
(`_release_shares`), worked from the documents alone, so a payment posted
today and the backfill of an older book write the same lines, and releases
that add up to all that was held recognize each bill's held part once. An
edit of a held bill rewrites its releases' payments too. A plain check or
journal entry against 2050 still has nothing to recognize (*Known limits*).

*Invoices (pay applications, #555).* The invoice's A/R debit splits between
1100 and 1150 Retainage Receivable, and the same rule applies mirrored, with
one difference: an owner's retainage is held on the work, before tax, so a
payment collects the tax whole. `recognition_pool` gives a pay application's
invoice its sales tax (its liability lines) whole and its income lines less
the retainage held, 1150 left out, so the pool totals the amount due and a
payment prorates it by `applied / amount due` (the Sales Tax report's cash
reading prorates the tax on the same base, so the two agree). A release
application adds the held income of the job's earlier applications, as a
release bill does. A negative retainage this period (a dropped percent) is a
release. A deposit applied to the invoice is a payment like any other: the
deposit itself (Dr bank / Cr 2210, `both`) is a liability on both bases until
it is applied (QuickBooks' reading; the invariant above lists customer
deposits among the liabilities).

**Inventory and cost of goods.** The stock movement an invoice posts (Dr
cost of goods / Cr inventory, under the invoice's own source) is among
the invoice's recognition lines, so on the cash basis the cost of a sale
lands with its income, in the paid proportion; a bill's inventory debit
likewise lands when the bill is paid. The cash Balance Sheet carries the
inventory paid for.

**Multi-currency.** A payment relieves A/R at each invoice's booked rate
(`ar_home_credits`), and the cash pair is built from the home-currency
lines of the invoice's entry at that same share, so the realized-FX line
(`both`) is the difference between cash in and income recognized on the
cash basis too.

**A sales receipt** is an invoice (accrual) and a payment (cash) on one
day; nothing special is written, and both views show its income on its
date. **A deposit straight to income** and a register entry are `both`.

## Profit & Loss and Balance Sheet (step 1)

`profit_loss` and `balance_sheet` take `basis`; it reaches
`_totals_by_account(basis=…)`, which runs its one query with the
execution option. The prior-year comparison and the by-month columns run
each period on the same basis. Nothing else in the response changes:
`net_income` under `basis=accrual` or with the parameter omitted (and the
default setting at `accrual`) is the figure of the day before this
branch, byte for byte. `_pl_section` / `_bs_section` and the two CSVs add
"(cash basis)" to the period line only when the basis is cash. The
accountant's package follows the company's report basis: its Profit &
Loss and Balance Sheet (the statements pack) are cash when the default is
cash, and its README and manifest say which. Its General Ledger and Trial
Balance follow the company basis from step 2a; the agings stay the books as
posted, and the README says so.

The screen: a Basis select (Company default / Accrual / Cash) in the two
statements' toolbar, riding on the address, the saved report and the
PDF / CSV links like Sort by does; the heading says "cash basis" when it
is.

## Tests to write (step 1)

`tests/test_cash_basis.py`, each through the API and the invariant:

- invoice → partial payment → payment: cash P&L shows each payment's
  share on its date, prorated over two income accounts and sales tax;
  A/R zero on the cash Balance Sheet; the accrual P&L unchanged.
- bill → payment: the mirror, A/P zero.
- void after payment: cash income gone, both views balanced.
- prepayment: Unapplied Cash Payment Income, then applied, moved.
- prior-year compare on the cash basis.
- the backfill on a file written without the column (lines tagged,
  cash lines written once; a second run writes nothing).
- `basis` omitted or `accrual`: the flat report byte-equal to the
  response without the parameter; `report_basis=cash` flips the default.
- the migration: one head, the column, the default, downgrade.
- `test_books_balance_invariants.py` and every existing suite green with
  no edits: the accrual reading is the default everywhere.

## Rollout in PRs

**Step 1 (this branch).** The column and migration; the start-up
backfill; accrual tagging by source type; cash recognition on customer
payments (receive, batch, online, apply, void) and bill payments (pay,
void); `report_basis`; `basis` on the P&L and Balance Sheet, their PDF
and CSV, and the screen toggle; `tests/test_cash_basis.py`.

**Step 2a (built).** The Trial Balance, General Ledger, P&L Detail, account
drill-down (an account's and a P&L total's), the grids (by Class, by Job, by
month), Job Profitability and the nonprofit statements (Activities,
Financial Position, Functional Expenses) take `basis`; the accountant's
package's General Ledger and Trial Balance follow the company default.
Each reads the ledger through the same execution option the P&L does, the
balance brought forward on the same basis (so a General Ledger on the cash
basis foots: opening plus activity equals closing, account by account), and
carries `basis` and `basis_note` in its data, "(cash basis)" in its PDF and
CSV period line, and the Basis select on its screen. A drill-down opened
from a statement takes that statement's basis, so its lines add up to the
figure that was clicked. On the cash basis a P&L Detail or drill-down line
is the payment's recognition line: its type, number and name are the
payment's; the invoice it recognizes is not on the line (the open item
below).

**Step 2b.** Edits of a paid document recompute its payments' cash lines;
the journal form's basis choice.

**Step 3.** Edits of a paid document recompute its payments' cash lines;
the journal form's basis choice (see the step 3 PR).

**Step 4.** The Sales Tax Liability report on a basis; retainage on bills
(release cash lines); the payroll decision; invoice-side retainage waits for
#576.

## Decisions (2026-10-26, this branch)

1. **The payment's A/R and A/P line is `both`** and the cash pair carries
   its own A/R or A/P line, so each entry balances in every view and the
   raw sum, the stored balances and every unfiltered reader keep their
   figures. (Keith's note had the payment's offset as `accrual`; the net
   effect in the cash view is the one it described — A/R and its offset
   gone — reached with every entry balanced.)
2. **The ledger reads accrual unless asked**, through one session-level
   filter, instead of a `basis` filter at every query site: a reader that
   forgets gets today's figures, never a double-counted income line.
3. **Tagging by source type** inside `create_journal_entry`, so every
   importer and document path tags alike; a line may still say its own
   basis (the cash pair does).
4. **The backfill runs at start**, idempotent, not inside the migration.
5. **4950 / 6990** for the two unapplied-cash accounts, created on demand.
6. **Apply-later reclass lives in the payment's entry**, on the payment's
   date, not in an entry of its own: `test_apply_payment_credit` holds
   that applying a remainder posts no transaction, the period's cash net
   income is unchanged either way, and the void then has one entry to
   reverse. (The text above first had it dated the later of payment and
   invoice, today when closed; dropped for that reason.)
7. **Edits of a paid document rewrite its payments' cash lines** (step 2b;
   v1 left them alone): the recognition follows the document as it now
   stands, still prorated per payment by the amount applied, except for
   payments on or before the closing date.
8. **Independent proration per payment**; a cent may stay on a line.
   Superseded by the running-total rule (#804, "Partial payments"): the
   settling payment carries the rounding.
9. **Payroll and journal entries are `both`** in step 1.
10. **Review calls (Trent, #572):** 4950 / 6990 show up on any
    overpayment; a prepayment applied later writes its cash lines on the
    payment's date; the account, job and cost-code delete guards count
    every basis, so an account holding only cash lines cannot be deleted;
    the package follows the company basis in this PR.
11. **Screen toggle built** in step 1: the toolbar pattern (`_adjOpts`)
    made it a few lines.

12. **Step 2a calls.** Fund Balances and the one-class P&L page keep
    their step-1 behaviour (accrual; the class P&L page through the
    P&L's own `class_id`); a nonprofit's Fund Balances tracks restricted
    gifts as posted. The other job reports (a job's own page, Job Budget vs
    Actual) stay accrual. `job_profitability` takes `basis` for the report;
    its other callers pass none.
13. **Payroll stays `both` on both bases** (step 4), the QuickBooks rule:
    no liability-payment document exists to carry cash lines, and the
    invariant already counts payroll liabilities as non-cash movements.
14. **Retainage on a bill is left out of the cash recognition** (step 4): the
    net paid is spread over the expense lines, so the cash lines still
    balance on A/P, and the release recognizes the held remainder (above).
    The expense recognized equals the money paid at every step.
15. **The Sales Tax Liability report's cash reading lists the payments**, not
    the invoices: each allocation in the dates takes the invoice's sales,
    taxable amount and tax by `applied / invoice total` (home currency).
    The ledger block (tax posted, paid, balance) reads 2200 on the basis, so
    the two are checked against each other. Each settlement takes the
    invoice's figures by the running-total rule (#804), counting the
    settlements before the dates toward the running total, so a fully paid
    invoice reads its whole tax on the report and on 2200; inside a part
    paid invoice a cent can still differ in a period when the tax line is
    the largest share (`prorate` puts the leftover cent on it).
16. **Retainage releases are recognized on their payment** (v2.31.11): a paid release bill or release pay application
    recognizes the held documents' own lines, not 6990 / 4950, and an
    owner payment collects its tax whole with the net, so held owner
    retainage is neither cash income nor a cash 1150 asset. A deposit stays
    a liability until applied.

## Known limits of step 1

- **Proration drifts when a late fee is added between partial payments.**
  Each payment prorates over the document's recognition lines as they
  stand that day; an edit now rewrites every payment's lines, but a late
  fee posted on the invoice is not an edit and shifts the split for the
  later payment only. The totals stay exact, and since #804 the running
  total trues it up: the next payment takes the lines as they now stand
  less what the earlier payments recognized, so the fee lands on it and
  the invoice still ends equal on both bases.
- **A payment that produces no cash lines, such as a $0 payment, is
  looked at again on every boot.** The backfill finds payments by "has no
  cash line", and nothing marks one that legitimately has none.
- **An invoice the QuickBooks Online ledger import posted counts once, on
  the posting's date, until it is edited.** Its `qbo_ledger` lines are
  `both`, so its income is already in the cash view; a payment on it writes
  no recognition lines (its `both` bank/A-R pair already takes A/R to
  zero), and applying an unapplied remainder to it only moves Unapplied Cash
  Payment Income back to A/R. An edit adopts the posting: the invoice then
  posts as accrual and every payment on it is given its recognition lines
  (step 2b), so its income lands on the payment dates.

- **Sales Tax report, cash basis:** credit memos are not counted, and an
  invoice the QuickBooks Online ledger import posted shows its tax on its
  own date in the ledger block but not in the list (the difference line
  names it). The report has no PDF or CSV, so there is no period line to
  mark; the screen says "cash basis".
- **A release of bill retainage with no link to the bill** (a plain check
  or journal entry against 2050, or a bill entered by hand with retainage
  and no commitment, which has no release route) leaves a debit on 2050 and
  no expense on the cash basis; the cash Balance Sheet then shows 2050 as an
  asset-like debit. Release through the commitment's **Release retainage**.

## Open items (not settled; conservative choice taken)

- **A link from a cash line to its document** is built (step 2b:
  `recognizes_invoice_id` / `recognizes_bill_id`, nullable, no foreign key,
  written by every new cash line; older lines stay NULL). The true-up of
  the final payment's rounding is done (#804, "Partial payments": the
  running-total rule reads what earlier settlements recognized through it).
  Still open: giving the cash-basis P&L Detail a Name column.
- **The accountant's Trial Balance** (settled in step 2a): the package's
  Trial Balance and General Ledger follow the company basis with the
  statements; the agings stay as posted.
- **Opening balances through the wizard** post A/R and A/P as `both`; a
  company that starts on the cash basis with open invoices should enter
  them as invoices, which the wizard already suggests.

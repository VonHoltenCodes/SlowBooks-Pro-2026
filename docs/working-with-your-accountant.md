# Working with your accountant

The question behind this page: *"My accountant reviews our books in
QuickBooks. How do I send them the QBX and apply the QBY when it comes
back?"*

**Short answer: you can't, and with SlowBooks you don't need to.** QBX and
QBY are Intuit's own Accountant's Copy formats. Only QuickBooks Desktop can
make or apply them, so SlowBooks doesn't read or write them. The Accountant's
Copy exists so you and your accountant can work on separate copies at the
same time and merge their changes back. SlowBooks gives you the same outcome
its own way. There are four routes, from the most direct down.

## 1. Your accountant signs in (Server Edition)

Server Edition is the same download, running as a server so everyone uses a
browser ([server-edition.md](server-edition.md)). **Invite your accountant**
under **Settings → Users**: the sign-in gets the **Accountant** role, a
one-time password you send once with the address, and an end date if the
engagement has one; they choose their own password at the first sign-in.
What the role can do:

- they read everything, the reports, every list except Employees, and the
  **Audit Log** included, so they can see exactly what moved and who moved
  it;
- they post **journal entries** and void the ones they posted, and move
  lines between accounts with Reports → **Reclassify Transactions** (the
  lines parked on an account for a period, ticked and moved to the account
  they belong on, and a class, as one audited change naming every line),
  and nothing else: an invoice, a bill, a payment, a bank entry, a setting
  or a user is refused to them with a sentence that says so;
- payroll and HR stay the administrator's, reads included, as they are for
  every role but admin.

Their entries post as **adjusting entries**: marked on the entry and on the
Journal Entries list (where *Show: Adjusting entries only* lists them alone),
with the name of whoever posted each one. Reports → **Adjusting Journal
Entries** lists them for a period, as PDF or CSV, and the Profit & Loss and
Trial Balance can be run *without* them, for a before-and-after view.

Set a **Closing Date** (Settings → Closing Date, with its own password) once
they have signed off a period, the way QuickBooks' closing date works: a
change dated into the closed period then takes the password, and each use
is written to the audit log. Reports → **Closing Date Exceptions** lists who
used the password, when, and every row changed, before and after, as PDF or
CSV.

**An accountant's own software or agent** gets an **API token** rather than
a sign-in: an administrator makes one under **Settings → API Tokens** with
the Accountant or Read-only role. A token is never an administrator (one
made as an administrator before 2.29.0 is refused since 2.31.2 and flagged in
the list; revoke it and make a new one). It wears the same limits as the
role, every change it makes is attributed to `token:<label>` in the audit
log, and on a server with several companies its requests name the company
with `X-SlowBooks-Company`. Details in [server-edition.md](server-edition.md),
"API tokens".

If your accountant is not on your network, give them a way onto it rather
than opening the server: a VPN (Tailscale is the simplest) that puts their
computer on the office network while they need it, or a tunnel with an
access policy in front, both described in [server-edition.md](server-edition.md),
"Reaching it from outside the office". Server Edition is plain HTTP, so it
must never be port-forwarded. Books that should live off-site belong on a
cloud server behind HTTPS ([cloud-hosting.md](cloud-hosting.md)).

## 2. Send them the reports

If they would rather not sign in, send:

- Reports → **Financial Statements Pack**: the Profit & Loss, Balance Sheet
  and Trial Balance in one PDF;
- Reports → **General Ledger** → Save CSV: every line, by account;
- Reports → **A/R Aging** and **A/P Aging** → Save CSV: who owes, and who
  is owed, by age;
- the bank reconciliation reports, as needed;
- or all of it at once: Reports → **Accountant's Package**, one zip for the
  period with the Financial Statements Pack, the General Ledger and Trial
  Balance CSVs, both agings, each reconciliation completed in the period, the
  Adjusting Journal Entries and Closing Date Exceptions reports, and the audit
  log since the closing date.

Then enter their adjustments yourself under **Journal Entries**, with the
**Adjusting entry** box ticked, so they show on the Adjusting Journal Entries
report like anyone else's.

## 3. They insist on QuickBooks Desktop

Interoperability → **Export to IIF** carries the whole ledger into
QuickBooks Desktop: the lists, invoices, sales receipts, payments, bills,
deposits, checks, card charges, transfers, bill payments and journal entries
([migrate-from-quickbooks.md](migrate-from-quickbooks.md) describes the
file). Their adjustments come back two ways: an IIF with GENERAL JOURNAL
blocks imports them as journal entries (tick *This is an accountant's file*
to mark them adjusting), and QuickBooks Desktop's Transaction Detail by
Account report, filtered to Transaction Type = Journal, imports under
**Adjusting entries only**, which refuses the file whole if it holds anything
else, so a whole-ledger upload can never double the books. A second upload
of either posts nothing twice.

## 4. SlowBooks' own Accountant's Copy (no server at all)

The most direct answer to "send the QBX, apply the QBY". SlowBooks is free,
so your accountant simply installs it.

1. **Send to accountant** (Companies, an administrator): pick a dividing
   date and type the company's name to confirm. SlowBooks copies the books
   into a file encrypted under a passphrase it shows once (with a Copy
   button of its own), with your sign-ins and secrets left out. Send the
   file one way and the passphrase another.
2. **Open accountant's copy** (your accountant, their own SlowBooks Pro,
   Companies): the file and the passphrase. The copy becomes a company file
   of its own on their picker, opened with a password they choose, and it
   takes journal entries dated on or before the dividing date and nothing
   else; a banner on every page says so.
3. **Send changes back** (the copy, Companies): one small file holding just
   the entries they posted, signed by the copy.
4. **Apply accountant's changes** (you, Companies): every entry is shown
   first, then the new ones post as adjusting entries in the accountant's
   name, on the Adjusting Journal Entries report like any other. A file
   applied twice posts nothing twice; a file from another company, or
   altered on the way, is refused whole; an entry dated after the dividing
   date, or on an account you don't have, is refused with the reason.

You keep working after the dividing date the whole time. The reports and
the Accountant's Package above still work beside it, and an accountant who
would rather sign in than open a copy gets the Accountant role.

A server install on PostgreSQL cannot make a copy (the copy is made from a
company file); it offers the sign-in and the package instead.

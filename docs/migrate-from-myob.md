# Migrating from MYOB

SlowBooks imports MYOB **exports**, not company files — `.MYO` (AccountRight
Classic) and `.myox` or an online file (current AccountRight) are MYOB's own
databases and only MYOB opens them. The good news: three quick exports from
inside MYOB are all the importer needs, and the dry-run verifies everything
against your trial balance before a single record is written.

Works with MYOB Business AccountRight Plus and Premier (the desktop app or
the browser), MYOB Business Lite and Pro, and exports already made from
AccountRight Classic (v19 and earlier, read-only since February 2026).
Tab-separated `.TXT`, comma `.CSV` and Excel `.xlsx` exports all import,
singly or together in a `.zip`.

---

## Step 1 — Export the three files from MYOB

### AccountRight (the desktop app)

1. **Accounts list** — `File → Import/Export Assistant → Export data →
   Next`, choose **Accounts list**, tab-separated with the field names in
   the first row, every field, and save it as `Accounts.TXT`.
2. **Journals** — the same assistant, data type **Transaction journals**,
   every journal, with the dates covering everything you want to bring
   across (e.g. 01/01/2000 to today). Save as `Journal.TXT`. (**General
   journal entries** alone exports only the general-journal transactions,
   not the sales, purchases and banking ones.)
   *If your version exports one file per journal type (Sales, Purchases,
   Disbursements …), upload them together — and never together with an
   "All" export, or every transaction counts twice (the dry-run's
   trial-balance check will catch it, loudly). Journal types with no
   transactions simply won't export; upload the ones you have.*
3. **Trial balance** — `Reports → Index to Reports → Accounts → Trial
   Balance`, set the same period end, display it, then `File → Export →
   CSV` (or tab-separated text). Save as `TrialBalance.TXT`.

AccountRight Classic (v19) had these under `File → Export Data → Accounts →
Account Information` and `… → Transaction Journals`; an export made there
imports the same way.

### MYOB Business (browser)

1. **Chart of accounts** — the settings menu (⚙) → **Import and export
   data** → **Export** → **Chart of accounts** (a tab-separated `.TXT`).
2. **Journals** — **Reporting → Reports → General ledger [Detail]**, the
   full date range, **Export → Excel**. The browser exports reports to Excel
   or PDF only; upload the `.xlsx` as it is (SlowBooks reads MYOB's
   day-first dates from it).
3. **Trial balance** — **Reporting → Reports → Trial balance**, the same
   period end, **Export → Excel**.

**Filenames matter**: keep "account"/"chart", "journal"/"ledger", and
"trial" in the names — the importer recognizes each file's role by
filename.

## Step 2 — Dry-run in SlowBooks

Open **Migrate Data** in the sidebar, choose **MYOB**, select all three
files, and click **Dry Run**. Nothing is written; you'll get a report:

- every reconstructed journal must balance,
- every GL line must reference an account in the chart,
- GL account totals must match the trial balance.

Fix anything it flags (usually a truncated date range or a missing
journal type) and re-run until it passes.

## Step 3 — Import

The **Import** button unlocks once the dry-run passes. Accounts are
created (existing same-named accounts are reused), every journal posts
through the standard double-entry engine, and the Opening Balances
wizard is marked ready.

## Opening balances

Opening balances are entered as account setup in MYOB, so they never
appear in a journal export. When the dry-run sees trial-balance
differences that **net to zero** (MYOB offsets them through the
Historical Balancing account), it treats them as opening balances and
the import posts one balanced opening journal dated the day before your
earliest transaction. Differences that don't net to zero remain errors —
that means journals are actually missing (usually a truncated date
range).

## Quirks handled automatically

- Tab-separated classic exports and comma CSVs (detected per file)
- `dd/mm/yyyy` dates (MYOB's AU/NZ convention)
- Non-postable **header accounts** (skipped)
- Journal lines grouped by **ID No.**
- Lines carrying only an **account number** (dashed `1-1100` or flat)
- Contra lines expressed as a **negative amount** in one column
- `($1,234.00)`-style accounting negatives
- Report-style trial balance exports (address block and period header
  skipped; YTD columns preferred over single-month movement)
- Reused journal IDs (MYOB stamps every electronic payment `EP`) —
  grouped by ID + date + memo
- Duplicate account names under different numbers (imported as
  `Name (code)` so both survive)

All of the above were validated against MYOB's own **Clearwater** sample
company: 101 accounts and 328 journals import with the ledger balanced
to the cent and every account reconciling exactly to MYOB's trial
balance.

If your export uses a column heading the importer doesn't recognize, the
dry-run lists it — send the header row (only the header, no figures) to
support@slowbookspro.com and it's a one-line alias fix.

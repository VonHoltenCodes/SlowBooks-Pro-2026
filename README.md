# Slowbooks Pro 2026

**A personal bookkeeping application "decompiled" from the ashes of QuickBooks 2003 Pro.**

Free and open source. Runs on Windows, macOS, and Linux. No Intuit activation servers required.

**Get started:** `docker compose up` — see **[INSTALL.md](INSTALL.md)** for all install options.

![Slowbooks Pro 2026 — Splash Screen](screenshots/splash.png)

---

## The Story

I ran QuickBooks 2003 Pro for 14 years for side business invoicing and bookkeeping. Then the hard drive died. Intuit's activation servers have been dead since ~2017, so the software can't be reinstalled. The license I paid for is worthless.

So I built my own replacement. I transferred all my data from the old .QBW file using IIF export/import.

The codebase is annotated with "decompilation" comments referencing `QBW32.EXE` offsets, Btrieve table layouts, and MFC class names — a tribute to the software that served me well for 14 years before its maker decided it should stop working.

**This is a clean-room reimplementation.** No Intuit source code was available or used.

---

## Features

### Invoicing & Payments (Accounts Receivable)
- **Invoices** — Create, edit, duplicate, void, mark as sent, email as PDF. Auto-numbering, auto due-date from terms, dynamic line items with running totals. Print/PDF generation via WeasyPrint. Inline customer creation from invoice form
- **Estimates** — Full estimate workflow with convert-to-invoice (deep-copies all fields and line items). Inline customer creation from estimate form
- **Payments** — Record payments with allocation across multiple invoices. Auto-updates invoice balances and status (draft/sent/partial/paid). Void payments with reversing journal entries
- **Recurring Invoices** — Schedule automatic invoice generation (weekly/monthly/quarterly/yearly) with manual "Generate Now" or cron script
- **Batch Payments** — Apply payments to multiple invoices across multiple customers in a single transaction
- **Credit Memos** — Issue credits against customers, apply to invoices to reduce balance due. Proper reversing journal entries
- **Quick Entry Mode** — Batch invoice entry for paper invoice backlog. Save & Next (Ctrl+Enter) with running log

![Invoices with IRS Pub 583 Mock Data](screenshots/invoices.png)

### Accounts Payable
- **Purchase Orders** — Non-posting documents to vendors with auto-numbering, convert-to-bill workflow
- **Bills** — Enter vendor bills (AP mirror of invoices). Track payables with status progression (draft/unpaid/partial/paid/void). Vendor default expense account pre-fill (account resolution: explicit → item → vendor default → global fallback)
- **Bill Payments** — Pay vendor bills with allocation. Journal: DR AP, CR Bank
- **AP Aging Report** — Outstanding payables grouped by vendor with 30/60/90 day buckets

### Double-Entry Accounting
- **Manual Journal Entries** — Full CRUD for manual journal entries with dynamic line rows, running debit/credit totals, balance indicator, and void with reversing entries
- **Auto Journal Entries** — Every invoice, payment, bill, and payroll run automatically creates balanced journal entries. Void creates reversing entries
- **Chart of Accounts** — 39+ seeded accounts (Contractor template), 6 account types (asset, liability, equity, income, COGS, expense)
- **Closing Date Enforcement** — Prevent modifications to transactions before a configurable closing date with optional password protection
- **Audit Log** — Automatic logging of all create/update/delete operations with old/new value tracking via SQLAlchemy event hooks
- **Account Balances** — Updated in real-time as transactions post

### Payroll
- **Employees** — Full employee records with pay type (hourly/salary), pay rate, filing status, allowances
- **Pay Runs** — Create pay runs with automatic withholding calculations: Federal (progressive brackets), State (5% flat), Social Security (6.2%), Medicare (1.45%)
- **Process Payroll** — Creates journal entries: DR Wage Expense, CR Federal Withholding, CR State Withholding, CR SS Payable, CR Medicare Payable, CR Bank
- Tax calculations are approximate — disclaimer included. Verify with a tax professional

### Banking
- **Bank Accounts** — Register view with deposits and withdrawals
- **Check Register** — Filtered bank transaction view with running balance, payment/deposit columns, sorted by date
- **Make Deposits** — Move funds from Undeposited Funds to a bank account. Select pending payments, choose target account, create deposit
- **Credit Card Charges** — Enter credit card charges as expenses (DR Expense, CR Credit Card Payable). Dedicated charge entry form with vendor, amount, and expense category
- **Check Printing** — Generate check PDFs in standard 3-per-page format (stub/stub/check) with payee, amount in words, memo, and signature line
- **Bank Reconciliation** — Full workflow: enter statement balance, toggle cleared items, validate difference = $0, complete
- **OFX/QFX Import** — Import bank transactions from OFX/QFX files with FITID dedup, preview before import, auto-match by amount/date

### Reports & Tax
- **QuickBooks-style period selector** — All reports support preset periods (This Month, This Quarter, This/Last Year, Year to Date, Custom Date) with live refresh
- **Profit & Loss** — Income vs expenses for any date range
- **Balance Sheet** — Assets, liabilities, and equity as of any date
- **A/R Aging** — Outstanding receivables grouped by customer with 30/60/90 day buckets
- **A/P Aging** — Outstanding payables grouped by vendor with 30/60/90 day buckets
- **Sales Tax** — Tax collected by invoice with taxable/non-taxable breakdowns. Pay Sales Tax feature records payments to government (DR Sales Tax Payable, CR Bank)
- **General Ledger** — All journal entries grouped by account with debit/credit totals
- **Income by Customer** — Sales totals per customer with invoice counts
- **Customer Statements** — PDF statement with invoice/payment history and running balance
- **Schedule C (Tax)** — Generate Schedule C data from P&L with configurable account-to-tax-line mappings. Export as CSV

### Dashboard
- Company Snapshot with Total Receivables, Overdue Invoices, Active Customers, Total Payables
- **AR Aging Bar Chart** — Color-coded stacked bar (Current/30/60/90+ days)
- **Monthly Revenue Trend** — Bar chart showing last 12 months of invoiced revenue
- Recent invoices and payments tables
- Bank balances at a glance

![Dashboard — Light Mode](screenshots/dashboard-light.png)

![Dashboard — Dark Mode](screenshots/dashboard-dark.png)

### Analytics (Phase 9)
Real-time business intelligence layer that sits on top of the accounting engine. Powered by `AnalyticsEngine` (`app/services/analytics.py`) with 8 aggregation methods and 7 REST endpoints at `/api/analytics/*`. **Fully integrated inline** into the main SPA as a hash-routed page — no separate shell, no full page reload. Click **Analytics** in the sidebar (under Accounting → Reports → Analytics → Tax Reports) to land on `#/analytics`.

**Metrics computed:**
- **Revenue by Customer** — Paid revenue per customer for the selected period, ranked high-to-low
- **12-Month Revenue Trend** — Monthly paid-revenue history with proper calendar-month bucketing
- **Expenses by Category** — Paid-bill expenses grouped by expense account number
- **A/R Aging** — Open invoice balances bucketed Current / 30 / 60 / 90+ days old, using `invoices.balance_due` so partial payments don't double-count. Table is sorted worst-offender first and includes a TOTAL row.
- **A/P Aging** — Open bill balances bucketed Current / 30 / 60 / 90+ days old (same treatment as A/R)
- **DSO (Days Sales Outstanding)** — `(open A/R balance ÷ last-30-day paid revenue) × 30`
- **90-Day Cash Forecast** — 14 weekly cumulative buckets of expected A/R collections vs A/P payments due on-or-before each cutoff. Net column color-coded green/red.
- **Customer Profitability** — Lifetime paid revenue per customer (first pass; COGS attribution on the roadmap)

**Dashboard UI (`#/analytics`, inline SPA page):**
- **4 KPI cards** — Revenue, Expenses, DSO, Margin%
- **Chart.js visualizations** (self-hosted 206 KB UMD bundle at `/static/js/chart.umd.js` — no CDN, LAN-deployable):
  - Revenue trend — 12-month line chart with filled area + hover tooltips
  - Expenses by category — doughnut chart with legend
  - A/R aging — horizontal stacked bar chart (one bar per customer, stacks = current/30/60/90)
  - A/P aging — horizontal stacked bar chart (one bar per vendor)
  - Cash forecast — dual-line (collections vs payments) + net bar chart overlay
- **Detail tables** under every chart with sort-by-total-descending and TOTAL footer rows
- **Period selector** — Dropdown for Month / Quarter / Year to Date; re-fetches dashboard + re-builds all charts instantly
- **Refresh button** — Re-fetch without navigating away
- **Export CSV** — Downloads flat CSV of the current snapshot at `/api/analytics/export.csv?period=...`
- **Export PDF** — Downloads a print-optimized PDF rendered by WeasyPrint (same dep used for invoice PDFs)
- **Theme-aware** — All Chart.js text/grid colors read from `<html data-theme>` so the charts flip with the main SPA theme toggle

**Date-range filtering (all endpoints):**
```
?period=month|quarter|year          (also accepts mtd/qtd/ytd)
?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD    (explicit override)
```
Unknown period names and missing params fall back to month-to-date. Explicit dates take precedence over named periods.

**CSV export:**
```bash
curl 'http://localhost:3001/api/analytics/export.csv?period=year' -o analytics.csv
```
Flat CSV with columns `(section, key, subkey, value)` covering 9 sections: period, revenue_by_customer, revenue_trend, expenses_by_category, ar_aging, ap_aging, dso, cash_forecast, customer_profit. Drops straight into Excel / Google Sheets / any BI tool.

#### AI Insights (Phase 9.5)
An optional LLM layer sits on top of the analytics snapshot and produces a compact **3 observations / 3 risks / 3 recommendations** executive brief. Nothing is sent until you click the **AI Insights** button — the feature is zero-cost by default.

**Seven providers supported out of the box** (verified April 2026):

| Provider | Wire format | Default model | Free tier |
|---|---|---|---|
| **xAI Grok** | OpenAI-compat | `grok-4.1-fast` | $25 signup credit |
| **Groq (LPU Cloud)** | OpenAI-compat | `llama-3.3-70b-versatile` | Generous free tier, no card |
| **Cloudflare Workers AI** | OpenAI-compat | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | 10k neurons/day, no card |
| **Cloudflare Worker Gateway** (self-hosted) | OpenAI-compat | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | Same 10k neurons/day — **your keys stay in *your* Cloudflare account** |
| **Anthropic Claude** | `/v1/messages` | `claude-sonnet-4-6` | Paid only |
| **OpenAI** | `/v1/chat/completions` | `gpt-5.4-mini` | Paid only |
| **Google Gemini** | `generateContent` | `gemini-2.5-flash` | Free Flash tier via AI Studio |

Each provider's model string is user-editable from the **⚙ AI** button in the analytics header, so renames ("gemini-2.5-flash" → "gemini-3.0-nano" or whatever the vendors ship next quarter) are a UI change, not a code change. Cloudflare gets an extra field for your account ID since its endpoint is account-scoped. The dedicated **Cloudflare Worker Gateway** provider adds a second field for your Worker URL — see the self-hosted gateway section below.

**Settings encryption.** API keys are stored in the `settings` table under `ai_api_key`, encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256) via `app/services/crypto.py`. Ciphertext rows carry the prefix `fernet:v1:` so legacy plaintext rows are detected and migrated gracefully. The master key is resolved in priority order:

1. `SETTINGS_ENCRYPTION_KEY` environment variable (ops-preferred)
2. `.slowbooks-master.key` file next to the repo (zero-config default; auto-created at 0600)
3. Fresh generation on first run (logged as a warning)

**Never commit `.slowbooks-master.key`** — it is in `.gitignore`. Losing it means losing every encrypted secret.

`GET /api/analytics/ai-config` returns `{provider, model, cloudflare_account_id, worker_url, has_api_key, api_key_encrypted, providers}` — the raw key is **never** in the response body. `PUT /api/analytics/ai-config` accepts a Pydantic `AIConfigUpdate` model — `{provider, model, cloudflare_account_id, worker_url, api_key}` — so malformed payloads are rejected with a 422 before they reach the service layer. An empty/missing `api_key` is interpreted as "keep the existing encrypted value", so re-saving the provider won't clobber the stored key.

**Endpoints:**
- `GET  /api/analytics/ai-config` — read display config (no secrets)
- `PUT  /api/analytics/ai-config` — update provider/model/key/account_id (key is encrypted on save)
- `POST /api/analytics/ai-config/test` — one-word smoke test against the configured provider
- `POST /api/analytics/ai-insights?period=month|quarter|year[&force=true]` — run the full dashboard analysis; results are cached in-process for 10 minutes per `(provider, model, period)`, unless `force=true`

All calls go through a **hardened** `httpx.Client` with a 60-second timeout, `verify=True` (TLS cert validation), `follow_redirects=False` (no sneaky 302-to-metadata tricks), and a minimal `User-Agent`. Error messages redact the API key before raising, so logs and 502 responses never leak your secret.

**Security hardening (April 2026 external audit pass):**
- **SSRF guard #1** — Cloudflare account IDs must match `^[a-f0-9]{32}$` (regex enforced both at request-build time and in `PUT /ai-config`)
- **SSRF guard #2** — `validate_worker_url()` in `app/services/ai_service.py` rejects plain `http://`, embedded credentials, localhost, `127.0.0.1`, all RFC1918 private ranges, link-local (`169.254.x.x` including the AWS metadata endpoint), multicast, and reserved blocks. URLs are capped at 2048 chars
- **MITM protection** — `verify=True`, `follow_redirects=False`, HTTPS-only enforcement on the Worker URL
- **CSV formula injection** — `_csv_safe()` in `export_csv()` prefixes any user-controlled cell starting with `=`, `+`, `-`, `@`, `\t`, or `\r` with a leading apostrophe before writing to CSV, neutralizing Excel/Sheets formula execution
- **Request schema validation** — `PUT /ai-config` uses a Pydantic `AIConfigUpdate` model instead of a raw `dict`, so malformed payloads are rejected with a 422 before they reach the service layer
- **Constant-time secret compare** — the shared-secret auth in `cloudflare/worker.js` uses a byte-wise constant-time compare instead of `===`, closing timing side-channels

#### Self-hosted Cloudflare Worker Gateway (per-LAN-owner)

For installations where you don't want **any** AI credentials stored inside Slowbooks (even encrypted), the `cloudflare/` directory ships a minimal Worker that you deploy to **your own** Cloudflare account. Slowbooks only ever holds a shared secret scoped to your one Worker, so dumping the SQLite file doesn't expose enough to talk to Workers AI as you.

**What it buys you:**
- **Your keys never touch Slowbooks' DB.** Workers AI is accessed via the `env.AI.run()` binding — no Cloudflare API token is stored anywhere, in Slowbooks or in the Worker source
- **Per-installation isolation.** Every LAN owner installs their own Worker in their own Cloudflare account. One compromised install can't reach another; one abused install can't burn someone else's quota
- **Free tier friendly.** Cloudflare gives every account 10,000 neurons/day for free — plenty for hundreds of AI Insights runs and tool-calling Q&A sessions
- **Bearer-token auth.** Slowbooks sends `Authorization: Bearer <shared-secret>`; the Worker compares it against `env.AUTH_TOKEN` in constant time and rejects anything else with a 401
- **OpenAI wire format.** The Worker translates Workers AI's native response into OpenAI-shaped JSON (including `tool_calls`) so Slowbooks' existing OpenAI-compat code path works unchanged

**5-minute setup:** `wrangler login` → `openssl rand -hex 32` → `wrangler secret put AUTH_TOKEN` → `wrangler deploy` → paste the Worker URL + shared secret into Slowbooks **⚙ AI** → Provider: **Cloudflare Worker Gateway (self-hosted)**. Full step-by-step in **[cloudflare/README.md](cloudflare/README.md)**.

**What it does *not* protect against:** a compromised Slowbooks install still has the shared secret, so it can still invoke *your* Worker — rotate the secret if you suspect compromise; abnormal Worker traffic shows up in the Cloudflare dashboard immediately.

#### AI Q&A Assistant — Tool Calling (Phase 9.5b)

Beyond the static insights brief, the analytics page has a **chat assistant** powered by LLM function/tool calling. Users ask arbitrary questions about their books and the LLM autonomously calls read-only query tools to answer them:

> *"How much did I spend at Jack in the Box in 2025?"*
> *"What are my unpaid invoices from ABC Corp?"*
> *"Show me my top 5 customers by revenue"*
> *"What were my HSA-tagged payments in 2024?"*

The chat panel sits directly under the AI Insights card and supports multi-turn conversations with visible tool-call history (click "Used N tools" to expand and see which queries the LLM made).

**16 read-only tools** are registered in `app/services/ai_tools.py`:

| Tool | Purpose |
|---|---|
| `search_bills` | Filter bills by vendor name, date range, status |
| `search_invoices` | Filter invoices by customer name, date range, status |
| `search_transactions` | Search journal entries by memo/reference |
| `list_vendors` | List vendors (with name filter) |
| `list_customers` | List customers (with name filter) |
| `list_accounts` | List chart of accounts (with type filter) |
| `get_account_balance` | Get current balance of a specific account |
| `get_pl_summary` | Current P&L (income, expense, net) |
| `get_balance_sheet` | Current balance sheet (assets, liabilities, equity) |
| `get_tax_summary` | Sales tax collected + expenses by category |
| `get_sales_by_customer` | Total revenue grouped by customer |
| `get_expenses_by_category` | Total expenses grouped by account |
| `get_aging_report` | A/R and A/P aging by bucket (current/30/60/90) |
| `get_current_date` | Server date for "today" / relative-date queries |
| `search_payments` | Customer payments by customer/date |
| `search_bill_payments` | Vendor bill payments by vendor/date |

Every tool is **strictly read-only**: it builds `db.query()` calls that cannot insert, update, or delete. The tool loop runs up to 8 iterations per question — if the LLM hasn't produced a final answer after 8 tool calls, it returns "Max tool calls reached" rather than looping forever.

**Wire formats:** `call_with_tools()` in `app/services/ai_service.py` supports all three tool-calling formats used by the 7 providers:
- **OpenAI-compat** (Grok, Groq, Cloudflare, OpenAI): `choices[0].message.tool_calls`
- **Anthropic** (`/v1/messages`): `content[].type=tool_use`
- **Gemini** (`generateContent`): `candidates[0].content.parts[].functionCall`

**Endpoint:**
- `POST /api/analytics/ai-query?question=...` — ask a question; returns `{provider, model, final_response, tool_calls, call_count, success}`

Results are not cached (each question may be distinct). If you ask the same question twice you'll run the loop twice.

**Test coverage:** 92 pytest tests cover AI security, analytics, auth flow, CORS, CSV safety, attachments, IIF import, invoice posting/editing, reporting, and rate limiting — all running in under 6 seconds with zero network dependencies.

**PDF export:**
```bash
curl 'http://localhost:3001/api/analytics/export.pdf?period=year' -o analytics.pdf
```
Print-ready PDF via WeasyPrint + Jinja2 (`app/templates/analytics_pdf.html` → `app/services/pdf_service.py::generate_analytics_pdf`). Same template handles all periods. Output: letter-size, page numbers in footer, KPI strip + every table the UI shows. ~18 KB typical for a small business; renders in WeasyPrint in well under a second.

**Performance:** `GET /api/analytics/dashboard` issues exactly **10 SQL queries** regardless of dataset size — every method is single-query (or at most two) with no N+1 relationship loads. Measured on SQLite with 3,000 invoices + 1,500 bills: **~26 ms** engine / ~40 ms full HTTP round-trip; with 8,000 invoices + 4,000 bills: **~50 ms**. The `period` parameter adds zero extra queries. PDF export renders end-to-end in ~100 ms on the medium dataset.

**Tested** with 25-assertion backend regression + 42-assertion headless UI smoke test that loads the real Chart.js UMD bundle in a `vm` context and confirms all 5 chart instances initialize with the expected dataset shapes (1 line chart with 12 points, 1 doughnut with N slices, 2 stacked bars with 4 datasets each, 1 combo chart with 3 datasets).

Quick smoke test once the app is running:
```bash
curl http://localhost:3001/api/analytics/dashboard
curl http://localhost:3001/api/analytics/dashboard?period=year
curl http://localhost:3001/api/analytics/export.csv > snapshot.csv
curl http://localhost:3001/api/analytics/export.pdf > snapshot.pdf
```

### Online Payments
- **Stripe Checkout** — Accept online payments via Stripe's hosted checkout page. Customers click "Pay Online" in emailed invoices, pay on Stripe, and the payment auto-records with journal entries (DR Undeposited Funds, CR A/R)
- **Public Payment Page** — Standalone `/pay/{token}` page (no login required) shows invoice summary with "Pay with Stripe" button. Supports light/dark mode
- **Copy Payment Link** — One-click copy of the public payment URL from the invoice view modal
- **Webhook Handler** — Idempotent Stripe webhook processes `checkout.session.completed` events with signature verification
- **Setup Guide** — See **[SETUP_STRIPE.md](SETUP_STRIPE.md)**

### QuickBooks Online Integration
- **OAuth 2.0** — Connect to QuickBooks Online via Intuit's OAuth Authorization Code flow with automatic token refresh
- **Import from QBO** — Pull accounts, customers, vendors, items, invoices, and payments from QBO with dependency-ordered import and duplicate detection
- **Export to QBO** — Push Slowbooks data to QBO with entity type mapping and ID tracking
- **ID Mapping** — `qbo_mappings` table tracks QBO ID ↔ Slowbooks ID per entity for dedup and re-sync
- **Setup Guide** — See **[SETUP_QBO.md](SETUP_QBO.md)**

![QuickBooks Online Integration](screenshots/qbo-integration.png)

### Communication & Export
- **Invoice Email** — Send invoices as PDF attachments via SMTP with configurable email settings. Includes "Pay Online" button when Stripe is enabled
- **CSV Import/Export** — Import/export customers, vendors, items, invoices, and chart of accounts as CSV
- **Print Preview** — Browser print dialog for invoices and estimates via dedicated HTML preview endpoints. Native OS print dialog with "Save as PDF" option
- **Print-Optimized PDF** — Enhanced invoice PDF template with company logo support
- **IIF Import/Export** — Full QuickBooks 2003 Pro interoperability (see below)

### Security & Authentication (Phase 9.7)
- **Single-user authentication** — Password-protected access with setup wizard on first run. Session-based auth with secure cookie (`strict` SameSite, 30-day TTL)
- **Security headers** — X-Content-Type-Options, X-Frame-Options (DENY), Referrer-Policy, Permissions-Policy on all responses
- **CORS lockdown** — No wildcard origins; defaults to localhost, configurable via `CORS_ALLOW_ORIGINS` env var
- **Rate limiting** — Configurable via slowapi; disabled in tests, toggle via `RATE_LIMIT_ENABLED`
- **Path traversal protection** — Backup download/restore and attachment uploads validated with `is_relative_to()`
- **Sensitive key filtering** — Password hashes and session secrets never returned from the settings API
- **Atomic secret writes** — Session key and encryption master key use `mkstemp` + `os.replace` to prevent race conditions
- **Encrypted API keys** — AI provider keys encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)
- **Non-root Docker** — Container runs as `slowbooks` user (UID 1000), not root
- **Pinned dependencies** — All `requirements.txt` entries have upper-bound version caps

### System & Administration
- **Dark Mode** — Toggle between QB2003 Blue theme and dark mode (Alt+D or toolbar button). Persists in localStorage
- **Backup/Restore** — Create and download PostgreSQL backups from the settings page
- **Multi-Company** — Support for multiple company databases, switchable from UI
- **Global Search** — Unified server-side search across customers, vendors, items, invoices, estimates, and payments
- **Attachments** — Upload files (PDF, images) to invoices, bills, and other entities with MIME type and extension validation
- **Bank Rules** — Auto-categorize imported bank transactions with pattern-matching rules
- **Budgets** — Create and track budgets by account and period with actual-vs-budget comparison
- **Email Templates** — Customizable email templates for invoices, estimates, and statements

### UI
- Authentic QB2003 "Default Blue" skin with navy/gold color palette (+ dark mode)
- Splash screen with build info and decompilation provenance
- Windows XP-era toolbar, sidebar navigator with icons, status bar
- Keyboard shortcuts: `Alt+N` (new invoice), `Alt+P` (payment), `Alt+Q` (quick entry), `Alt+H` (home), `Alt+D` (dark mode), `Ctrl+S` (save modal form), `Ctrl+K` (search), `Escape` (close modal)
- No frameworks — vanilla HTML/CSS/JS single-page app
- 35+ SPA routes, 34 sidebar nav links

### QuickBooks 2003 Pro Interoperability
- **IIF Export** — Export all Slowbooks data (accounts, customers, vendors, items, invoices, payments, estimates) as .iif files importable into QB2003 via File > Utilities > Import > IIF Files
- **IIF Import** — Parse and import .iif files exported from QB2003 with duplicate detection and per-row error handling
- **Validation** — Pre-flight validation of .iif files before import (checks structure, account types, balanced transactions)
- **Date Range Filtering** — Export invoices and payments for specific date ranges
- **Round-Trip Safe** — Export from Slowbooks, re-import into Slowbooks — deduplication prevents double-entry

### Utilities
- **Backup Script** — `scripts/backup.sh` — pg_dump with gzip compression, keeps last 30 backups
- **Recurring Invoice Cron** — `scripts/run_recurring.py` — Standalone script for generating due recurring invoices
- **IRS Mock Data** — `scripts/seed_irs_mock_data.py` — Seeds realistic test data from IRS Publication 583 (Henry Brown's Auto Body Shop: 8 customers, 13 vendors, 10 invoices, 5 payments, 3 estimates)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12 + FastAPI (43 routers, 200+ routes) |
| Database | PostgreSQL 16 / SQLite + SQLAlchemy 2.0 |
| Migrations | Alembic |
| Frontend | Vanilla HTML/CSS/JS (no framework) + self-hosted Chart.js 4.4.6 for analytics |
| PDF | WeasyPrint 60.2 + Jinja2 |
| Bank Import | ofxparse (OFX/QFX) |
| Payments | Stripe Checkout (hosted) |
| QBO Sync | python-quickbooks + intuit-oauth (OAuth 2.0) |
| Port | 3001 |

---

## Quick Start

### Docker (Windows, macOS, Linux)

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
docker compose up
```

Open **http://localhost:3001**. That's it — PostgreSQL, migrations, and seed data are handled automatically.

### Native Install (Linux)

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
pip install -r requirements.txt

# Create database
sudo -u postgres psql -c "CREATE USER bookkeeper WITH PASSWORD 'bookkeeper'"
sudo -u postgres psql -c "CREATE DATABASE bookkeeper OWNER bookkeeper"

cp .env.example .env
alembic upgrade head
python3 scripts/seed_database.py
python3 run.py
```

Open **http://localhost:3001**.

See **[INSTALL.md](INSTALL.md)** for detailed instructions, macOS native install, demo data, and troubleshooting.

### Backup

```bash
./scripts/backup.sh
# Backs up to ~/bookkeeper-backups/ with gzip compression
# Keeps the 30 most recent backups
```

---

## Project Structure

```
SlowBooks-Pro-2026/
├── .env.example              # Environment config template
├── requirements.txt          # Python dependencies (14 packages)
├── run.py                    # Uvicorn entry point (port 3001)
├── alembic.ini               # Alembic config
├── alembic/                  # Database migrations
├── app/
│   ├── main.py               # FastAPI app + 43 routers (200+ routes)
│   ├── config.py             # Environment-based settings (CORS, origins)
│   ├── database.py           # SQLAlchemy engine + session
│   ├── models/               # 25 model modules (40 tables)
│   │   ├── accounts.py       # Chart of Accounts (self-referencing)
│   │   ├── contacts.py       # Customers + Vendors
│   │   ├── items.py          # Products, services, materials, labor
│   │   ├── invoices.py       # Invoices + line items
│   │   ├── estimates.py      # Estimates + line items
│   │   ├── payments.py       # Payments + allocations
│   │   ├── transactions.py   # Journal entries (double-entry core)
│   │   ├── banking.py        # Bank accounts, transactions, reconciliations
│   │   ├── settings.py       # Key-value company settings
│   │   ├── audit.py          # Audit log
│   │   ├── purchase_orders.py # Purchase orders + lines
│   │   ├── bills.py          # Bills + lines + payments + allocations
│   │   ├── credit_memos.py   # Credit memos + lines + applications
│   │   ├── recurring.py      # Recurring invoices + lines
│   │   ├── email_log.py      # Email delivery log
│   │   ├── tax.py            # Tax category mappings
│   │   ├── backups.py        # Backup records
│   │   ├── companies.py      # Multi-company records
│   │   ├── payroll.py        # Employees, pay runs, pay stubs
│   │   ├── qbo_mapping.py    # QBO ↔ Slowbooks ID mappings
│   │   ├── attachments.py    # File attachments (Phase 10)
│   │   ├── bank_rules.py     # Bank transaction categorization rules
│   │   ├── budgets.py        # Budget tracking by account/period
│   │   └── email_templates.py # Customizable email templates
│   ├── schemas/              # Pydantic request/response models
│   ├── routes/               # FastAPI routers (43 routers)
│   ├── services/
│   │   ├── accounting.py     # Double-entry journal entry engine
│   │   ├── analytics.py      # Phase 9: business intelligence aggregates (8 methods)
│   │   ├── audit.py          # SQLAlchemy after_flush audit hooks
│   │   ├── closing_date.py   # Closing date enforcement guard
│   │   ├── payroll_service.py # Withholding calculations
│   │   ├── recurring_service.py # Recurring invoice generation
│   │   ├── email_service.py  # SMTP email delivery
│   │   ├── csv_export.py     # CSV export (5 entity types)
│   │   ├── csv_import.py     # CSV import with error handling
│   │   ├── ofx_import.py     # OFX/QFX bank feed parser
│   │   ├── tax_export.py     # Schedule C data + CSV export
│   │   ├── backup_service.py # pg_dump/pg_restore wrapper
│   │   ├── company_service.py # Multi-company DB management
│   │   ├── iif_export.py     # IIF export (8 export functions)
│   │   ├── iif_import.py     # IIF parser + import + validation
│   │   ├── pdf_service.py    # WeasyPrint PDF generation
│   │   ├── stripe_service.py # Stripe Checkout + webhook verification
│   │   ├── qbo_service.py    # QBO OAuth + token management + client factory
│   │   ├── qbo_import.py     # Import 6 entity types from QBO
│   │   ├── qbo_export.py     # Export 6 entity types to QBO
│   │   ├── auth.py           # Phase 9.7: single-user password auth + session management
│   │   ├── rate_limit.py     # Phase 9.7: slowapi rate limiting
│   │   ├── settings_service.py # Settings CRUD with sensitive key filtering
│   │   └── crypto.py         # Fernet encryption for API keys + master key management
│   ├── templates/            # Jinja2 templates (PDF, email, checks, collection letters)
│   ├── seed/                 # Chart of Accounts seed data
│   └── static/
│       ├── css/
│       │   ├── style.css     # QB2003 "Default Blue" skin
│       │   └── dark.css      # Dark mode CSS overrides
│       └── js/               # SPA router, API wrapper, 35+ page modules
├── scripts/
│   ├── seed_database.py      # Seed the Chart of Accounts
│   ├── seed_irs_mock_data.py # IRS Pub 583 mock data
│   ├── run_recurring.py      # Cron script for recurring invoices
│   └── backup.sh             # PostgreSQL backup with rotation
├── screenshots/              # README images
├── cloudflare/               # Self-hosted Cloudflare Worker AI gateway
│   ├── worker.js             # Hardened proxy (model allowlist, rate limiting, security headers)
│   ├── wrangler.toml         # Deployment config
│   └── README.md             # Setup guide
├── tests/                    # 92 pytest tests (auth, security, posting, reporting, import)
└── index.html                # SPA shell (35+ script tags)
```

---

## Database Schema

40 tables with a double-entry accounting foundation:

| Table | Purpose |
|-------|---------|
| `accounts` | Chart of Accounts — asset, liability, equity, income, expense, COGS |
| `customers` | Customer contacts with billing/shipping addresses |
| `vendors` | Vendor contacts |
| `items` | Product/service/material/labor items with rates |
| `invoices` | Invoice headers with status tracking |
| `invoice_lines` | Invoice line items |
| `estimates` | Estimate headers |
| `estimate_lines` | Estimate line items |
| `payments` | Payment records |
| `payment_allocations` | Maps payments to invoices (many-to-many) |
| `transactions` | Journal entry headers |
| `transaction_lines` | Journal entry splits (debit OR credit) |
| `bank_accounts` | Bank accounts linked to COA |
| `bank_transactions` | Bank register entries (with OFX import fields) |
| `reconciliations` | Bank reconciliation sessions |
| `settings` | Company settings key-value store |
| `audit_log` | Automatic change tracking for all entities |
| `purchase_orders` | Purchase order headers |
| `purchase_order_lines` | PO line items with received quantities |
| `bills` | Vendor bills (AP mirror of invoices) |
| `bill_lines` | Bill line items with expense account tracking |
| `bill_payments` | Bill payment records |
| `bill_payment_allocations` | Maps bill payments to bills |
| `credit_memos` | Customer credit memos |
| `credit_memo_lines` | Credit memo line items |
| `credit_applications` | Maps credit memos to invoices |
| `recurring_invoices` | Recurring invoice templates |
| `recurring_invoice_lines` | Recurring invoice line items |
| `email_log` | Email delivery history |
| `tax_category_mappings` | Account-to-tax-line mappings for Schedule C |
| `backups` | Backup file records |
| `companies` | Multi-company database list |
| `employees` | Employee records for payroll |
| `pay_runs` | Pay run headers with totals |
| `pay_stubs` | Individual pay stubs with withholding breakdowns |
| `qbo_mappings` | QBO ID ↔ Slowbooks ID mapping for sync deduplication |
| `attachments` | File attachments linked to invoices, bills, etc. |
| `bank_rules` | Pattern-matching rules for auto-categorizing bank imports |
| `budgets` | Budget amounts by account and period |
| `email_templates` | Customizable email templates |

---

## API

All endpoints under `/api/`. Swagger docs at `/docs`. 200+ routes across 43 routers. All routes (except `/api/auth/*`, `/health`, `/pay/*`, and `/api/stripe/webhook`) require an authenticated session.

### Authentication (Phase 9.7)
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/auth/status` | GET | Auth state: `{setup_needed, authenticated}` |
| `/api/auth/setup` | POST | First-run password setup (min 8 chars) |
| `/api/auth/login` | POST | Login with password |
| `/api/auth/logout` | POST | Clear session |

### Core (Original)
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/dashboard` | GET | Company snapshot stats |
| `/api/dashboard/charts` | GET | AR aging buckets + monthly revenue trend |
| `/api/settings` | GET, PUT | Company settings |
| `/api/settings/test-email` | POST | Send SMTP test email |
| `/api/search` | GET | Unified search across all entities |
| `/api/accounts` | GET, POST, PUT, DELETE | Chart of Accounts CRUD |
| `/api/customers` | GET, POST, PUT, DELETE | Customer management |
| `/api/vendors` | GET, POST, PUT, DELETE | Vendor management |
| `/api/items` | GET, POST, PUT, DELETE | Items & services |
| `/api/invoices` | GET, POST, PUT | Invoice CRUD with line items |
| `/api/invoices/{id}/pdf` | GET | Generate invoice PDF |
| `/api/invoices/{id}/void` | POST | Void with reversing journal entry |
| `/api/invoices/{id}/send` | POST | Mark invoice as sent |
| `/api/invoices/{id}/email` | POST | Email invoice as PDF attachment |
| `/api/invoices/{id}/duplicate` | POST | Duplicate invoice as new draft |
| `/api/invoices/{id}/print-preview` | GET | Browser print preview (HTML) |
| `/api/estimates` | GET, POST, PUT | Estimate CRUD with line items |
| `/api/estimates/{id}/convert` | POST | Convert estimate to invoice |
| `/api/estimates/{id}/print-preview` | GET | Browser print preview (HTML) |
| `/api/payments` | GET, POST | Record payments with invoice allocation |
| `/api/payments/{id}/void` | POST | Void payment with reversing journal entry |
| `/api/banking/accounts` | GET, POST, PUT | Bank account management |
| `/api/banking/transactions` | GET, POST | Bank register entries |
| `/api/banking/reconciliations` | GET, POST | Reconciliation sessions |

### Accounts Payable
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/purchase-orders` | GET, POST, PUT | Purchase order CRUD |
| `/api/purchase-orders/{id}/convert-to-bill` | POST | Convert PO to bill |
| `/api/bills` | GET, POST, PUT | Bill CRUD with line items |
| `/api/bills/{id}/void` | POST | Void bill |
| `/api/bill-payments` | POST | Pay vendor bills with allocation |
| `/api/credit-memos` | GET, POST | Credit memo CRUD |
| `/api/credit-memos/{id}/apply` | POST | Apply credit to invoices |

### Productivity
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/recurring` | GET, POST, PUT, DELETE | Recurring invoice templates |
| `/api/recurring/generate` | POST | Generate due recurring invoices |
| `/api/batch-payments` | POST | Batch payment application |

### Payroll
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/employees` | GET, POST, PUT | Employee CRUD |
| `/api/payroll` | GET, POST | Pay run CRUD |
| `/api/payroll/{id}/process` | POST | Process pay run (creates journal entries) |

### Banking & Deposits
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/banking/check-register` | GET | Check register with running balance |
| `/api/deposits/pending` | GET | Pending deposits in Undeposited Funds |
| `/api/deposits` | GET, POST | Create deposits (move funds to bank) |
| `/api/cc-charges` | GET, POST | Credit card charge entry |
| `/api/checks/print` | GET | Generate check PDF (3-per-page format) |

### Journal Entries
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/journal` | GET, POST | Manual journal entry CRUD |
| `/api/journal/{id}` | GET | Get journal entry with lines |
| `/api/journal/{id}/void` | POST | Void with reversing entry |

### Reports & Tax
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/reports/profit-loss` | GET | P&L report |
| `/api/reports/balance-sheet` | GET | Balance sheet |
| `/api/reports/ar-aging` | GET | Accounts receivable aging |
| `/api/reports/ap-aging` | GET | Accounts payable aging |
| `/api/reports/sales-tax` | GET | Sales tax collected |
| `/api/reports/sales-tax/pay` | POST | Record sales tax payment to government |
| `/api/reports/general-ledger` | GET | All journal entries by account |
| `/api/reports/income-by-customer` | GET | Sales totals per customer |
| `/api/tax/schedule-c` | GET | Schedule C data from P&L |
| `/api/tax/schedule-c/csv` | GET | Schedule C CSV export |

### Import/Export
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/iif/export/all` | GET | Export everything as .iif |
| `/api/iif/import` | POST | Import .iif file |
| `/api/csv/export/{type}` | GET | Export entities as CSV |
| `/api/csv/import/{type}` | POST | Import CSV file |
| `/api/bank-import/preview` | POST | Preview OFX/QFX transactions |
| `/api/bank-import/import/{id}` | POST | Import OFX/QFX into bank account |

### QuickBooks Online
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/qbo/auth-url` | GET | Generate Intuit OAuth authorization URL |
| `/api/qbo/callback` | GET | OAuth redirect handler (stores tokens) |
| `/api/qbo/disconnect` | POST | Clear stored QBO tokens |
| `/api/qbo/status` | GET | Connection status (never returns raw tokens) |
| `/api/qbo/import` | POST | Import all entity types from QBO |
| `/api/qbo/import/{entity}` | POST | Import single entity type |
| `/api/qbo/export` | POST | Export all entity types to QBO |
| `/api/qbo/export/{entity}` | POST | Export single entity type |

### Online Payments
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/pay/{token}` | GET | Public payment page (no auth) |
| `/api/stripe/create-checkout-session` | POST | Create Stripe Checkout session |
| `/api/stripe/webhook` | POST | Stripe webhook handler |
| `/api/stripe/payment-link/{id}` | GET | Get public payment URL for invoice |

### System
| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/audit` | GET | Audit log viewer |
| `/api/backups` | GET, POST | Backup management |
| `/api/backups/{id}/download` | GET | Download backup file |
| `/api/companies` | GET, POST | Multi-company management |
| `/api/uploads/logo` | POST | Upload company logo |
| `/api/attachments/{type}/{id}` | GET, POST, DELETE | File attachments CRUD |
| `/api/bank-rules` | GET, POST, PUT, DELETE | Bank transaction categorization rules |
| `/api/budgets` | GET, POST, PUT, DELETE | Budget management |
| `/api/email-templates` | GET, POST, PUT, DELETE | Custom email template management |
| `/health` | GET | Liveness probe (no auth required) |

### Analytics
All read endpoints accept `?period=month|quarter|year` (or `mtd/qtd/ytd`), or explicit `?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`.

| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/analytics/dashboard` | GET | Complete analytics snapshot (all 8 metrics, includes `period` echo) |
| `/api/analytics/revenue` | GET | Windowed revenue by customer + 12-month trend |
| `/api/analytics/expenses` | GET | Windowed expense breakdown by account number |
| `/api/analytics/cash-flow` | GET | Cash forecast + DSO + A/R and A/P aging (`?days=90`) |
| `/api/analytics/profitability` | GET | Lifetime paid revenue per customer |
| `/api/analytics/export.csv` | GET | Flat CSV of the full snapshot (`section,key,subkey,value`) — honors period params |
| `/api/analytics/export.pdf` | GET | Print-ready PDF via WeasyPrint — honors period params |
| `/api/analytics/ai-config` | GET, PUT | Display config (no raw key) / update provider/model/key/worker_url (key Fernet-encrypted at rest; worker_url validated against SSRF/MITM) |
| `/api/analytics/ai-config/test` | POST | Smoke-test the configured provider with a one-word prompt |
| `/api/analytics/ai-insights` | POST | Run the dashboard through the configured LLM; `?force=true` bypasses 10-min cache |
| `/api/analytics/ai-query` | POST | Tool-calling Q&A — LLM autonomously calls 16 read-only tools to answer `?question=...` |
| `/analytics` | GET | Backwards-compat 307 redirect to the SPA hash route `/#/analytics` |

---

## QuickBooks 2003 Pro — IIF Interoperability

Slowbooks can exchange data with QuickBooks 2003 Pro via **Intuit Interchange Format (IIF)** — a tab-delimited text format that QB2003 uses for file-based import/export.

![QuickBooks Interop Page](screenshots/iif-interop.png)

### Exporting from Slowbooks to QB2003

1. Navigate to **QuickBooks Interop** in the sidebar
2. Click **Export All Data** (or export individual sections)
3. For invoices/payments, optionally set a date range
4. Save the `.iif` file
5. In QuickBooks 2003: **File > Utilities > Import > IIF Files** and select the file

**What gets exported:**

| Section | IIF Header | Fields |
|---------|-----------|--------|
| Chart of Accounts | `!ACCNT` | Name, type (BANK/AR/AP/INC/EXP/EQUITY/COGS/etc.), number, description |
| Customers | `!CUST` | Name, company, address, phone, email, terms, tax ID |
| Vendors | `!VEND` | Name, address, phone, email, terms, tax ID |
| Items | `!INVITEM` | Name, type (SERV/PART/OTHC), description, rate, income account |
| Invoices | `!TRNS/!SPL` | Customer, date, line items, amounts, tax, terms |
| Payments | `!TRNS/!SPL` | Customer, date, amount, deposit account, invoice allocation |
| Estimates | `!TRNS/!SPL` | Customer, date, line items (non-posting) |

### Importing from QB2003 to Slowbooks

1. In QuickBooks 2003: **File > Utilities > Export > Lists to IIF Files**
2. In Slowbooks: navigate to **QuickBooks Interop**
3. Drag and drop the `.iif` file (or click to browse)
4. Click **Validate** — checks structure, account types, and balanced transactions
5. If validation passes, click **Import**

The importer handles:
- Automatic account type mapping (QB's 14 types → Slowbooks' 6 types)
- Parent:Child colon-separated account names
- Duplicate detection (skips records that already exist by name or document number)
- Per-row error collection (a bad row won't abort the entire import)
- Windows-1252 and UTF-8 encoded files

### IIF Format Reference

IIF is tab-delimited with `\r\n` line endings. Header rows start with `!`. Transaction blocks use `TRNS`/`SPL`/`ENDTRNS` grouping. Sign convention: TRNS amount is positive (debit), SPL amounts are negative (credits), and they must sum to zero.

```
!TRNS	TRNSTYPE	DATE	ACCNT	NAME	AMOUNT	DOCNUM
!SPL	TRNSTYPE	DATE	ACCNT	NAME	AMOUNT	DOCNUM
!ENDTRNS
TRNS	INVOICE	01/15/2026	Accounts Receivable	John E. Marks	444.96	2001
SPL	INVOICE	01/15/2026	Service Income	John E. Marks	-438.00	2001
SPL	INVOICE	01/15/2026	Sales Tax Payable	John E. Marks	-6.96	2001
ENDTRNS
```

### Account Type Mapping

| Slowbooks Type | IIF Types (by account number range) |
|---------------|--------------------------------------|
| Asset | `BANK` (1000-1099), `AR` (1100), `OCASSET` (1101-1499), `FIXASSET` (1500-1999) |
| Liability | `AP` (2000), `OCLIAB` (2001-2499), `LTLIAB` (2500+) |
| Equity | `EQUITY` |
| Income | `INC` |
| Expense | `EXP` |
| COGS | `COGS` |

### Sample Data

The `scripts/seed_irs_mock_data.py` script populates Slowbooks with test data from **IRS Publication 583** (Rev. December 2024) — "Starting a Business and Keeping Records." The sample business is **Henry Brown's Auto Body Shop**, a sole proprietorship with:

- 8 customers (John E. Marks, Patricia Davis, Robert Garcia, Thompson & Sons, etc.)
- 13 vendors from the IRS check disbursements journal (Auto Parts Inc., ABC Auto Paint, Baker's Fender Co., etc.)
- 8 service/material items (Body Repair, Paint & Finish, Dent Removal, Frame Alignment, etc.)
- 10 invoices totaling $3,631.31 with 1.59% sales tax
- 5 payments totaling $1,498.00
- 3 pending estimates
- All with proper double-entry journal entries

```bash
python3 scripts/seed_irs_mock_data.py
```

---

## License

**Source Available — Free for personal and enterprise use. No commercial resale.**

You can use, modify, and run Slowbooks Pro for any personal, educational, or internal business purpose. You cannot sell it or offer it as a paid service. See [LICENSE](LICENSE) for full terms.

---

## Acknowledgments

- 14 years of QuickBooks 2003 Pro (1 license, $199.95, 2003 dollars)
- IDA Pro and the reverse engineering community
- The Pervasive PSQL documentation that nobody else has read since 2005
- Every small business owner who lost software they paid for when activation servers died

---

## Contributors

- [VonHoltenCodes](https://github.com/VonHoltenCodes) — Creator
- [PNWImport](https://github.com/PNWImport) — Security hardening (auth, CORS, path traversal, atomic writes, non-root Docker, rate limiting), analytics engine, AI insights with 7-provider support, Cloudflare Worker gateway
- [jake-378](https://github.com/jake-378) — Backup UI fixes, report period selectors, invoice terms autofill, date validation fixes
- [WC3D](https://github.com/WC3D) — Jinja2 XSS security fix

*Built by [VonHoltenCodes](https://github.com/VonHoltenCodes) with [Claude Code](https://claude.ai/code) as co-author.*

# QuickBooks Online Integration Setup

Connect Slowbooks to QuickBooks Online to import or export accounts, customers, vendors, items, invoices, and payments via Intuit's REST API.

---

## Prerequisites

- An Intuit Developer account (free)
- A QuickBooks Online account (a free sandbox company is provided for testing)
- Slowbooks Pro running and accessible

---

## Step 1: Create an Intuit Developer Account

1. Go to [https://developer.intuit.com](https://developer.intuit.com)
2. Click **Sign Up** (or sign in if you already have an Intuit account)
3. Complete the registration and verify your email

---

## Step 2: Create an App

1. In the Intuit Developer Portal, go to **My Apps** (or **Dashboard**)
2. Click **Create an app**
3. Select **QuickBooks Online and Payments**
4. Give your app a name (e.g., "Slowbooks Sync")
5. Select the **com.intuit.quickbooks.accounting** scope (Accounting)
6. Click **Create**

---

## Step 3: Get Your Sandbox Keys

1. On your app's page, go to **Keys & OAuth** (or **Keys & credentials**)
2. Look at the **Sandbox** section (not Production)
3. Copy your:
   - **Client ID** — a long alphanumeric string
   - **Client Secret** — click to reveal and copy
4. Under **Redirect URIs**, click **Add URI** and enter the callback URL for automatic completion:
   ```
   http://localhost:3001/api/qbo/callback
   ```
   If you use Intuit's OAuth Playground for manual completion, register the Playground Redirect URI shown there instead.
5. Save

**Important**: The redirect URI must match **exactly** what Slowbooks sends — including the port number and path. If your server runs on a different port, adjust accordingly.

---

## Step 4: Configure Slowbooks

1. Open Slowbooks and go to **Settings** (sidebar > System > Settings)
2. Scroll down to the **QuickBooks Online** section
3. Fill in:
   - **Enable QBO Integration**: `Enabled`
   - **Environment**: `Sandbox` (use `Production` only after Intuit approves your app)
   - **Client ID**: paste your Client ID from Step 3
   - **Client Secret**: paste your Client Secret from Step 3
   - **Redirect URI**: the exact URI registered in Step 3 (`http://localhost:3001/api/qbo/callback` for automatic completion, or the Playground Redirect URI for manual completion)
4. Click **Save Settings**

After saving, the **Client Secret**, **Access Token**, and **Refresh
Token** fields will display as `********` on subsequent page loads —
that's the redaction guard, not a save failure. Typing a new value
over the `********` replaces the stored secret; leaving it untouched
keeps the existing value.

---

## Step 5: Connect to QuickBooks

1. Navigate to **QuickBooks Online** in the sidebar (under Interop)
2. Click **Start connection with Intuit**; the authorization page opens in a new tab
3. Sign in on Intuit's login page
4. Select the company you want to connect (for sandbox, choose the sandbox company)
5. Click **Connect**
6. If Intuit redirects to a reachable SlowBooks callback, the connection completes automatically. Return to the original SlowBooks tab and refresh the QuickBooks Online page to see the status.

If the redirect cannot reach SlowBooks, copy the **full callback URL** from the new tab's address bar and paste it into the **Authorization Code** field on the original tab. SlowBooks extracts the code and Realm ID. You can also copy the `code` and `realmId` query values into their separate fields. Click **Finish QBO connection**. If your configured Redirect URI is Intuit's OAuth Playground, paste the Authorization Code and Realm ID supplied there directly. Authorization codes are single use, so obtain a fresh one if the exchange fails.

---

## Step 6: Import or Export Data

### Importing from QBO

Click **Import All Data** to pull everything from QBO in dependency order:

1. Accounts (must exist before items reference them)
2. Customers (must exist before invoices)
3. Vendors
4. Items (must exist before invoice lines)
5. Invoices
6. Payments
7. Sales Receipts
8. Journal Entries (the QBO JournalEntry API)
9. Posted Ledger Activity (the QBO General Ledger report)

Or use the checkboxes to import individual entity types.

The **Import log** below the controls updates every two seconds. Its timestamped rows show query pages and report periods, source items, validation, creation, mapping, skips, commits, rollbacks, and errors. Numeric provider codes are labeled `QBO`; local errors use `IMPORT_*` codes. **Pending** means the record has been created in the transaction; **Imported** increases only after the transaction commits.

Click **Errors** to show only error rows; click again to restore all events. Monitoring continues while filtered, including new errors. The compact **CODE**, **ITEM**, and **ACTION** columns have equal widths, leaving the rest for **MESSAGE**. Item IDs are never truncated. Messages identify source document numbers, related customer/project IDs, linked invoice IDs, and the actual accounts, dates, or amounts that failed validation.

The status shows the current step, elapsed time, item counts, and time since the last progress event. **Waiting** appears after 30 seconds without progress while the server remains reachable. Network failures are logged immediately; **Connection interrupted** appears after 15 seconds without server contact. The page retains its rows and reconnects automatically. HTTP failures are recorded with the requested action and `HTTP_*` code, rather than being mislabeled as a connection loss. Permanent failures pause monitoring and offer **Retry monitor**. HTTP 404, 405, or 501 shows **Server update required**: restart the Slowbooks server to load the updated import endpoints, then retry monitoring. Import buttons remain disabled until the monitor is available. An import request is logged before the server responds, so a slow start is visible too.

The importer runs on the server, so closing or leaving the page does not stop it. Returning to the page or refreshing restores the latest run. Only the latest import is kept, and an accepted new import replaces it. A server restart marks an unfinished run **Interrupted** rather than automatically retrying it. Connection diagnostics belong to the current page visit and do not advance the saved import event cursor or count as imported items.

Only one import per company can run at a time, including requests through the older import endpoints. The page disables both import buttons while a run is active. Imports require an administrator; authenticated users can read the latest log.

For integrations, `POST /api/qbo/import-runs` with `{}` starts all entity types; `{"entities":["accounts","journal_entries"]}` starts a selection in dependency order. The response is HTTP 202 with `run_id` and `status`. `GET /api/qbo/import-runs/latest?after=0` returns the latest run, up to 500 events, `has_more`, and `server_time`. Poll with the last event's `sequence` as `after`; fetch additional pages when `has_more` is true. A concurrent start returns HTTP 409 with the active `run_id`.

Logs are stored separately from the accounting database under `backups/.qbo-import/<company-key>/latest.sqlite3` (inside the configured data directory for desktop and local server installs). This keeps progress readable while an import writes to SQLite. The background runner uses the application's single server process and preserves the initiating user's audit identity. Database backup and restore operations do not include the import log.

**Duplicate detection**: If a record with the same name (accounts, customers, vendors, items) or document number (invoices) already exists in Slowbooks, it will be skipped and mapped to the existing record.

QBO Bank and Credit Card accounts also appear in Banking with a local statement identity. Reimporting Accounts repairs bank/card accounts mapped by older Slowbooks versions and brings in inactive accounts needed for historical postings. Posted Ledger Activity imports QBO's accrual General Ledger lines as balanced journal entries, including purchases, deposits, transfers, invoices, payments, and journal entries. It checks account mappings and balances before posting and skips entries already imported. If the report is incomplete or a posting cannot be mapped, it reports an error and posts no ledger activity. No synthetic opening balance is added.

Journal Entries queries `SELECT * FROM JournalEntry STARTPOSITION 1 MAXRESULTS 100` and continues through every page. It imports the transaction date, document number, private note, and each line's account, debit/credit direction, amount, and memo. Journal entries appear on the **Journal Entries** page and in account registers. Import Accounts first to map every referenced account. Reimporting skips existing journals and reuses journals already imported through Posted Ledger Activity, without posting them twice. Changed or incomplete journals are reported as errors. QBO journals are managed through the import and cannot be voided locally. See Intuit's [JournalEntry API reference](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/journalentry#query-a-journalentry).

Posting comparisons use transaction dates and amounts per account; a changed SyncToken alone is not a financial mismatch. Older ledger imports that incorrectly assigned child-account lines to their parent can be corrected when every line amount and date matches exactly. Repairs preserve transaction and line IDs, respect the closing date, and log old/new QBO and local account IDs with `IMPORT_ACCOUNT_ROLLUP_REPAIRED`. Other mismatches report the observed differences with `IMPORT_POSTING_MISMATCH`. An empty journal stub with one unsigned, zero-value account line is skipped only after QBO's General Ledger confirms no monetary posting on that date; this is logged as `IMPORT_NON_POSTING_JOURNAL`.

Invoices, payments, and sales receipts resolve QBO subcustomers already imported as local projects through the project's parent customer. Invoices and sales receipts retain their project assignment. A missing reference reports the document ID/number, CustomerRef ID/name, missing local mapping, and any linked transaction IDs.

### Exporting to QBO

Click **Export All Data** to push Slowbooks data to QBO. Already-exported records (tracked in the `qbo_mappings` table) are skipped.

---

## Entity Type Mapping

### Account Types

| QBO Type | Slowbooks Type |
|----------|---------------|
| Bank, Accounts Receivable, Other Current Asset, Fixed Asset, Other Asset | Asset |
| Accounts Payable, Credit Card, Other Current Liability, Long Term Liability | Liability |
| Equity | Equity |
| Income, Other Income | Income |
| Expense, Other Expense | Expense |
| Cost of Goods Sold | COGS |

### Item Types

| QBO Type | Slowbooks Type |
|----------|---------------|
| Service | Service |
| Inventory, Group | Product |
| NonInventory | Material |

### Invoice Status

| QBO Condition | Slowbooks Status |
|--------------|-----------------|
| Balance == Total | Sent |
| 0 < Balance < Total | Partial |
| Balance == 0 | Paid |

---

## Going to Production

Intuit requires several steps before you can use production credentials:

1. **App Details** (Intuit Developer Portal > your app > App details):
   - Verify your developer profile and email
   - Add end-user license agreement and privacy policy URLs
   - Add host domain, launch URL, disconnect URL, and connect/reconnect URL
   - Select a category for your app
   - Declare regulated industries (if any)
   - Specify where your app is hosted

2. **Compliance** — Complete Intuit's compliance checklist (security review)

3. Once approved, switch to your **Production** keys:
   - Update Client ID and Client Secret in Slowbooks Settings
   - Change **Environment** to `Production`
   - Add your production redirect URI in the Intuit Developer Portal
   - **HTTPS is required** for production redirect URIs (localhost is exempt for development)

For personal/internal use, the **sandbox environment works indefinitely** and doesn't require production approval.

---

## OAuth Flow Details

1. User clicks "Connect to QuickBooks" in Slowbooks
2. Slowbooks generates a CSRF state token and redirects to Intuit's authorization page
3. User logs in to Intuit and approves access
4. Intuit redirects back to `GET /api/qbo/callback?code=...&state=...&realmId=...`
5. Slowbooks exchanges the auth code for tokens:
   - **Access token** — expires in 60 minutes, auto-refreshed before each API call
   - **Refresh token** — valid for 100 days
6. Tokens are stored in the Slowbooks settings table (never exposed via the status API)
7. The CSRF state token is verified and cleared after use

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "redirect_uri is invalid" | Make sure the redirect URI in Slowbooks Settings matches **exactly** what's listed in your Intuit app's Redirect URIs (including port) |
| "OAuth callback failed: value too long" | The settings.value column needs to be TEXT type — run `ALTER TABLE settings ALTER COLUMN value TYPE TEXT` |
| "Not connected to QuickBooks Online" | Click Connect and complete the OAuth flow. Check that Client ID and Secret are saved in Settings |
| Token expired / 401 errors | Tokens auto-refresh, but if the refresh token expires (100 days), reconnect by clicking Connect again |
| Import shows 0 records | The QBO company may have no data. Sandbox companies come with sample data — try creating a new sandbox company in the Intuit Developer Portal |
| Rate limit errors | QBO allows 500 requests/minute per realm. Large imports are sequential by design. If you hit limits, wait a minute and retry |
| "Customer not found" during invoice import | Import customers before invoices. Use "Import All" to ensure dependency order |

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/qbo/auth-url` | GET | Get Intuit authorization URL |
| `/api/qbo/callback` | GET | OAuth redirect handler |
| `/api/qbo/connect-manual` | POST | Complete a pending connection with Authorization Code and Realm ID (admin) |
| `/api/qbo/disconnect` | POST | Clear tokens, disconnect |
| `/api/qbo/status` | GET | Connection status (no raw tokens) |
| `/api/qbo/import` | POST | Import all entity types |
| `/api/qbo/import/{entity}` | POST | Import one type (accounts, customers, vendors, items, invoices, payments, sales_receipts, journal_entries, ledger) |
| `/api/qbo/export` | POST | Export all entity types |
| `/api/qbo/export/{entity}` | POST | Export one type |

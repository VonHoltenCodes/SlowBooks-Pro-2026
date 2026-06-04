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
4. Under **Redirect URIs**, click **Add URI** and enter:
   ```
   http://localhost:3001/api/qbo/callback
   ```
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
   - **Redirect URI**: `http://localhost:3001/api/qbo/callback` (should already be set)
4. Click **Save Settings**

After saving, the **Client Secret**, **Access Token**, and **Refresh
Token** fields will display as `********` on subsequent page loads —
that's the redaction guard, not a save failure. Typing a new value
over the `********` replaces the stored secret; leaving it untouched
keeps the existing value.

---

## Step 5: Connect to QuickBooks

1. Navigate to **QuickBooks Online** in the sidebar (under Interop)
2. Click **Connect to QuickBooks**
3. You'll be redirected to Intuit's login page
4. Sign in with your Intuit account
5. Select the company you want to connect (for sandbox, choose the sandbox company)
6. Click **Connect**
7. You'll be redirected back to Slowbooks — the status should show **Connected** with the company name

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

Or use the checkboxes to import individual entity types.

**Duplicate detection**: If a record with the same name (accounts, customers, vendors, items) or document number (invoices) already exists in Slowbooks, it will be skipped and mapped to the existing record.

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
| `/api/qbo/disconnect` | POST | Clear tokens, disconnect |
| `/api/qbo/status` | GET | Connection status (no raw tokens) |
| `/api/qbo/import` | POST | Import all entity types |
| `/api/qbo/import/{entity}` | POST | Import one type (accounts, customers, vendors, items, invoices, payments) |
| `/api/qbo/export` | POST | Export all entity types |
| `/api/qbo/export/{entity}` | POST | Export one type |

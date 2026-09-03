# Data Model

Schema reference for the Slowbooks PostgreSQL database. 55 tables on
a double-entry accounting foundation. For migration history, see the
files under `migrations/versions/`; for model code, see `app/models/`.

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
| `inventory_movements` | Per-item qty/cost ledger (purchases, sales, adjustments) |
| `saved_reports` | Named (report_type + parameters) tuples |
| `document_audits` | SHA-256 hash chain for generated tax-form PDFs (W-2/W-3/940/941) |
| `portal_accesses` | Audit log for self-service portal hits (success + failure) |
| `login_attempts` | Authentication-attempt audit log |
| `reseller_permits` | Per-entity sales-tax reseller permits with expiration + verification trail |

## jobs (Customer:Job / Projects)

| column | notes |
|---|---|
| id, customer_id (FK customers) | a job belongs to exactly one customer |
| name, job_number | name unique per customer, case-insensitive (API-enforced) |
| status | pending · awarded · in_progress · closed · not_awarded |
| job_type, description, site_address, notes | free text |
| start_date, projected_end_date, end_date | |
| contract_amount | Numeric(12,2), drives billed-vs-contract |
| is_active | inactive = hidden from pickers, kept on history |

`job_id` (nullable FK) lives on: transactions, transaction_lines, invoices,
invoice_lines, bills, bill_lines, estimates, estimate_lines, purchase_orders,
purchase_order_lines, credit_memos, recurring_invoices, time_entries.
`class_id` was added to transaction_lines, invoice_lines and bill_lines.
Attribution rule for reports: `coalesce(line.job_id, transaction.job_id)`.
Migration `e9f0a1b2c3d4_add_jobs`.

## cost_codes (job-costing chart)

| column | notes |
|---|---|
| id, code (unique, case-insensitive via API), name | e.g. `03` Concrete |
| cost_type | labor · material · subcontract · equipment · other |
| account_id (FK accounts, nullable) | default posting account |
| notes, is_active | inactive = hidden from pickers |

`cost_code_id` (nullable FK) on transaction_lines, bill_lines, invoice_lines,
estimate_lines, purchase_order_lines, time_entries. `is_billable` +
`billed_invoice_line_id` (FK invoice_lines) on transaction_lines and
bill_lines. `purchase_orders.job_id` / `purchase_order_lines.job_id` feed
committed cost. Migration `f0a1b2c3d4e5_add_cost_codes`.


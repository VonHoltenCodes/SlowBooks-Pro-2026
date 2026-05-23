# Development

Tech stack, project layout, and contributor flow. For install
instructions see [INSTALL.md](../INSTALL.md); for the public security
policy and disclosure process see [SECURITY.md](../SECURITY.md); for
internal hardening notes see
[docs/security-hardening.md](security-hardening.md).

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.13 + FastAPI (44 routers, 213+ routes) |
| Database | PostgreSQL 17 / SQLite + SQLAlchemy 2.0 |
| Migrations | Alembic |
| Frontend | Vanilla HTML/CSS/JS (no framework) + self-hosted Chart.js 4.4.6 for analytics |
| PDF | WeasyPrint 60.2 + Jinja2 |
| Bank Import | ofxparse (OFX/QFX) |
| Payments | Stripe Checkout (hosted) |
| QBO Sync | python-quickbooks + intuit-oauth (OAuth 2.0) |
| Port | 3001 |

---

## Project Structure

```
SlowBooks-Pro-2026/
├── .env.example              # Environment config template
├── requirements.txt          # Python dependencies (production)
├── requirements-dev.txt      # Test + lint deps
├── run.py                    # Uvicorn entry point (port 3001)
├── alembic.ini               # Alembic config
├── migrations/               # Alembic database migrations (script_location in alembic.ini)
├── app/
│   ├── main.py               # FastAPI app + 48 routers (250+ routes)
│   ├── config.py             # Environment-based settings (CORS, origins)
│   ├── database.py           # SQLAlchemy engine + session + table auto-creation
│   ├── models/               # 30+ model modules (50+ tables)
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
│   │   ├── payroll.py        # Employees, pay runs, pay stubs, bank accounts
│   │   ├── hr.py             # HR module: onboarding, time entries, PTO, deductions
│   │   ├── pto.py            # PTO policies, requests, accruals
│   │   ├── time_entries.py   # Time entry tracking with approval workflow
│   │   ├── deductions.py     # Deduction types, employee deductions, garnishments
│   │   ├── qbo_mapping.py    # QBO ↔ Slowbooks ID mappings
│   │   ├── attachments.py    # File attachments
│   │   ├── bank_rules.py     # Bank transaction categorization rules
│   │   ├── budgets.py        # Budget tracking by account/period
│   │   ├── document_audit.py # SHA-256 hash chain for tax-form PDFs
│   │   ├── portal_access.py  # Portal access audit log
│   │   ├── reseller_permit.py # Per-entity reseller permits
│   │   └── email_templates.py # Customizable email templates
│   ├── schemas/              # Pydantic request/response models
│   ├── routes/               # FastAPI routers (43+ routers)
│   ├── services/
│   │   ├── accounting.py     # Double-entry journal entry engine
│   │   ├── analytics.py      # Business intelligence aggregates (8 methods)
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
│   │   ├── auth.py           # Single-user password auth + session management
│   │   ├── rate_limit.py     # slowapi rate limiting
│   │   ├── settings_service.py # Settings CRUD with sensitive key filtering
│   │   ├── encryption.py     # Fernet encryption + versioned ciphertext + rewrap CLI
│   │   └── crypto.py         # Master key resolution
│   ├── templates/            # Jinja2 templates (PDF, email, checks, collection letters)
│   ├── seed/                 # Chart of Accounts seed data
│   └── static/
│       ├── css/
│       │   ├── style.css     # QB2003 "Default Blue" skin
│       │   └── dark.css      # Dark mode CSS overrides
│       └── js/               # SPA router, API wrapper, 40+ page modules
│           ├── app.js              # Main SPA router with 40 routes
│           ├── api.js              # HTTP wrapper (API.get/post/put/del)
│           ├── employees.js        # Employee CRUD + Details modal
│           ├── onboarding.js       # Onboarding checklists + e-signature
│           ├── time_entries.js     # Time tracking + approve/reject workflow
│           ├── pto.js              # PTO policies + request workflow
│           ├── deductions.js       # Deduction types, employee deductions, garnishments
│           ├── tax_forms.js        # W-2, W-3, 940, 941 form generation UI
│           └── [30+ more pages]    # Invoices, customers, reports, analytics, etc.
├── scripts/
│   ├── seed_database.py      # Seed the Chart of Accounts
│   ├── seed_irs_mock_data.py # IRS Pub 583 mock data
│   ├── run_recurring.py      # Cron script for recurring invoices
│   └── backup.sh             # PostgreSQL backup with rotation
├── screenshots/              # README + docs images
├── cloudflare/               # Self-hosted Cloudflare Worker AI gateway
│   ├── worker.js             # Hardened proxy (model allowlist, rate limiting, security headers)
│   ├── wrangler.toml         # Deployment config
│   └── README.md             # Setup guide
├── tests/                    # 295 pytest tests (auth, security, posting, reporting, import, payroll Tiers 1-3, HR, wiring audit)
└── index.html                # SPA shell
```

---

## Running tests

```bash
pip install -r requirements-dev.txt
pytest                       # full suite (~50s)
pytest tests/test_wiring.py  # 0.15s — JS <-> backend wiring audit
pytest -k "audit or portal"  # subset by keyword
```

The wiring audit is also a boot-time tripwire in
`docker-entrypoint.sh`: containers built off `requirements-dev.txt`
will fail to start if any JS API call points at a removed route.
Production images (built off `requirements.txt` only) skip the
boot check silently — CI already gates the same condition before the
image is built.

### Manual end-to-end smoke test

`scripts/integration_test_frontend.py` is a live-HTTP integration
script (deliberately not under `tests/` so the pytest collector
skips it). Hits every SPA page + API endpoint over real HTTP and
confirms JS bundles load without errors. Run it against a running
server when you've touched routing or the SPA shell:

```bash
uvicorn app.main:app --port 8000 &
python scripts/integration_test_frontend.py
```

---

## Lint + format

```bash
pip install "black>=24.8.0" "ruff>=0.6.0"
black --check app/main.py app/services/audit.py tests/   # mirrors CI scope
ruff check app/main.py app/services/audit.py tests/
```

CI gates a curated file allowlist (`.github/workflows/ci.yml`).
Earlier-phase routes carry pre-black style and are tracked in
[todo.md](todo.md) for a dedicated cleanup pass — new files MUST
land in the allowlist so they don't slip the gate.

---

## Contributor flow

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full guide. The short
version:

1. Branch off `main`
2. Make changes; add/update tests
3. `pytest` green locally
4. Open a PR — CI runs lint, full pytest, pip-audit, and Docker build
5. Reviewer merges once green

The PR template prompts for: summary, test plan, security/perf notes,
and any docs updates.

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
| Backend | Python 3.13 + FastAPI (89 routers, 735 API operations on 588 paths in 2.31.3) |
| Database | SQLite company files (desktop, Server Edition) or PostgreSQL 17 + SQLAlchemy 2.0 |
| Migrations | Alembic; every table and column change needs one (see below) |
| Frontend | Vanilla HTML/CSS/JS (no framework) + self-hosted Chart.js 4.4.6 for analytics |
| PDF | WeasyPrint 70.0 + Jinja2 |
| Bank feeds and imports | SimpleFIN, Mercury; ofxparse (OFX/QFX), CSV |
| Payments | Stripe Checkout, PayPal and Square (hosted pages), one abstraction in `app/services/payments/` |
| Auth | Named users in four roles, sessions, scoped API tokens |
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
│   ├── main.py               # FastAPI app + the routers, role policy, startup checks
│   ├── config.py             # Environment-based settings (CORS, origins)
│   ├── database.py           # SQLAlchemy engine + session + table auto-creation
│   ├── models/               # 60 model modules (105 tables)
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
│   │   ├── qbo_mapping.py    # what an earlier QuickBooks Online import left behind
│   │   ├── attachments.py    # File attachments
│   │   ├── bank_rules.py     # Bank transaction categorization rules
│   │   ├── budgets.py        # Budget tracking by account/period
│   │   ├── document_audit.py # a SHA-256 hash per generated tax-form PDF
│   │   ├── portal_access.py  # Portal access audit log
│   │   ├── reseller_permit.py # Per-entity reseller permits
│   │   └── email_templates.py # Customizable email templates
│   ├── schemas/              # Pydantic request/response models
│   ├── routes/               # FastAPI routers (89; payroll and reports are packages)
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
│   │   ├── backup_service.py # backups: pg_dump on PostgreSQL, a file copy on SQLite
│   │   ├── company_service.py # Multi-company DB management
│   │   ├── iif_export.py     # IIF export (8 export functions)
│   │   ├── iif_import.py     # IIF parser + import + validation
│   │   ├── pdf_service.py    # WeasyPrint PDF generation
│   │   ├── payments/         # stripe, paypal, square, recorder, clearing
│   │   ├── qbo_export_import.py # QuickBooks Online's Export data zip → Migrate Data
│   │   ├── auth.py           # Users, roles, sessions, API tokens
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
│           ├── app.js              # Main SPA router (76 routes)
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
│   ├── run_recurring.py      # Cron script for recurring invoices + auto-enter memorized transactions
│   └── backup.sh             # PostgreSQL backup with rotation
├── screenshots/              # README + docs images
├── cloudflare/               # Self-hosted Cloudflare Worker AI gateway
│   ├── worker.js             # Hardened proxy (model allowlist, rate limiting, security headers)
│   ├── wrangler.toml         # Deployment config
│   └── README.md             # Setup guide
├── tests/                    # ~7,200 pytest tests (auth, security, posting, reporting, imports, payroll, HR, browser tests under Playwright, the wiring, schema, autoescape, rounding, race-condition, N+1, closing-date, secret-redaction, void-symmetry, IIF round-trip and shell-injection audits)
└── index.html                # SPA shell
```

### Where things go when you add code

- **New HTTP endpoint** — drop into `app/routes/<domain>.py`. Define
  an `APIRouter(prefix="/api/...", tags=[...])`, attach `@router.get`
  / `@router.post` / etc., then `app.include_router(router)` in
  `app/main.py`. Group by domain — add to the smallest existing
  router file, or create a new one for a new feature area.
- **New DB table or column** — model goes in `app/models/<name>.py`
  **and** an Alembic migration in `migrations/versions/` (`alembic
  revision -m "..."`, then `alembic upgrade head`). Both are required:
  the app refuses to start against a database behind the migration
  head, and `tests/test_gate_290_fixes.py` fails on a table only
  `create_all()` makes.
- **New Pydantic shape** — `app/schemas/<domain>.py`. **Beware the
  `date: date` field-shadows-type collision** — see
  [CONTRIBUTING.md → Schema conventions](../CONTRIBUTING.md#-the-date-date-field-name-shadows-the-type-collision).
- **New SPA page** — `app/static/js/<name>.js` exporting a module
  object with a `render()` method; register the hash route in
  `app/static/js/app.js`. Talk to the backend through `API.get/post/
  put/del` (the `del` spelling matters — not `delete`).
- **Tests** — `tests/test_<area>.py`. The wiring audit
  (`tests/test_wiring.py`) will fail CI if your new endpoint has no
  SPA caller AND isn't on the `_INTENTIONAL_BACKEND_ONLY` allowlist.

---

## Running tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                       # the full suite, about 7,200 tests; 40 minutes on one machine
pytest tests/test_wiring.py  # under a second: the JS <-> backend wiring audit
pytest -k "audit or portal"  # subset by keyword
```

The browser tests need Playwright's Chromium (`playwright install
chromium`) and skip without it; the PostgreSQL-only tests run when
`SLOWBOOKS_TEST_POSTGRES_URL` names a database. To run the app while you
work: `cp .env.example .env`, set `APP_DEBUG=true`, `APP_HOST=127.0.0.1` and
a `PAYROLL_ENCRYPTION_SECRET`, then `python run.py` and open
http://localhost:3001 (INSTALL.md, Option 2, has the whole setup). The
desktop window is `python desktop_launcher.py`.

The wiring audit is also a boot-time tripwire in
`docker-entrypoint.sh`: a container that has pytest installed (the
Dockerfile installs `requirements.txt` only, so add `requirements-dev.txt`
in a debug image) fails to start if any JS API call points at a removed
route. Production images skip the boot check silently — CI already gates
the same condition before the image is built.

### Manual end-to-end smoke test

`scripts/integration_test_frontend.py` is a live-HTTP integration
script (deliberately not under `tests/` so the pytest collector
skips it). It covers the HR and payroll pages against a fresh
database at `127.0.0.1:8000`, confirming the pages and their API calls
answer over real HTTP. Run it against a server started for it when
you've touched those pages:

```bash
uvicorn app.main:app --port 8000 &
python scripts/integration_test_frontend.py
```

---

## Lint + format

```bash
pip install "black>=24.8.0" "ruff>=0.6.0"
black --check app/ tests/ scripts/   # what CI runs
ruff check app/ tests/ scripts/
```

CI lints the whole tree (`.github/workflows/ci.yml`); both pass on every
release tag.

---

## Contributor flow

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full guide. The short
version:

1. Branch off `main`
2. Make changes; add/update tests
3. `pytest` green locally
4. Open a PR — CI runs lint, the full suite on Linux and Windows, pip-audit, CodeQL and the Docker build; the installer and DMG build on their own workflows
5. Reviewer merges once green

The PR template prompts for: summary, test plan, security/perf notes,
and any docs updates.

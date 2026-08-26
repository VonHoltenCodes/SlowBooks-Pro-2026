# Slowbooks Pro 2026

**A personal bookkeeping application raised from the ashes of QuickBooks 2003 Pro.**

Free, source-available, and complete: double-entry accounting, unlimited
invoicing, US payroll with tamper-evident tax forms, perpetual inventory,
bank feeds, analytics — with every record in local files you control. No
cloud, no account, no telemetry, no caps, no paid tiers. **Multi-user
Server Edition is built into the same signed installer — no Docker
required** (Docker remains an optional path for Linux servers).

**Get started:**
[Windows installer](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-Setup-x64.exe) ·
[macOS DMG](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-macos-arm64.dmg) ·
[Docker / Linux](#quick-start) ·
[slowbookspro.com](https://www.slowbookspro.com)

![Slowbooks Pro 2026 Edition and Server Edition](screenshots/hero-abouts.png)

*One free product, two shapes: your desktop — or the whole office from one PC.*

---

## The Story

I ran QuickBooks 2003 Pro for 14 years for side-business invoicing and
bookkeeping. Then the hard drive died. Intuit's activation servers have
been dead since ~2017, so the software can't be reinstalled. The license
I paid for is worthless.

So I built my own replacement, and transferred my data out of the old
.QBW file using IIF export/import. Early versions wore the grief openly —
the code was annotated with invented "decompilation" comments referencing
`QBW32.EXE` offsets and Btrieve table layouts as a tribute to software
that served me well until its maker decided it should stop working. The
codebase has since grown up and the fiction now lives only in this
origin story; the software never depended on it.

**This is an independent, from-scratch reimplementation.** No Intuit
source code or binaries were available, decompiled, or used.

---

## What's New

**v2.6 — Sales receipts.** Point-of-sale style sales on one screen: the
sale and its payment recorded together, deposited where you say, posted
atomically — and kept on their own page so they don't clutter your
invoices. Your existing receipt history imports too: `CASH SALE` blocks
from QuickBooks Desktop IIF files and the SalesReceipt entity over the
QuickBooks Online connection, with a migration guide covering both paths
([docs/migrate-from-quickbooks.md](docs/migrate-from-quickbooks.md)).
Built because a user asked for it.

**v2.5 — Server Edition.** The same signed installer can serve your whole
office from one Windows PC: users with roles (admin / bookkeeper /
read-only), username logins, per-user audit attribution, and a startup
task that has the books serving before anyone logs in — everyone else
just needs a browser. An edition is a state, not a SKU: add a second user
and you've promoted yourself, free either way. Field-verified on real
office hardware before release. See
**[docs/server-edition.md](docs/server-edition.md)**.

v2.5 also debuts the **signed & notarized Apple Silicon macOS app**
(maintained by [@ContractorKeith](https://github.com/ContractorKeith)) —
a native `.app` in a DMG, no Docker or Python required.

![Server Edition: LAN-served dashboard and user management](screenshots/server-edition-grid.png)

**v2.4 — Bank feeds & the AI-ready API.** Automatic transaction sync via
[SimpleFIN](https://www.simplefin.org/) — you hold the bank credential,
no middleman server, dedup + bank rules on arrival
([docs/setup-bank-feeds.md](docs/setup-bank-feeds.md)). Every install
also serves a self-documenting 357-operation local REST API; point
Claude Code or any agentic CLI at it —
[slowbookspro.com/ai](https://www.slowbookspro.com/ai/) has the
paste-prompt.

**v2.3 — Migrate from anywhere.** One Migrate Data page for Xero, MYOB,
Sage 50, Wave, Zoho Books, and GnuCash — every import dry-run-verified
against your trial balance before a single record is written, with
opening balances posted automatically.

Full history in **[CHANGELOG.md](CHANGELOG.md)**.

---

## Wait — it does *that*?

**Cryptographically tamper-evident tax forms.** Every W-2, W-3, 940, and
941 PDF carries a SHA-256 content hash and audit ID printed in the
footer. An auditor can recompute the hash and confirm the form hasn't
been edited since generation, against the local `document_audits` chain.
Not a watermark — a verification trail.

**Bring-your-own-AI, including your own gateway.** AI Insights runs
against any of seven providers (xAI Grok, Groq, Cloudflare Workers AI,
Anthropic Claude, OpenAI, Google Gemini, or a Cloudflare Worker you host
yourself) — keys encrypted at rest with versioned, rotatable ciphertext.
And the whole app is agent-operable through its local API: see the
[AI setup guide](https://www.slowbookspro.com/ai/).

**One-click reseller-permit verification.** Per-state format validation
(WA/CA/TX), one click opens the state's official lookup, and the
who-and-when verification trail lands on the customer record.

**Boots refuse to lie to you.** Dev and debug containers run the
frontend↔backend wiring audit *before* uvicorn binds the port — drift
between the JS and the routes fails the boot instead of 404-ing
mid-feature. Release images gate on the same check in CI.

---

## What it does

Full catalog (300+ entries) in **[docs/features.md](docs/features.md)**. Highlights:

- **Accounts receivable** — invoices, estimates, payments with
  multi-invoice allocation, credit memos, recurring schedules, batch
  payments, Quick Entry for paper backlogs
- **Accounts payable** — purchase orders, bills, bill payments, AP aging
- **Double-entry core** — auto + manual journals, closing-date
  enforcement, automatic audit log, 50-account contractor chart
- **Banking** — register, deposits, reconciliation, check printing,
  OFX/QFX + Chase/PayPal CSV import with dedup, SimpleFIN bank feeds,
  shared auto-categorization rules
- **Reports & tax** — P&L (plain & by Class), Balance Sheet, Trial
  Balance, agings, GL, Cash Flow, Sales Tax with pay-to-government flow,
  Schedule C, printable PDF pack
- **Payroll & HR** — full US module with W-2/W-3/940/941, deductions,
  garnishments, PTO, onboarding, and a token-accessed employee portal
  ([docs/payroll-hr-module.md](docs/payroll-hr-module.md))
- **Inventory** — perpetual ledger, weighted-average cost, automatic
  COGS, reorder points
- **Analytics + AI** — 8 live metrics, 90-day cash forecast, optional
  BYOK insights
- **Server Edition** — users, roles, attributed audit trail, serves the
  office from one PC ([docs/server-edition.md](docs/server-edition.md))
- **Online payments** — [Stripe](docs/setup-stripe.md),
  [PayPal](docs/setup-paypal.md), [Square](docs/setup-square.md) behind
  one abstraction, desktop-mode recording included
- **Interop & migration** — QuickBooks IIF round-trip incl. sales
  receipts ([docs/migrate-from-quickbooks.md](docs/migrate-from-quickbooks.md)),
  [QBO OAuth sync](docs/setup-qbo.md), Migrate Data for Xero / MYOB /
  Sage 50 / Wave / Zoho Books / GnuCash, Opening Balances wizard
- **Fixed assets** — register, depreciation runs, disposal with
  gain/loss, reconciliation report
- **Duplicate detection** — fuzzy customer/vendor matching at create time

![Company Snapshot in light and dark themes](screenshots/hero-themes.png)

*Both themes ship in the box — toggle from the topbar or `Alt+D`; the choice persists.*

![Invoicing, analytics, inventory, and duplicate detection](screenshots/features-grid.png)

---

## Quick Start

### Windows — signed installer

Download **[SlowBooksPro-Setup-x64.exe](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-Setup-x64.exe)**
and double-click. Fully self-contained (64-bit Windows 10/11); portable
.zip on the [releases page](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest).
Each company is one SQLite file under `%LOCALAPPDATA%\SlowBooksPro` —
upgrades and even uninstalls never touch your books.

**Serve the office (Server Edition):** on the host PC, run the bundled
`serveredition-install.ps1` from an elevated PowerShell — firewall,
startup task, and machine-wide data location handled. Details in
[docs/server-edition.md](docs/server-edition.md).

### macOS — signed Apple Silicon app

Download **[SlowBooksPro-macos-arm64.dmg](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-macos-arm64.dmg)**,
drag **SlowBooks Pro** to Applications, launch. Signed and notarized;
macOS 14+. Intel Macs: use Docker until a tested Intel build ships.

### Docker (Linux servers, Intel Mac)

Docker is optional — multi-user LAN serving on Windows is **Server
Edition**, built into the signed installer above (no containers involved).
Docker remains the path for Linux servers and Intel Macs:

```bash
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git
cd SlowBooks-Pro-2026
docker compose up
```

Open **http://localhost:3001** — PostgreSQL, migrations, and seed data
are automatic.

Native installs, demo data, troubleshooting: **[INSTALL.md](INSTALL.md)**.
Backups, restore, key rotation: **[docs/operations.md](docs/operations.md)**.
Production checklist: **[docs/release-checklist.md](docs/release-checklist.md)**.

---

## Documentation

| Doc | Covers |
|-----|--------|
| [INSTALL.md](INSTALL.md) | Install / first-run / upgrade (installer + DMG + Docker + native) |
| [docs/server-edition.md](docs/server-edition.md) | Serving the office: setup, users & roles, troubleshooting |
| [packaging/macos/README.md](packaging/macos/README.md) | macOS maintainer build, signing, notarization runbook |
| [docs/features.md](docs/features.md) | Full feature catalog + API endpoint reference |
| [docs/development.md](docs/development.md) | Tech stack, project structure, contributor flow |
| [docs/data-model.md](docs/data-model.md) | Database schema — 55 tables |
| [docs/operations.md](docs/operations.md) | Backups, restore, key rotation, monitoring |
| [docs/payroll-hr-module.md](docs/payroll-hr-module.md) | Payroll / HR module reference |
| [docs/release-checklist.md](docs/release-checklist.md) | Production deployment checklist |
| [docs/tls-proxy-setup.md](docs/tls-proxy-setup.md) | Real certs in front of Slowbooks (Caddy, nginx, Traefik) |
| [docs/security-hardening.md](docs/security-hardening.md) | Security pass — what changed, why, how it's tested |
| [docs/hipaa-compliance.md](docs/hipaa-compliance.md) | HIPAA mapping — honest gap list included |
| [docs/wiring-audit.md](docs/wiring-audit.md) | Frontend ↔ backend drift audit methodology |
| [docs/setup-bank-feeds.md](docs/setup-bank-feeds.md) | SimpleFIN bank feeds |
| [docs/setup-qbo.md](docs/setup-qbo.md) · [Stripe](docs/setup-stripe.md) · [PayPal](docs/setup-paypal.md) · [Square](docs/setup-square.md) | Integrations |
| [docs/migrate-from-myob.md](docs/migrate-from-myob.md) | MYOB migration walkthrough |
| [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) | Policy, contributing, history |

---

## Tech Stack

Python + FastAPI on PostgreSQL (SQLite for tests and desktop companies,
one file each) with SQLAlchemy 2.0 and Alembic. Vanilla HTML/CSS/JS
single-page app — no framework, no build step. WeasyPrint + Jinja2 for
PDFs; self-hosted Chart.js (no CDN, LAN-deployable). Hosted-checkout
payments only — card data never touches the app. Port 3001.

The Windows and Apple Silicon desktop builds freeze the same codebase
with PyInstaller + pywebview. Both sign in CI on every release tag:
Windows via Azure Trusted Signing, macOS with the project's Apple
Developer ID — signed, notarized, and stapled on the runner (signing
credentials live only in repo secrets, never in the repo).

Full layout in [docs/development.md](docs/development.md).

---

## License

**Source Available — free for personal and enterprise use. No commercial
resale.** Use it, modify it, run your business on it; don't sell it or
offer it as a paid service. Full terms in [LICENSE](LICENSE).

---

## Acknowledgments

- 14 years of QuickBooks 2003 Pro (1 license, $199.95, 2003 dollars)
- The reverse-engineering community, for the aesthetic the early
  codebase cosplayed
- The Pervasive PSQL documentation that nobody else has read since 2005
- Every small business owner who lost software they paid for when
  activation servers died

---

## Contributors

- [VonHoltenCodes](https://github.com/VonHoltenCodes) — Creator
- [PNWImport](https://github.com/PNWImport) — Security hardening (auth, CORS, path traversal, atomic writes, non-root Docker, rate limiting), analytics engine, AI insights with 7-provider support, Cloudflare Worker gateway, inventory ledger, drill-down reports, fuzzy duplicate detection, saved reports, payroll/HR module, tax-form audit chain, reseller-permit module, customer details popout
- [jake-378](https://github.com/jake-378) — Backup UI fixes, report period selectors, invoice terms autofill, date validation fixes
- [moshgrossman](https://github.com/moshgrossman) — Native Windows desktop mode groundwork: SQLite file-per-company with manifest, company picker, desktop launcher, SQLite-compatible migrations, print/PDF window handling; desktop download/save fixes
- [WC3D](https://github.com/WC3D) — Jinja2 XSS security fix
- [Alex Jordan (@LayoverLogic)](https://github.com/LayoverLogic) — Security hardening, IIF BILL/DEPOSIT import, class tracking design, multi-currency design with the Bank of Canada FX service, sortable list columns, country dropdowns
- [amazon1148](https://github.com/amazon1148) — CSV bank import with auto-detection for Chase and PayPal statement formats
- [Joel Macklow (@joelmacklow)](https://github.com/joelmacklow) — Fixed assets, Xero import with dry-run, opening-balance wizard, report PDF pipeline, and security regression-suite concepts, specified in his NZ localization fork
- [Keith (@ContractorKeith)](https://github.com/ContractorKeith) — macOS maintainer: built the native Apple Silicon `.app`/DMG pipeline and the sign/notarize/staple release tooling that now runs in CI, per-user Application Support data layout, frozen-bundle fontconfig self-containment, `.env` permission hardening; long-time macOS field tester

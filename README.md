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
codebase has since grown up; the last of those comments came out in
v2.14.0, and the fiction now lives only in this origin story. The
software never depended on it.

**This is an independent, from-scratch reimplementation.** No Intuit
source code or binaries were available, decompiled, or used.

---

## Accessibility

SlowBooks Pro strives to conform to WCAG 2.1 AA: labelled controls,
real dialogs, live notifications, AA contrast in both themes, and every
generated PDF tagged (PDF/UA-1) so tax forms read to a screen reader.
Details, known gaps and how to report a barrier:
[docs/accessibility.md](docs/accessibility.md).

## What's New

**v2.16 — The year at a glance.** Two new overview cards, both opt-in under
Customize: **P&L: Year to Date** with cumulative net by month, and a
**Balance Sheet Trend** over the last twelve month-ends that balances at
every point and agrees with the report. Contributed by @jarvis4openclaw
(#166). The chart of accounts import offers a CSV template.

![The two opt-in overview cards in light and dark theme: Balance Sheet Trend, three lines for assets, liabilities and equity over the last twelve month-ends with the latest figures above the chart, and P&L Year to Date, net for the year with income, expenses and a bar per month of cumulative net](screenshots/overview-cards.png)

*The two new overview cards, light and dark, on the stress-test company the release gate runs against. Turn them on under Customize.*

**v2.15 — Your chart, from your file.** Import a chart of accounts from a
CSV in the export's own columns, any spreadsheet with Number / Name / Type,
or hledger's account list — tested against files hledger itself wrote. A
dry run shows every row's fate first. Accounts you already have take the
file's names, including the control accounts the software posts to by
number, renamed in place and never duplicated, so every document still finds
its account. Asked for by @tresero (#139, #161).

**v2.14 — In and out of a company, and the terms once.** Opening the
app lands in your last company. Sign out, a new **Switch company** button
and *Choose a different company* on the sign-in screen return to the
company picker instead of the same password prompt, which used to mean
closing the program. A multi-user install lists the users at sign-in. The
first launch shows the short form of the license once, and the Windows
installer shows the full license with an accept step — the first release
under [LICENSE 2.0](LICENSE).

Full history, with the reasoning behind each change, in
**[CHANGELOG.md](CHANGELOG.md)**; the same entries with the test count
per release at [slowbookspro.com/changelog](https://www.slowbookspro.com/changelog/).

![Nonprofit mode on macOS and Windows: the Company Snapshot in donor words, the Statement of Functional Expenses, the Statement of Activities compared to prior year, Releases from Restriction, the Report Center in dark theme, and a Pledge Report PDF](screenshots/nonprofit-grid.png)

*Nonprofit mode: one switch in Settings, and a church, a club or a PTO sees its own words on every screen and every printed page.*

---

## Wait — it does *that*?

**Cryptographically tamper-evident tax forms.** Every W-2, W-3, 940, and
941 PDF carries a SHA-256 content hash and audit ID printed in the
footer. An auditor can recompute the hash and confirm the form hasn't
been edited since generation, against the local `document_audits` chain.
Not a watermark — a verification trail.

**Bring-your-own-AI, including your own gateway.** AI Insights runs
against any of eight providers (xAI Grok, Groq, Cloudflare Workers AI,
Anthropic Claude, OpenAI, Google Gemini, a Cloudflare Worker you host
yourself, or any OpenAI-compatible endpoint you name) — keys encrypted at rest with versioned, rotatable ciphertext.
And the whole app is agent-operable: every install serves a
self-documenting local REST API (507 operations in v2.16) — point Claude
Code or any agentic CLI at it; the
[AI setup guide](https://www.slowbookspro.com/ai/) has the paste-prompt.

**One-click reseller-permit verification.** Per-state format validation
(WA/CA/TX), one click opens the state's official lookup, and the
who-and-when verification trail lands on the customer record.

**Boots refuse to lie to you.** Dev and debug containers run the
frontend↔backend wiring audit *before* uvicorn binds the port — drift
between the JS and the routes fails the boot instead of 404-ing
mid-feature. Release images gate on the same check in CI.

---

## What it does

Full catalog in **[docs/features.md](docs/features.md)**. Highlights:

- **Accounts receivable** — invoices, estimates, payments with
  multi-invoice allocation, credit memos, recurring schedules, batch
  payments, Quick Entry for paper backlogs
- **Accounts payable** — purchase orders, bills, bill payments, vendor
  credits, AP aging
- **Double-entry core** — auto + manual journals, closing-date
  enforcement, automatic audit log, 50-account contractor chart
- **Banking** — the register is the ledger (entries post, feeds are a review queue, reconciliation over ledger lines), transfers, deposits, check printing,
  OFX/QFX + Bank of America/Chase/PayPal CSV import with dedup, SimpleFIN bank feeds,
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
  office from one PC, built into the same signed installer
  ([docs/server-edition.md](docs/server-edition.md))
- **Bank feeds** — [SimpleFIN](https://www.simplefin.org/): you hold the
  bank credential, no middleman server
  ([docs/setup-bank-feeds.md](docs/setup-bank-feeds.md))
- **Jobs & job costing** — Customer:Job on every form, cost codes and
  types with burden, time posted at loaded rates, budget vs actual
- **Receipt intake** — scan a photo or PDF into a Bill, Expense or Sales
  Receipt with the OCR built into macOS and Windows (Tesseract on Linux)
- **Online payments** — [Stripe](docs/setup-stripe.md),
  [PayPal](docs/setup-paypal.md), [Square](docs/setup-square.md) behind
  one abstraction, desktop-mode recording included
- **Interop & migration** — QuickBooks IIF round-trip incl. sales
  receipts ([docs/migrate-from-quickbooks.md](docs/migrate-from-quickbooks.md)),
  [QBO OAuth sync](docs/setup-qbo.md), Migrate Data for Xero / MYOB /
  Sage 50 / Wave / Zoho Books / GnuCash, Opening Balances wizard
- **Fixed assets** — register, depreciation runs, disposal with
  gain/loss, reconciliation report
- **Nonprofit mode** — your own words on every screen and document; funds
  with restrictions, releases, functional expenses, donor acknowledgments,
  giving statements, pledges
  ([docs/nonprofit-module.md](docs/nonprofit-module.md))
- **Accessibility** — WCAG 2.1 AA, tagged PDFs
  ([docs/accessibility.md](docs/accessibility.md))
- **Duplicate detection** — fuzzy customer/vendor matching at create time

![Company Snapshot in light and dark themes](screenshots/hero-themes.png)

*Both themes ship in the box — toggle from the topbar or `Alt+D`; the choice persists.*

![Invoicing, analytics, inventory, and duplicate detection](screenshots/features-grid.png)

![Server Edition: LAN-served dashboard and user management](screenshots/server-edition-grid.png)

*Server Edition: an edition is a state, not a SKU — add a second user and you've promoted yourself, free either way.*

---

## Quick Start

### Windows — signed installer

Download **[SlowBooksPro-Setup-x64.exe](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-Setup-x64.exe)**
and double-click. Fully self-contained (64-bit Windows 10/11); a portable
.zip is on the [releases page](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest)
— it needs the Microsoft Edge WebView2 runtime, which Windows 11 has and the
installer sets up; without it the app offers to open in your browser instead.
Each company is one SQLite file under `%LOCALAPPDATA%\SlowBooksPro` —
upgrades and even uninstalls never touch your books.

**Serve the office (Server Edition):** on the host PC, run the bundled
`serveredition-install.ps1` from an elevated PowerShell — firewall,
startup task, and machine-wide data location handled. Details in
[docs/server-edition.md](docs/server-edition.md).

### macOS — signed Apple Silicon app

Download **[SlowBooksPro-macos-arm64.dmg](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/latest/download/SlowBooksPro-macos-arm64.dmg)**,
drag **SlowBooks Pro** to Applications, launch. Signed and notarized with
the project's Apple Developer ID on every release; macOS 14+, Apple
Silicon. Intel Macs: Docker.

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
are automatic. The image includes `tesseract-ocr` and `poppler-utils` so
receipt scanning works out of the box; native installs add them with
`sudo apt install tesseract-ocr poppler-utils` (optional — scanning
degrades gracefully when they're absent).

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
| [docs/data-model.md](docs/data-model.md) | Database schema |
| [docs/operations.md](docs/operations.md) | Backups, restore, key rotation, monitoring |
| [docs/payroll-hr-module.md](docs/payroll-hr-module.md) | Payroll / HR module reference |
| [docs/release-checklist.md](docs/release-checklist.md) | Production deployment checklist |
| [docs/tls-proxy-setup.md](docs/tls-proxy-setup.md) | Real certs in front of Slowbooks (Caddy, nginx, Traefik) |
| [docs/security-hardening.md](docs/security-hardening.md) | Security pass — what changed, why, how it's tested |
| [docs/hipaa-compliance.md](docs/hipaa-compliance.md) | HIPAA mapping — honest gap list included |
| [docs/wiring-audit.md](docs/wiring-audit.md) | Frontend ↔ backend drift audit methodology |
| [docs/banking.md](docs/banking.md) | The register is the ledger: entries, feeds as a review queue, transfers, reconciliation |
| [docs/nonprofit-module.md](docs/nonprofit-module.md) | Nonprofit mode: funds, restrictions, functional expenses, donor documents |
| [docs/accessibility.md](docs/accessibility.md) | WCAG 2.1 AA conformance, known gaps, how to report a barrier |
| [docs/migrate-from-quickbooks.md](docs/migrate-from-quickbooks.md) | QuickBooks Desktop (IIF) and Online migration, sales receipts included |
| [docs/state-withholding.md](docs/state-withholding.md) | State income-tax withholding tables and their sources |
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

**Source-available. Free forever. Yours to self-host.** Use it for
yourself or your business, modify it, redistribute it, keep your clients'
books on it. Don't sell it, offer it as a paid service, or build it into
one, in whole or in part. Tools and connectors that talk to it are
welcome, commercial or not. Illinois law. The full terms, version 2.0,
are in [LICENSE](LICENSE); the app shows the short form once on first
launch and the Windows installer shows the whole thing. Contributions
come in under the Contributor Terms in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Acknowledgments

- 14 years of QuickBooks 2003 Pro (1 license, $199.95, 2003 dollars)
- Every small business owner who lost software they paid for when
  activation servers died

---

## Contributors

- [VonHoltenCodes](https://github.com/VonHoltenCodes) — creator and maintainer
- [Keith (@ContractorKeith)](https://github.com/ContractorKeith) — macOS testing and review

Maintainers by platform are in [CONTRIBUTING.md](CONTRIBUTING.md). Everyone
who has contributed is credited in the [CHANGELOG](CHANGELOG.md) entry that
shipped their work and in the git history.

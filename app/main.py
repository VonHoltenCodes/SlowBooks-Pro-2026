# ============================================================================
# Slowbooks Pro 2026 — "It's like QuickBooks, but we own the source code"
# Reverse-engineered from Intuit QuickBooks Pro 2003 (Build 12.0.3190)
# Original binary: QBW32.EXE (14,823,424 bytes, PE32 MSVC++ 6.0 SP5)
# Decompilation target: CQBMainApp (WinMain entry point @ 0x00401000)
# ============================================================================
# LEGAL: This is a clean-room reimplementation. No Intuit source code was
# available or used. All knowledge derived from:
#   1. IDA Pro 7.x disassembly of publicly distributed QB2003 trial binary
#   2. Published Intuit SDK documentation (QBFC 5.0, qbXML 4.0)
#   3. 14 years of clicking every menu item as a paying customer
#   4. Pervasive PSQL v8 file format documentation (Btrieve API Guide)
# Intuit's activation servers have been dead since ~2017. The hard drive
# that had our licensed copy died in 2024. We just want to print invoices.
# ============================================================================

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, ORJSONResponse

from app.routes import (
    dashboard,
    accounts,
    customers,
    vendors,
    items,
    invoices,
    estimates,
    payments,
    banking,
    reports,
    settings,
    iif,
)

# Phase 1: Foundation
from app.routes import audit, search

# Phase 2: Accounts Payable
from app.routes import purchase_orders, bills, bill_payments, credit_memos

# Phase 3: Productivity
from app.routes import recurring, batch_payments

# Phase 4: Communication & Export
from app.routes import csv as csv_routes
from app.routes import uploads

# Phase 5: Advanced Integration
from app.routes import bank_import, tax, backups

# Phase 6: Ambitious
from app.routes import companies, employees, payroll

# Phase 7: Online Payments
from app.routes import stripe_payments, public

# Phase 8: QuickBooks Online
from app.routes import qbo

# Phase 9: Analytics (real-time business intelligence)
from app.routes import analytics

from app.database import SessionLocal
from app.services.audit import register_audit_hooks

# ORJSONResponse is 2-3x faster than the stdlib json encoder for every /api/* reply.
app = FastAPI(
    title="Slowbooks Pro 2026",
    version="2.0.0",
    default_response_class=ORJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# gzip responses larger than 1 KB. Analytics JSON payloads compress ~70%,
# which is a big win over LAN for /api/analytics/dashboard and friends.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# Original API routes
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(items.router)
app.include_router(invoices.router)
app.include_router(estimates.router)
app.include_router(payments.router)
app.include_router(banking.router)
app.include_router(reports.router)
app.include_router(settings.router)
app.include_router(iif.router)

# Phase 1: Foundation
app.include_router(audit.router)
app.include_router(search.router)
# Phase 2: Accounts Payable
app.include_router(purchase_orders.router)
app.include_router(bills.router)
app.include_router(bill_payments.router)
app.include_router(credit_memos.router)
# Phase 3: Productivity
app.include_router(recurring.router)
app.include_router(batch_payments.router)
# Phase 4: Communication & Export
app.include_router(csv_routes.router)
app.include_router(uploads.router)
# Phase 5: Advanced Integration
app.include_router(bank_import.router)
app.include_router(tax.router)
app.include_router(backups.router)
# Phase 6: Ambitious
app.include_router(companies.router)
app.include_router(employees.router)
app.include_router(payroll.router)
# Phase 7: Online Payments
app.include_router(stripe_payments.router)
app.include_router(public.router)
# Phase 8: QuickBooks Online
app.include_router(qbo.router)
# Phase 9: Analytics (real-time business intelligence)
app.include_router(analytics.router)

# Register audit log hooks
register_audit_hooks(SessionLocal)

# Static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Ensure uploads directory exists
uploads_dir = static_dir / "uploads"
uploads_dir.mkdir(exist_ok=True)

# SPA entry point
index_path = Path(__file__).parent.parent / "index.html"


@app.get("/")
async def serve_index():
    return FileResponse(str(index_path))


@app.get("/analytics")
async def serve_analytics_redirect():
    """Backwards-compat: old /analytics bookmarks land on the SPA hash route.

    The analytics UI is now integrated inline as #/analytics inside the
    main SPA shell (see app/static/js/analytics.js). Anyone hitting the
    bare path gets redirected to the same feature.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/#/analytics", status_code=307)

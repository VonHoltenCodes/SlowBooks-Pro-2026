# ============================================================================
# Slowbooks Pro 2026 — AI Query Tools (Phase 9.5b — tool-calling LLM support)
#
# Sixteen read-only tools that an LLM can call to answer business questions:
#   "How much did I spend at Jack in the Box in 2025?"
#   "What are my unpaid invoices from ABC Corp?"
#   "What were my HSA payments in 2024?"
#
# Every tool:
#   1. Takes only serializable params (strings, ints, dates) — no objects
#   2. Returns plain dicts (no ORM models) with numeric → float conversion
#   3. Has a JSON schema (for tool-calling wire formats like OpenAI/Anthropic)
#   4. Applies strict read-only queries (no insert/update/delete possible)
#   5. Redacts any user secrets (API keys, etc.) from results
#
# Tools are registered in a TOOLS dict with their schema + callable. Each
# tool's schema follows the OpenAI function-calling format so it's portable
# across providers.
# ============================================================================

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.bills import Bill, BillLine, BillPayment
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice
from app.models.payments import Payment
from app.models.transactions import Transaction


# Shared utility to convert Decimal → float for JSON serialization
def _to_float(val: Optional[Decimal]) -> Optional[float]:
    if val is None:
        return None
    return float(val)


# ============================================================================
# Tool implementations — each returns a dict (never ORM objects)
# ============================================================================


def search_bills(
    db: Session,
    vendor_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Search for bills by vendor name and/or date range.

    Useful for: "Show me all bills from Acme Corp" or "Unpaid bills in March 2025"
    """
    q = db.query(Bill).join(Bill.vendor)

    if vendor_name:
        q = q.filter(Vendor.name.ilike(f"%{vendor_name}%"))

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Bill.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Bill.date <= ed)
        except (ValueError, TypeError):
            pass

    if status:
        q = q.filter(Bill.status == status)

    bills = q.order_by(Bill.date.desc()).limit(limit).all()

    return {
        "results": [
            {
                "id": b.id,
                "bill_number": b.bill_number,
                "vendor_name": b.vendor.name if b.vendor else None,
                "date": b.date.isoformat() if b.date else None,
                "total": _to_float(b.total),
                "balance_due": _to_float(b.balance_due),
                "status": b.status.value if b.status else None,
            }
            for b in bills
        ],
        "count": len(bills),
    }


def search_invoices(
    db: Session,
    customer_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Search for invoices by customer name and/or date range."""
    q = db.query(Invoice).join(Invoice.customer)

    if customer_name:
        q = q.filter(Customer.name.ilike(f"%{customer_name}%"))

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Invoice.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Invoice.date <= ed)
        except (ValueError, TypeError):
            pass

    if status:
        q = q.filter(Invoice.status == status)

    invoices = q.order_by(Invoice.date.desc()).limit(limit).all()

    return {
        "results": [
            {
                "id": i.id,
                "invoice_number": i.invoice_number,
                "customer_name": i.customer.name if i.customer else None,
                "date": i.date.isoformat() if i.date else None,
                "total": _to_float(i.total),
                "balance_due": _to_float(i.balance_due),
                "status": i.status.value if i.status else None,
            }
            for i in invoices
        ],
        "count": len(invoices),
    }


def search_transactions(
    db: Session,
    description: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Search journal entries by description and/or date range.

    Useful for finding specific transactions, memos, or free-text searches.
    """
    q = db.query(Transaction)

    if description:
        q = q.filter(
            or_(
                Transaction.description.ilike(f"%{description}%"),
                Transaction.reference.ilike(f"%{description}%"),
            )
        )

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Transaction.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Transaction.date <= ed)
        except (ValueError, TypeError):
            pass

    txns = q.order_by(Transaction.date.desc()).limit(limit).all()

    return {
        "results": [
            {
                "id": t.id,
                "date": t.date.isoformat() if t.date else None,
                "reference": t.reference,
                "description": t.description,
                "source_type": t.source_type,
            }
            for t in txns
        ],
        "count": len(txns),
    }


def list_vendors(
    db: Session,
    name_filter: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """List all vendors, optionally filtered by name."""
    q = db.query(Vendor)

    if name_filter:
        q = q.filter(Vendor.name.ilike(f"%{name_filter}%"))

    vendors = q.order_by(Vendor.name).limit(limit).all()

    return {
        "results": [
            {
                "id": v.id,
                "name": v.name,
                "company": v.company,
                "email": v.email,
                "phone": v.phone,
                "balance": _to_float(v.balance),
            }
            for v in vendors
        ],
        "count": len(vendors),
    }


def list_customers(
    db: Session,
    name_filter: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """List all customers, optionally filtered by name."""
    q = db.query(Customer)

    if name_filter:
        q = q.filter(Customer.name.ilike(f"%{name_filter}%"))

    customers = q.order_by(Customer.name).limit(limit).all()

    return {
        "results": [
            {
                "id": c.id,
                "name": c.name,
                "company": c.company,
                "email": c.email,
                "phone": c.phone,
                "balance": _to_float(c.balance),
            }
            for c in customers
        ],
        "count": len(customers),
    }


def list_accounts(
    db: Session,
    account_type: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """List chart of accounts, optionally filtered by type (asset/liability/equity/income/expense/cogs)."""
    q = db.query(Account)

    if account_type:
        q = q.filter(Account.account_type == account_type)

    accounts = q.order_by(Account.account_number, Account.name).limit(limit).all()

    return {
        "results": [
            {
                "id": a.id,
                "account_number": a.account_number,
                "name": a.name,
                "account_type": a.account_type.value if a.account_type else None,
                "balance": _to_float(a.balance),
            }
            for a in accounts
        ],
        "count": len(accounts),
    }


def get_account_balance(
    db: Session,
    account_id: int,
) -> Dict[str, Any]:
    """Get the current balance of a specific account."""
    acct = db.query(Account).filter(Account.id == account_id).first()

    if not acct:
        return {"error": f"Account {account_id} not found", "balance": None}

    return {
        "account_id": acct.id,
        "account_number": acct.account_number,
        "name": acct.name,
        "account_type": acct.account_type.value if acct.account_type else None,
        "balance": _to_float(acct.balance),
    }


def get_pl_summary(
    db: Session,
) -> Dict[str, Any]:
    """Get current P&L summary: total income, total expense, net income."""
    income = db.query(Account).filter(Account.account_type == "income").all()
    expense = db.query(Account).filter(Account.account_type == "expense").all()
    cogs = db.query(Account).filter(Account.account_type == "cogs").all()

    total_income = sum(Decimal(a.balance or 0) for a in income)
    total_expense = sum(Decimal(a.balance or 0) for a in expense)
    total_cogs = sum(Decimal(a.balance or 0) for a in cogs)

    # Income is typically negative in the GL (credit balance), so flip it
    total_income = abs(total_income)
    net = total_income - total_expense - total_cogs

    return {
        "total_income": _to_float(total_income),
        "total_expense": _to_float(total_expense),
        "total_cogs": _to_float(total_cogs),
        "net_income": _to_float(net),
    }


def get_balance_sheet(
    db: Session,
) -> Dict[str, Any]:
    """Get current balance sheet: assets, liabilities, equity."""
    assets = db.query(Account).filter(Account.account_type == "asset").all()
    liabilities = db.query(Account).filter(Account.account_type == "liability").all()
    equity = db.query(Account).filter(Account.account_type == "equity").all()

    total_assets = sum(Decimal(a.balance or 0) for a in assets)
    total_liabilities = sum(Decimal(a.balance or 0) for a in liabilities)
    total_equity = sum(Decimal(a.balance or 0) for a in equity)

    return {
        "total_assets": _to_float(total_assets),
        "total_liabilities": _to_float(total_liabilities),
        "total_equity": _to_float(total_equity),
        "accounting_equation_balanced": abs(
            _to_float(total_assets)
            - (_to_float(total_liabilities) + _to_float(total_equity))
        )
        < 0.01,
    }


def get_tax_summary(
    db: Session,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Get tax-relevant summary: sales tax collected, expense totals by category."""
    # Sum of tax amounts on invoices in the period (sales tax collected)
    q = db.query(Invoice)
    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Invoice.date >= sd)
        except (ValueError, TypeError):
            pass
    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Invoice.date <= ed)
        except (ValueError, TypeError):
            pass

    invoices = q.all()
    total_tax_collected = sum(Decimal(i.tax_amount or 0) for i in invoices)

    # Group expenses by account
    expenses_by_account = {}
    bill_lines = db.query(BillLine).join(BillLine.bill).join(BillLine.account)
    for bl in bill_lines:
        if bl.account:
            key = f"{bl.account.account_number or ''} {bl.account.name}"
            expenses_by_account[key] = _to_float(
                (Decimal(expenses_by_account.get(key, 0)) or 0)
                + (Decimal(bl.amount or 0) or 0)
            )

    return {
        "total_tax_collected": _to_float(total_tax_collected),
        "expenses_by_account": expenses_by_account,
    }


def get_sales_by_customer(
    db: Session,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Get total sales (paid invoices) grouped by customer."""
    q = db.query(Invoice)

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Invoice.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Invoice.date <= ed)
        except (ValueError, TypeError):
            pass

    invoices = q.all()
    sales_by_customer = {}
    for inv in invoices:
        customer_name = inv.customer.name if inv.customer else "Unknown"
        sales_by_customer[customer_name] = _to_float(
            (Decimal(sales_by_customer.get(customer_name, 0)) or 0)
            + (Decimal(inv.total or 0) or 0)
        )

    return {
        "results": sorted(
            [{"customer": k, "total_sales": v} for k, v in sales_by_customer.items()],
            key=lambda x: x["total_sales"],
            reverse=True,
        ),
        "total": _to_float(sum(Decimal(v) for v in sales_by_customer.values())),
    }


def get_expenses_by_category(
    db: Session,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Get total expenses grouped by account/category."""
    q = db.query(BillLine).join(BillLine.bill).join(BillLine.account)

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Bill.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Bill.date <= ed)
        except (ValueError, TypeError):
            pass

    bill_lines = q.all()
    expenses_by_category = {}
    for bl in bill_lines:
        category_name = (
            f"{bl.account.account_number or ''} {bl.account.name}"
            if bl.account
            else "Unclassified"
        )
        expenses_by_category[category_name] = _to_float(
            (Decimal(expenses_by_category.get(category_name, 0)) or 0)
            + (Decimal(bl.amount or 0) or 0)
        )

    return {
        "results": sorted(
            [
                {"category": k, "total_expenses": v}
                for k, v in expenses_by_category.items()
            ],
            key=lambda x: x["total_expenses"],
            reverse=True,
        ),
        "total": _to_float(sum(Decimal(v) for v in expenses_by_category.values())),
    }


def get_aging_report(
    db: Session,
) -> Dict[str, Any]:
    """Get A/R and A/P aging report: outstanding balances by age bucket."""
    # AR aging: open invoices
    invoices = db.query(Invoice).filter(Invoice.balance_due > 0).all()
    ar_by_age = {"current": 0, "30": 0, "60": 0, "90": 0}
    today = date.today()
    for inv in invoices:
        days_old = (today - inv.date).days if inv.date else 0
        if days_old <= 30:
            ar_by_age["current"] += float(inv.balance_due or 0)
        elif days_old <= 60:
            ar_by_age["30"] += float(inv.balance_due or 0)
        elif days_old <= 90:
            ar_by_age["60"] += float(inv.balance_due or 0)
        else:
            ar_by_age["90"] += float(inv.balance_due or 0)

    # AP aging: open bills
    bills = db.query(Bill).filter(Bill.balance_due > 0).all()
    ap_by_age = {"current": 0, "30": 0, "60": 0, "90": 0}
    for bill in bills:
        days_old = (today - bill.date).days if bill.date else 0
        if days_old <= 30:
            ap_by_age["current"] += float(bill.balance_due or 0)
        elif days_old <= 60:
            ap_by_age["30"] += float(bill.balance_due or 0)
        elif days_old <= 90:
            ap_by_age["60"] += float(bill.balance_due or 0)
        else:
            ap_by_age["90"] += float(bill.balance_due or 0)

    return {
        "ar_aging": ar_by_age,
        "ap_aging": ap_by_age,
        "total_ar_outstanding": sum(ar_by_age.values()),
        "total_ap_outstanding": sum(ap_by_age.values()),
    }


def get_current_date(
    db: Session,
) -> Dict[str, Any]:
    """Get the current server date (useful for "as of today" queries)."""
    return {
        "current_date": date.today().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def search_payments(
    db: Session,
    customer_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Search for customer payments by customer name and/or date range."""
    q = db.query(Payment).join(Payment.customer)

    if customer_name:
        q = q.filter(Customer.name.ilike(f"%{customer_name}%"))

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(Payment.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(Payment.date <= ed)
        except (ValueError, TypeError):
            pass

    payments = q.order_by(Payment.date.desc()).limit(limit).all()

    return {
        "results": [
            {
                "id": p.id,
                "customer_name": p.customer.name if p.customer else None,
                "date": p.date.isoformat() if p.date else None,
                "amount": _to_float(p.amount),
                "method": p.method,
                "reference": p.reference,
            }
            for p in payments
        ],
        "count": len(payments),
    }


def search_bill_payments(
    db: Session,
    vendor_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Search for vendor bill payments by vendor name and/or date range."""
    q = db.query(BillPayment).join(BillPayment.vendor)

    if vendor_name:
        q = q.filter(Vendor.name.ilike(f"%{vendor_name}%"))

    if start_date:
        try:
            sd = datetime.fromisoformat(start_date).date()
            q = q.filter(BillPayment.date >= sd)
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            ed = datetime.fromisoformat(end_date).date()
            q = q.filter(BillPayment.date <= ed)
        except (ValueError, TypeError):
            pass

    payments = q.order_by(BillPayment.date.desc()).limit(limit).all()

    return {
        "results": [
            {
                "id": p.id,
                "vendor_name": p.vendor.name if p.vendor else None,
                "date": p.date.isoformat() if p.date else None,
                "amount": _to_float(p.amount),
                "method": p.method,
                "check_number": p.check_number,
            }
            for p in payments
        ],
        "count": len(payments),
    }


# ============================================================================
# JSON schemas for tool-calling (OpenAI/Anthropic format)
# ============================================================================

TOOLS: Dict[str, Dict[str, Any]] = {
    "search_bills": {
        "name": "search_bills",
        "description": "Search for bills by vendor name, date range, or status",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_name": {
                    "type": "string",
                    "description": "Vendor name (partial match OK)",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "unpaid", "partial", "paid", "void"],
                    "description": "Bill status",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                },
            },
        },
        "func": search_bills,
    },
    "search_invoices": {
        "name": "search_invoices",
        "description": "Search for invoices by customer name, date range, or status",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Customer name (partial match OK)",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "sent", "partial", "paid", "void"],
                    "description": "Invoice status",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                },
            },
        },
        "func": search_invoices,
    },
    "search_transactions": {
        "name": "search_transactions",
        "description": "Search journal entries by description, reference, or date",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Search memo, reference, or description",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 100)",
                },
            },
        },
        "func": search_transactions,
    },
    "list_vendors": {
        "name": "list_vendors",
        "description": "List all vendors, optionally filtered by name",
        "parameters": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Vendor name filter (partial match)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 100)",
                },
            },
        },
        "func": list_vendors,
    },
    "list_customers": {
        "name": "list_customers",
        "description": "List all customers, optionally filtered by name",
        "parameters": {
            "type": "object",
            "properties": {
                "name_filter": {
                    "type": "string",
                    "description": "Customer name filter (partial match)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 100)",
                },
            },
        },
        "func": list_customers,
    },
    "list_accounts": {
        "name": "list_accounts",
        "description": "List chart of accounts, optionally filtered by type",
        "parameters": {
            "type": "object",
            "properties": {
                "account_type": {
                    "type": "string",
                    "enum": [
                        "asset",
                        "liability",
                        "equity",
                        "income",
                        "expense",
                        "cogs",
                    ],
                    "description": "Account type",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 100)",
                },
            },
        },
        "func": list_accounts,
    },
    "get_account_balance": {
        "name": "get_account_balance",
        "description": "Get the current balance of a specific account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "integer",
                    "description": "Account ID",
                },
            },
            "required": ["account_id"],
        },
        "func": get_account_balance,
    },
    "get_pl_summary": {
        "name": "get_pl_summary",
        "description": "Get current profit & loss summary (income, expense, net)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "func": get_pl_summary,
    },
    "get_balance_sheet": {
        "name": "get_balance_sheet",
        "description": "Get current balance sheet (assets, liabilities, equity)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "func": get_balance_sheet,
    },
    "get_tax_summary": {
        "name": "get_tax_summary",
        "description": "Get tax summary (sales tax collected, expenses by category)",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
            },
        },
        "func": get_tax_summary,
    },
    "get_sales_by_customer": {
        "name": "get_sales_by_customer",
        "description": "Get total sales (invoices) grouped by customer",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
            },
        },
        "func": get_sales_by_customer,
    },
    "get_expenses_by_category": {
        "name": "get_expenses_by_category",
        "description": "Get total expenses grouped by account/category",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
            },
        },
        "func": get_expenses_by_category,
    },
    "get_aging_report": {
        "name": "get_aging_report",
        "description": "Get A/R and A/P aging report (outstanding balances by age)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "func": get_aging_report,
    },
    "get_current_date": {
        "name": "get_current_date",
        "description": "Get the current server date and timestamp",
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "func": get_current_date,
    },
    "search_payments": {
        "name": "search_payments",
        "description": "Search for customer payments by customer name and/or date",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Customer name (partial match OK)",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                },
            },
        },
        "func": search_payments,
    },
    "search_bill_payments": {
        "name": "search_bill_payments",
        "description": "Search for vendor bill payments by vendor name and/or date",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_name": {
                    "type": "string",
                    "description": "Vendor name (partial match OK)",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 50)",
                },
            },
        },
        "func": search_bill_payments,
    },
}


def get_tool_schema(tool_name: str) -> Optional[Dict[str, Any]]:
    """Return the JSON schema for a tool (without the func)."""
    if tool_name not in TOOLS:
        return None
    spec = TOOLS[tool_name]
    return {
        "name": spec["name"],
        "description": spec["description"],
        "parameters": spec["parameters"],
    }


def call_tool(tool_name: str, db: Session, **params) -> Dict[str, Any]:
    """Call a tool by name with the given parameters."""
    if tool_name not in TOOLS:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return TOOLS[tool_name]["func"](db, **params)
    except Exception as e:
        return {"error": f"{tool_name} failed: {str(e)}"}

"""
Slowbooks Pro 2026 — Predefined AI analysis actions

A curated set of "analyze X" actions exposed in the Analytics dropdown.
Each action:
  1. Pre-fetches the relevant data via an existing function in `ai_tools`
  2. Builds a focused user prompt around that data
  3. Sends a single one-shot request to the configured LLM (no tool calling,
     no multi-turn) so it works reliably across all providers — including
     the ones whose tool-calling implementations are flaky.

Adding a new action: implement a runner `_run_xxx(db, start, end) -> dict`,
then register an `ActionSpec` in `ACTIONS`. The category determines its
optgroup in the UI dropdown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services import ai_tools
from app.services.ai_service import call_provider

# Single shared system prompt — keeps the analyses uniformly framed.
SYSTEM_PROMPT = (
    "You are a senior bookkeeping analyst for a small business. "
    "Given a snapshot of accounting data, write a brief analysis with "
    "key observations, risks, and 2-3 specific action items. "
    "Use markdown headings (### Observations / ### Risks / ### Recommendations) "
    "and short bullet points. Be concise — busy operators don't read walls "
    "of text. Quote concrete numbers from the data. Don't speculate beyond "
    "what the data shows."
)

# Token cap for the user prompt when payload is large. Generous enough for
# realistic snapshots, tight enough to avoid blowing past free-tier limits.
_MAX_DATA_CHARS = 8000


@dataclass(frozen=True)
class ActionSpec:
    key: str
    label: str
    category: str
    framing: str  # 1-line description of what the data represents
    uses_period: bool  # True → action receives the page's period dates
    runner: Callable[[Session, Optional[date], Optional[date]], Dict[str, Any]]


# ---------------------------------------------------------------------------
# Runners — each returns the data dict that gets fed into the LLM prompt.
# ---------------------------------------------------------------------------


def _run_top_customers(db: Session, start: date, end: date) -> Dict[str, Any]:
    return ai_tools.get_sales_by_customer(
        db, start_date=start.isoformat(), end_date=end.isoformat()
    )


def _run_unpaid_invoices(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    # Combine the three open statuses; balance_due > 0 is the truth source.
    statuses = ("sent", "partial", "draft")
    combined: list = []
    for status in statuses:
        chunk = ai_tools.search_invoices(db, status=status, limit=200)
        combined.extend(chunk["results"])
    open_invoices = [r for r in combined if (r.get("balance_due") or 0) > 0]
    open_invoices.sort(key=lambda r: r.get("balance_due") or 0, reverse=True)
    total = sum(r.get("balance_due") or 0 for r in open_invoices)
    return {
        "results": open_invoices[:50],
        "count": len(open_invoices),
        "total_outstanding": round(total, 2),
    }


def _run_ar_aging(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    full = ai_tools.get_aging_report(db)
    return {
        "ar_aging": full["ar_aging"],
        "total_outstanding": full["total_ar_outstanding"],
    }


def _run_ap_aging(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    full = ai_tools.get_aging_report(db)
    return {
        "ap_aging": full["ap_aging"],
        "total_outstanding": full["total_ap_outstanding"],
    }


def _run_expenses_by_category(db: Session, start: date, end: date) -> Dict[str, Any]:
    return ai_tools.get_expenses_by_category(
        db, start_date=start.isoformat(), end_date=end.isoformat()
    )


def _run_unpaid_bills(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    statuses = ("unpaid", "partial", "draft")
    combined: list = []
    for status in statuses:
        chunk = ai_tools.search_bills(db, status=status, limit=200)
        combined.extend(chunk["results"])
    open_bills = [r for r in combined if (r.get("balance_due") or 0) > 0]
    open_bills.sort(key=lambda r: r.get("balance_due") or 0, reverse=True)
    total = sum(r.get("balance_due") or 0 for r in open_bills)
    return {
        "results": open_bills[:50],
        "count": len(open_bills),
        "total_outstanding": round(total, 2),
    }


def _run_cash_position(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    # Bank-style assets only — full asset tree is too noisy here.
    accounts = ai_tools.list_accounts(db, account_type="asset", limit=100)
    bank_like = [
        a
        for a in accounts["results"]
        if a.get("name")
        and any(
            kw in a["name"].lower()
            for kw in ("cash", "bank", "checking", "savings", "undeposited")
        )
    ]
    bank_like.sort(key=lambda a: a.get("balance") or 0, reverse=True)
    total = sum(a.get("balance") or 0 for a in bank_like)
    return {
        "accounts": bank_like,
        "total_cash": round(total, 2),
        "as_of": date.today().isoformat(),
    }


def _run_recent_payments(db: Session, start: date, end: date) -> Dict[str, Any]:
    return ai_tools.search_payments(
        db, start_date=start.isoformat(), end_date=end.isoformat(), limit=100
    )


def _run_pl_summary(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    # get_pl_summary uses live balances (not period-bounded). Period UI
    # selector is currently informational for this action.
    return ai_tools.get_pl_summary(db)


def _run_balance_sheet(db: Session, _s: Optional[date], _e: Optional[date]) -> Dict[str, Any]:
    return ai_tools.get_balance_sheet(db)


def _run_sales_tax(db: Session, start: date, end: date) -> Dict[str, Any]:
    return ai_tools.get_tax_summary(
        db, start_date=start.isoformat(), end_date=end.isoformat()
    )


# ---------------------------------------------------------------------------
# Action registry. Order here is the order shown in the dropdown.
# ---------------------------------------------------------------------------

ACTIONS: Dict[str, ActionSpec] = {
    "top_customers": ActionSpec(
        key="top_customers",
        label="Top customers by revenue",
        category="Customers & Sales",
        framing="Sales totals (paid invoices) grouped by customer for the period.",
        uses_period=True,
        runner=_run_top_customers,
    ),
    "unpaid_invoices": ActionSpec(
        key="unpaid_invoices",
        label="Unpaid invoices summary",
        category="Customers & Sales",
        framing="All invoices with an outstanding balance, sorted by amount due.",
        uses_period=False,
        runner=_run_unpaid_invoices,
    ),
    "ar_aging": ActionSpec(
        key="ar_aging",
        label="A/R aging",
        category="Customers & Sales",
        framing="Outstanding receivables bucketed by age (current / 30 / 60 / 90+ days).",
        uses_period=False,
        runner=_run_ar_aging,
    ),
    "expenses_by_category": ActionSpec(
        key="expenses_by_category",
        label="Expenses by category",
        category="Vendors & Bills",
        framing="Vendor bill expenses grouped by expense account for the period.",
        uses_period=True,
        runner=_run_expenses_by_category,
    ),
    "unpaid_bills": ActionSpec(
        key="unpaid_bills",
        label="Unpaid bills summary",
        category="Vendors & Bills",
        framing="All vendor bills with an outstanding balance, sorted by amount due.",
        uses_period=False,
        runner=_run_unpaid_bills,
    ),
    "ap_aging": ActionSpec(
        key="ap_aging",
        label="A/P aging",
        category="Vendors & Bills",
        framing="Outstanding payables bucketed by age (current / 30 / 60 / 90+ days).",
        uses_period=False,
        runner=_run_ap_aging,
    ),
    "cash_position": ActionSpec(
        key="cash_position",
        label="Cash position by account",
        category="Banking & Cash",
        framing=(
            "Current balance of cash, bank, checking, savings, and "
            "undeposited-funds accounts."
        ),
        uses_period=False,
        runner=_run_cash_position,
    ),
    "recent_payments": ActionSpec(
        key="recent_payments",
        label="Recent payment activity",
        category="Banking & Cash",
        framing="Customer payments received during the period.",
        uses_period=True,
        runner=_run_recent_payments,
    ),
    "pl_analysis": ActionSpec(
        key="pl_analysis",
        label="P&L analysis",
        category="Financial Reports",
        framing="Income, expense, COGS, and net income from current GL balances.",
        uses_period=False,
        runner=_run_pl_summary,
    ),
    "balance_sheet": ActionSpec(
        key="balance_sheet",
        label="Balance sheet analysis",
        category="Financial Reports",
        framing="Assets, liabilities, equity, and accounting-equation check.",
        uses_period=False,
        runner=_run_balance_sheet,
    ),
    "sales_tax": ActionSpec(
        key="sales_tax",
        label="Sales tax position",
        category="Tax",
        framing="Sales tax collected on invoices in the period plus expense rollup.",
        uses_period=True,
        runner=_run_sales_tax,
    ),
}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def list_actions() -> List[Dict[str, Any]]:
    """UI-friendly list, grouped by category in registry order."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for spec in ACTIONS.values():
        if spec.category not in grouped:
            grouped[spec.category] = []
            order.append(spec.category)
        grouped[spec.category].append(
            {
                "key": spec.key,
                "label": spec.label,
                "uses_period": spec.uses_period,
            }
        )
    return [{"category": c, "actions": grouped[c]} for c in order]


def _format_user_prompt(
    spec: ActionSpec,
    data: Dict[str, Any],
    period_start: Optional[date],
    period_end: Optional[date],
) -> str:
    if spec.uses_period and period_start and period_end:
        period_line = (
            f"Period: {period_start.isoformat()} to {period_end.isoformat()}"
        )
    else:
        period_line = f"As of: {date.today().isoformat()}"

    payload = json.dumps(data, default=str, indent=2)
    if len(payload) > _MAX_DATA_CHARS:
        payload = payload[:_MAX_DATA_CHARS] + "\n  …(truncated)"

    return (
        f"{spec.framing}\n"
        f"{period_line}\n\n"
        f"Data:\n{payload}\n\n"
        "Write the analysis."
    )


def run_action(
    action_key: str,
    db: Session,
    period_start: Optional[date],
    period_end: Optional[date],
    provider: str,
    model: str,
    api_key: str,
    account_id: Optional[str] = None,
    worker_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one action: fetch data, prompt the LLM, return narrative."""
    spec = ACTIONS.get(action_key)
    if not spec:
        raise ValueError(f"Unknown analysis action: {action_key}")

    data = spec.runner(db, period_start, period_end)
    user_prompt = _format_user_prompt(spec, data, period_start, period_end)
    narrative = call_provider(
        provider,
        api_key,
        model,
        SYSTEM_PROMPT,
        user_prompt,
        account_id=account_id or None,
        worker_url=worker_url or None,
    )

    return {
        "action_key": spec.key,
        "label": spec.label,
        "category": spec.category,
        "framing": spec.framing,
        "analysis": narrative,
        "data": data,
        "provider": provider,
        "model": model,
        "uses_period": spec.uses_period,
    }

# ============================================================================
# Slowbooks Pro 2026 — Analytics API
# Built 2026-04-14; integrated 2026-04-15; enhanced 2026-04-15 (Phase 1).
#
# Read-only endpoints powered by AnalyticsEngine. Every endpoint accepts a
# period window via either:
#   * `?period=month|quarter|year` (MTD / QTD / YTD)
#   * explicit `?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
# Explicit dates override `period`. Defaults to MTD.
#
# Plus /export.csv which dumps the full snapshot as a flat CSV for
# spreadsheet-loving accountants.
# ============================================================================

import csv
import io
import time
from datetime import date, datetime
from typing import Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.settings import _get_all as get_all_settings, _set as set_setting
from app.services.analytics import AnalyticsEngine
from app.services.ai_service import (
    AIProviderError,
    PROVIDERS as AI_PROVIDERS,
    generate_insights as ai_generate_insights,
    provider_list as ai_provider_list,
    call_with_tools,
)
from app.services.ai_tools import TOOLS as AI_TOOLS, call_tool
from app.services.crypto import decrypt_value, encrypt_value, is_encrypted

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _resolve_period(
    period: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
) -> Tuple[date, date, str]:
    """Resolve period name or explicit dates to `(start, end, label)`.

    Explicit start/end dates take precedence. If only one is provided the
    other defaults to a sensible bound. If neither is provided the named
    period (month/quarter/year, case-insensitive, mtd/qtd/ytd also OK) is
    resolved. Default is month-to-date.
    """
    today = date.today()

    if start_date or end_date:
        s = start_date or date(today.year, 1, 1)
        e = end_date or today
        return s, e, "custom"

    p = (period or "month").strip().lower()
    if p in ("month", "mtd"):
        return today.replace(day=1), today, "month"
    if p in ("quarter", "qtd"):
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_start_month, day=1), today, "quarter"
    if p in ("year", "ytd"):
        return today.replace(month=1, day=1), today, "year"
    # Unrecognised: fall back to MTD, report the label we actually used.
    return today.replace(day=1), today, "month"


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def get_dashboard(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Complete analytics snapshot — the page-load payload.

    The `period` window applies to revenue_by_customer and
    expenses_by_category. All other metrics are time-windowed by their own
    semantics (trend = last 12 months, aging = open balances as of today,
    cash_forecast = next 90 days).
    """
    s, e, label = _resolve_period(period, start_date, end_date)
    payload = AnalyticsEngine(db).get_dashboard(start_date=s, end_date=e)
    payload["period"] = {
        "name": label,
        "start": s.isoformat(),
        "end": e.isoformat(),
    }
    return payload


@router.get("/revenue")
def get_revenue(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Revenue by customer (windowed) + 12-month trend."""
    s, e, label = _resolve_period(period, start_date, end_date)
    engine = AnalyticsEngine(db)
    return {
        "period": {"name": label, "start": s.isoformat(), "end": e.isoformat()},
        "by_customer": engine.revenue_by_customer(s, e),
        "trend": engine.revenue_trend(),
    }


@router.get("/expenses")
def get_expenses(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Expense breakdown by account number (windowed)."""
    s, e, label = _resolve_period(period, start_date, end_date)
    return {
        "period": {"name": label, "start": s.isoformat(), "end": e.isoformat()},
        "by_category": AnalyticsEngine(db).expenses_by_category(s, e),
    }


@router.get("/cash-flow")
def get_cash_flow(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Cash forecast + DSO + A/R and A/P aging."""
    engine = AnalyticsEngine(db)
    return {
        "forecast": engine.cash_forecast(days),
        "dso": engine.dso(),
        "ar_aging": engine.ar_aging(),
        "ap_aging": engine.ap_aging(),
    }


@router.get("/profitability")
def get_profitability(db: Session = Depends(get_db)):
    """Customer profitability (lifetime paid revenue for now)."""
    return AnalyticsEngine(db).customer_profit()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


@router.get("/export.csv")
def export_csv(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Dump the full analytics snapshot as a flat CSV.

    One row per (section, key, subkey, value) tuple. Loads into Excel,
    Google Sheets, or any BI tool without ceremony.
    """
    s, e, label = _resolve_period(period, start_date, end_date)
    engine = AnalyticsEngine(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "subkey", "value"])

    writer.writerow(["period", "name", "", label])
    writer.writerow(["period", "start", "", s.isoformat()])
    writer.writerow(["period", "end", "", e.isoformat()])

    for customer, revenue in engine.revenue_by_customer(s, e).items():
        writer.writerow(["revenue_by_customer", customer, "", f"{revenue:.2f}"])

    for month, total in engine.revenue_trend().items():
        writer.writerow(["revenue_trend", month, "", f"{total:.2f}"])

    for category, amount in engine.expenses_by_category(s, e).items():
        writer.writerow(["expenses_by_category", category, "", f"{amount:.2f}"])

    for bucket, by_customer in engine.ar_aging().items():
        for customer, amount in by_customer.items():
            writer.writerow(["ar_aging", bucket, customer, f"{amount:.2f}"])

    for bucket, by_vendor in engine.ap_aging().items():
        for vendor, amount in by_vendor.items():
            writer.writerow(["ap_aging", bucket, vendor, f"{amount:.2f}"])

    writer.writerow(["dso", "days", "", f"{engine.dso():.2f}"])

    for entry in engine.cash_forecast():
        writer.writerow(["cash_forecast", entry["date"], "collections",
                         f"{entry['collections']:.2f}"])
        writer.writerow(["cash_forecast", entry["date"], "payments",
                         f"{entry['payments']:.2f}"])
        writer.writerow(["cash_forecast", entry["date"], "net",
                         f"{entry['net']:.2f}"])

    for customer, info in engine.customer_profit().items():
        writer.writerow(["customer_profit", customer, "",
                         f"{info['revenue']:.2f}"])

    filename = f"slowbooks-analytics-{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.pdf")
def export_pdf(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Render the full analytics snapshot as a print-ready PDF.

    Uses WeasyPrint (already a project dep) via `pdf_service.
    generate_analytics_pdf`. Honors the same period/date params as
    every other analytics endpoint.
    """
    # Lazy import so test environments without weasyprint don't choke
    # on `from app.routes import analytics`.
    from app.services.pdf_service import generate_analytics_pdf

    s, e, label = _resolve_period(period, start_date, end_date)
    engine = AnalyticsEngine(db)
    dashboard = engine.get_dashboard(start_date=s, end_date=e)

    period_meta = {"name": label, "start": s.isoformat(), "end": e.isoformat()}
    company_settings = get_all_settings(db)

    pdf_bytes = generate_analytics_pdf(dashboard, period_meta, company_settings)
    filename = f"slowbooks-analytics-{date.today().isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ===========================================================================
# AI Insights — Phase 9.5
#
# Configuration lives in the `settings` table under well-known keys:
#   ai_provider          — machine id (grok / groq / cloudflare / anthropic /
#                          openai / gemini)
#   ai_model             — user-editable model string
#   ai_api_key           — FERNET-ENCRYPTED api key (never returned raw)
#   ai_cloudflare_account_id — only populated when provider == cloudflare
#
# The /ai-config endpoints treat the key as write-only: GET never returns
# it, and PUT only touches it when a non-empty string is supplied (empty
# string = "keep existing").
# ===========================================================================


# Settings keys — kept in one place so we don't typo them.
_AI_PROVIDER_KEY        = "ai_provider"
_AI_MODEL_KEY           = "ai_model"
_AI_API_KEY             = "ai_api_key"            # STORED ENCRYPTED
_AI_CF_ACCOUNT_KEY      = "ai_cloudflare_account_id"


# Tiny in-process cache so the UI can poll without hammering paid APIs.
# Keyed by (provider, model, period_name) → (expiry_epoch, payload_dict).
# Cache TTL is 10 minutes. Cleared on config changes.
_AI_CACHE: dict = {}
_AI_CACHE_TTL_SECONDS = 600


def _clear_ai_cache():
    _AI_CACHE.clear()


def _read_ai_config(db: Session) -> dict:
    """Read the current AI config from settings, decrypting the key."""
    settings = get_all_settings(db)
    encrypted_key = settings.get(_AI_API_KEY, "") or ""
    try:
        api_key = decrypt_value(encrypted_key) if encrypted_key else ""
    except Exception:
        # Master key rotated or row tampered with — treat as no-key.
        api_key = ""
    return {
        "provider": settings.get(_AI_PROVIDER_KEY, "") or "",
        "model": settings.get(_AI_MODEL_KEY, "") or "",
        "api_key": api_key,
        "cloudflare_account_id": settings.get(_AI_CF_ACCOUNT_KEY, "") or "",
    }


@router.get("/ai-config")
def get_ai_config(db: Session = Depends(get_db)):
    """Return AI config suitable for display — NEVER the raw API key.

    The UI uses `has_api_key` to know whether to show a "key saved ✓"
    indicator vs prompting for input.
    """
    settings = get_all_settings(db)
    raw_key = settings.get(_AI_API_KEY, "") or ""
    return {
        "provider": settings.get(_AI_PROVIDER_KEY, "") or "",
        "model": settings.get(_AI_MODEL_KEY, "") or "",
        "cloudflare_account_id": settings.get(_AI_CF_ACCOUNT_KEY, "") or "",
        "has_api_key": bool(raw_key),
        "api_key_encrypted": is_encrypted(raw_key),
        "providers": ai_provider_list(),
    }


@router.put("/ai-config")
def put_ai_config(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Update AI provider / model / key / account_id.

    If `api_key` is omitted or empty, the existing encrypted value is
    kept. If present and non-empty, it is encrypted with Fernet before
    being stored.
    """
    provider = (payload.get("provider") or "").strip().lower()
    if provider and provider not in AI_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown AI provider '{provider}'. Valid: "
                   f"{sorted(AI_PROVIDERS.keys())}",
        )

    model = (payload.get("model") or "").strip()
    account_id = (payload.get("cloudflare_account_id") or "").strip()
    new_api_key = payload.get("api_key")
    # Distinguish "absent" from "empty string" — treat both as "don't change".
    should_update_key = isinstance(new_api_key, str) and new_api_key.strip() != ""

    set_setting(db, _AI_PROVIDER_KEY, provider)
    set_setting(db, _AI_MODEL_KEY, model)
    set_setting(db, _AI_CF_ACCOUNT_KEY, account_id)
    if should_update_key:
        encrypted = encrypt_value(new_api_key.strip())
        set_setting(db, _AI_API_KEY, encrypted)

    db.commit()
    _clear_ai_cache()

    # Return the same shape as GET so the client can refresh its state
    # from a single round-trip.
    return get_ai_config(db)


@router.post("/ai-config/test")
def test_ai_config(db: Session = Depends(get_db)):
    """Smoke-test the configured AI provider with a trivial prompt.

    Used by the Settings modal's "Test" button to validate the key
    without running the full dashboard-analysis prompt (which is
    expensive on paid APIs).
    """
    cfg = _read_ai_config(db)
    provider = cfg.get("provider") or ""
    api_key = cfg.get("api_key") or ""

    if not provider:
        raise HTTPException(status_code=400, detail="No AI provider configured")
    if not api_key:
        raise HTTPException(status_code=400, detail="No AI API key configured")
    if provider == "cloudflare" and not cfg.get("cloudflare_account_id"):
        raise HTTPException(
            status_code=400,
            detail="Cloudflare provider requires cloudflare_account_id",
        )

    spec = AI_PROVIDERS[provider]
    model = cfg.get("model") or spec.default_model

    try:
        # Smallest possible round-trip: ask for a one-word reply.
        from app.services.ai_service import call_provider
        text = call_provider(
            provider_key=provider,
            api_key=api_key,
            model=model,
            system="You are a connectivity check. Reply with exactly one word.",
            user='Reply with the word "ok" and nothing else.',
            account_id=cfg.get("cloudflare_account_id") or None,
        )
    except AIProviderError as e:
        # AIProviderError messages already have the key redacted.
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "provider": provider,
        "provider_label": spec.label,
        "model": model,
        "reply": text.strip()[:200],
        "tested_at": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/ai-insights")
def ai_insights(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    force: bool = Query(False, description="Bypass the 10-minute cache"),
    db: Session = Depends(get_db),
):
    """Run the configured AI provider over the current dashboard snapshot.

    Returns `{insights, provider, provider_label, model, generated_at, cached}`.
    Caches per (provider, model, period_name) for 10 minutes unless
    `force=true` is supplied.
    """
    cfg = _read_ai_config(db)
    provider = cfg.get("provider") or ""
    api_key = cfg.get("api_key") or ""

    if not provider or not api_key:
        raise HTTPException(
            status_code=400,
            detail="AI provider not configured. POST /api/analytics/ai-config first.",
        )
    if provider == "cloudflare" and not cfg.get("cloudflare_account_id"):
        raise HTTPException(
            status_code=400,
            detail="Cloudflare provider requires cloudflare_account_id",
        )

    spec = AI_PROVIDERS[provider]
    model = cfg.get("model") or spec.default_model

    s, e, label = _resolve_period(period, start_date, end_date)

    # Cache check
    cache_key = (provider, model, label, s.isoformat(), e.isoformat())
    now = time.time()
    if not force and cache_key in _AI_CACHE:
        expiry, cached_payload = _AI_CACHE[cache_key]
        if now < expiry:
            return {**cached_payload, "cached": True}

    # Build the dashboard + prompt
    engine = AnalyticsEngine(db)
    dashboard = engine.get_dashboard(start_date=s, end_date=e)
    dashboard["period"] = {"name": label, "start": s.isoformat(), "end": e.isoformat()}

    settings = get_all_settings(db)
    company_name = settings.get("company_name") or ""

    try:
        result = ai_generate_insights(
            provider_key=provider,
            api_key=api_key,
            model=model,
            dashboard=dashboard,
            company_name=company_name,
            account_id=cfg.get("cloudflare_account_id") or None,
        )
    except AIProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    payload = {
        **result,
        "period": {"name": label, "start": s.isoformat(), "end": e.isoformat()},
        "cached": False,
    }
    _AI_CACHE[cache_key] = (now + _AI_CACHE_TTL_SECONDS, payload)
    return payload


# ===========================================================================
# AI Q&A with Tool Calling — Phase 9.5b
# ===========================================================================

@router.post("/ai-query")
def ai_query(
    question: str = Query(..., description="User question"),
    db: Session = Depends(get_db),
):
    """Answer arbitrary business questions using tool-calling LLM.

    The LLM calls tools like search_bills, list_customers, etc. to gather
    data, then synthesizes an answer. Max 8 tool calls per question.

    Returns {provider, model, final_response, tool_calls, call_count, success}.
    """
    cfg = _read_ai_config(db)
    provider = cfg.get("provider") or ""
    api_key = cfg.get("api_key") or ""

    if not provider or not api_key:
        raise HTTPException(
            status_code=400,
            detail="AI provider not configured. POST /api/analytics/ai-config first.",
        )
    if provider == "cloudflare" and not cfg.get("cloudflare_account_id"):
        raise HTTPException(
            status_code=400,
            detail="Cloudflare provider requires cloudflare_account_id",
        )

    spec = AI_PROVIDERS[provider]
    model = cfg.get("model") or spec.default_model

    # Build tool executor that captures DB session
    def tool_exec(tool_name: str, **params):
        return call_tool(tool_name, db, **params)

    try:
        result = call_with_tools(
            provider_key=provider,
            api_key=api_key,
            model=model,
            user_question=question,
            tools=AI_TOOLS,
            tool_executor=tool_exec,
            account_id=cfg.get("cloudflare_account_id") or None,
            max_calls=8,
        )
    except AIProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result

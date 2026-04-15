# ============================================================================
# Slowbooks Pro 2026 — AI Insights service (Phase 9.5)
#
# Runs the analytics dashboard through an LLM and returns a structured
# "3 observations / 3 risks / 3 recommendations" report. Six providers
# are hardcoded with sensible April-2026 defaults; users can override the
# model string per provider from the UI so they're not stuck when the
# vendors inevitably rename everything next quarter.
#
# Providers (verified April 2026):
#   * grok        — xAI, OpenAI-compat,    https://api.x.ai/v1
#   * groq        — Groq LPU cloud, OpenAI-compat, https://api.groq.com/openai/v1
#                   (GENEROUS free tier)
#   * cloudflare  — Cloudflare Workers AI, OpenAI-compat, account-scoped URL
#                   (10k neurons/day free)
#   * anthropic   — Claude native /v1/messages
#   * openai      — OpenAI /v1/chat/completions
#   * gemini      — Google generativelanguage.googleapis.com generateContent
#                   (Flash models free)
#
# Every network call goes through httpx with a 60-second timeout. API keys
# are passed in from the caller — this module has no database access and
# never logs key material.
# ============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0  # seconds
MAX_TOKENS = 1024
TEMPERATURE = 0.3       # low — we want grounded analysis, not creative writing


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata about a supported AI provider."""
    key: str                   # machine id used in settings + UI
    label: str                 # human-readable name for the UI
    default_model: str         # recommended default as of April 2026
    wire_format: str           # "openai" | "anthropic" | "gemini"
    docs_url: str              # where users go to get a key
    free_tier_hint: str        # 1-line description for the UI
    needs_account_id: bool = False  # Cloudflare special-case


PROVIDERS: Dict[str, ProviderSpec] = {
    "grok": ProviderSpec(
        key="grok",
        label="xAI Grok",
        default_model="grok-4.1-fast",
        wire_format="openai",
        docs_url="https://console.x.ai/",
        free_tier_hint="$25 promotional credit on signup",
    ),
    "groq": ProviderSpec(
        key="groq",
        label="Groq (LPU Cloud)",
        default_model="llama-3.3-70b-versatile",
        wire_format="openai",
        docs_url="https://console.groq.com/keys",
        free_tier_hint="Free tier with generous rate limits — no credit card",
    ),
    "cloudflare": ProviderSpec(
        key="cloudflare",
        label="Cloudflare Workers AI",
        default_model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        wire_format="openai",
        docs_url="https://dash.cloudflare.com/profile/api-tokens",
        free_tier_hint="10,000 neurons/day free — no credit card",
        needs_account_id=True,
    ),
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic Claude",
        default_model="claude-sonnet-4-6",
        wire_format="anthropic",
        docs_url="https://console.anthropic.com/",
        free_tier_hint="Paid only (no free tier)",
    ),
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI",
        default_model="gpt-5.4-mini",
        wire_format="openai",
        docs_url="https://platform.openai.com/api-keys",
        free_tier_hint="Paid only (no free tier)",
    ),
    "gemini": ProviderSpec(
        key="gemini",
        label="Google Gemini",
        default_model="gemini-2.5-flash",
        wire_format="gemini",
        docs_url="https://aistudio.google.com/app/apikey",
        free_tier_hint="Free tier for Flash models via AI Studio",
    ),
}


def provider_list() -> list:
    """Return provider metadata in a UI-friendly shape (no secrets)."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "default_model": p.default_model,
            "docs_url": p.docs_url,
            "free_tier_hint": p.free_tier_hint,
            "needs_account_id": p.needs_account_id,
        }
        for p in PROVIDERS.values()
    ]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You are a senior financial analyst reviewing a small business's "
    "bookkeeping snapshot. Your job is to produce a short, actionable "
    "executive brief using ONLY the numbers in the data provided. "
    "Do not make up figures. Be specific — cite customer names, account "
    "codes, and dollar amounts from the data. Keep the total response "
    "under 400 words. Format as three sections: Observations, Risks, "
    "Recommendations. Use 3 bullet points per section."
)


def _top_agers(aging: Dict[str, Dict[str, float]], n: int = 3) -> list:
    """Return the top N names from an aging dict sorted by total balance."""
    totals = {}
    for bucket, by_name in (aging or {}).items():
        for name, amount in (by_name or {}).items():
            totals[name] = totals.get(name, 0.0) + float(amount or 0)
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:n]


def build_insights_prompt(dashboard: Dict[str, Any], company_name: str = "") -> str:
    """Turn a dashboard dict into a structured analyst prompt."""
    revenue_by_customer = dashboard.get("revenue_by_customer", {}) or {}
    expenses_by_category = dashboard.get("expenses_by_category", {}) or {}
    revenue_trend = dashboard.get("revenue_trend", {}) or {}
    ar_aging = dashboard.get("ar_aging", {}) or {}
    ap_aging = dashboard.get("ap_aging", {}) or {}
    cash_forecast = dashboard.get("cash_forecast", []) or []
    dso = dashboard.get("dso", 0) or 0

    total_revenue = sum(float(v or 0) for v in revenue_by_customer.values())
    total_expenses = sum(float(v or 0) for v in expenses_by_category.values())
    margin_pct = (
        ((total_revenue - total_expenses) / total_revenue) * 100
        if total_revenue > 0 else 0
    )
    net_income = total_revenue - total_expenses

    # Top revenue customers
    top_customers = sorted(
        revenue_by_customer.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    # Top expense categories
    top_expenses = sorted(
        expenses_by_category.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    # Top agers
    worst_ar = _top_agers(ar_aging, 3)
    worst_ap = _top_agers(ap_aging, 3)

    # 90-day forecast summary — first, middle, last buckets
    forecast_summary = ""
    if cash_forecast:
        first = cash_forecast[0]
        last = cash_forecast[-1]
        forecast_summary = (
            f"90-day forecast: starting at ${first.get('net', 0):,.0f} net "
            f"(collections ${first.get('collections', 0):,.0f} − "
            f"payments ${first.get('payments', 0):,.0f}), "
            f"ending at ${last.get('net', 0):,.0f} net."
        )

    # Revenue trend line
    trend_lines = [
        f"  {month}: ${float(val or 0):,.0f}"
        for month, val in list(revenue_trend.items())[-6:]  # last 6 months
    ]

    period = dashboard.get("period", {}) or {}
    period_label = (
        f"{period.get('name', 'month').upper()} "
        f"({period.get('start', '?')} → {period.get('end', '?')})"
        if period else "(unspecified window)"
    )

    company = company_name or "the business"

    lines = [
        f"Financial snapshot for {company} — {period_label}",
        "",
        "=== KEY METRICS ===",
        f"Revenue:     ${total_revenue:,.0f}",
        f"Expenses:    ${total_expenses:,.0f}",
        f"Net income:  ${net_income:,.0f}",
        f"Margin:      {margin_pct:.1f}%",
        f"DSO:         {float(dso):.1f} days",
        "",
        "=== TOP REVENUE CUSTOMERS ===",
    ]
    lines += [f"  {name}: ${float(amt or 0):,.0f}" for name, amt in top_customers] or ["  (none)"]

    lines += ["", "=== TOP EXPENSE CATEGORIES ==="]
    lines += [f"  {cat}: ${float(amt or 0):,.0f}" for cat, amt in top_expenses] or ["  (none)"]

    lines += ["", "=== RECENT REVENUE TREND (last 6 months) ==="]
    lines += trend_lines or ["  (no data)"]

    lines += ["", "=== ACCOUNTS RECEIVABLE (worst outstanding balances) ==="]
    lines += [f"  {name}: ${amt:,.0f} open"
              for name, amt in worst_ar] or ["  (nothing outstanding)"]

    lines += ["", "=== ACCOUNTS PAYABLE (worst outstanding balances) ==="]
    lines += [f"  {name}: ${amt:,.0f} open"
              for name, amt in worst_ap] or ["  (nothing outstanding)"]

    if forecast_summary:
        lines += ["", "=== CASH FORECAST ===", f"  {forecast_summary}"]

    lines += [
        "",
        "Produce the analyst brief now. Use only the numbers above.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider adapters — each returns a dict with method/url/headers/json
# ---------------------------------------------------------------------------


def _openai_style_request(
    url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
) -> Dict[str, Any]:
    """Build an OpenAI-compatible chat-completions request.

    Shared by OpenAI, Grok, Groq, and Cloudflare (all OpenAI-compat endpoints).
    """
    return {
        "method": "POST",
        "url": url,
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        "json": {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        },
    }


def build_request(
    provider_key: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an httpx-ready request dict for the given provider.

    Separated from the network call so unit tests can verify the exact
    URL / headers / body without mocking httpx.
    """
    if provider_key not in PROVIDERS:
        raise ValueError(f"Unknown AI provider: {provider_key}")
    spec = PROVIDERS[provider_key]
    model = model or spec.default_model

    if provider_key == "grok":
        return _openai_style_request(
            "https://api.x.ai/v1/chat/completions",
            api_key, model, system, user,
        )

    if provider_key == "groq":
        return _openai_style_request(
            "https://api.groq.com/openai/v1/chat/completions",
            api_key, model, system, user,
        )

    if provider_key == "openai":
        return _openai_style_request(
            "https://api.openai.com/v1/chat/completions",
            api_key, model, system, user,
        )

    if provider_key == "cloudflare":
        if not account_id:
            raise ValueError("Cloudflare Workers AI requires an account_id")
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/ai/v1/chat/completions"
        )
        return _openai_style_request(url, api_key, model, system, user)

    if provider_key == "anthropic":
        return {
            "method": "POST",
            "url": "https://api.anthropic.com/v1/messages",
            "headers": {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            "json": {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "temperature": TEMPERATURE,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        }

    if provider_key == "gemini":
        # Gemini takes the API key as a URL query param (!) and has its own
        # request/response shape with role: "user" and parts: [{text: ...}].
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        return {
            "method": "POST",
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "json": {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [
                    {"role": "user", "parts": [{"text": user}]},
                ],
                "generationConfig": {
                    "temperature": TEMPERATURE,
                    "maxOutputTokens": MAX_TOKENS,
                },
            },
        }

    raise ValueError(f"Unhandled AI provider: {provider_key}")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_response(provider_key: str, body: Dict[str, Any]) -> str:
    """Extract the assistant's text from a provider response body.

    Each API puts the text in a different spot; we normalise here.
    Returns an empty string on parse failure — the caller decides
    whether to treat that as an error.
    """
    if provider_key in ("grok", "groq", "openai", "cloudflare"):
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    if provider_key == "anthropic":
        try:
            # content is a list of blocks; we want the first text block
            blocks = body.get("content") or []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text") or ""
            return ""
        except (KeyError, TypeError):
            return ""

    if provider_key == "gemini":
        try:
            parts = body["candidates"][0]["content"]["parts"]
            # Gemini parts are a list; concatenate any text fields.
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        except (KeyError, IndexError, TypeError):
            return ""

    return ""


# ---------------------------------------------------------------------------
# Network call
# ---------------------------------------------------------------------------


class AIProviderError(Exception):
    """Raised when the AI provider returns a non-2xx or malformed response."""


def call_provider(
    provider_key: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    account_id: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: Optional[httpx.Client] = None,
) -> str:
    """Make the HTTP call and return the assistant's text.

    `client` is injectable for tests that want to stub out the transport.
    """
    req = build_request(provider_key, api_key, model, system, user, account_id)

    try:
        if client is None:
            with httpx.Client(timeout=timeout) as c:
                resp = c.request(req["method"], req["url"],
                                 headers=req["headers"], json=req["json"])
        else:
            resp = client.request(req["method"], req["url"],
                                  headers=req["headers"], json=req["json"])
    except httpx.HTTPError as e:
        # Never include api_key in the exception — it might have been
        # substituted into the URL (Gemini).
        raise AIProviderError(f"{provider_key}: network error") from e

    if resp.status_code >= 400:
        # Surface the provider's error message (minus any echoed keys)
        # to the caller so the UI can show something useful.
        text = resp.text
        if api_key and api_key in text:
            text = text.replace(api_key, "***REDACTED***")
        raise AIProviderError(
            f"{provider_key}: HTTP {resp.status_code} — {text[:500]}"
        )

    try:
        body = resp.json()
    except ValueError as e:
        raise AIProviderError(f"{provider_key}: non-JSON response") from e

    text = parse_response(provider_key, body)
    if not text:
        raise AIProviderError(
            f"{provider_key}: empty response (body shape unexpected)"
        )
    return text


def generate_insights(
    provider_key: str,
    api_key: str,
    model: str,
    dashboard: Dict[str, Any],
    company_name: str = "",
    account_id: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """End-to-end: build prompt, call provider, return structured result."""
    prompt = build_insights_prompt(dashboard, company_name)
    text = call_provider(
        provider_key=provider_key,
        api_key=api_key,
        model=model,
        system=SYSTEM_PROMPT,
        user=prompt,
        account_id=account_id,
        client=client,
    )
    return {
        "insights": text,
        "provider": provider_key,
        "provider_label": PROVIDERS[provider_key].label,
        "model": model or PROVIDERS[provider_key].default_model,
        "generated_at": date.today().isoformat(),
    }

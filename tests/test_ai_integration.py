"""AI integration — end-to-end tests for the tool-calling loop, response
extraction across all 3 wire formats, and tool execution against seeded data.

Network is fully mocked (httpx.Client replaced with a scripted fake) so the
tests stay offline and deterministic.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.ai_service import (
    _extract_tool_calls,
    _parse_json_args,
    call_with_tools,
    validate_worker_url,
    AIProviderError,
)


# ---------------------------------------------------------------------------
# Wire-format extraction — unit tests
# ---------------------------------------------------------------------------


def test_extract_openai_tool_calls():
    """OpenAI format: choices[0].message.tool_calls — arguments are JSON strings."""
    body = {
        "choices": [{
            "message": {
                "tool_calls": [
                    {
                        "id": "c_1",
                        "type": "function",
                        "function": {
                            "name": "list_customers",
                            "arguments": '{"limit": 5, "search": "Acme"}',
                        },
                    },
                ],
            },
        }],
    }
    calls = _extract_tool_calls("openai", body)
    assert len(calls) == 1
    assert calls[0]["name"] == "list_customers"
    assert calls[0]["arguments"] == {"limit": 5, "search": "Acme"}


def test_extract_openai_tool_calls_returns_empty_without_tool_calls():
    body = {"choices": [{"message": {"content": "just text"}}]}
    assert _extract_tool_calls("openai", body) == []


def test_extract_anthropic_tool_use_blocks():
    """Anthropic format: content[] with type=tool_use — input is already a dict."""
    body = {
        "content": [
            {"type": "text", "text": "Let me look that up."},
            {"type": "tool_use", "name": "get_pl_summary", "input": {"year": 2026}},
        ],
    }
    calls = _extract_tool_calls("anthropic", body)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_pl_summary"
    assert calls[0]["arguments"] == {"year": 2026}


def test_extract_gemini_function_call():
    """Gemini format: candidates[0].content.parts[].functionCall"""
    body = {
        "candidates": [{
            "content": {
                "parts": [
                    {"functionCall": {"name": "list_vendors", "args": {"limit": 3}}},
                ],
            },
        }],
    }
    calls = _extract_tool_calls("gemini", body)
    assert len(calls) == 1
    assert calls[0]["name"] == "list_vendors"
    assert calls[0]["arguments"] == {"limit": 3}


def test_parse_json_args_handles_bad_json():
    """Bad JSON in OpenAI tool args shouldn't blow up the whole request."""
    assert _parse_json_args("not{valid json") == {}


def test_parse_json_args_passes_dict_through():
    """Pass-through for already-parsed dicts."""
    d = {"a": 1}
    assert _parse_json_args(d) is d


def test_extract_unknown_wire_format_returns_empty():
    assert _extract_tool_calls("aol-compat-v3", {"anything": True}) == []


# ---------------------------------------------------------------------------
# SSRF / URL validation (already partly covered but expanded)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/v1/chat",         # plain http + loopback
    "https://127.0.0.1/",               # loopback
    "https://10.0.0.5/",                # RFC1918
    "https://192.168.1.5/",             # RFC1918
    "https://169.254.169.254/latest",   # AWS metadata
    "https://localhost/",               # localhost
    "https://user:pass@evil.com/",      # embedded creds
    "https://example.com/" + "x" * 3000,  # > 2048 chars
])
def test_validate_worker_url_rejects_dangerous_urls(url):
    with pytest.raises(ValueError):
        validate_worker_url(url)


def test_validate_worker_url_accepts_https():
    # Public domain, https, no creds, under length cap
    url = "https://slowbooks-gateway.example.workers.dev/v1/chat/completions"
    assert validate_worker_url(url) == url


# ---------------------------------------------------------------------------
# call_with_tools loop — mock the httpx.Client
# ---------------------------------------------------------------------------


def _mock_response(body: dict, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def _fake_client(responses: list):
    """Return an object with a .request() method that yields each response in order."""
    client = MagicMock()
    client.request = MagicMock(side_effect=responses)
    return client


_FAKE_TOOLS = {
    "list_customers": {
        "name": "list_customers",
        "description": "List customers",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
}


def test_call_with_tools_openai_roundtrip_with_one_tool_call():
    """OpenAI path: 1st response asks for a tool, 2nd response gives final text."""
    responses = [
        _mock_response({
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "t1",
                        "type": "function",
                        "function": {
                            "name": "list_customers",
                            "arguments": '{"limit": 2}',
                        },
                    }],
                },
            }],
        }),
        _mock_response({
            "choices": [{"message": {"content": "You have 2 customers."}}],
        }),
    ]
    client = _fake_client(responses)

    tool_executor = MagicMock(return_value={"count": 2, "items": ["A", "B"]})

    result = call_with_tools(
        provider_key="openai",
        api_key="sk-fake",
        model="gpt-5.4-mini",
        user_question="How many customers do I have?",
        tools=_FAKE_TOOLS,
        tool_executor=tool_executor,
        client=client,
    )

    assert result["success"] is True
    assert result["call_count"] == 2
    assert "2 customers" in result["final_response"]
    assert len(result["tool_calls"]) == 1
    tool_executor.assert_called_once_with("list_customers", limit=2)


def test_call_with_tools_anthropic_roundtrip():
    """Anthropic path: same structure, different wire format."""
    responses = [
        _mock_response({
            "content": [
                {"type": "tool_use", "name": "list_customers", "input": {"limit": 3}},
            ],
            "stop_reason": "tool_use",
        }),
        _mock_response({
            "content": [{"type": "text", "text": "Found 3."}],
            "stop_reason": "end_turn",
        }),
    ]
    client = _fake_client(responses)
    tool_executor = MagicMock(return_value={"count": 3})

    result = call_with_tools(
        provider_key="anthropic",
        api_key="sk-ant-fake",
        model="claude-sonnet-4-6",
        user_question="How many?",
        tools=_FAKE_TOOLS,
        tool_executor=tool_executor,
        client=client,
    )

    assert result["success"] is True
    assert "3" in result["final_response"]
    tool_executor.assert_called_once_with("list_customers", limit=3)


def test_call_with_tools_gemini_roundtrip():
    """Gemini path: functionCall in parts → functionResponse in history."""
    responses = [
        _mock_response({
            "candidates": [{
                "content": {"parts": [
                    {"functionCall": {"name": "list_customers", "args": {"limit": 1}}},
                ]},
            }],
        }),
        _mock_response({
            "candidates": [{
                "content": {"parts": [{"text": "One customer named A."}]},
            }],
        }),
    ]
    client = _fake_client(responses)
    tool_executor = MagicMock(return_value={"count": 1})

    result = call_with_tools(
        provider_key="gemini",
        api_key="gemini-fake",
        model="gemini-2.5-flash",
        user_question="Names?",
        tools=_FAKE_TOOLS,
        tool_executor=tool_executor,
        client=client,
    )

    assert result["success"] is True
    assert "A" in result["final_response"]


def test_call_with_tools_max_iterations_stops_loop():
    """If the LLM keeps asking for tools, the loop bails at max_calls."""
    # Every response asks for the same tool forever
    stuck_response = _mock_response({
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "t", "type": "function",
                    "function": {"name": "list_customers", "arguments": "{}"},
                }],
            },
        }],
    })
    responses = [stuck_response] * 10  # more than max_calls
    client = _fake_client(responses)
    tool_executor = MagicMock(return_value={"count": 0})

    result = call_with_tools(
        provider_key="openai",
        api_key="sk-fake",
        model="gpt-5.4-mini",
        user_question="...",
        tools=_FAKE_TOOLS,
        tool_executor=tool_executor,
        client=client,
        max_calls=3,
    )
    assert result["call_count"] == 3
    # The final response should indicate no text was produced — success=False
    assert result["success"] is False


def test_call_with_tools_propagates_http_error():
    """A 500 from the provider becomes an AIProviderError."""
    client = _fake_client([_mock_response({"error": "down"}, status=500)])
    tool_executor = MagicMock()

    with pytest.raises(AIProviderError):
        call_with_tools(
            provider_key="openai",
            api_key="sk-fake",
            model="x",
            user_question="hi",
            tools=_FAKE_TOOLS,
            tool_executor=tool_executor,
            client=client,
        )


def test_call_with_tools_api_key_not_leaked_in_errors():
    """When the provider echoes our key in an error body, the redactor hides it."""
    secret = "sk-DO-NOT-LEAK-12345"
    client = _fake_client([
        _mock_response(
            {"error": f"auth failed for {secret}"},
            status=401,
        ),
    ])
    tool_executor = MagicMock()

    with pytest.raises(AIProviderError) as exc_info:
        call_with_tools(
            provider_key="openai",
            api_key=secret,
            model="x",
            user_question="hi",
            tools=_FAKE_TOOLS,
            tool_executor=tool_executor,
            client=client,
        )
    assert secret not in str(exc_info.value)
    assert "REDACTED" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AI tools — execute against seeded data
# ---------------------------------------------------------------------------


def test_list_customers_tool_returns_seeded_customer(db_session, seed_customer):
    from app.services.ai_tools import list_customers
    result = list_customers(db_session)
    assert result["count"] >= 1
    names = [c["name"] for c in result["results"]]
    assert "Test Customer" in names


def test_list_customers_respects_name_filter(db_session, seed_customer):
    from app.services.ai_tools import list_customers
    hit = list_customers(db_session, name_filter="Test")
    assert hit["count"] >= 1
    miss = list_customers(db_session, name_filter="zzzzzunique")
    assert miss["count"] == 0


def test_get_current_date_tool_returns_iso_today(db_session):
    from app.services.ai_tools import get_current_date
    result = get_current_date(db_session)
    # Returned as "current_date" plus a full timestamp
    assert "current_date" in result
    assert len(result["current_date"]) == 10


def test_call_tool_dispatches_to_registered_function(db_session, seed_customer):
    from app.services.ai_tools import call_tool
    result = call_tool("list_customers", db_session)
    assert "count" in result


def test_call_tool_rejects_unknown_tool(db_session):
    from app.services.ai_tools import call_tool
    result = call_tool("format_hard_drive", db_session)
    assert "error" in result


# ---------------------------------------------------------------------------
# HTTP endpoints — auth and validation
# ---------------------------------------------------------------------------


def test_ai_query_requires_configured_provider(client):
    """ai-query should 400 if no provider configured yet."""
    r = client.post("/api/analytics/ai-query?question=hello")
    # 400 = not configured; 429 would be rate-limited; anything != 401 proves auth OK
    assert r.status_code in (400, 429)


def test_ai_insights_requires_configured_provider(client):
    r = client.post("/api/analytics/ai-insights?period=month")
    assert r.status_code in (400, 429)


def test_ai_config_get_returns_provider_list(client):
    r = client.get("/api/analytics/ai-config")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    # Should list all 7 providers
    provider_keys = {p["key"] for p in body["providers"]}
    assert {"openai", "anthropic", "gemini", "grok", "groq", "cloudflare"}.issubset(provider_keys)


def test_ai_config_rejects_bad_cloudflare_account_id(client):
    r = client.put("/api/analytics/ai-config", json={
        "provider": "cloudflare",
        "cloudflare_account_id": "GGGG-not-hex",
    })
    assert r.status_code == 400


def test_ai_config_rejects_ssrf_worker_url(client):
    r = client.put("/api/analytics/ai-config", json={
        "provider": "cloudflare_worker",
        "worker_url": "https://169.254.169.254/latest/meta-data/",
    })
    assert r.status_code == 400


def test_ai_config_never_returns_raw_api_key(client, db_session):
    """The GET response must carry has_api_key as a bool, never the key itself."""
    # Minimal put with a key
    client.put("/api/analytics/ai-config", json={
        "provider": "openai",
        "api_key": "sk-very-secret-12345",
    })
    r = client.get("/api/analytics/ai-config")
    assert r.status_code == 200
    body = r.json()
    assert "has_api_key" in body
    # Scan the entire serialized response for our secret
    raw = json.dumps(body)
    assert "sk-very-secret-12345" not in raw

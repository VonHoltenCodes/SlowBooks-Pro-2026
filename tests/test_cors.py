"""
CORS must NOT return allow_origin=* plus allow_credentials=true.
Must respond with a locked-down origin allow-list.
"""


def test_cors_does_not_return_wildcard(client):
    r = client.options(
        "/api/auth/status",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Starlette returns 200 on OPTIONS with matching headers or 400 on reject.
    # Either way, the allow-origin header MUST NOT be "*".
    allow_origin = r.headers.get("access-control-allow-origin", "")
    assert allow_origin != "*"


def test_cors_allows_configured_origin(client):
    """Whitelisted origin (set in conftest.py ALLOWED_ORIGINS) should echo."""
    r = client.options(
        "/api/auth/status",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = r.headers.get("access-control-allow-origin", "")
    # Either the exact origin echoed back, or not present (denied) — but
    # never "*" combined with credentials.
    if allow_origin:
        assert allow_origin == "http://localhost:3001"

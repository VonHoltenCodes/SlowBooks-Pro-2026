"""Coverage for the CORS allowlist resolver.

Locks in the default (loopback-only) and the env-var override path so that
a future edit can't silently return us to `allow_origins=["*"]`.
"""


def test_default_is_loopback_on_default_port():
    from app.config import resolve_cors_origins
    assert resolve_cors_origins(env={}) == [
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]


def test_default_follows_app_port_override():
    from app.config import resolve_cors_origins
    assert resolve_cors_origins(env={"APP_PORT": "8080"}) == [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ]


def test_explicit_env_wins():
    from app.config import resolve_cors_origins
    got = resolve_cors_origins(env={
        "CORS_ALLOW_ORIGINS": "https://books.example.com, https://admin.example.com ",
    })
    assert got == ["https://books.example.com", "https://admin.example.com"]


def test_empty_explicit_falls_back_to_defaults():
    from app.config import resolve_cors_origins
    assert resolve_cors_origins(env={"CORS_ALLOW_ORIGINS": "   "}) == [
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]


def test_app_not_using_wildcard_origins():
    """Guardrail: the live app must not re-introduce allow_origins=['*'].
    A wildcard combined with allow_credentials=True is a CORS spec violation
    and a dead giveaway that someone reverted this hardening.
    """
    from app.main import app
    cors = next(
        (m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"),
        None,
    )
    assert cors is not None, "CORS middleware should be registered"
    origins = cors.kwargs.get("allow_origins") if hasattr(cors, "kwargs") else None
    if origins is None:
        # Starlette stores middleware options in .options on some versions
        origins = getattr(cors, "options", {}).get("allow_origins")
    assert origins is not None
    assert origins != ["*"], "wildcard CORS regressed"
    assert "*" not in origins, "wildcard origin present in CORS allowlist"

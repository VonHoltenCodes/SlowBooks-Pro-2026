"""
Rate limiter wiring.

We don't try to exhaust real limits across tests (per-process state
collides with other tests), we just verify slowapi is attached to the
app and that the exception handler is registered.
"""

from app.main import app


def test_limiter_attached_to_app_state():
    assert hasattr(app.state, "limiter"), "slowapi Limiter not on app.state"
    limiter = app.state.limiter
    assert limiter is not None


def test_rate_limit_exception_handler_registered():
    # FastAPI keeps exception handlers in app.exception_handlers
    handlers = getattr(app, "exception_handlers", {}) or {}
    # Handlers are keyed by exception class
    keys = list(handlers.keys())
    names = [getattr(k, "__name__", str(k)) for k in keys]
    assert any(
        "RateLimit" in n for n in names
    ), f"RateLimitExceeded handler not registered; got {names}"

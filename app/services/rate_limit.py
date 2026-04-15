# ============================================================================
# Slowbooks Pro 2026 — Rate limiter (Phase 9.7)
#
# Shared slowapi Limiter instance. Lives in its own module so
# app.routes.analytics and app.main can both import it without creating
# a circular dependency.
#
# Toggle with RATE_LIMIT_ENABLED env var (default on). Tests set this to
# "0" in conftest.py so decorator calls don't bleed per-test counters.
# ============================================================================

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_enabled = os.environ.get("RATE_LIMIT_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

limiter = Limiter(key_func=get_remote_address, enabled=_enabled)

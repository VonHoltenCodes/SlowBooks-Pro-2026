# ============================================================================
# SlowBooks Pro 2026 — Rate limiter (Phase 9.7)
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

# Where the counters live. Unset = slowapi's in-process MemoryStorage, which
# is correct for a single-worker container (and the desktop app) and WRONG the
# moment there is more than one worker or replica: each keeps its own tally, so
# a "5 per minute" login limit becomes 5 x workers x replicas. The prod compose
# ships APP_WORKERS=4, so the default there is already 4x looser than it reads.
# Point this at shared storage (e.g. redis://redis:6379/0 or memcached://...)
# for any multi-worker or multi-replica deploy; app.main warns at startup when
# it is unset in a configuration where it matters. Default stays in-memory.
RATE_LIMIT_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "").strip()

limiter = Limiter(
    key_func=get_remote_address,
    enabled=_enabled,
    **({"storage_uri": RATE_LIMIT_STORAGE_URI} if RATE_LIMIT_STORAGE_URI else {}),
)

"""Compatibility wrapper for the renamed NZ demo data seed script.

Older tests and tooling still import ``scripts.seed_irs_mock_data`` and may
override ``SessionLocal`` on that module before calling ``seed()``. Forward
that override into the renamed implementation module so the legacy import keeps
behaving the same way.
"""

import scripts.seed_nz_demo_data as _impl

SessionLocal = _impl.SessionLocal


def __getattr__(name):
    return getattr(_impl, name)


def seed():
    _impl.SessionLocal = SessionLocal
    return _impl.seed()

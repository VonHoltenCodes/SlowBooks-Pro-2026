"""An empty PAYROLL_ENCRYPTION_SECRET is refused where the dev default is
(2.31.3).

.env.example ships the line as `PAYROLL_ENCRYPTION_SECRET=`. The startup
check compared the value with the dev default only, so a native install
made from the example started against PostgreSQL with "" and encrypted
employee bank numbers under a key derived from an empty string, the same on
every such install. Docker already refused it (the compose files' `:?`
form); this is the native path's door.
"""

import pytest

from app.main import _payroll_secret_problem

DEV = "slowbooks-dev-payroll-key-change-me"
REAL = "a-real-secret-not-the-dev-one"


@pytest.mark.parametrize("secret", ["", "short", "fifteen-chars--"])
def test_an_empty_or_short_secret_is_refused_on_a_real_database(secret):
    problem = _payroll_secret_problem(secret, is_real_db=True, app_debug=True)
    assert (
        problem and "empty or shorter than 16" in problem and "openssl rand" in problem
    )


def test_an_empty_secret_is_refused_in_production_on_sqlite_too():
    assert "empty or shorter" in _payroll_secret_problem(
        "", is_real_db=False, app_debug=False
    )


def test_the_dev_default_still_gets_its_own_sentence():
    assert "public dev default" in _payroll_secret_problem(
        DEV, is_real_db=True, app_debug=True
    )


def test_sqlite_in_development_is_exempt():
    # the test suite and local development: nothing to set
    assert _payroll_secret_problem("", is_real_db=False, app_debug=True) is None
    assert _payroll_secret_problem(DEV, is_real_db=False, app_debug=True) is None


def test_a_real_secret_passes_everywhere():
    for real_db in (True, False):
        for debug in (True, False):
            assert _payroll_secret_problem(REAL, real_db, debug) is None


def test_the_startup_check_raises_on_the_empty_secret(monkeypatch):
    import app.config as cfg
    import app.main as m

    monkeypatch.setattr(cfg, "DATABASE_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setattr(cfg, "PAYROLL_ENCRYPTION_SECRET", "")
    monkeypatch.setattr(cfg, "APP_DEBUG", True)
    with pytest.raises(RuntimeError, match="empty or shorter"):
        m._run_startup_security_checks()


def test_a_typed_short_secret_is_refused_on_the_desktop_too():
    # SQLite with APP_DEBUG on is every desktop install; a hand-edited .env
    # with a short value used to pass and encrypt bank numbers with it
    problem = _payroll_secret_problem("tooshort", is_real_db=False, app_debug=True)
    assert problem and "shorter than 16" in problem and "clear the line" in problem

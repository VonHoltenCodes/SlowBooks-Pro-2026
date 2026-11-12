"""SLOWBOOKS_SERVER_EDITION=1: the Server Edition's own start-up switch (#812).

A Server Edition host serves its office network over plain HTTP. Up to
2.31.x its .env carried APP_DEBUG=true to get past the HTTPS start-up
check, which also waived every other production check. The switch waives
the HTTPS refusal alone, with debug off: the encryption-key checks and the
database TLS check still run. A host that still has APP_DEBUG=true starts
as before.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import desktop_launcher

REAL = "a-real-secret-not-the-dev-one"
DEV = "slowbooks-dev-payroll-key-change-me"
SQLITE = "sqlite:///server-edition.db"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def startup(monkeypatch):
    """The start-up checks with the database steps stubbed out: returns a
    function that runs them and says whether they got past every check."""
    import app.config as cfg
    import app.main as m
    from app.services import company_file

    reached = []
    monkeypatch.setattr(company_file, "refuse_a_locked_file", lambda: None)
    monkeypatch.setattr(m, "_refuse_a_database_behind_head", lambda: None)
    monkeypatch.setattr(m, "_create_missing_tables", lambda: reached.append(1))
    monkeypatch.setattr(m, "_check_rate_limit_storage", lambda: None)
    monkeypatch.delenv("SLOWBOOKS_PRIVATE_NETWORK", raising=False)

    def configure(*, switch, debug, force_https=False, url=SQLITE, secret=REAL):
        monkeypatch.setattr(cfg, "DATABASE_URL", url)
        monkeypatch.setattr(cfg, "PAYROLL_ENCRYPTION_SECRET", secret)
        monkeypatch.setattr(cfg, "APP_DEBUG", debug)
        monkeypatch.setattr(m, "FORCE_HTTPS", force_https)
        if switch:
            monkeypatch.setenv("SLOWBOOKS_SERVER_EDITION", "1")
        else:
            monkeypatch.delenv("SLOWBOOKS_SERVER_EDITION", raising=False)

    def run():
        reached.clear()
        m._run_startup_security_checks()
        return bool(reached)

    run.configure = configure
    return run


# ---- the start-up checks: switch on/off, with and without APP_DEBUG --------


def test_switch_on_debug_off_starts_on_plain_http(startup, caplog):
    startup.configure(switch=True, debug=False)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert startup()
    assert "SLOWBOOKS_SERVER_EDITION=1" in caplog.text
    assert "trusted network" in caplog.text


def test_switch_off_debug_off_still_refuses_plain_http(startup):
    startup.configure(switch=False, debug=False)
    with pytest.raises(RuntimeError, match="FORCE_HTTPS=false in production"):
        startup()


def test_switch_on_with_debug_on_starts(startup):
    startup.configure(switch=True, debug=True)
    assert startup()


def test_a_host_that_still_has_app_debug_starts_as_before(startup):
    # the upgrade case: a 2.31.x .env with APP_DEBUG=true and no switch
    startup.configure(switch=False, debug=True)
    assert startup()


def test_switch_on_with_https_on_has_nothing_to_waive(startup, caplog):
    startup.configure(switch=True, debug=False, force_https=True)
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert startup()
    assert "SLOWBOOKS_SERVER_EDITION=1" not in caplog.text


@pytest.mark.parametrize("value", ["0", "", "no", "false"])
def test_only_a_one_or_true_turns_the_switch_on(startup, monkeypatch, value):
    startup.configure(switch=False, debug=False)
    monkeypatch.setenv("SLOWBOOKS_SERVER_EDITION", value)
    with pytest.raises(RuntimeError, match="FORCE_HTTPS=false"):
        startup()


# ---- everything else APP_DEBUG loosened stays strict under the switch ------


def test_the_dev_key_is_refused_under_the_switch(startup):
    startup.configure(switch=True, debug=False, secret=DEV)
    with pytest.raises(RuntimeError):
        startup()


def test_an_empty_secret_is_refused_under_the_switch(startup):
    # APP_DEBUG=true lets SQLite start with no key at all; the switch does not
    startup.configure(switch=True, debug=False, secret="")
    with pytest.raises(RuntimeError, match="empty or shorter than 16"):
        startup()


def test_a_plaintext_database_is_refused_under_the_switch(startup):
    startup.configure(
        switch=True, debug=False, url="postgresql://u:p@db.example:5432/books"
    )
    with pytest.raises(RuntimeError, match="TLS mode"):
        startup()


def test_the_switch_parses_one_and_true():
    from app.config import server_edition_switch

    for on in ("1", "true", "TRUE", " 1 "):
        assert server_edition_switch({"SLOWBOOKS_SERVER_EDITION": on})
    for off in ("0", "", "yes", "false"):
        assert not server_edition_switch({"SLOWBOOKS_SERVER_EDITION": off})
    assert not server_edition_switch({})


def _force_https_default(tmp_path, **env):
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    child = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FORCE_HTTPS", "APP_DEBUG", "SLOWBOOKS_SERVER_EDITION")
    }
    child.update(env, SLOWBOOKS_ENV_FILE=str(empty))
    out = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(c.FORCE_HTTPS)"],
        cwd=ROOT,
        env=child,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_force_https_defaults_off_under_the_switch(tmp_path):
    assert _force_https_default(tmp_path, SLOWBOOKS_SERVER_EDITION="1") == "False"
    assert _force_https_default(tmp_path) == "True"
    assert _force_https_default(tmp_path, APP_DEBUG="true") == "False"


# ---- the launcher writes the switch for Server Edition ---------------------


def test_the_lan_server_gets_the_switch_and_debug_off():
    env = desktop_launcher._server_env("sqlite:///x.db", 3001, bind_host="0.0.0.0")
    assert env["SLOWBOOKS_SERVER_EDITION"] == "1"
    assert env["APP_DEBUG"] == "false"
    assert env["FORCE_HTTPS"] == "false"


def test_the_window_server_keeps_debug_and_no_switch():
    env = desktop_launcher._server_env("sqlite:///x.db", 3001)
    assert env["SLOWBOOKS_SERVER_EDITION"] == "0"
    assert env["APP_DEBUG"] == "true"


@pytest.fixture
def launcher_env(tmp_path, monkeypatch):
    env_file = tmp_path / "SlowBooksPro" / ".env"
    monkeypatch.setattr(desktop_launcher, "env_file", lambda: env_file)
    monkeypatch.setattr(desktop_launcher, "ENV_EXAMPLE", tmp_path / "missing")
    monkeypatch.setattr(
        desktop_launcher, "get_data_dir", lambda: env_file.parent / "data"
    )
    # prepare_env sets it in os.environ; register it so pytest restores it
    monkeypatch.setenv("SLOWBOOKS_ENV_FILE", "")
    return env_file


def test_server_edition_env_has_the_switch_not_app_debug(launcher_env):
    # a host set up before 2.32.0 has APP_DEBUG=true in its .env
    launcher_env.parent.mkdir(parents=True)
    launcher_env.write_text("APP_DEBUG=true\nFORCE_HTTPS=false\n", encoding="utf-8")
    desktop_launcher.prepare_env(server_edition=True)
    assert desktop_launcher.get_env_value("SLOWBOOKS_SERVER_EDITION") == "1"
    assert desktop_launcher.get_env_value("APP_DEBUG") == "false"
    assert desktop_launcher.get_env_value("FORCE_HTTPS") == "false"
    text = launcher_env.read_text(encoding="utf-8")
    assert "APP_DEBUG=true" not in text
    assert text.count("SLOWBOOKS_SERVER_EDITION=") == 1


def test_the_window_puts_a_shared_env_back(launcher_env):
    # the quick trial's --serve-lan and the window share one .env
    desktop_launcher.prepare_env(server_edition=True)
    desktop_launcher.prepare_env()
    assert desktop_launcher.get_env_value("APP_DEBUG") == "true"
    assert desktop_launcher.get_env_value("SLOWBOOKS_SERVER_EDITION") == "0"


def test_the_window_adds_no_switch_line_to_its_own_env(launcher_env):
    desktop_launcher.prepare_env()
    assert desktop_launcher.get_env_value("SLOWBOOKS_SERVER_EDITION") is None
    assert desktop_launcher.get_env_value("APP_DEBUG") == "true"

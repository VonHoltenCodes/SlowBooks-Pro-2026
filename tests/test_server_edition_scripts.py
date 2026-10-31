"""The Server Edition install script and the installer agree with the app
about where a server keeps its books, and the task they register survives
an update, a battery and three days (2.31.3).

Read as text, so they run on every platform. What they guard:

- The script copies desktop books from %LOCALAPPDATA%\\SlowBooksPro\\data
  (the launcher's data dir) and passes `--data-dir <home>\\data`, so the .env
  the launcher writes beside the data dir lands in the home, not in
  C:\\ProgramData itself. The 2.31.2 script looked one folder too high and
  copied nothing, and its .env ended up at C:\\ProgramData\\.env.
- The task is registered with Register-ScheduledTask and explicit settings:
  no execution time limit (schtasks /Create left Task Scheduler's 72-hour
  default), allowed to start and to keep running on battery, restarted when
  it dies.
- The firewall rule covers private and domain networks only.
- The data home is restricted to SYSTEM and Administrators.
- The installer starts the task again on a Server Edition host instead of
  offering to open the desktop window, which took the server's port.
- Windows PowerShell 5.1 reads a BOM-less script as ANSI, so the scripts
  stay ASCII.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts" / "windows"
INSTALL = SCRIPTS / "serveredition-install.ps1"
ISS = ROOT / "packaging" / "windows" / "SlowBooksPro.iss"


def test_the_scripts_are_ascii():
    for path in SCRIPTS.glob("*.ps1"):
        text = path.read_text(encoding="utf-8")
        bad = [ch for ch in text if ord(ch) > 127]
        assert not bad, f"{path.name}: non-ASCII {bad[:5]}"


def test_the_server_data_dir_is_the_launchers_layout():
    text = INSTALL.read_text(encoding="utf-8")
    assert '$ServerData = Join-Path $DataDir "data"' in text
    # the task's argument names the data\ folder, so the launcher's .env
    # (data dir's parent) is <home>\.env
    assert re.search(
        r'--data-dir `"\$ServerData`"', text
    ), "the task must pass --data-dir <home>\\data"
    # desktop books are looked for where the launcher keeps them
    assert '$DesktopData = Join-Path $DesktopHome "data"' in text
    assert 'Join-Path $env:LOCALAPPDATA "SlowBooksPro"' in text
    # the 2.31.2 layout is moved into place, keys included
    assert 'Join-Path $env:ProgramData ".env"' in text
    assert "Move-Item $oldEnv" in text


def test_the_task_has_no_time_limit_and_runs_on_battery():
    text = INSTALL.read_text(encoding="utf-8")
    assert "Register-ScheduledTask -TaskName $TaskName" in text
    assert not any(
        ln.strip().startswith("schtasks /Create") for ln in text.splitlines()
    )
    start = text.index("New-ScheduledTaskSettingsSet")
    block = text[start : text.index("Register-ScheduledTask", start)]
    for flag in (
        "-AllowStartIfOnBatteries",
        "-DontStopIfGoingOnBatteries",
        "-ExecutionTimeLimit ([TimeSpan]::Zero)",
        "-RestartCount",
    ):
        assert flag in block, flag
    assert "New-ScheduledTaskTrigger -AtStartup" in text
    assert re.search(r'New-ScheduledTaskPrincipal -UserId "SYSTEM"', text)


def test_the_firewall_rule_is_private_and_domain_only():
    text = INSTALL.read_text(encoding="utf-8")
    rule = re.search(r"netsh advfirewall firewall add rule(.*?)\| Out-Null", text, re.S)
    assert rule and "profile=domain,private" in rule.group(1), rule and rule.group(1)


def test_the_data_home_is_restricted_to_system_and_administrators():
    text = INSTALL.read_text(encoding="utf-8")
    assert re.search(
        r"icacls \$DataDir /inheritance:r /grant .*S-1-5-18.*S-1-5-32-544", text
    )


def test_the_script_stops_the_server_before_touching_the_books():
    text = INSTALL.read_text(encoding="utf-8")
    stop = text.index("Stop-ScheduledTask -TaskName $TaskName")
    assert stop < text.index("Move-Item") and stop < text.index("Copy-Item")


def _run_entries():
    text = ISS.read_text(encoding="utf-8")
    m = re.search(r"^\[Run\][ \t]*$(.*?)(?=^\[\w+\][ \t]*$|\Z)", text, re.M | re.S)
    assert m
    # continuation lines end in a backslash
    joined = re.sub(r"\\\n\s*", " ", m.group(1))
    return [
        ln.strip()
        for ln in joined.splitlines()
        if ln.strip() and not ln.lstrip().startswith(";")
    ]


def test_the_installer_restarts_the_server_task_and_skips_the_window_on_a_server_host():
    entries = _run_entries()
    launch = [e for e in entries if "SlowBooksPro.exe" in e and "postinstall" in e]
    assert len(launch) == 1 and "Check: not ServerTaskExists" in launch[0], launch
    start = [e for e in entries if "schtasks.exe" in e]
    assert len(start) == 1, start
    assert (
        "/Run /TN SlowBooksProServer" in start[0]
        and "Check: ServerTaskExists" in start[0]
    ), start[0]
    text = ISS.read_text(encoding="utf-8")
    assert "function ServerTaskExists(): Boolean;" in text
    assert (
        "'/Query /TN ' + ServerTask" in text
        and "ServerTask = 'SlowBooksProServer'" in text
    )

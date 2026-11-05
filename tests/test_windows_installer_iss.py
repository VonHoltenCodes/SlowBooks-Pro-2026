"""The Windows installer installs for one user without an administrator, and
for all users when an administrator chooses it (issue #699).

Inno Setup's default, with no PrivilegesRequired, is "admin": a standard
user saw "You must be logged in as an administrator when installing this
program." and could not install at all. The script now asks for the lowest
privileges and lets the first page (or /ALLUSERS, /CURRENTUSER) pick the
mode, so every path, shortcut and registry root in it has to follow that
mode. Inno Setup does not run on Linux; these read the script as text, so
they run on every platform.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "windows" / "SlowBooksPro.iss"
WINDOWS_YML = ROOT / ".github" / "workflows" / "windows.yml"

# The AppId every release so far was installed under: a changed one would
# install a second copy beside the old instead of upgrading it, in either mode.
APP_ID = "{f67ccf1c-2072-4f4a-9102-4e3897758a97}"

# Constants that name one mode's folder whatever the mode, and the auto form
# that follows it ({autopf}, {autoprograms}/{group}, {autodesktop}, ...).
FIXED_MODE_CONSTANTS = (
    "{pf}",
    "{pf32}",
    "{pf64}",
    "{commonpf}",
    "{commonpf32}",
    "{commonpf64}",
    "{userpf}",
    "{commonprograms}",
    "{userprograms}",
    "{commondesktop}",
    "{userdesktop}",
    "{commonstartmenu}",
    "{userstartmenu}",
    "{commonstartup}",
    "{userstartup}",
    "{commonappdata}",
)

# Programs whose work needs an administrator: a [Run] or [UninstallRun]
# entry that starts one must only run in an all-users install.
NEEDS_ADMIN = ("schtasks.exe", "netsh", "sc.exe", "reg.exe", "powershell", "icacls")


def _text():
    return ISS.read_text(encoding="utf-8")


def _section(text, name):
    """The entries of one [Section], comments and blank lines dropped, each
    continued line (a trailing backslash) joined to the next."""
    m = re.search(
        rf"^\[{re.escape(name)}\][ \t]*$(.*?)(?=^\[\w+\][ \t]*$|\Z)",
        text,
        re.M | re.S,
    )
    if not m:
        return []
    joined, entries = "", []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.endswith("\\"):
            joined += line[:-1] + " "
            continue
        entries.append(joined + line)
        joined = ""
    return entries


def _directives(name="Setup"):
    out = {}
    for entry in _section(_text(), name):
        key, sep, value = entry.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _params(entry):
    return {
        key: value.strip().strip('"')
        for key, value in re.findall(r'(\w+):\s*("[^"]*"|[^;]*)', entry)
    }


def _code():
    return "\n".join(_section(_text(), "Code"))


def _pascal(kind, name):
    m = re.search(rf"^{kind} {name}\(.*?^end;", _code(), re.M | re.S | re.IGNORECASE)
    assert m, f"no {kind} {name} in [Code]"
    return m.group(0)


def _uncommented():
    """The script without its comment lines (; in sections, // in [Code])."""
    return "\n".join(
        line
        for line in _text().splitlines()
        if not line.lstrip().startswith((";", "//"))
    )


def test_a_standard_user_can_install_and_an_administrator_can_choose_all_users():
    setup = _directives()
    assert setup.get("PrivilegesRequired") == "lowest", setup
    allowed = setup.get("PrivilegesRequiredOverridesAllowed", "").split()
    assert "dialog" in allowed, allowed
    assert "commandline" in allowed, allowed  # /ALLUSERS and /CURRENTUSER
    # an upgrade keeps the mode the copy there was installed in
    assert setup.get("UsePreviousPrivileges", "yes") == "yes", setup


def test_the_app_id_is_the_one_every_release_was_installed_under():
    # [Setup] writes a literal { as {{
    assert _directives().get("AppId") == "{" + APP_ID, _directives().get("AppId")


def test_the_install_folder_follows_the_mode():
    assert _directives().get("DefaultDirName") == r"{autopf}\SlowBooks Pro 2026"


def test_no_path_is_fixed_to_one_mode():
    text = _uncommented().lower()
    left = [c for c in FIXED_MODE_CONSTANTS if c in text]
    assert not left, f"use the auto form instead of {left}"


def test_shortcuts_land_in_the_start_menu_and_desktop_of_the_mode():
    for entry in _section(_text(), "Icons"):
        name = _params(entry)["Name"]
        assert name.startswith(("{group}", "{autoprograms}", "{autodesktop}")), name


def test_registry_entries_use_the_mode_root():
    for entry in _section(_text(), "Registry"):
        root = _params(entry).get("Root", "")
        assert root.upper().startswith("HKA"), entry


def test_run_entries_that_need_an_administrator_only_run_for_all_users():
    for section in ("Run", "UninstallRun"):
        for entry in _section(_text(), section):
            params = _params(entry)
            program = params.get("Filename", "").lower()
            if any(tool in program for tool in NEEDS_ADMIN):
                assert "IsAdminInstallMode" in params.get("Check", ""), entry


def test_the_server_task_is_started_again_only_by_an_all_users_install():
    entries = [_params(e) for e in _section(_text(), "Run")]
    task = [e for e in entries if "schtasks" in e.get("Filename", "")]
    assert len(task) == 1, entries
    check = task[0]["Check"]
    assert "IsAdminInstallMode" in check and "ServerTaskExists" in check, check
    # and the desktop window still is not offered on a Server Edition host
    launch = [e for e in entries if e.get("Filename") == r"{app}\SlowBooksPro.exe"]
    assert len(launch) == 1 and launch[0]["Check"] == "not ServerTaskExists", launch


def test_a_per_user_run_over_an_all_users_copy_stops_and_says_why():
    key = re.search(r"AllUsersUninstallKey\s*=\s*'([^']+)'", _code())
    assert key, "AllUsersUninstallKey not declared in [Code]"
    assert key.group(1) == (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + APP_ID + "_is1"
    ), key.group(1)
    exists = _pascal("function", "AllUsersInstallExists")
    assert "HKLM64" in exists, exists  # the x64 install mode's view
    init = _pascal("function", "InitializeSetup")
    assert re.search(r"not\s+IsAdminInstallMode", init), init
    assert "AllUsersInstallExists()" in init, init
    assert "Result := False" in init, init
    assert "only an administrator can update that copy" in init, init
    assert "SuppressibleMsgBox(" in init, init  # /SILENT still exits


def test_a_runtime_the_bootstrapper_could_not_fix_is_reported():
    entries = [_params(e) for e in _section(_text(), "Run")]
    boot = [
        e
        for e in entries
        if e.get("Filename") == r"{tmp}\MicrosoftEdgeWebView2Setup.exe"
    ]
    assert len(boot) == 1, entries
    # it still runs in both modes: unelevated it installs a missing runtime
    # for the user, so no IsAdminInstallMode guard on it
    assert boot[0].get("Check") == "not IsWebView2Current", boot
    assert boot[0].get("AfterInstall") == "ReportWebView2", boot
    body = _pascal("procedure", "ReportWebView2")
    assert "IsWebView2Current()" in body, body
    assert "IsAdminInstallMode" in body, body
    assert "needs an administrator" in body, body
    assert "SuppressibleMsgBox(" in body, body


def test_the_install_mode_page_reads_plainly():
    messages = _directives("Messages")
    assert "administrator" in messages["PrivilegesRequiredOverrideAllUsers"]
    assert "me only" in messages["PrivilegesRequiredOverrideCurrentUserRecommended"]
    # PrivilegesRequired=lowest shows Text2
    assert "no administrator" in messages["PrivilegesRequiredOverrideText2"]
    assert "books" in messages["PrivilegesRequiredOverrideText2"]


def test_ci_still_builds_and_signs_the_installer_the_same_way():
    yml = WINDOWS_YML.read_text(encoding="utf-8")
    assert (
        r'& $iscc "/DAppVersion=$env:APP_VERSION" packaging\windows\SlowBooksPro.iss'
        in yml
    )
    assert "Sign installer SlowBooksPro-Setup-x64.exe (Trusted Signing)" in yml
    assert "trusted-signing-account-name: easyamp-signing" in yml
    assert _directives().get("OutputBaseFilename") == "SlowBooksPro-Setup-x64"


def test_no_line_begins_with_a_hash_character_constant():
    """ISCC's preprocessor reads a line that begins with '#' as a directive
    ("Unknown preprocessor directive", the 2.31.7 round-1 build): a Pascal
    character constant such as #13#10 must not start a line."""
    import re
    from pathlib import Path

    iss = Path(__file__).resolve().parents[1] / "packaging/windows/SlowBooksPro.iss"
    known = (
        "#define",
        "#if",
        "#ifdef",
        "#ifndef",
        "#else",
        "#endif",
        "#include",
        "#expr",
        "#pragma",
        "#emit",
    )
    bad = [
        f"{n}: {line.strip()[:60]}"
        for n, line in enumerate(iss.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\s*#", line) and not line.strip().lower().startswith(known)
    ]
    assert bad == [], "\n".join(bad)

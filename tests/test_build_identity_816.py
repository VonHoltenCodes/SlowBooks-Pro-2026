"""Build identity on every platform (#816).

The Windows build carried no commit and Contact support's diagnostics read a
SLOWBOOKS_COMMIT nothing set, so two builds of one release could not be told
apart. Now the packaged builds bake the commit as app/BUILD_SHA, and About,
the smoke test and the diagnostics all name the build as <version>+<sha>
(the 12-character short SHA the macOS bundle's CFBundleVersion carries)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from app import __version__, build_info
from app.services import support_diagnostics as sd

ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = "0123456789abcdef0123456789abcdef01234567"
SHORT = FULL_SHA[:12]

_spec = importlib.util.spec_from_file_location(
    "version_info_816", ROOT / "packaging" / "windows" / "version_info.py"
)
version_info = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_info)


@pytest.fixture
def no_build(monkeypatch, tmp_path):
    """No env commit and no baked file: a plain source run."""
    monkeypatch.delenv("SLOWBOOKS_COMMIT", raising=False)
    monkeypatch.setattr(
        build_info, "_build_file_candidates", lambda: [tmp_path / "BUILD_SHA"]
    )
    return tmp_path / "BUILD_SHA"


# --- the composition --------------------------------------------------------


def test_version_string_with_a_sha_is_version_plus_short_sha():
    assert build_info.version_string("2.32.0", FULL_SHA) == f"2.32.0+{SHORT}"


def test_version_string_without_a_sha_is_the_bare_version():
    assert build_info.version_string("2.32.0", "") == "2.32.0"
    assert build_info.version_string("2.32.0", "unknown") == "2.32.0"


def test_version_string_defaults_to_this_build(no_build, monkeypatch):
    assert build_info.version_string() == __version__
    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    assert build_info.version_string() == f"{__version__}+{SHORT}"


@pytest.mark.parametrize(
    "value, expected",
    [
        (FULL_SHA, SHORT),
        (FULL_SHA.upper(), SHORT),
        (f"  {FULL_SHA}\n", SHORT),
        ("abc1234", "abc1234"),
        ("", None),
        (None, None),
        ("unknown", None),
        ("not a commit", None),
    ],
)
def test_normalize_sha(value, expected):
    assert build_info.normalize_sha(value) == expected


# --- where the commit comes from ---------------------------------------------


def test_no_env_and_no_baked_file_is_no_commit(no_build):
    assert build_info.build_sha() is None


def test_the_baked_file_is_read(no_build):
    no_build.write_text(FULL_SHA + "\n", encoding="utf-8")
    assert build_info.build_sha() == SHORT


def test_an_empty_baked_file_is_no_commit(no_build):
    no_build.write_text("", encoding="utf-8")
    assert build_info.build_sha() is None


def test_the_environment_wins_over_the_baked_file(no_build, monkeypatch):
    no_build.write_text("fedcba9876543210", encoding="utf-8")
    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    assert build_info.build_sha() == SHORT


def test_a_junk_environment_value_falls_back_to_the_file(no_build, monkeypatch):
    no_build.write_text(FULL_SHA, encoding="utf-8")
    monkeypatch.setenv("SLOWBOOKS_COMMIT", "   ")
    assert build_info.build_sha() == SHORT


def test_a_frozen_bundle_reads_app_build_sha_under_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv("SLOWBOOKS_COMMIT", raising=False)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "BUILD_SHA").write_text(FULL_SHA, encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert tmp_path / "app" / "BUILD_SHA" in build_info._build_file_candidates()


# --- the diagnostics ------------------------------------------------------------


def test_app_commit_is_the_build_sha(no_build, monkeypatch):
    assert sd.app_commit() is None
    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    assert sd.app_commit() == SHORT


def test_environment_block_version_line_with_a_sha():
    block = sd.environment_block("2.32.0", SHORT, "Windows", "3.12.7", "native")
    assert block.splitlines()[0] == f"Version: v2.32.0+{SHORT}"


def test_environment_block_version_line_without_a_sha():
    block = sd.environment_block("2.32.0", None, "Windows", "3.12.7", "native")
    assert block.splitlines()[0] == "Version: v2.32.0"


def test_diagnostics_route_with_a_sha(client, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    body = client.get("/api/support/diagnostics").json()
    assert body["commit"] == SHORT
    assert body["build_version"] == f"{__version__}+{SHORT}"
    assert f"Version: v{__version__}+{SHORT}" in body["environment"]


def test_diagnostics_route_without_a_sha(client, no_build):
    body = client.get("/api/support/diagnostics").json()
    assert body["commit"] is None
    assert body["build_version"] == __version__
    assert body["environment"].splitlines()[0] == f"Version: v{__version__}"


def test_system_route_names_the_build_for_about(client, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    body = client.get("/api/system").json()
    assert body["version"] == __version__
    assert body["commit"] == SHORT
    assert body["build_version"] == f"{__version__}+{SHORT}"


def test_system_route_without_a_sha(client, no_build):
    body = client.get("/api/system").json()
    assert body["commit"] is None
    assert body["build_version"] == __version__


def test_about_has_a_place_for_the_build():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="splash-version"' in html
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "$('#splash-version')" in js and "App.buildLabel(info)" in js


# --- the smoke test ---------------------------------------------------------------


def test_smoke_prints_the_build(no_build, monkeypatch, capsys):
    import desktop_launcher as dl

    monkeypatch.setenv("SLOWBOOKS_COMMIT", FULL_SHA)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert dl._smoke_build_identity() is True
    assert f"smoke: version {__version__}+{SHORT}" in capsys.readouterr().out


def test_smoke_fails_a_frozen_bundle_with_no_commit(no_build, monkeypatch, capsys):
    import desktop_launcher as dl

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert dl._smoke_build_identity() is False
    out = capsys.readouterr().out
    assert f"smoke: version {__version__}\n" in out
    assert "smoke: FAIL" in out


def test_smoke_from_source_may_have_no_commit(no_build, monkeypatch, capsys):
    import desktop_launcher as dl

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert dl._smoke_build_identity() is True
    assert f"smoke: version {__version__}\n" in capsys.readouterr().out


def test_run_smoke_test_checks_the_build_first():
    import inspect

    import desktop_launcher as dl

    src = inspect.getsource(dl.run_smoke_test)
    assert src.index("_smoke_build_identity()") < src.index("manifest_create_company")


# --- the Windows build ------------------------------------------------------------


def test_windows_build_sha_from_the_ci_env(tmp_path):
    assert version_info.build_sha(tmp_path, {"APP_BUILD_SHA": FULL_SHA}) == SHORT


def test_windows_build_sha_from_the_checkout():
    try:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        pytest.skip("not a git checkout")
    assert version_info.build_sha(ROOT, {}) == head[:12]


def test_windows_build_sha_without_git_or_env_is_empty(tmp_path):
    assert version_info.build_sha(tmp_path / "not-a-repo", {}) == ""
    assert version_info.build_sha(tmp_path, {"APP_BUILD_SHA": "unknown"}) == ""


def test_the_written_file_is_what_the_app_reads(tmp_path, monkeypatch):
    out = version_info.write_build_sha(SHORT, tmp_path / "build-identity")
    assert out.name == build_info.BUILD_FILE_NAME
    monkeypatch.delenv("SLOWBOOKS_COMMIT", raising=False)
    monkeypatch.setattr(build_info, "_build_file_candidates", lambda: [out])
    assert build_info.build_sha() == SHORT
    empty = version_info.write_build_sha("", tmp_path / "none")
    monkeypatch.setattr(build_info, "_build_file_candidates", lambda: [empty])
    assert build_info.build_sha() is None


def test_both_specs_bundle_the_build_file_as_app_build_sha():
    win = (ROOT / "packaging" / "windows" / "SlowBooksPro.spec").read_text("utf-8")
    assert "_version_info.build_sha(" in win
    assert 'datas += [(str(BUILD_SHA_FILE), "app")]' in win
    mac = (ROOT / "packaging" / "macos" / "SlowBooksPro-mac.spec").read_text("utf-8")
    assert 'datas += [(_BUILD_SHA_FILE, "app")]' in mac
    # the file is written before Analysis collects the datas
    assert mac.index("_BUILD_SHA_FILE =") < mac.index("Analysis(")


def test_both_workflows_set_and_check_the_build():
    win = (ROOT / ".github" / "workflows" / "windows.yml").read_text("utf-8")
    assert '"APP_BUILD_SHA=$env:GITHUB_SHA"' in win
    assert "smoke: version $build" in win
    mac = (ROOT / ".github" / "workflows" / "macos.yml").read_text("utf-8")
    assert 'echo "APP_BUILD_SHA=$GITHUB_SHA"' in mac
    assert "smoke: version ${APP_VERSION}+${GITHUB_SHA:0:12}" in mac

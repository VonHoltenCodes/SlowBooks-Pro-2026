"""The vendored line client is SlowBooks Switchboard's own (#1087).

app/switchboard holds copies of the sbplive modules the line needs, each
the source with a two-line header and its imports pointed at
app.switchboard (scripts/sync_switchboard.py). Against a checkout of the
private source repository the copies must match it exactly; CI has no
checkout, so that test skips there. The rest holds everywhere.
"""

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "app" / "switchboard"
SOURCE_ENV = "SLOWBOOKS_SWITCHBOARD_SOURCE"


def _sync():
    spec = importlib.util.spec_from_file_location(
        "sync_switchboard", ROOT / "scripts" / "sync_switchboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout():
    raw = os.environ.get(SOURCE_ENV) or str(
        Path.home() / "Development" / "SlowBooks-Pro-MCP"
    )
    path = Path(raw).expanduser()
    return path if (path / "sbplive" / "line.py").is_file() else None


def test_every_vendored_file_is_listed_with_its_header():
    sync = _sync()
    on_disk = sorted(p.name for p in VENDOR.glob("*.py"))
    assert on_disk == sorted(sync.FILES)
    for name in sync.FILES:
        text = (VENDOR / name).read_text(encoding="utf-8")
        assert text.startswith(sync.header(name)), name
        # no import of the companion's own package is left
        assert not re.search(r"^\s*(from|import) sbplive\b", text, re.M), name


def test_source_md_names_the_commit_and_the_files():
    source = (VENDOR / "SOURCE.md").read_text(encoding="utf-8")
    assert re.search(r"\b[0-9a-f]{40}\b", source)
    for name in _sync().FILES:
        assert f"`{name}`" in source, name


def test_the_relay_is_one_constant_and_nothing_is_added_to_requirements():
    from app.services import switchboard_link

    assert switchboard_link.RELAY_URL == "https://relay.sbplive.com"
    for req in ("requirements.txt", "requirements-desktop.txt"):
        assert "sbplive" not in (ROOT / req).read_text(encoding="utf-8")


def test_the_copies_match_the_source_checkout():
    checkout = _checkout()
    if checkout is None:
        pytest.skip("no SlowBooks Switchboard source checkout here")
    sync = _sync()
    for name in sync.FILES:
        copy = (VENDOR / name).read_text(encoding="utf-8")
        source = (checkout / "sbplive" / name).read_text(encoding="utf-8")
        assert sync.original(name, copy) == source, name
    pinned = re.search(
        r"\b([0-9a-f]{40})\b", (VENDOR / "SOURCE.md").read_text(encoding="utf-8")
    ).group(1)
    # the pinned commit is in the checkout's history, and its files are these
    for name in sync.FILES:
        shown = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{pinned}:sbplive/{name}"],
            capture_output=True,
            text=True,
        )
        if shown.returncode != 0:
            pytest.skip("the pinned commit is not in this checkout")
        copy = (VENDOR / name).read_text(encoding="utf-8")
        assert sync.original(name, copy) == shown.stdout, name

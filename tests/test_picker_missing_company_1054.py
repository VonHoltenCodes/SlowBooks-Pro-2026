"""A company whose file was deleted by hand stays off the picker's way (#1054).

The companies list (companies.json) kept listing a company whose .db file
had been deleted from the company folder, and nothing in the app took it
off. The picker now marks such a company as missing, with Remove from list
beside it; that removes the list's entry only and touches nothing on disk.
A company whose file is there is listed as before, and Remove from list
refuses it."""

import json
import re
from pathlib import Path

import pytest

import desktop_launcher as dl
from app.services import company_service


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "companies").mkdir(parents=True)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    return data


def _manifest(data: Path, *files: str, last=None) -> None:
    (data / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": f.removesuffix(".db").title(), "file": f} for f in files
                ],
                "last_opened": last,
            }
        ),
        encoding="utf-8",
    )


def _listed(data: Path) -> list:
    return [
        c["file"]
        for c in json.loads((data / "companies.json").read_text())["companies"]
    ]


def _folder(data: Path) -> dict:
    return {p.name: p.read_bytes() for p in (data / "companies").iterdir()}


def test_a_company_whose_file_is_gone_is_listed_as_missing(data_dir):
    (data_dir / "companies" / "kept.db").write_bytes(b"books")
    _manifest(data_dir, "kept.db", "demo.db")
    listed = {c["file"]: c for c in dl.PickerApi(port=0).list_companies()["companies"]}
    assert listed["kept.db"]["missing"] is False
    assert listed["demo.db"]["missing"] is True


def test_remove_from_list_drops_the_entry_and_touches_nothing_on_disk(data_dir):
    (data_dir / "companies" / "kept.db").write_bytes(b"books")
    (data_dir / "companies" / "kept.db-wal").write_bytes(b"wal")
    _manifest(data_dir, "demo.db", "kept.db", last="demo.db")
    before = _folder(data_dir)
    api = dl.PickerApi(port=0)
    assert api.remove_from_list("demo.db") == {"success": True}
    assert _listed(data_dir) == ["kept.db"]
    assert _folder(data_dir) == before
    # the last company opened was the missing one: the list remembers the one left
    assert api.list_companies()["last_opened"] == "kept.db"


def test_a_company_whose_file_is_there_is_refused(data_dir):
    (data_dir / "companies" / "kept.db").write_bytes(b"books")
    _manifest(data_dir, "kept.db")
    result = dl.PickerApi(port=0).remove_from_list("kept.db")
    assert result == {"success": False, "error": company_service.MISSING_FILE_PRESENT}
    assert _listed(data_dir) == ["kept.db"]
    assert (data_dir / "companies" / "kept.db").read_bytes() == b"books"


def test_a_company_not_on_the_list_is_refused(data_dir):
    _manifest(data_dir, "demo.db")
    result = dl.PickerApi(port=0).remove_from_list("other.db")
    assert result == {"success": False, "error": company_service.MISSING_NOT_LISTED}
    assert _listed(data_dir) == ["demo.db"]


def test_an_entry_no_company_file_could_have_is_missing_and_removable(data_dir):
    _manifest(data_dir, "../outside.db")
    listed = company_service.manifest_list_companies()
    assert listed[0]["missing"] is True
    assert company_service.remove_missing_from_list("../outside.db") == {
        "success": True
    }
    assert _listed(data_dir) == []


def _picker_js() -> str:
    html = dl.PICKER_HTML
    return html[
        html.index("async function refresh()") : html.index(
            "async function openCompany("
        )
    ]


def test_the_picker_draws_missing_with_remove_from_list_and_does_not_open_it():
    js = _picker_js()
    assert "if (c.missing) {" in js
    assert '<div class="company missing" data-file="' in js
    assert "Missing: the company file is no longer in the company folder." in js
    assert '<button type="button" class="remove" data-remove="' in js
    assert ">Remove from list</button>" in js
    # only a company that is there opens on a click
    assert "list.querySelectorAll('.company:not(.missing)').forEach(" in js
    assert "window.pywebview.api.remove_from_list(file)" in js
    # first launch does not open a missing last company, and the list drawn
    # again after a removal opens nothing
    assert (
        "const last = !autoOpenTried && info.companies.find(function (c) "
        "{ return c.file === info.last_opened && !c.missing; });" in js
    )
    assert "autoOpenTried = true;" in js
    assert "Nothing in the company folder was changed." in js


def test_the_picker_script_still_parses(tmp_path):
    """The page is a Python string: a stray quote would break the whole
    picker. Node checks the script parses."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = re.search(
        r"<script>([\s\S]*)</script>", dl.PICKER_HTML, flags=re.IGNORECASE
    ).group(1)
    # from a file: node --check reading the script on stdin never returned
    # on the Windows runner
    path = tmp_path / "picker.js"
    path.write_text(script, encoding="utf-8")
    r = subprocess.run(
        [node, "--check", str(path)], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr

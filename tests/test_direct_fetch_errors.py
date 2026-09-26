"""Pages that send their own fetch() say what went wrong in a sentence
(explore 2.17.3 integration, I3).

Uploads, imports and downloads post FormData or read the answer as a file,
so they cannot go through API.request, and each one formatted a refusal
itself: ``data.detail || 'Upload failed'``. A 422 is a list of entries, so
the person read "[object Object]"; an answer that is not JSON (a proxy's
error page) came out as a SyntaxError. They all go through api.js's
API.responseError now, which gives the sentence API.request shows.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
PROBE = ROOT / "tests" / "js" / "direct_fetch_errors_probe.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _shown(refusal):
    out = subprocess.run(
        ["node", str(PROBE), json.dumps(refusal)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_refused_upload_shows_the_servers_sentence(client):
    # An upload with no file: FastAPI's own 422, a list with a "message".
    real = client.post("/api/iif/import")
    assert real.status_code == 422
    assert real.json()["detail"][0]["message"] == "File is required."

    shown = _shown({"status": 422, "body": real.json()})
    assert len(shown) == 20
    for path, message in shown.items():
        assert message == "File is required.", path


@needs_node
def test_a_refusal_in_words_is_shown_as_it_is():
    shown = _shown({"status": 400, "body": {"detail": "That file has no rows."}})
    assert set(shown.values()) == {"That file has no rows."}


@needs_node
def test_an_answer_that_is_not_json_names_the_failure_and_the_status():
    shown = _shown({"status": 502, "body": "<html>Bad gateway</html>"})
    for path, message in shown.items():
        assert message.endswith("failed (HTTP 502)") or message == (
            "Could not load the document (HTTP 502)"
        ), (path, message)
        assert "JSON" not in message and "token" not in message, (path, message)


# Files whose fetch() calls show nothing when they fail (the version
# footer, What's New), or are covered by their own tests (auth.js and the
# logo upload: test_validation_messages.py).
_NOT_IN_PROBE = {"api.js", "auth.js", "bootstrap.js", "settings.js", "chart.umd.js"}


def test_every_page_that_fetches_for_itself_is_in_the_probe():
    probe = PROBE.read_text(encoding="utf-8")
    for js in sorted(JS.glob("*.js")):
        if js.name in _NOT_IN_PROBE or "fetch(" not in js.read_text(encoding="utf-8"):
            continue
        assert f"'{js.name} " in probe, f"{js.name} calls fetch() itself"


def test_no_page_prints_a_refusals_detail_as_it_came():
    for js in sorted(JS.glob("*.js")):
        text = js.read_text(encoding="utf-8")
        assert not re.search(r"\.detail\s*\|\|", text), js.name

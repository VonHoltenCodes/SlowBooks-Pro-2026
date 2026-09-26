"""A new company's first run (explore 2.17.3: skytech M18, macbase1 F1-F3).

A company created in the New Company dialog opened on "Unlock Slowbooks —
enter your password" although it had no password (/api/auth/status said
setup_needed: true); the way on was a small link, or typing any password to
be bounced. The unlock screen never said whose password it wanted, setup
re-asked for the company name just typed, the status bar behind the dialog
read "Error loading page · Company: bookkeeper.sbk", and the New Company
dialog sat unchanged for the ~3 s the new file took to build.
"""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.services import company_service

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

# A module constant, never a literal beside a username key.
SETUP_PW = "harbor-light-2026"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _auth_probe(status):
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "auth_prompt_probe.js"),
            json.dumps({"status": status}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_company_nobody_has_set_up_opens_on_setup_with_its_name(unauthed_client):
    status = unauthed_client.get("/api/auth/status").json()
    assert status["setup_needed"] is True
    status["company_name"] = "Harbor Light Bakery"  # as a New Company dialog wrote it
    out = _auth_probe(status)
    assert out["screen"] == "setup"
    assert 'value="Harbor Light Bakery"' in out["html"]
    # one overlay, shared by every caller that asked at once
    assert out["overlays"] == 1 and out["results"] == [True, True, True]
    # and nothing behind it claims a failure
    assert out["status_text"] == "Set up this company to continue"
    assert out["status_company"] == "Company: Harbor Light Bakery"


@needs_node
def test_the_unlock_screen_names_the_company(unauthed_client):
    assert unauthed_client.post(
        "/api/auth/setup",
        json={"password": SETUP_PW, "company_name": "Harbor Light Bakery"},
    ).is_success
    unauthed_client.post("/api/auth/logout")
    status = unauthed_client.get("/api/auth/status").json()
    assert status["setup_needed"] is False
    assert status["company_name"] == "Harbor Light Bakery"
    out = _auth_probe(status)
    assert out["screen"] == "login"
    assert "Unlock Harbor Light Bakery" in out["html"]
    assert "Enter the password for Harbor Light Bakery to continue." in out["html"]
    assert out["status_text"] == "Sign in to continue"
    assert out["status_company"] == "Company: Harbor Light Bakery"


@needs_node
def test_the_company_name_is_escaped_where_it_is_shown():
    out = _auth_probe(
        {
            "setup_needed": False,
            "authenticated": False,
            "multi_user": False,
            "company_name": '<img src=x onerror="alert(1)">',
        }
    )
    assert "<img" not in out["html"]
    assert "Unlock &lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in out["html"]


@needs_node
def test_a_signed_in_session_paints_nothing():
    out = _auth_probe({"setup_needed": False, "authenticated": True})
    assert out["overlays"] == 0 and out["results"] == [False, False, False]


def test_status_falls_back_to_the_name_the_picker_shows(unauthed_client, monkeypatch):
    """A company file made before its name was written into it: the picker's
    name for the open file is the next best thing. The shipped placeholder is
    nobody's name."""
    monkeypatch.setattr(
        company_service, "current_manifest_name", lambda: "Riverbend Books"
    )
    assert unauthed_client.get("/api/auth/status").json()["company_name"] == (
        "Riverbend Books"
    )
    monkeypatch.setattr(company_service, "current_manifest_name", lambda: "My Company")
    assert unauthed_client.get("/api/auth/status").json()["company_name"] == ""


def test_a_new_company_file_carries_the_name_typed_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    result = company_service.manifest_create_company("Harbor Light Bakery")
    assert result["success"], result
    path = tmp_path / "data" / "companies" / result["file"]
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'company_name'"
        ).fetchone()
    assert row == ("Harbor Light Bakery",)
    # ...which is what the sign-in screen reads when this file is served
    monkeypatch.setattr(company_service, "DATABASE_URL", "sqlite:///" + path.as_posix())
    assert company_service.current_manifest_name() == "Harbor Light Bakery"


def _api_probe(scenario):
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "api_request_probe.js"),
            json.dumps(scenario),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_page_request_waits_for_sign_in_instead_of_failing(unauthed_client):
    real = unauthed_client.get("/api/dashboard")
    assert real.status_code == 401
    refused = {"status": 401, "body": real.json()}
    out = _api_probe(
        {"call": ["GET", "/dashboard"], "responses": [refused], "auth": True}
    )
    # no error for the page to paint behind the dialog ("Error loading page")
    assert out.get("pending") is True and "error" not in out
    assert out["auth_prompts"] == 1
    # a session that turns out to be signed in still gets the error
    out = _api_probe(
        {"call": ["GET", "/dashboard"], "responses": [refused], "auth": False}
    )
    assert out["error"] == {"message": "Not authenticated", "status": 401}


def test_the_new_company_dialog_shows_it_is_working():
    src = (JS / "companies.js").read_text(encoding="utf-8")
    body = src[src.index("async create(e) {") :]
    before_post = body[: body.index("await API.post('/companies'")]
    assert "btn.disabled = true" in before_post
    assert "btn.textContent = 'Creating…'" in before_post
    assert "this takes a few seconds" in before_post
    after = body[body.index("} catch (err) {") :]
    assert "btn.disabled = false" in after and "'Create Company'" in after

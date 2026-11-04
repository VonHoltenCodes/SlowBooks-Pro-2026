"""The company logo is changed by an administrator, and can be removed
(2.18.0). The logo is a company setting, and every other setting is the
administrator's; a bookkeeper could still upload or remove it. Settings
also had no way to remove a logo once uploaded."""

import base64
import io
import shutil
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
# a whole 1x1 PNG with good chunk checksums: the upload verifies a raster
# logo before keeping it (NEW-201)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _upload(c):
    return c.post(
        "/api/uploads/logo", files={"file": ("logo.png", io.BytesIO(PNG), "image/png")}
    )


def test_only_an_administrator_changes_the_logo(client, seed_accounts):
    r = client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    assert r.status_code == 201, r.text
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert _upload(keeper).status_code == 403
    assert _upload(client).status_code in (200, 201)
    assert keeper.delete("/api/uploads/logo").status_code == 403
    assert client.delete("/api/uploads/logo").status_code in (200, 204)
    info = client.get("/api/uploads/logo")
    assert info.status_code in (200, 404)
    if info.status_code == 200:
        assert not (info.json() or {}).get("path")


def test_settings_offers_remove_logo():
    js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert 'onclick="SettingsPage.removeLogo()">Remove logo</button>' in js
    assert "await API.del('/uploads/logo');" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_settings_offers_the_logo_picker_to_an_administrator_only():
    # A bookkeeper was offered the file picker and answered 403 after
    # choosing a file; the page says who changes the logo instead.
    import json
    import subprocess

    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "settings_logo_role_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    rows = {r["role"]: r for r in map(json.loads, out.stdout.splitlines())}
    assert rows["admin"] == {
        "role": "admin",
        "picker": True,
        "remove": True,
        "adminOnly": False,
        "preview": True,
    }
    for role in ("bookkeeper", "readonly"):
        assert rows[role] == {
            "role": role,
            "picker": False,
            "remove": False,
            "adminOnly": True,
            "preview": True,
        }, rows[role]

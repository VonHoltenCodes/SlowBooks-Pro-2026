"""Employee portal link: absolute, and never swallowed by the desktop shim.

Field report: clicking the portal link inside the desktop app rendered the
dashboard in a document window whose tabs did nothing. The shim fetched the
same-origin _blank link and showed the HTML with no origin to resolve links
against. The portal is the employee's own cookie-based site — it must open
in a real browser, with an absolute URL the employee can actually reach."""

from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def test_portal_url_is_absolute(client, seed_accounts):
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Pat",
            "last_name": "Portal",
            "pay_type": "hourly",
            "pay_rate": 20,
        },
    ).json()
    tok = client.get(f"/api/employees/{emp['id']}/portal-token").json()
    assert tok["portal_url"].startswith("http://testserver/portal/")
    assert tok["portal_url"].endswith(tok["portal_token"])
    rotated = client.post(f"/api/employees/{emp['id']}/portal-token").json()
    assert rotated["portal_url"].startswith("http://testserver/portal/")
    assert rotated["portal_token"] != tok["portal_token"]
    # the link still claims a session end to end
    r = client.get(rotated["portal_url"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/portal/"


def test_desktop_shim_leaves_portal_links_to_the_browser():
    shim = (ROOT / "app/static/js/desktop_shim.js").read_text()
    assert "function isPortalUrl" in shim
    assert "isSameOrigin(url) && !isPortalUrl(url)" in shim  # window.open path
    assert "if (isPortalUrl(a.href))" in shim  # click path
    assert "open_external" in shim


def test_launcher_open_external_uses_the_system_browser():
    import desktop_launcher

    api = desktop_launcher.PickerApi(3001)
    with patch("webbrowser.open") as opened:
        assert api.open_external("http://127.0.0.1:3001/portal/abc") == {
            "success": True
        }
        opened.assert_called_once_with("http://127.0.0.1:3001/portal/abc")
        bad = api.open_external("file:///etc/passwd")
        assert bad["success"] is False
        assert opened.call_count == 1

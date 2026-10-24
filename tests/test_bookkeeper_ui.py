"""A bookkeeper is offered nothing the server refuses it (2.18.0).

Server Edition's bookkeeper keeps the daily books, but some writes are the
administrator's: company settings, backups, new company files, the logo,
and bringing books in from another program (app.main's _ADMIN_WRITE_PREFIXES, and the routes that call
require_admin). The pages offered them to a bookkeeper all the same. Settings
could be filled in top to bottom before Save Settings answered "Admin role
required"; Send Test Email, Create Backup, Restore, the OCR engine, "+ New
Company" and every button of Migrate Data answered 403 too. Opening an email template even sent the whole Settings form: its
Edit button had no type, so it submitted the form it sits in.

The pages mark the administrator's controls where they are built, and
App.adminPass takes them away for any role but admin: a field shows its value
locked, a button is not offered, and a sentence says why. AI Insights' key
and endpoint are company settings too (the endpoint receives the dashboard's
figures). Everything else stays: the lists on Settings (email templates,
classes, cost types and codes, equipment) and every page the server lets a
bookkeeper use.

tests/js/admin_controls_probe.js draws those pages for each role, the first
page's order included (the role arrives after the page). Here every control
it finds is accounted for, with what it sends: each one a bookkeeper keeps is
answered for a bookkeeper's API token, and each one taken away is refused.
"""

import io
import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
KEEPER_PW = "long-enough-pw"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)
UPLOAD = "a file"  # the logo picker sends the image itself, not JSON

# ---------------------------------------------------------------------------
# What each control sends, by the key the probe gives it ("section › label").
# A request is (method, path, body, the answer); "{name}" is a record the
# test makes first.
# ---------------------------------------------------------------------------

# Settings: what a bookkeeper keeps, and the answer it gets.
SETTINGS_KEPT = {
    "Email Templates › Seed Default Templates": [
        ("POST", "/api/email-templates/seed-defaults", None, 200)
    ],
    "Email Templates › Edit": [("GET", "/api/email-templates/{template}", None, 200)],
    "Edit Email Template › Preview": [
        (
            "POST",
            "/api/email-templates/preview",
            {"invoice_id": "{invoice}", "subject_template": "Hi", "body_template": "x"},
            200,
        )
    ],
    "Edit Email Template › Save Template": [
        (
            "PUT",
            "/api/email-templates/{template}",
            {"subject_template": "Invoice", "body_template": "<p>Hello</p>"},
            200,
        )
    ],
    "Edit Email Template › Cancel": [],
    "Classes › Add Class": [("POST", "/api/classes", {"name": "Wholesale"}, 201)],
    "Classes › Rename": [("PUT", "/api/classes/{klass}", {"name": "Counter"}, 200)],
    "Classes › Archive": [("PUT", "/api/classes/{klass}", {"is_archived": True}, 200)],
    "Cost Types › Add Cost Type": [
        ("POST", "/api/cost-types", {"code": "permits", "name": "Permits"}, 201)
    ],
    "Cost Types › Create default offset accounts": [
        ("POST", "/api/cost-types/setup-offsets", None, 200)
    ],
    "Cost Types › Save": [
        (
            "PUT",
            "/api/cost-types/{cost_type}",
            {"name": "Bonding", "burden_pct": 5},
            200,
        )
    ],
    "Cost Types › Deactivate": [
        ("PUT", "/api/cost-types/{cost_type}", {"is_active": False}, 200)
    ],
    "Cost Codes › Add Cost Code": [
        ("POST", "/api/cost-codes", {"code": "05", "name": "Metals"}, 201)
    ],
    "Cost Codes › Load standard list": [
        ("POST", "/api/cost-codes/standard", None, 200)
    ],
    "Cost Codes › Import CSV": [
        ("POST", "/api/cost-codes/import", {"csv": "04,Masonry,material,"}, 200)
    ],
    "Cost Codes › Rename": [
        ("PUT", "/api/cost-codes/{cost_code}", {"name": "Cast concrete"}, 200)
    ],
    "Cost Codes › Deactivate": [
        ("PUT", "/api/cost-codes/{cost_code}", {"is_active": False}, 200)
    ],
    "Equipment › Add Equipment": [
        ("POST", "/api/equipment", {"name": "Mini excavator", "hourly_rate": 95}, 201)
    ],
    "Equipment › Set rate": [
        ("PUT", "/api/equipment/{equipment}", {"hourly_rate": 90}, 200)
    ],
    "Equipment › Deactivate": [
        ("PUT", "/api/equipment/{equipment}", {"is_active": False}, 200)
    ],
}
# The page's own reads: what it shows loads for a bookkeeper
SETTINGS_READS = [
    "/api/settings",
    "/api/uploads/logo",
    "/api/backups",
    "/api/email-templates",
    "/api/invoice-messages",
    "/api/analytics/ai-config",
    "/api/classes?include_archived=true",
    "/api/cost-codes?include_inactive=true",
    "/api/cost-types?include_inactive=true",
    "/api/equipment?include_inactive=true",
    "/api/accounts",
    "/api/ocr/status",
]
# The fields and pickers a control sends
SETTINGS_KEPT_PARTS = {
    "Classes › #new-class-name": "Classes › Add Class",
    "Cost Types › #new-ct-code": "Cost Types › Add Cost Type",
    "Cost Types › #new-ct-name": "Cost Types › Add Cost Type",
    "Cost Types › #new-ct-labor": "Cost Types › Add Cost Type",
    "Cost Types › .ct-name": "Cost Types › Save",
    "Cost Types › .ct-labor": "Cost Types › Save",
    "Cost Types › .ct-burden": "Cost Types › Save",
    "Cost Types › .ct-burden-method": "Cost Types › Save",
    "Cost Types › .ct-default": "Cost Types › Save",
    "Cost Types › .ct-offset": "Cost Types › Save",
    "Cost Types › .ct-burden-offset": "Cost Types › Save",
    "Cost Codes › #new-cc-code": "Cost Codes › Add Cost Code",
    "Cost Codes › #new-cc-name": "Cost Codes › Add Cost Code",
    "Cost Codes › #new-cc-type": "Cost Codes › Add Cost Code",
    "Cost Codes › #new-cc-parent": "Cost Codes › Add Cost Code",
    "Equipment › #new-eq-code": "Equipment › Add Equipment",
    "Equipment › #new-eq-name": "Equipment › Add Equipment",
    "Equipment › #new-eq-rate": "Equipment › Add Equipment",
    "Edit Email Template › name": "Edit Email Template › Save Template",
    "Edit Email Template › template_type": "Edit Email Template › Save Template",
    "Edit Email Template › subject_template": "Edit Email Template › Save Template",
    "Edit Email Template › body_template": "Edit Email Template › Save Template",
    "Edit Email Template › #email-template-invoice": "Edit Email Template › Preview",
}

# Settings: what is taken from a bookkeeper. Every named field of the form
# is sent by Save Settings as well.
SETTINGS_TAKEN = {
    "Save Settings": ("PUT", "/api/settings", {"company_name": "Harbor Light"}),
    "Company Information › company_type": (
        "PUT",
        "/api/settings",
        {"company_type": "nonprofit"},
    ),
    "Company Information › entity_type": (
        "PUT",
        "/api/settings",
        {"entity_type": "s_corp"},
    ),
    "Closing Date › Clear": ("PUT", "/api/settings", {"closing_date": ""}),
    "Email (SMTP) › Send Test Email": ("POST", "/api/settings/test-email", None),
    "Receipt Scanning › #ocr-engine-pref": (
        "PUT",
        "/api/settings",
        {"ocr_engine": "tesseract"},
    ),
    "Company Logo › #logo-upload": ("POST", "/api/uploads/logo", UPLOAD),
    "Company Logo › Remove logo": ("DELETE", "/api/uploads/logo", None),
    "Backup / Restore › Create Backup": ("POST", "/api/backups", None),
    "Backup / Restore › Restore…": (
        "POST",
        "/api/backups/restore",
        {"filename": "harbor-light_2026-09-25_2210.db"},
    ),
    "Backup / Restore › Download": (
        "GET",
        "/api/backups/download/harbor-light_2026-09-25_2210.db",
        None,
    ),
    "AI Insights › Save AI settings": (
        "PUT",
        "/api/analytics/ai-config",
        {"provider": "anthropic", "model": ""},
    ),
    "AI Insights › Test": ("POST", "/api/analytics/ai-config/test", {}),
    "AI Insights › Refresh models": ("GET", "/api/analytics/ai-models", None),
    "AI Insights › Remove": (
        "PUT",
        "/api/analytics/ai-config",
        {"provider": "anthropic", "api_key": ""},
    ),
    # the saved invoice messages (#392) are company settings by another name
    "Invoice Messages › + Add Message": (
        "POST",
        "/api/invoice-messages",
        {"name": "Card", "body": "To pay by card, ask for a link."},
    ),
    "Invoice Messages › Edit": (
        "PUT",
        "/api/invoice-messages/{message}",
        {"body": "To pay by Zelle use pay@example.com."},
    ),
    "Invoice Messages › .msg-default": (
        "PUT",
        "/api/invoice-messages/{message}",
        {"is_default": True},
    ),
    "Invoice Messages › Delete": ("DELETE", "/api/invoice-messages/{message}", None),
}
# Users and API tokens refuse every API token whatever its role, so these are
# asked with a bookkeeper's own sign-in. Settings shows the two sections to
# an administrator only.
SETTINGS_TAKEN_FROM_A_SIGN_IN = {
    "Users — Server Edition › Add User": (
        "POST",
        "/api/users",
        {"username": "rita", "password": KEEPER_PW, "role": "readonly"},
    ),
    "API Tokens — agents & integrations › Create Token": (
        "POST",
        "/api/tokens",
        {"label": "kim-agent", "role": "bookkeeper"},
    ),
    # inviting an accountant (#345) makes a sign-in: an administrator's
    "Users — Server Edition › Invite your accountant…": (
        "POST",
        "/api/users/invite",
        {"username": "pat", "display_name": "Pat CPA"},
    ),
}
SETTINGS_TAKEN_PARTS = {
    "AI Insights › #ai-settings-provider": "AI Insights › Save AI settings",
    "AI Insights › #ai-settings-model-select": "AI Insights › Save AI settings",
    "AI Insights › #ai-settings-key": "AI Insights › Save AI settings",
    "Users — Server Edition › #user-new-username": "Users — Server Edition › Add User",
    "Users — Server Edition › #user-new-display": "Users — Server Edition › Add User",
    "Users — Server Edition › #user-new-password": "Users — Server Edition › Add User",
    "Users — Server Edition › #user-new-role": "Users — Server Edition › Add User",
    "API Tokens — agents & integrations › #token-new-label": (
        "API Tokens — agents & integrations › Create Token"
    ),
    "API Tokens — agents & integrations › #token-new-role": (
        "API Tokens — agents & integrations › Create Token"
    ),
}

COMPANIES_TAKEN = {
    "+ New Company": ("POST", "/api/companies", {"name": "Harbor Light Catering"}),
    # applying an accountant's changes posts to the books (#346): an administrator's
    "Apply accountant's changes…": (
        "POST",
        "/api/accountants-copy/changes/apply",
        None,
    ),
}
MIGRATE_TAKEN = {
    "Dry Run": ("POST", "/api/migration/xero/dry-run", None),
    "Import": ("POST", "/api/migration/xero/import", None),
}


KEPT = {**SETTINGS_KEPT}
TAKEN = {**SETTINGS_TAKEN, **COMPANIES_TAKEN, **MIGRATE_TAKEN}


def _control(key, names):
    """The control a key is sent by: a field or a picker belongs to its
    button, and every named field of the Settings form to Save Settings."""
    if key in KEPT or key in TAKEN or key in SETTINGS_TAKEN_FROM_A_SIGN_IN:
        return key
    for parts in (SETTINGS_KEPT_PARTS, SETTINGS_TAKEN_PARTS):
        if key in parts:
            return parts[key]
    if " › " in key and key.split(" › ")[-1] in names:
        return "Save Settings"
    return key


@lru_cache(maxsize=1)
def _run_probe():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "admin_controls_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout)


def _probe():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    return _run_probe()


def _yes(controls):
    return {key for key, on in controls.items() if on}


def _pages(probe, role):
    """Each page's controls a role is offered."""
    runs = {
        "settings": [probe["settings"][role]["offered"]],
        "companies": [probe["companies"][role]["offered"]],
    }
    if role == "bookkeeper":
        runs["settings"].append(probe["settings"]["bookkeeper_first_page"]["offered"])
    names = set(probe["settings"]["admin"]["fields"]["names"])
    out = {
        page: {_control(k, names) for c in cs for k in _yes(c)}
        for page, cs in runs.items()
    }
    # the way back from the page that says it is the administrator's
    out["migrate"] = set(probe["migrate"][role]["buttons"]) - {"Return to Dashboard"}
    return out


# ---- the pages, drawn for each role ---------------------------------------


def test_settings_shows_a_bookkeeper_the_company_settings_locked():
    for run in ("bookkeeper", "bookkeeper_first_page"):
        got = _probe()["settings"][run]
        # every field Save Settings sends shows its value, locked
        assert got["fields"]["total"] >= 50 and got["fields"]["enabled"] == [], run
        # a sentence says why, and the backups say whose they are
        assert got["notes"] == [
            "Company settings are changed by an administrator.",
            "AI settings are changed by an administrator.",
            "Backups are made, downloaded and restored by an administrator.",
        ], run
        # nothing on the page is an edit: leaving never asks
        assert (got["dirty"], got["asked_on_leaving"]) == (False, 0), run


def test_settings_leaves_a_bookkeeper_the_lists_and_templates():
    kept = set(SETTINGS_KEPT) | set(SETTINGS_KEPT_PARTS)
    for run in ("bookkeeper", "bookkeeper_first_page"):
        offered = _yes(_probe()["settings"][run]["offered"])
        # each is offered, and nothing else is
        assert (sorted(kept - offered), sorted(offered - kept)) == ([], []), run


def test_settings_is_as_it_was_for_an_administrator():
    got = _probe()["settings"]["admin"]
    assert got["fields"]["enabled"] == got["fields"]["names"]
    everything = (
        set(SETTINGS_KEPT)
        | set(SETTINGS_KEPT_PARTS)
        | set(SETTINGS_TAKEN)
        | set(SETTINGS_TAKEN_FROM_A_SIGN_IN)
        | set(SETTINGS_TAKEN_PARTS)
    )
    assert sorted(everything - _yes(got["offered"])) == []
    assert got["notes"] == []
    assert got["dirty"] is False and got["dirty_after_edit"] is True


def test_opening_a_template_does_not_send_the_settings_form():
    # Edit sat in the Settings form with no type, so it submitted the form:
    # an administrator's settings were saved behind the editor, and a
    # bookkeeper was told "Your role doesn't allow this action".
    for run in ("admin", "bookkeeper", "readonly"):
        assert _probe()["settings"][run]["submits"] == ["Save Settings"], run


def test_a_read_only_sign_in_has_its_own_sentence_on_settings_not_two():
    got = _probe()["settings"]["readonly"]
    assert got["fields"]["enabled"] == []
    assert (got["readonly_notes"], got["notes"]) == (1, [])
    assert _yes(got["offered"]) & set(SETTINGS_TAKEN) == set()


def test_companies_leaves_a_new_company_to_an_administrator():
    got = _probe()["companies"]
    assert _yes(got["bookkeeper"]["shown"]) == set()
    assert got["bookkeeper"]["notes"] == [
        "New company files are created by an administrator."
    ]
    assert got["admin"]["notes"] == [] and got["readonly"]["notes"] == []


def test_migrate_data_is_an_administrators_page():
    got = _probe()["migrate"]
    assert got["admin"]["sidebar_link_shown"] is True
    for run in ("bookkeeper", "readonly", "bookkeeper_first_page"):
        text = got[run]["text"]
        assert text.startswith("Migrate Data is for administrators"), run
        assert "Bringing books in from another program is open to an " in text, run
        assert got[run]["buttons"] == ["Return to Dashboard"], run
    # the sidebar leaves it out once the role is known
    assert got["bookkeeper"]["sidebar_link_shown"] is False


# ---- and the server agrees ------------------------------------------------


def _keeper_token(client):
    r = client.post("/api/tokens", json={"label": "keeper", "role": "bookkeeper"})
    assert r.status_code == 201, r.text
    keeper = TestClient(app)
    keeper.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return keeper


def _send(c, method, path, body, made):
    path = path.format(**made)
    if body == UPLOAD:
        files = {"file": ("logo.png", io.BytesIO(PNG), "image/png")}
        return c.request(method, path, files=files)
    if body is not None:
        # "{invoice}" stands for the id of the record the test made
        body = {
            k: made[v.strip("{}")] if isinstance(v, str) and v[:1] == "{" else v
            for k, v in body.items()
        }
    return c.request(method, path, json=body)


@pytest.fixture
def made(client, db_session, seed_accounts, seed_customer):
    """What the controls act on, made by the administrator."""
    from app.models.invoices import Invoice, InvoiceStatus

    invoice = Invoice(
        invoice_number="1001",
        customer_id=seed_customer.id,
        date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        subtotal=Decimal("125"),
        total=Decimal("125"),
        balance_due=Decimal("125"),
        status=InvoiceStatus.SENT,
    )
    db_session.add(invoice)
    db_session.commit()

    def ok(r):
        assert r.status_code in (200, 201), r.text
        return r.json()

    ok(client.post("/api/email-templates/seed-defaults"))
    return {
        "invoice": invoice.id,
        "template": ok(client.get("/api/email-templates"))[0]["id"],
        "message": ok(
            client.post(
                "/api/invoice-messages", json={"name": "Zelle", "body": "Pay by Zelle."}
            )
        )["id"],
        "klass": ok(client.post("/api/classes", json={"name": "Retail"}))["id"],
        "cost_type": ok(
            client.post("/api/cost-types", json={"code": "bonding", "name": "Bond"})
        )["id"],
        "cost_code": ok(
            client.post("/api/cost-codes", json={"code": "03", "name": "Concrete"})
        )["id"],
        "equipment": ok(
            client.post(
                "/api/equipment", json={"name": "Skid steer", "hourly_rate": 85}
            )
        )["id"],
    }


def test_every_control_a_bookkeeper_is_offered_the_server_answers(client, made):
    """Each control the pages leave a bookkeeper, sent as it sends it with a
    bookkeeper's API token: none is refused, and each gets its answer."""
    offered = set().union(*_pages(_probe(), "bookkeeper").values())
    unknown = sorted(
        offered - set(KEPT) - set(TAKEN) - set(SETTINGS_TAKEN_FROM_A_SIGN_IN)
    )
    assert unknown == []
    keeper = _keeper_token(client)
    for path in SETTINGS_READS:
        assert keeper.get(path).status_code == 200, path
    refused = []
    for control in sorted(offered):
        if control in SETTINGS_TAKEN_FROM_A_SIGN_IN:  # no API token may ask
            refused.append(control)
            continue
        calls = KEPT[control] if control in KEPT else [TAKEN[control]]
        for method, path, body, *answer in calls:
            r = _send(keeper, method, path, body, made)
            if r.status_code == 403:
                refused.append(control)
            else:
                assert [r.status_code] == answer, (control, path, r.status_code, r.text)
    assert refused == []


def test_every_control_taken_from_a_bookkeeper_the_server_refuses(client, made):
    """What the pages show an administrator and not a bookkeeper is exactly
    what the server keeps for the administrator."""
    probe = _probe()
    admin, keeper_pages = _pages(probe, "admin"), _pages(probe, "bookkeeper")
    taken = {page: admin[page] - keeper_pages[page] for page in admin}
    assert taken == {
        "settings": set(SETTINGS_TAKEN) | set(SETTINGS_TAKEN_FROM_A_SIGN_IN),
        "companies": set(COMPANIES_TAKEN),
        "migrate": set(MIGRATE_TAKEN),
    }
    keeper = _keeper_token(client)
    for control, (method, path, body) in TAKEN.items():
        r = _send(keeper, method, path, body, made)
        assert r.status_code == 403, (control, method, path, r.status_code, r.text)
    # users and tokens, from a bookkeeper's own sign-in: no API token may
    # manage either, whatever its role
    r = client.post(
        "/api/users",
        json={"username": "kim", "password": KEEPER_PW, "role": "bookkeeper"},
    )
    assert r.status_code == 201, r.text
    kim = TestClient(app)
    r = kim.post("/api/auth/login", json={"username": "kim", "password": KEEPER_PW})
    assert r.status_code == 200, r.text
    for control, (method, path, body) in SETTINGS_TAKEN_FROM_A_SIGN_IN.items():
        r = kim.request(method, path, json=body)
        assert r.status_code == 403, (control, r.status_code, r.text)

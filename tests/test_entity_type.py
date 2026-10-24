"""Legal entity type (#488).

`entity_info` is the one table that maps an entity type (and, for an LLC, its
tax election) to a label and the federal return it files. Schedule C is
offered only to a sole proprietor or a single-member LLC taxed as one; the
other entities answer 409 at the Schedule C routes, and an unset entity type
leaves Schedule C exactly as before.
"""

from pathlib import Path

from app.services.settings_service import entity_info

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_JS = ROOT / "app/static/js/settings.js"
TAX_JS = ROOT / "app/static/js/tax.js"


# entity_type, llc_tax_election -> label, return_form, schedule_c
ENTITY_TABLE = [
    ("", "", "", "", False),
    ("sole_proprietor", "", "a sole proprietor", "Schedule C", True),
    ("general_partnership", "", "a general partnership", "Form 1065", False),
    ("limited_partnership", "", "a limited partnership", "Form 1065", False),
    ("llp", "", "a limited liability partnership", "Form 1065", False),
    ("c_corp", "", "a C corporation", "Form 1120", False),
    ("s_corp", "", "an S corporation", "Form 1120-S", False),
    ("llc", "disregarded", "an LLC taxed as a sole proprietor", "Schedule C", True),
    ("llc", "partnership", "an LLC taxed as a partnership", "Form 1065", False),
    ("llc", "s_corp", "an LLC taxed as an S corporation", "Form 1120-S", False),
    ("llc", "c_corp", "an LLC taxed as a C corporation", "Form 1120", False),
    # an LLC with no election yet: no return we can name, so Schedule C stays
    ("llc", "", "an LLC", "", False),
]


def test_entity_info_table():
    for entity_type, election, label, return_form, schedule_c in ENTITY_TABLE:
        info = entity_info({"entity_type": entity_type, "llc_tax_election": election})
        assert info["label"] == label, entity_type
        assert info["return_form"] == return_form, entity_type
        assert info["schedule_c"] == schedule_c, entity_type


def test_entity_info_ignores_a_stray_election_for_non_llc():
    # llc_tax_election is only meaningful for an LLC; a leftover value from a
    # company that was an LLC and switched is ignored.
    info = entity_info({"entity_type": "s_corp", "llc_tax_election": "disregarded"})
    assert info["return_form"] == "Form 1120-S"
    assert info["election"] == ""


def test_entity_info_handles_none_and_unset():
    assert entity_info(None)["return_form"] == ""
    assert entity_info({})["schedule_c"] is False


# ---------------------------------------------------------------------------
# The Schedule C gate
# ---------------------------------------------------------------------------


def test_schedule_c_refuses_an_s_corp(client):
    client.put("/api/settings", json={"entity_type": "s_corp"})
    for path in ("/api/tax/schedule-c", "/api/tax/schedule-c/csv"):
        r = client.get(path)
        assert r.status_code == 409, path
        detail = r.json()["detail"]
        assert detail["code"] == "not_schedule_c"
        assert "Form 1120-S" in detail["message"]
        assert "Schedule C" in detail["message"]


def test_schedule_c_refuses_an_llc_taxed_as_a_partnership(client):
    client.put(
        "/api/settings",
        json={"entity_type": "llc", "llc_tax_election": "partnership"},
    )
    r = client.get("/api/tax/schedule-c")
    assert r.status_code == 409
    assert r.json()["detail"]["message"].startswith(
        "This company is an LLC taxed as a partnership; it files Form 1065"
    )


def test_schedule_c_passes_for_a_sole_proprietor(client, seed_accounts):
    client.put("/api/settings", json={"entity_type": "sole_proprietor"})
    assert client.get("/api/tax/schedule-c").status_code == 200


def test_schedule_c_passes_for_a_disregarded_llc(client, seed_accounts):
    client.put(
        "/api/settings",
        json={"entity_type": "llc", "llc_tax_election": "disregarded"},
    )
    assert client.get("/api/tax/schedule-c").status_code == 200


def test_schedule_c_unset_entity_type_is_as_before(client, seed_accounts):
    assert client.get("/api/tax/schedule-c").status_code == 200
    # an LLC with no election chosen also stays open (we can't name a return)
    client.put("/api/settings", json={"entity_type": "llc"})
    assert client.get("/api/tax/schedule-c").status_code == 200


# ---------------------------------------------------------------------------
# The LLC "Taxed as" select only renders for an LLC (source assertions)
# ---------------------------------------------------------------------------


def test_llc_election_select_is_hidden_unless_llc():
    js = SETTINGS_JS.read_text()
    # the group is hidden unless the stored entity type is llc
    assert "id=\"llc-election-group\" ${s.entity_type === 'llc' ? '' : 'hidden'}" in js
    # and the onchange toggles it as the picker changes
    assert "SettingsPage._onEntityTypeChange" in js
    assert "group.hidden = sel.value !== 'llc';" in js


def test_tax_page_gates_on_entity_info():
    js = TAX_JS.read_text()
    assert "info.type && !info.scheduleC && info.returnForm" in js
    assert "not Schedule C" in js

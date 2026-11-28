"""2.24.0 round 3: what round 2 left (skytech W-23 to W-26, macbase1 NEW-149
on the Mac, NEW-150, NEW-158, NEW-165, NEW-166).

- W-25 / NEW-158: the page's own backups list says which are plain, and
  Settings counts them from that list, not from a race.
- NEW-149 (the Mac): the first page waits for the sign-in's state; a page
  refused to a one-time sign-in draws the form, not the refusal.
- W-23 / NEW-150: the desktop's invitation says its address is this
  computer's only, and how an accountant elsewhere gets the books.
- W-26 / NEW-166: a flag (must_change_password) is not a secret.
- W-24: a taken company name at setup shows its sentence and stays.
- NEW-165: the copy's made-on day on the person's clock.
"""

from pathlib import Path


from app.services import company_file
from app.services.audit_redaction import REDACTED, redact_values

ROOT = Path(__file__).resolve().parents[1]


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# W-25 / NEW-158: the page's list
# ---------------------------------------------------------------------------


def test_the_companys_backups_say_which_are_plain(
    client, db_session, tmp_path, monkeypatch
):
    from app.services import backup_service as bs

    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)
    slug = bs.company_slug()
    plain = tmp_path / f"{slug}_20260101_000000.db"
    conn = company_file.connect_raw(plain, None)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    enc = tmp_path / f"{slug}_20260102_000000.db"
    conn = company_file.connect_raw(enc, bytes(range(32)))
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    rows = {b["filename"]: b for b in bs.list_company_backups(db_session)}
    assert (
        rows[plain.name]["encrypted"] is False and rows[enc.name]["encrypted"] is True
    )
    # the route the page reads
    listed = {b["filename"]: b for b in client.get("/api/backups").json()}
    assert (
        listed[plain.name]["encrypted"] is False
        and listed[enc.name]["encrypted"] is True
    )


def test_settings_counts_the_plain_backups_from_the_list_it_reads():
    js = _src("app/static/js/settings.js")
    section = js[
        js.index("async loadCompanyFile() {") : js.index("async _companyNameNow() {")
    ]
    assert "backups = await API.get('/backups');" in section
    assert "const plain = backups.filter(b => b.encrypted === false).length;" in section
    assert "SettingsPage.loadBackups();" in section
    listing = js[js.index("async loadBackups() {") : js.index("_andList(names) {")]
    assert "plain SQLite, made before encryption: readable with no key" in listing


# ---------------------------------------------------------------------------
# NEW-149 on the Mac: the first page waits for the sign-in's state
# ---------------------------------------------------------------------------


def test_the_first_page_waits_for_the_sign_ins_state():
    app = _src("app/static/js/app.js")
    assert (
        # (the Fast Rack's list, #912, read alongside)
        "Promise.all([App.loadCompanySettings(), App.initSystemInfo(), rack]).then(() => {"
        in app
    )
    init = app[
        app.index("async initSystemInfo() {") : app.index(
            "\n    },\n", app.index("async initSystemInfo() {")
        )
    ]
    # the flag is set, the page is not drawn from here any more
    assert "App.mustChangePassword = true;" in init
    assert "App.navigate(location.hash || '#/');" not in init
    # a page refused to the one-time sign-in draws the form
    catch = app[
        app.index(
            "} catch (err) {\n            if (App._overtaken(seq)) return;"
            "\n            if (adminOnly()) return showAdminOnly();"
        ) :
    ][:600]
    assert (
        "if (App.mustChangePassword) {" in catch
        and "App._passwordChangeHtml()" in catch
    )
    # Return to Dashboard works on the dashboard's own address too
    assert (
        '<a href="#/" class="btn btn-secondary" onclick="App.navigate(\'#/\')">Return to Dashboard</a>'
        in app
    )


# ---------------------------------------------------------------------------
# W-23 / NEW-150: the desktop's invitation
# ---------------------------------------------------------------------------


def test_the_desktops_invitation_says_this_computer_only(client, monkeypatch):
    from app.models.users import ROLE_ACCOUNTANT
    from app.routes.users import DESKTOP_ADDRESS_NOTE

    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")
    monkeypatch.delenv("SLOWBOOKS_SERVER_MODE", raising=False)
    r = client.post(
        "/api/users/invite",
        json={"username": "cpa", "role": ROLE_ACCOUNTANT},
        headers={"host": "127.0.0.1:3001"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["address"] == "http://127.0.0.1:3001"
    assert r.json()["note"] == DESKTOP_ADDRESS_NOTE
    assert "Accountant's Copy" in DESKTOP_ADDRESS_NOTE
    # Server Edition on the host itself: the LAN address, no note
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    r = client.post(
        "/api/users/invite",
        json={"username": "cpa2", "role": ROLE_ACCOUNTANT},
        headers={"host": "127.0.0.1:3001"},
    )
    assert r.json()["note"] == "" and "127.0.0.1" not in r.json()["address"]
    js = _src("app/static/js/settings.js")
    assert "${r.note ? ` (${r.note})` : ''}" in js


# ---------------------------------------------------------------------------
# W-26 / NEW-166: a flag stays readable
# ---------------------------------------------------------------------------


def test_a_flag_is_not_a_secret():
    out = redact_values(
        "users", {"must_change_password": True, "password_hash": "$argon2id$x"}
    )
    assert out["must_change_password"] is True and out["password_hash"] == REDACTED
    out = redact_values("settings", {"key": "ask_password_on_start", "value": "true"})
    assert out["value"] == "true"


def test_the_audit_shows_the_one_time_password_replaced(client, db_session):
    from app.models.audit import AuditLog
    from app.models.users import ROLE_ACCOUNTANT

    one_time = client.post(
        "/api/users/invite", json={"username": "cpa", "role": ROLE_ACCOUNTANT}
    ).json()["password"]
    client.post("/api/auth/logout")
    assert (
        client.post(
            "/api/auth/login", json={"username": "cpa", "password": one_time}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/password", json={"new_password": "cpa-own-password-1"}
        ).status_code
        == 200
    )
    rows = [
        r
        for r in db_session.query(AuditLog)
        .filter_by(table_name="users", action="UPDATE")
        .all()
        if "must_change_password" in (r.changed_fields or [])
    ]
    assert rows and rows[-1].old_values["must_change_password"] is True
    assert rows[-1].new_values["must_change_password"] is False
    assert rows[-1].new_values["password_hash"] == REDACTED


# ---------------------------------------------------------------------------
# W-24, NEW-165
# ---------------------------------------------------------------------------


def test_a_taken_company_name_at_setup_shows_its_sentence_and_stays():
    js = _src("app/static/js/auth.js")
    assert (
        'if (err.status === 409 && !/already named/i.test(err.message || "")) {' in js
    )


def test_a_taken_company_name_at_setup_is_a_409_naming_the_other_file(
    unauthed_client, monkeypatch
):
    from app.services import company_service

    monkeypatch.setattr(
        company_service,
        "company_name_taken_by",
        lambda name, exclude_file=None: "other.db",
    )
    r = unauthed_client.post(
        "/api/auth/setup",
        json={"password": "a-strong-password-1", "company_name": "Harbor Light"},
    )
    assert r.status_code == 409 and "already named" in r.json()["detail"]


def test_the_copys_made_on_day_is_the_persons():
    js = _src("app/static/js/companies.js")
    assert "new Date(ac.copy.made_at).toLocaleDateString()" in js

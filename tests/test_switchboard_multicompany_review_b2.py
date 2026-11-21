# ruff: noqa: F811
"""2.32.0-beta.2 review: whose backups are whose, who sets a company up, what
a token is told, and what the AI assistant panel says and offers.

- A backup's company part never belongs to two companies: two long company
  names that differ only at the end keep their backups apart, and a new
  company whose backups would be named like another's is refused.
- A company with no password yet is set up only from the sign-in that
  opened it under Companies, whether the request names it by cookie or by
  its company header.
- The sign-in status a program reads answers only for the company its own
  token opens.
- An AI assistant's token whose maker is no longer an administrator says
  so, in the gate's answer and in Settings, API Tokens, and the panel offers
  Add again at once.
- A client company's administrator learns nothing of the other companies
  when they cannot be opened now, and gets no Disconnect.
- Take off changes nothing when the record cannot be written, and a full
  connection says it is full.

Every password and token is made at run time.
"""

import json
import re
import secrets
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.database import Base
from app.main import app
from app.services import backup_service
from app.services import switchboard_link as link
from tests.test_backups_per_company import _make_books, books  # noqa: F401
from tests.test_served_companies import _make_company
from tests.test_switchboard_multicompany_b2 import (  # noqa: F401
    COMPANIES,
    _connected_with,
    _record,
    _rows,
    _set_role,
    _signed_in,
    _switch,
    relay,
    three,
)
from tests.test_switchboard_multicompany_followups_b2 import (
    REFUSED_HEADER,
    _bot,
    _hand_token,
)

# 61 and 61 characters: the same first 60
LONG_11 = "northern-illinois-commercial-construction-remodeling-group-11.db"
LONG_12 = "northern-illinois-commercial-construction-remodeling-group-12.db"


def _list_companies(host, extra):
    rows = [{"name": n, "file": f} for f, n in COMPANIES]
    rows += [{"name": n, "file": f} for f, n in extra]
    (host.root / "companies.json").write_text(
        json.dumps({"companies": rows, "last_opened": "harbor.db"}), encoding="utf-8"
    )


def _person(company_file, username, password):
    person = TestClient(app)
    assert (
        person.post("/api/auth/company", json={"file": company_file}).status_code == 200
    )
    r = person.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return person


# -- backups: one company part per company -------------------------------------------


def test_two_long_names_that_differ_at_the_end_keep_their_backups_apart(three):
    pw11, pw12 = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    _make_company(
        three.folder / LONG_11, "Group Eleven", [("owner1", pw11, "admin", True)]
    )
    _make_company(
        three.folder / LONG_12, "Group Twelve", [("owner2", pw12, "admin", True)]
    )
    _list_companies(three, [(LONG_11, "Group Eleven"), (LONG_12, "Group Twelve")])
    eleven = _person(LONG_11, "owner1", pw11)
    r = eleven.post("/api/backups", json={})
    assert r.status_code == 200, r.text
    made = r.json()["filename"]
    assert [b["filename"] for b in eleven.get("/api/backups").json()] == [made]

    twelve = _person(LONG_12, "owner2", pw12)
    assert twelve.get("/api/backups").json() == []
    assert twelve.get(f"/api/backups/download/{made}").status_code == 404
    for confirmed in (False, True):
        r = twelve.post(
            "/api/backups/restore",
            json={"filename": made, "allow_other_company": confirmed},
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "other_company_server"
        assert "Group Eleven" not in r.text
    # Twelve's books are its own still
    with closing(sqlite3.connect(three.folder / LONG_12)) as conn:
        name = conn.execute(
            "SELECT value FROM settings WHERE key = 'company_name'"
        ).fetchone()[0]
    assert name == "Group Twelve"


def test_a_backup_named_the_old_way_is_not_the_other_long_named_companys(three):
    pw11, pw12 = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    _make_company(
        three.folder / LONG_11, "Group Eleven", [("owner1", pw11, "admin", True)]
    )
    _make_company(
        three.folder / LONG_12, "Group Twelve", [("owner2", pw12, "admin", True)]
    )
    _list_companies(three, [(LONG_11, "Group Eleven"), (LONG_12, "Group Twelve")])
    # before 2.32.0-beta.2 both companies' backups were named with this
    old = LONG_11[:60]
    assert old == LONG_12[:60]
    stray = backup_service.BACKUP_DIR / f"{old}_20261101_120000.db"
    _make_books(stray, "Group Eleven", "eleven")
    twelve = _person(LONG_12, "owner2", pw12)
    assert twelve.get("/api/backups").json() == []
    assert twelve.get(f"/api/backups/download/{stray.name}").status_code == 404


@pytest.mark.parametrize(
    "stem",
    [
        "harbor-light-bakery",
        "harbor_light_bakery",
        "harbor-light--bakery",
        "slowbooks",
        "slowbooks-books",
        "a" * 61,
        "a" * 60,
        "acme--0123456789",
    ],
)
def test_no_two_stems_share_a_backup_part(stem):
    others = [
        "harbor-light-bakery",
        "harbor_light_bakery",
        "harbor-light--bakery",
        "slowbooks",
        "slowbooks-books",
        "a" * 61,
        "a" * 62,
        "a" * 60,
        "acme--0123456789",
        "acme",
    ]
    key = backup_service.backup_key_for_stem(stem)
    assert len(key) <= 60
    assert re.fullmatch(r"[a-z0-9-]+", key)
    clashes = [
        o for o in others if o != stem and backup_service.backup_key_for_stem(o) == key
    ]
    assert clashes == []


def test_a_plain_stem_keeps_the_name_its_backups_always_had():
    assert backup_service.backup_key_for_stem("harbor-light-bakery") == (
        "harbor-light-bakery"
    )
    assert backup_service.backup_key_for_stem("slowbooks") == "slowbooks-books"


def test_a_backup_named_the_old_way_still_lists_for_its_own_company(
    tmp_path, monkeypatch, client
):
    """A company whose file stem is not a plain name ("harbor_light") had
    backups named "harbor-light_..."; they are still its own, even after a
    rename, while no other listed company has that name."""
    import app.database as db_module

    from tests.test_backups_per_company import _KeepTheTestConnection

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    live = tmp_path / "companies" / "harbor_light.db"
    live.parent.mkdir()
    _make_books(live, "Harbor Light Bakery", "original")
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    r = client.put("/api/settings", json={"company_name": "Harbor Light Bakery"})
    assert r.status_code == 200, r.text
    old = backups / "harbor-light_20250101_120000.db"
    _make_books(old, "Harbor Light Old Name", "old")
    r = client.post("/api/backups")
    assert r.status_code == 200, r.text
    made = r.json()["filename"]
    assert made.startswith("harbor-light--"), made
    listed = {b["filename"] for b in client.get("/api/backups").json()}
    assert listed == {made, old.name}


def test_a_new_company_whose_backups_would_mix_with_anothers_is_refused(
    tmp_path, monkeypatch
):
    from app.services import company_service, served_companies

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    served_companies._manifest_cache.update(stamp=None, rows=[])
    (tmp_path / "companies").mkdir()
    (tmp_path / "companies.json").write_text(
        json.dumps({"companies": [{"name": "Group Eleven", "file": LONG_11}]}),
        encoding="utf-8",
    )
    # its file stem is the first 60 characters of LONG_11's: the part LONG_11's
    # backups were named with before
    result = company_service.manifest_create_company(
        "Northern Illinois Commercial Construction Remodeling Group 1"
    )
    assert result["success"] is False
    assert LONG_11 in result["error"] and "Nothing was created." in result["error"]
    assert not (
        tmp_path
        / "companies"
        / "northern-illinois-commercial-construction-remodeling-group-1.db"
    ).exists()


def test_a_one_company_server_restores_an_old_backup_once_confirmed(
    books, client, monkeypatch  # noqa: F811
):
    """With one company on a server its backups are not shared with
    another's: a backup of its own from before a rename is restored once the
    second confirmation is given, as on the desktop."""
    monkeypatch.setenv("SLOWBOOKS_SERVER_MODE", "1")
    old = books.backups / "slowbooks_20250101_120000.db"
    _make_books(old, "Harbor Light Old Name", "legacy")
    r = client.post("/api/backups/restore", json={"filename": old.name})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "other_company"
    r = client.post(
        "/api/backups/restore",
        json={"filename": old.name, "allow_other_company": True},
    )
    assert r.status_code == 200, r.text


# -- setting a company up --------------------------------------------------------------


def _blank(path, name):
    from app.services.settings_service import set_setting

    engine = db_module.create_engine("sqlite:///" + path.as_posix())
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as db:
        set_setting(db, "company_name", name)
        db.commit()
    engine.dispose()


def test_a_company_with_no_password_is_not_set_up_by_its_header(three):
    _blank(three.folder / "fresh.db", "Fresh Co")
    _list_companies(three, [("fresh.db", "Fresh Co")])
    pw = secrets.token_urlsafe(16)
    anon = TestClient(app)
    r = anon.post(
        "/api/auth/setup",
        json={"password": pw, "company_name": "Fresh Co"},
        headers={"X-SlowBooks-Company": "fresh.db"},
    )
    assert r.status_code == 403, r.text
    assert anon.get("/api/auth/status").json()["authenticated"] is False
    from app.services.auth import password_is_set

    with link._in_company("fresh.db") as db:
        assert password_is_set(db) is False


# -- the sign-in status a program reads ----------------------------------------------


def test_a_token_is_told_only_of_the_company_it_opens(three):
    _id, secret = _hand_token("harbor.db", "office script")
    r = _bot(secret, "lake.db").get("/api/auth/status")
    assert r.status_code == 401, r.text
    assert "Lake" not in r.text and "cli" not in r.text
    r = _bot(secret, "harbor.db").get("/api/auth/status")
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == "Harbor Books"
    assert "usernames" not in r.json()


# -- an AI assistant's token whose maker is no longer an administrator --------------


def test_a_token_whose_maker_was_demoted_says_so_and_the_list_shows_it(three):
    jdoe = _signed_in(three, "jdoe")
    r = jdoe.post(
        "/api/tokens",
        json={"label": "AI assistant (Switchboard)", "role": "bookkeeper"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["maker_gone"] is False
    secret = r.json()["token"]
    _set_role(three, "harbor.db", "jdoe", "bookkeeper")
    _set_role(three, "harbor.db", "bkpr", "admin")
    r = _bot(secret).get("/api/customers")
    assert r.status_code == 401
    assert r.headers.get(REFUSED_HEADER) == "1"
    assert r.json()["detail"] == (
        'The API token "AI assistant (Switchboard)" stopped working because the '
        "administrator who made it is no longer an administrator of this company. "
        "Nothing was done. An administrator can make a new one under Settings, "
        "API Tokens."
    )
    assert "revoked" not in r.text
    bkpr = _signed_in(three, "bkpr")
    listed = {t["label"]: t for t in bkpr.get("/api/tokens").json()}
    row = listed["AI assistant (Switchboard)"]
    assert row["is_active"] is True and row["maker_gone"] is True


def test_the_panel_offers_add_at_once_when_the_maker_was_demoted(three, relay):
    jdoe = _signed_in(three, "jdoe", company="lake.db")
    r = jdoe.post("/api/switchboard/connect")
    assert r.status_code == 200, r.text
    relay.next_line()
    first = _record()["companies"][0]
    _set_role(three, "lake.db", "jdoe", "bookkeeper")
    cli = _signed_in(three, "cli", company="lake.db")
    st = cli.get("/api/switchboard/status").json()
    assert st["can_add"] is True, st
    assert [c["state"] for c in st["companies"]] == ["refused"]
    r = cli.post("/api/switchboard/add")
    assert r.status_code == 200, r.text
    again = _record()["companies"]
    assert len(again) == 1 and again[0]["token_sha256"] != first["token_sha256"]
    rows = {t.hash: t for t in _rows(three, "lake.db")}
    assert rows[first["token_sha256"]].active is False
    assert rows[again[0]["token_sha256"]].active is True
    assert rows[again[0]["token_sha256"]].created_by == "cli"


# -- what a client company's administrator learns ------------------------------------


def test_a_client_administrator_learns_nothing_when_the_others_are_locked(
    three, relay, monkeypatch
):
    _connected_with(three, relay, "smith.db", "lake.db")
    cli = _signed_in(three, "cli", company="lake.db")
    monkeypatch.setattr(link, "_locked", lambda f: f in ("harbor.db", "smith.db"))
    r = cli.get("/api/switchboard/status")
    assert r.status_code == 200, r.text
    st = r.json()
    for name in ("Harbor", "Smith", "harbor", "smith"):
        assert name not in r.text
    assert st["can_disconnect"] is False and "companies" not in st
    assert "count_others" not in st and "code_needs" not in st
    r = cli.post("/api/switchboard/disconnect")
    assert r.status_code == 403, r.text
    assert len(_record()["companies"]) == 3


def test_the_person_who_added_a_locked_company_may_still_disconnect(
    three, relay, monkeypatch
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    monkeypatch.setattr(link, "_locked", lambda f: f == "lake.db")
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_disconnect"] is True
    assert [c["state"] for c in st["companies"]] == ["ready", "ready", "locked"]


# -- Take off and Add say what was done ----------------------------------------------


def test_take_off_that_cannot_be_written_changes_nothing(three, relay, monkeypatch):
    jdoe = _connected_with(three, relay, "smith.db")

    def cannot_write(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(link, "_save", cannot_write)
    r = jdoe.post("/api/switchboard/take-off", json={"file": "harbor.db"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == link.NOT_WRITTEN
    assert [t.active for t in _rows(three, "harbor.db")] == [True]
    assert [e["file"] for e in _record()["companies"]] == ["harbor.db", "smith.db"]


def test_a_full_connection_says_it_is_full(three, relay, monkeypatch):
    jdoe = _connected_with(three, relay)
    monkeypatch.setattr(link, "MAX_COMPANIES", 1)
    _switch(jdoe, "smith.db")
    full = (
        "The AI assistant connection already reaches 1 companies, the most it can. "
        "Nothing was done."
    )
    st = jdoe.get("/api/switchboard/status").json()
    assert st["can_add"] is False and st["sentence"] == full
    r = jdoe.post("/api/switchboard/add")
    assert r.status_code == 409 and r.json()["detail"] == full
    assert "data folder" not in r.text


@pytest.mark.parametrize(
    "sentence",
    [
        link.ALREADY,
        link.ALREADY_ON,
        link.NOT_ON,
        link.NOT_ADMIN_THERE,
        link.NOT_A_MANAGER,
        link.EDIT_BUSY,
        link.DAMAGED_ELSEWHERE,
    ],
)
def test_a_refusal_says_nothing_was_done(sentence):
    assert "Nothing was done." in sentence or "nothing was done" in sentence


def test_refusals_shared_with_the_panel_say_nothing_was_done_when_raised(three, relay):
    _connected_with(three, relay, "smith.db")
    cli = _signed_in(three, "cli", company="lake.db")
    r = cli.post("/api/switchboard/add")
    assert r.status_code == 403
    assert r.json()["detail"].endswith(" Nothing was done.")


def test_renaming_refusals_say_nothing_was_changed():
    from app.main import LINK_TOKEN_ADDRESS_REFUSAL
    from app.routes import api_tokens

    for sentence in (
        api_tokens.LINK_LABEL_LOCKED,
        api_tokens.LINK_LABEL_TAKEN,
        api_tokens.AI_LABEL_KEPT,
    ):
        assert sentence.endswith(" Nothing was changed.")
    assert LINK_TOKEN_ADDRESS_REFUSAL.endswith(" Nothing was done.")

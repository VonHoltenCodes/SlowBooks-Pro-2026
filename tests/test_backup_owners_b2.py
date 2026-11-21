"""2.32.0-beta.2 review: whose is a backup two companies' names could fit?

Before 2.32.0-beta.2 two company files could share the part their backups
were named with ("north_co" and "acme--co" were "north-co" and "acme-co",
like the plain "north-co" and "acme-co" still are). A restore replaces a
company's backups table with the backup's, so the safety copy taken before
it, and every backup made after the one restored, are in no table.

- Such a company still lists its own: the install settles whose each one is
  from what the company files hold (a table that lists it, or the books the
  file is a copy of), and on the desktop from the name the file carries.
- On a server serving several companies, the company whose part is the
  plain name no longer lists, downloads or restores the old backups of the
  other company that shared it.
- Every backup made from now on is recorded as its company's as it is made.
"""

import json
import secrets
import sqlite3
from contextlib import closing

import pytest

import app.database as db_module
from app.services import backup_service, served_companies
from tests.test_backups_per_company import _KeepTheTestConnection, _make_books
from tests.test_switchboard_multicompany_b2 import relay, three  # noqa: F401
from tests.test_switchboard_multicompany_review_b2 import _list_companies, _person
from tests.test_served_companies import _make_company


def _rows(path, rows):
    """Give a company file (or a backup) a backups table holding ``rows``:
    [(filename, created_at)]."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS backups (id INTEGER PRIMARY KEY, "
            "filename VARCHAR(255) NOT NULL, file_size INTEGER, "
            "backup_type VARCHAR(20), notes TEXT, created_at DATETIME)"
        )
        conn.execute("DELETE FROM backups")
        for filename, created in rows:
            conn.execute(
                "INSERT INTO backups (filename, file_size, backup_type, created_at)"
                " VALUES (?, 1, 'manual', ?)",
                (filename, created),
            )
        conn.commit()


def _backup_file(folder, filename, company, held=()):
    """A SQLite backup of ``company``'s books whose own backups table holds
    ``held`` (the backups that company had made before this one)."""
    _make_books(folder / filename, company, filename)
    _rows(folder / filename, held)
    return filename


def _listed(person):
    r = person.get("/api/backups")
    assert r.status_code == 200, r.text
    return {b["filename"] for b in r.json()}


# -- a server serving several companies -----------------------------------------------


@pytest.fixture
def north(three):  # noqa: F811
    """north-co.db and north_co.db on one server: before the update both
    named their backups "north-co_...". north_co then restored its second
    backup, so its table lost that one's row and the safety copy has none."""
    pw = {"one": secrets.token_urlsafe(16), "two": secrets.token_urlsafe(16)}
    _make_company(
        three.folder / "north-co.db", "North Co", [("one", pw["one"], "admin", True)]
    )
    _make_company(
        three.folder / "north_co.db",
        "North Company",
        [("two", pw["two"], "admin", True)],
    )
    _list_companies(
        three, [("north-co.db", "North Co"), ("north_co.db", "North Company")]
    )
    folder = backup_service.BACKUP_DIR
    first = ("north-co_20261101_100000.db", "2026-11-01 16:00:00")
    second = ("north-co_20261102_100000.db", "2026-11-02 16:00:00")
    plain = ("north-co_20261101_090000.db", "2026-11-01 15:00:00")
    _backup_file(folder, first[0], "North Company")
    _backup_file(folder, second[0], "North Company", [first])
    safety = _backup_file(
        folder,
        "north-co_20261103_100000-before-restore.db",
        "North Company",
        [first, second],
    )
    _rows(three.folder / "north_co.db", [first])  # as the restored backup's
    _backup_file(folder, plain[0], "North Co")
    _rows(three.folder / "north-co.db", [plain])
    three.north = {
        "pw": pw,
        "company": {first[0], second[0], safety},
        "plain": {plain[0]},
    }
    return three


def test_a_company_whose_old_backup_name_was_shared_lists_all_its_own(north):
    two = _person("north_co.db", "two", north.north["pw"]["two"])
    assert _listed(two) == north.north["company"]
    safety = next(f for f in north.north["company"] if "before-restore" in f)
    assert two.get(f"/api/backups/download/{safety}").status_code == 200


def test_the_plain_named_company_no_longer_reaches_the_others_old_backups(north):
    one = _person("north-co.db", "one", north.north["pw"]["one"])
    assert _listed(one) == north.north["plain"]
    for theirs in sorted(north.north["company"]):
        assert one.get(f"/api/backups/download/{theirs}").status_code == 404
        for confirmed in (False, True):
            r = one.post(
                "/api/backups/restore",
                json={"filename": theirs, "allow_other_company": confirmed},
            )
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "other_company_server"


def test_a_backup_made_now_is_recorded_as_its_companys(north):
    one = _person("north-co.db", "one", north.north["pw"]["one"])
    r = one.post("/api/backups", json={})
    assert r.status_code == 200, r.text
    made = r.json()["filename"]
    assert made.startswith("north-co_"), made  # the name two companies could fit
    assert made in _listed(one)
    record = json.loads((backup_service.BACKUP_DIR / ".owners.json").read_text())
    assert record["owners"][made] == "north-co"
    two = _person("north_co.db", "two", north.north["pw"]["two"])
    assert made not in _listed(two)
    assert two.get(f"/api/backups/download/{made}").status_code == 404


def test_a_forged_company_name_does_not_claim_anothers_backup_on_a_server(north):
    """The name a backup carries is the company's own to change: on a server
    it never decides whose a backup is."""
    # a name the company list would refuse now, as a file may hold one
    with closing(sqlite3.connect(north.folder / "north-co.db")) as conn:
        conn.execute(
            "UPDATE settings SET value = 'North Company' WHERE key = 'company_name'"
        )
        conn.commit()
    one = _person("north-co.db", "one", north.north["pw"]["one"])
    assert _listed(one) == north.north["plain"]


# -- the desktop ----------------------------------------------------------------------


@pytest.fixture
def acme(tmp_path, monkeypatch, client, TestSession):
    """The desktop with acme_co.db open and acme--co.db listed beside it:
    both named their backups "acme-co_..." before the update. acme_co's
    safety copy from a restore is in no table and holds no backups table of
    its own to tell by (a file from before backups were recorded)."""
    from app.models.backups import Backup
    from app.services.settings_service import set_setting

    data = tmp_path / "data"
    (data / "companies").mkdir(parents=True)
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(data))
    served_companies._manifest_cache.update(stamp=None, rows=[])
    live = data / "companies" / "acme_co.db"
    _make_books(live, "Acme Co", "original")
    _make_books(data / "companies" / "acme--co.db", "Acme Two", "two")
    (data / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Acme Co", "file": "acme_co.db"},
                    {"name": "Acme Two", "file": "acme--co.db"},
                ]
            }
        ),
        encoding="utf-8",
    )
    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(backup_service, "DATABASE_URL", "sqlite:///" + live.as_posix())
    monkeypatch.setattr(backup_service, "BACKUP_DIR", backups)
    monkeypatch.setattr(db_module, "engine", _KeepTheTestConnection())
    mine = ["acme-co_20261101_100000.db", "acme-co_20261102_100000.db"]
    for name in mine:
        _make_books(backups / name, "Acme Co", name)
    with TestSession() as db:
        # set in the books, as the company list's name check reads this
        # suite's books as another company's
        set_setting(db, "company_name", "Acme Co")
        db.add_all(
            [Backup(filename=n, file_size=1, backup_type="manual") for n in mine]
        )
        db.commit()
    safety = "acme-co_20261103_100000-before-restore.db"
    _make_books(backups / safety, "Acme Co", "safety")
    renamed = "acme-co_20261001_100000-before-restore.db"
    _make_books(backups / renamed, "Acme Company Old Name", "renamed")
    theirs = "acme-co_20261104_100000-before-restore.db"
    _make_books(backups / theirs, "Acme Two", "theirs")
    yield {"mine": set(mine), "safety": safety, "renamed": renamed, "theirs": theirs}
    served_companies._manifest_cache.update(stamp=None, rows=[])


def test_the_desktop_lists_a_safety_copy_no_table_holds_by_its_name(acme, client):
    listed = {b["filename"] for b in client.get("/api/backups").json()}
    assert listed == acme["mine"] | {acme["safety"], acme["renamed"]}
    assert client.get(f"/api/backups/download/{acme['safety']}").status_code == 200


def test_the_desktop_asks_again_before_restoring_the_other_companys(acme, client):
    r = client.post("/api/backups/restore", json={"filename": acme["theirs"]})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "other_company"

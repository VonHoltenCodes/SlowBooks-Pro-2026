"""The suite runs in a folder of its own, never on the machine's SlowBooks
files (2.18.0 gate, macbase1).

tests/conftest.py never set SLOWBOOKS_DATA_DIR, so company_service.data_dir()
fell back to the real per-user folder — on the Mac that ran the gate,
~/Library/Application Support/SlowBooksPro/data, where a real company named
"Harbor Light Bakery" made test_settings_validation's rename answer 409 — and
storage.files_root() fell back to app/static, so every attachment a test
uploaded (a "secret.pdf" among them) was written into the source tree. The
settings key file (.slowbooks-master.key) landed next to the code as well.
"""

import io
import os
import tempfile
from pathlib import Path

from app.services import company_service, storage

ROOT = Path(__file__).resolve().parents[1]


def _inside(path, folder) -> bool:
    return Path(path).resolve().is_relative_to(Path(folder).resolve())


def test_the_data_dir_is_a_temporary_folder_not_the_machines_own(monkeypatch):
    here = company_service.data_dir()
    assert _inside(here, tempfile.gettempdir()), here
    assert storage.files_root() == here and storage.backups_root() == here / "backups"
    # ...and not the per-user folder the desktop app keeps real books in
    monkeypatch.delenv("SLOWBOOKS_DATA_DIR")
    real = company_service.data_dir()
    assert not _inside(here, real) and not _inside(real, here)


def test_the_run_has_one_folder_and_every_file_root_is_in_it(suite_data_dir):
    from app.services import backup_service

    assert company_service.data_dir() == suite_data_dir
    # Uploads are kept in each company's database since 2.18.0; the old
    # shared uploads folder is only read, by the upgrade.
    for root in (storage.uploads_root(), backup_service.BACKUP_DIR):
        assert _inside(root, suite_data_dir), root


def test_a_company_on_the_machine_cannot_collide_with_a_tests_company(
    client, seed_accounts, tmp_path, monkeypatch
):
    """The duplicate-name guard reads the company list (companies.json) in
    data_dir(). skytech's box has a real "Explore Signs & Co", the name
    test_1099_forms and test_pay_stub_access give their company, and got
    409s (2.18.0 gate, N6); macbase1 got one from "Harbor Light Bakery"."""
    import json

    suite = os.environ.get("SLOWBOOKS_DATA_DIR")
    # The machine's own data folder, under a stand-in home directory.
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.delenv("SLOWBOOKS_DATA_DIR", raising=False)
    machine = company_service.data_dir()
    machine.mkdir(parents=True)
    (machine / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"name": "Explore Signs & Co", "file": "explore-signs-co.db"}
                ],
                "last_opened": "explore-signs-co.db",
            }
        ),
        encoding="utf-8",
    )
    if suite:
        monkeypatch.setenv("SLOWBOOKS_DATA_DIR", suite)

    r = client.put("/api/settings", json={"company_name": "Explore Signs & Co"})
    assert r.status_code == 200, r.text


def test_an_uploaded_attachment_lands_in_the_database_not_on_disk(
    client, seed_accounts, seed_customer
):
    r = client.post(
        f"/api/attachments/customer/{seed_customer.id}",
        files={
            "file": (
                "isolation-probe.pdf",
                io.BytesIO(b"%PDF-1.4\n"),
                "application/pdf",
            )
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["file_path"].startswith("stored_files/")
    for root in (storage.files_root(), ROOT / "app" / "static"):
        assert not list(root.rglob("isolation-probe.pdf")), root


def test_settings_encryption_writes_no_key_file(tmp_path, monkeypatch):
    from app.services import crypto

    key_file = tmp_path / ".slowbooks-master.key"
    monkeypatch.setattr(crypto, "_KEY_FILE", key_file)
    crypto.reset_cache_for_tests()
    try:
        assert crypto.decrypt_value(crypto.encrypt_value("hello")) == "hello"
    finally:
        crypto.reset_cache_for_tests()
    assert not key_file.exists()


def test_the_checkouts_own_env_file_is_not_read():
    # app/config.py loads SLOWBOOKS_ENV_FILE, else the checkout's .env — a
    # developer's DATABASE_URL, company name and keys.
    env_file = os.environ.get("SLOWBOOKS_ENV_FILE")
    assert env_file and _inside(env_file, company_service.data_dir())
    assert Path(env_file).resolve() != (ROOT / ".env").resolve()

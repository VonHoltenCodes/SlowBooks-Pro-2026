"""LICENSE section 13: the desktop app and the Windows installer display the
short terms before first use and record that they were shown. A file on
GitHub is not a contract with someone who only ever double-clicked the
installer; these pin the two places the terms are actually put in front of
a person, and that they say the same version the license does.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE = (ROOT / "LICENSE").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
BOOT = (ROOT / "app/static/js/bootstrap.js").read_text(encoding="utf-8")
ISS = (ROOT / "packaging/windows/SlowBooksPro.iss").read_text(encoding="utf-8")


def _license_version():
    m = re.search(r"Source Available License, version (\d+\.\d+)", LICENSE)
    assert m, "LICENSE has no version line"
    return m.group(1)


def test_the_splash_shows_the_four_points_the_license_names():
    block = INDEX[
        INDEX.index('id="splash-terms"') : INDEX.index('class="splash-legal"')
    ]
    for phrase in (
        "Free, forever, for what has shipped",
        "Do not sell it or offer it as a paid service",
        "aids, not advice",
        "not a substitute for a licensed professional",
        "No warranty, no liability, no obligation to maintain",
        "hold the author harmless",
        "Will County, Illinois",
        "blob/main/LICENSE",
    ):
        assert phrase in block, phrase


def test_the_splash_records_the_same_version_the_license_carries():
    m = re.search(r"const LICENSE_VERSION = '(\d+\.\d+)'", BOOT)
    assert m, "bootstrap.js has no LICENSE_VERSION"
    assert m.group(1) == _license_version()
    assert "localStorage.getItem(ACK_KEY) === LICENSE_VERSION" in BOOT
    assert "localStorage.setItem(ACK_KEY, LICENSE_VERSION)" in BOOT
    assert "dismiss.textContent = 'I understand'" in BOOT


def test_the_installer_shows_the_license_and_ships_it():
    m = re.search(r"^LicenseFile=(.+)$", ISS, re.M)
    assert m, "no LicenseFile in [Setup]"
    rel = m.group(1).strip().replace("\\", "/")
    assert (ROOT / "packaging" / "windows" / rel).resolve() == (
        ROOT / "LICENSE"
    ).resolve()
    assert 'DestName: "LICENSE.txt"' in ISS, "LICENSE not shipped beside the program"


# ── #220: the release notes, once per version, like the terms ─────────────


def test_the_release_notes_are_shown_once_per_version():
    # The splash opened on every load: the terms recorded their
    # acknowledgement, the what's-new panel never did (#220, reported by
    # stevenmirabito). Now the notes record the version they showed, and the
    # splash opens at start only while the terms or the notes are new.
    assert "const NOTES_KEY = 'slowbooks.whatsnew_ack'" in BOOT
    assert "localStorage.getItem(NOTES_KEY) === key" in BOOT
    assert "localStorage.setItem(NOTES_KEY, notesVersion)" in BOOT
    # with the terms acknowledged the splash is hidden before first paint
    assert "if (splash && acknowledged) {" in BOOT
    # the licence's own acknowledgement is untouched
    assert BOOT.count("localStorage.setItem(ACK_KEY, LICENSE_VERSION)") == 1


def test_about_says_only_who_it_is_not_affiliated_with():
    """The owner's wording for About: no origin story and no account of how
    it was built, only the line saying whom the product is not tied to."""
    legal = INDEX[INDEX.index('class="splash-legal"') :][:400]
    assert "Not affiliated with, endorsed by or sponsored by Intuit Inc.," in legal
    assert "Mercury Technologies, Inc., SimpleFIN, or any AI provider." in legal
    for gone in (
        "Born as",
        "activation servers",
        "reimplementation",
        "QBFC",
        "paying customer",
    ):
        assert gone not in INDEX, gone

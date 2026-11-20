"""The About dialog credits the artists (#635).

The community art repo promises every contributing artist a credit in the
app. Under the credits (or alone while there are none) the box asks for a
donation, linking the project's Ko-fi page (NEW-268: it linked the art
repository by its lower-case-b name). The credits are data,
/static/artists.json, so a new artist is a line in that file and not a code
change. These pin the file's shape, the wiring that reads and renders it
(the list, and the note with the repo link while the list is empty), and
that the file is served without a sign-in, like the release notes beside it.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTISTS = ROOT / "app" / "static" / "artists.json"
# the donation page the README and slowbookspro.com's Donate link use
DONATE = "https://ko-fi.com/Y7G628VSJ3"


def _credits():
    return json.loads(ARTISTS.read_text(encoding="utf-8"))


def test_the_credits_file_has_the_shape_the_dialog_reads():
    data = _credits()
    assert isinstance(data["artists"], list)
    # the request to donate, and the project's donation page
    assert isinstance(data["note"], str) and "donation" in data["note"]
    assert data["donate"] == DONATE
    assert "repo" not in data
    for artist in data["artists"]:
        assert isinstance(artist["name"], str) and artist["name"].strip()
        assert artist["pieces"], artist["name"]
        for piece in artist["pieces"]:
            assert isinstance(piece["what"], str) and piece["what"].strip()
            assert isinstance(piece["where"], str) and piece["where"].strip()
        link = artist.get("link")
        assert link is None or link.startswith("https://"), artist["name"]


def test_the_about_dialog_reads_and_renders_the_credits():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    splash = html[html.index('id="splash"') : html.index('id="app"')]
    for anchor in (
        'id="splash-artists"',
        'class="splash-artists-title">Artists<',
        'id="splash-artists-list"',
        'id="splash-artists-note"',
    ):
        assert anchor in splash, anchor

    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "fetch('/static/artists.json'" in js
    # About asks for them, so the dialog is never without
    about = js[js.index("showAbout() {") :]
    about = about[: about.index("},")]
    assert "App.loadArtists()" in about
    # and so does start-up: the splash opens by itself on a first run
    init = js[js.index("    init() {") :]
    init = init[: init.index("async initSystemInfo() {")]
    assert "App.loadArtists()" in init

    render = js[js.index("renderArtists(data) {") :]
    render = render[: render.index("box.hidden = !artists.length")]
    # each artist: the credit line, the pieces and where they are used, the
    # link when given, every one of them escaped
    for needle in ("a.name", "p.what", "p.where", "a.link", "escapeHtml("):
        assert needle in render, needle
    assert "innerHTML" in render and "textContent" not in render
    for raw in ("${a.name}", "${a.link}", "${p.what}", "${p.where}", "${data.note}"):
        assert raw not in render, f"{raw} reaches the page unescaped"
    # the note and the donation link, under the list or alone
    assert "data.note" in render and "data.donate" in render
    assert "data.repo" not in render
    assert "list.hidden = !artists.length" in render
    assert "title.hidden = !artists.length" in render


def test_the_credits_are_served_without_a_sign_in(unauthed_client):
    r = unauthed_client.get("/static/artists.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["donate"] == DONATE
    # as every response: revalidated, never served stale after an update
    assert r.headers.get("cache-control") == "no-cache"


def test_the_docs_and_the_changelog_say_so():
    features = (ROOT / "docs" / "features.md").read_text(encoding="utf-8")
    assert "#635" in features and "artists.json" in features
    # anywhere in the changelog: the paragraph starts under "Unreleased" and
    # keeps its place under the release's own heading once that is cut
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#635" in changelog and "Slowbooks-Pro-Art" in changelog

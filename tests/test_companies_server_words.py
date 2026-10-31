"""A server install's Companies page says what a new company is and how it
is opened (discussion #351: on Docker, a second company was created as its
own PostgreSQL database and the page said only that "the served company is
chosen on the host PC", a Windows Server Edition sentence that meant nothing
on Docker, so the person looked for it on the sign-in page). The docs carry
the recipe: a second server pointed at the database."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_companies_page_tells_a_server_install_how_to_open_a_second_company():
    js = (ROOT / "app/static/js/companies.js").read_text(encoding="utf-8")
    assert "chosen on the host PC" not in js
    assert "A server serves one database, the one its DATABASE_URL names." in js
    assert "run a second server pointed at it" in js
    assert (
        "The sign-in page names the company a server serves; it never lists them." in js
    )
    assert "Several companies on a server" in js
    # the create toast names the database it made
    assert "Company created as its own database" in js and "data.database_name" in js


def test_the_docs_carry_the_second_server_recipe():
    ops = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    assert "## Several companies on a server" in ops
    assert "slowbooks-second:" in ops and "YOUR_DATABASE_NAME" in ops
    assert '3002:3001"' in ops and "slowbooks_uploads_second" in ops
    features = (ROOT / "docs/features.md").read_text(encoding="utf-8")
    assert "switchable from UI" not in features
    assert "a second company is a second server pointed at its database" in features

"""The sidebar's bottom link asks for support on Ko-fi, not for artists.

The link under the left menu used to open the artists' call in GitHub
Discussions (#636). The art is in hand; the one outside link on the sidebar
now opens the Ko-fi page the README already carries. It is an external URL,
so it keeps target=_blank and rel=noopener: the desktop shim only
intercepts same-origin _blank links, and this one opens in the system
browser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOFI = "https://ko-fi.com/Y7G628VSJ3"


def _footer_link() -> str:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    footer = html[html.index('id="sidebar-footer"') :]
    footer = footer[: footer.index("</div>")]
    links = re.findall(r"<a\b[^>]*>.*?</a>", footer, flags=re.S)
    assert len(links) == 1, links
    return links[0]


def test_the_sidebar_footer_link_opens_the_kofi_page_in_the_system_browser():
    link = _footer_link()
    assert f'href="{KOFI}"' in link
    assert 'target="_blank"' in link and 'rel="noopener"' in link
    assert "Support SlowBooks Pro" in link
    assert "discussions" not in link and "turtle" not in link


def test_the_readme_and_the_sidebar_name_the_same_kofi_page():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert KOFI in readme

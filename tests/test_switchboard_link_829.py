"""SlowBooks Switchboard's link and words in the product (#829).

Switchboard (`sbplive`) is its own public repository; the product carries
only words and links to it:

- Settings → Connections has a "Claude and agents" block with the
  download (the releases page), the guide and the self-hosted door, saying
  what it does and that nothing is reachable from outside unless the user
  sets the door up; the block is words and links, no control and no setting;
- the API Tokens paragraph says a bookkeeper token is what `sbplive setup`
  asks for;
- docs/features.md and the agent docs name it, with the links.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")

REPO = "https://github.com/VonHoltenCodes/SlowBooks-Switchboard"
RELEASES = REPO + "/releases"
GUIDE = REPO + "/blob/main/docs/GUIDE.md"
REMOTE = REPO + "/blob/main/docs/REMOTE.md"


def _flat(text):
    return re.sub(r"\s+", " ", text)


def _block():
    start = JS.index('id="settings-switchboard"')
    tag = JS.rindex("<div", 0, start)
    end = JS.index('<div class="settings-section"', start)
    return JS[tag:end]


def test_the_connections_block_is_in_the_connections_group_with_its_heading():
    block = _block()
    assert 'data-group="connections"' in block[: block.index(">")]
    assert 'aria-labelledby="settings-h-claude-agents"' in block
    assert (
        '<h3 id="settings-h-claude-agents">'
        "Claude and agents: SlowBooks Switchboard (sbplive)</h3>"
    ) in block
    # between AI Insights and Receipt Scanning
    assert (
        JS.index('id="settings-ai"')
        < JS.index('id="settings-switchboard"')
        < JS.index('id="settings-ocr"')
    )


def test_the_block_says_what_it_does_and_what_stays_on_this_computer():
    text = _flat(_block())
    for words in (
        "lets Claude Desktop, Claude Code and other agents",
        "work this company's books",
        "<code>sbplive</code>, that runs on this computer",
        "<strong>bookkeeper API token</strong>",
        "only what that token's role allows",
        "<code>token:&lt;label&gt;</code>",
        "Run <code>sbplive setup</code>",
        "The token stays on this computer",
        "sends nothing anywhere but this SlowBooks Pro",
        "<code>sbplive serve-http</code>",
        "behind your own Cloudflare Tunnel",
        "unless you set that door up",
        "What Claude reads from the books goes to Claude",
    ):
        assert words in text, words


def test_the_block_links_the_download_the_guide_and_the_door_outside_the_app():
    block = _block()
    links = re.findall(r'<a href="([^"]+)"([^>]*)>([^<]+)</a>', block)
    assert [(h, t) for h, _, t in links] == [
        (RELEASES, "Download SlowBooks Switchboard"),
        (GUIDE, "The guide"),
        (REMOTE, "Claude on the web: the self-hosted door"),
    ]
    # external: opened in the system browser, like the sidebar's links
    assert all('target="_blank" rel="noopener"' in attrs for _, attrs, _ in links)


def test_the_block_is_words_and_links_only():
    block = _block()
    for control in ("<input", "<select", "<button", "<textarea", "onclick", "name="):
        assert control not in block, control


def test_the_api_tokens_paragraph_says_a_bookkeeper_token_is_what_setup_asks_for():
    start = JS.index('id="settings-api-tokens"')
    para = _flat(JS[start : JS.index("</p>", start)])
    assert (
        "A bookkeeper token is what <code>sbplive setup</code> asks for, "
        "to let Claude work the books through SlowBooks Switchboard"
    ) in para
    assert "make a read-only one for an agent that only reports" in para


def test_features_md_names_switchboard_as_the_way_an_agent_works_the_books():
    doc = _flat((ROOT / "docs/features.md").read_text(encoding="utf-8"))
    assert "### Claude and agents: SlowBooks Switchboard (sbplive)" in doc
    assert "is the way Claude Desktop, Claude Code and any other agent" in doc
    assert "a **bookkeeper token** is what `sbplive setup` asks for" in doc
    assert "An AI agent works the books through **SlowBooks Switchboard**" in doc
    for link in (RELEASES, GUIDE, REMOTE):
        assert f"]({link})" in doc, link


def test_the_agent_docs_name_switchboard_with_its_links():
    server = _flat((ROOT / "docs/server-edition.md").read_text(encoding="utf-8"))
    tokens = server[
        server.index("## API tokens") : server.index("What a token can do:")
    ]
    assert "works the books through **SlowBooks Switchboard**" in tokens
    assert "A **bookkeeper token** is what `sbplive setup` asks for" in tokens
    for link in (RELEASES, GUIDE, REMOTE):
        assert f"({link})" in tokens, link
    for name in ("working-with-your-accountant.md", "nonprofit-module.md"):
        doc = _flat((ROOT / "docs" / name).read_text(encoding="utf-8"))
        assert f"[SlowBooks Switchboard]({GUIDE})" in doc, name
        assert f"[download]({RELEASES})" in doc, name

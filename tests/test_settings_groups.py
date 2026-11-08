"""Company Settings' groups (#720), from the source.

The list of groups on the Settings page is built from the sections
themselves: each .settings-section carries data-group="<key>", and
SettingsPage.GROUPS holds the keys, in the list's order, with their names.
So adding a setting stays as cheap as adding its section, plus one word.
A section with no group, or a group the list does not know, fails here
rather than disappearing from the page. The browser side (what each
sign-in and company type sees, the keyboard, Find a setting) is
tests/test_browser_settings_groups.py.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")

# The issue's table: each group's sections, by heading id (the legacy
# files' section has none: it is named by its own id).
EXPECTED = {
    "company": [
        "settings-h-company-information",
        "settings-h-company-logo",
        "settings-h-closing-date",
    ],
    "sales": [
        "settings-h-invoice-defaults",
        "settings-h-payment-terms",
        "settings-h-invoice-messages",
        "settings-h-late-fees",
        "settings-h-giving-statement",
    ],
    "email": [
        "settings-h-email-smtp",
        "settings-h-email-templates",
        "settings-h-email-recent-sends",
    ],
    "payroll": [
        "settings-h-direct-deposit",
        "settings-h-payroll-accounts",
        "settings-h-check-printing",
    ],
    "lists": [
        "settings-h-classes",
        "settings-h-cost-types",
        "settings-h-cost-codes",
        "settings-h-trade-packages",
        "settings-h-equipment",
    ],
    "connections": [
        "settings-h-deposits",
        "settings-h-online-payments",
        "settings-h-ai-insights",
        "settings-h-receipt-scanning",
    ],
    "preferences": [
        "settings-h-my-preferences",
        "settings-h-menu",
        "settings-h-sounds",
        "settings-h-warnings",
    ],
    "security": [
        "settings-h-sign-in",
        "settings-h-users-server-edition",
        "settings-h-api-tokens-agents-integrations",
        "settings-h-company-file",
    ],
    "backups": ["settings-h-backup-restore", "settings-legacy-files"],
}


def _groups():
    block = JS[JS.index("SettingsPage.GROUPS = [") :]
    block = block[: block.index("];")]
    return re.findall(r"\{ key: '([a-z]+)', label: '([^']+)' \}", block)


def _section_tags():
    # every opening tag of a section, in the markup the page builds
    # (template literals and the string-built My Preferences alike)
    return re.findall(r"<div class=\"settings-section\"[^>]*>", JS)


def test_the_groups_are_the_nine_in_order_with_their_names():
    assert _groups() == [
        ("company", "Company"),
        ("sales", "Sales documents"),
        ("email", "Email"),
        ("payroll", "Payroll and checks"),
        ("lists", "Lists"),
        ("connections", "Connections"),
        ("preferences", "Preferences"),
        ("security", "Users and security"),
        ("backups", "Backups"),
    ]


def test_every_section_carries_a_known_group():
    known = {key for key, _ in _groups()}
    tags = _section_tags()
    assert len(tags) == 33  # every section of the page, the conditional ones too
    missing = [t for t in tags if 'data-group="' not in t]
    assert missing == [], "a section with no data-group: " + "; ".join(missing)
    unknown = [
        t for t in tags if re.search(r'data-group="([^"]*)"', t).group(1) not in known
    ]
    assert unknown == [], unknown
    # one group per section, written once
    assert all(t.count("data-group=") == 1 for t in tags)


def test_each_section_is_in_the_group_the_issue_gives_it():
    got = {}
    for tag in _section_tags():
        group = re.search(r'data-group="([^"]*)"', tag).group(1)
        named = re.search(r'aria-labelledby="([^"]+)"', tag)
        key = named.group(1) if named else re.search(r'id="([^"]+)"', tag).group(1)
        got.setdefault(group, []).append(key)
    assert {g: sorted(v) for g, v in got.items()} == {
        g: sorted(v) for g, v in EXPECTED.items()
    }


def test_the_list_is_built_from_the_sections_not_registered_elsewhere():
    init = JS[
        JS.index("SettingsPage._initGroups = function") : JS.index(
            "SettingsPage._fitNav = function"
        )
    ]
    # the tabs come from the groups present among the sections on the page
    assert "const present = new Set(sections.map(SettingsPage._groupOf));" in init
    assert "SettingsPage.GROUPS.filter(g => present.has(g.key))" in init
    # no section id or heading is named anywhere in the groups' code
    code = JS[
        JS.index("SettingsPage.GROUPS = [") : JS.index(
            "// --- Class tracking management"
        )
    ]
    assert "settings-h-" not in code
    # a section without a group is shown in every group, never hidden
    refresh = JS[JS.index("SettingsPage._refreshGroups = function") :]
    assert "on = !g || g === shown;" in refresh


def test_every_section_stays_in_the_one_form_or_beside_it_in_the_panel():
    render = JS[JS.index("async render() {") : JS.index("// Unsaved changes.")]
    page = render[render.rindex("return `") :]
    # the panel holds My Preferences (its own saves, every role's) and the
    # whole form; the list is outside both
    assert page.index('id="settings-panel" role="tabpanel"') < page.index(
        "${SettingsPage._myPreferencesHtml()}"
    )
    assert page.index("${SettingsPage._myPreferencesHtml()}") < page.index("${form}")
    assert page.index("${SettingsPage._groupNavHtml()}") < page.index(
        'id="settings-panel"'
    )


def test_the_guide_names_the_group_where_it_names_a_setting():
    # "Settings → Closing Date" was a heading to scroll to; now the group
    # comes first: "Settings → Company → Closing Date"
    names = {
        "Company Information",
        "Company Logo",
        "Closing Date",
        "Payment Terms",
        "Late Fees",
        "Giving Statement",
        "Email Templates",
        "Recent sends",
        "Direct Deposit (ACH)",
        "Payroll Accounts",
        "Check Printing",
        "Trade Packages",
        "Online Payments",
        "AI Insights",
        "Sounds",
        "My",
        "Users",
        "API Tokens",
        "Company File Encryption",
        "Backup / Restore",
        "Funds",
        "Company Type",
    }
    groups = {label for _, label in _groups()}
    stale = []
    for doc in sorted((ROOT / "docs").glob("*.md")):
        if doc.name == "features.md":  # the release's feature list
            continue
        text = re.sub(r"\s+", " ", doc.read_text(encoding="utf-8"))
        for m in re.finditer(
            r"Settings (?:→|>) ([A-Z][\w/() ]+?)(?= (?:→|>)|[.,;)*]|$)", text
        ):
            first = m.group(1).strip()
            if first in groups:
                continue
            if any(first == n or first.startswith(n + " ") for n in names):
                stale.append(f"{doc.name}: Settings → {first}")
    assert stale == []


def test_one_path_shows_and_hides_the_sections():
    """#725: a loader shows or hides its section through
    SettingsPage._showSection, the same path as the group switch, so a
    change of the sections showing always reaches the panel's rebuild on the
    Mac. None sets its section's hidden or style.display by hand, and none
    is drawn with an inline display: none."""
    show = JS[JS.index("SettingsPage._showSection = function") :]
    show = show[: show.index("\n};")]
    assert "section.hidden = !on;" in show
    assert "SettingsPage._refreshGroups({ filled: on ? section : null });" in show
    rest = JS.replace(show, "")
    assert re.findall(r"\bsection\.(?:hidden|style\.display)\s*=(?!=)", rest) == []
    for tag in _section_tags():
        assert "display:none" not in tag.replace(" ", ""), tag
    for loader in (
        "loadEmailLog",
        "loadCompanyFile",
        "loadUsers",
        "loadSignInPref",
        "loadApiTokens",
        "loadBackups",
        "loadLegacyFiles",
    ):
        start = JS.index(f"async {loader}(")
        body = JS[start : JS.index("\n    },\n", start)]
        assert "SettingsPage._showSection(" in body, loader
    # the rebuild runs when the set showing changes, from the refresh
    refresh = JS[JS.index("SettingsPage._refreshGroups = function") :]
    refresh = refresh[: refresh.index("\n};")]
    assert "SettingsPage._rebuildPanelTree();" in refresh

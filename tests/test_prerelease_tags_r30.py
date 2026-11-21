"""A pre-release tag (``v<version>-beta.<n>``) builds the version it is a
pre-release of, and its GitHub release is marked a pre-release.

Both build workflows used to refuse any tag that was not exactly
``v<version>``, so a beta could not be cut by its tag at all. The patterns
are read out of the workflow files and run here, so the files and this test
cannot drift apart.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = (ROOT / ".github" / "workflows" / "windows.yml").read_text(encoding="utf-8")
MACOS = (ROOT / ".github" / "workflows" / "macos.yml").read_text(encoding="utf-8")

VERSION = "2.32.0"
TAKEN = ["v2.32.0", "v2.32.0-beta.1", "v2.32.0-beta.12"]
REFUSED = [
    "v2.32.1",
    "v2.32.0-beta.",
    "v2.32.0-beta.1x",
    "v2.32.0-rc.1",
    "v2.32.0-beta.1-beta.2x",
    "v2x32.0",
    "v2.32.0.1",
]


def _windows_takes(tag: str) -> bool:
    # windows.yml: $tagver = "<tag>".TrimStart("v") -replace '<pattern>', ''
    #              if ($tagver -ne $ver) { ... exit 1 }
    found = re.search(r"\.TrimStart\(\"v\"\) -replace '([^']+)', ''", WINDOWS)
    assert found, "windows.yml no longer strips a pre-release suffix from the tag"
    return re.sub(found.group(1), "", tag.lstrip("v")) == VERSION


def _macos_takes(tag: str) -> bool:
    # macos.yml: [[ "${GITHUB_REF_NAME}" =~ ^v${APP_VERSION//./\\.}<rest>$ ]]
    found = re.search(
        r'\[\[ "\$\{GITHUB_REF_NAME\}" =~ \^v\$\{APP_VERSION//\./\\\\\.\}(\S+)\$ \]\]',
        MACOS,
    )
    assert found, "macos.yml no longer checks a tag against the app version"
    return re.fullmatch("v" + re.escape(VERSION) + found.group(1), tag) is not None


def test_a_release_tag_and_a_beta_tag_build_the_version():
    for tag in TAKEN:
        assert _windows_takes(tag), tag
        assert _macos_takes(tag), tag


def test_any_other_tag_is_still_refused():
    for tag in REFUSED:
        assert not _windows_takes(tag), tag
        assert not _macos_takes(tag), tag


def test_a_beta_tags_release_is_a_pre_release_on_both_platforms():
    line = "prerelease: ${{ contains(github.ref_name, '-beta.') }}"
    for name, text in (("windows.yml", WINDOWS), ("macos.yml", MACOS)):
        step = text[text.index("- name: Attach to release") :]
        step = step[: step.index("files:")]
        assert "softprops/action-gh-release@v2" in step, name
        assert line in step, name

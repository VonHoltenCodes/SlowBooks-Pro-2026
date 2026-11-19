"""Split the test files into CI shards (#1095).

CI runs the suite as several parallel jobs, each given whole test FILES:
pytest's ``--dist loadfile`` and the browser modules' module-scoped
fixtures (a Chromium, the seeded books) rely on one file staying in one
process, so a file is never split. The split is balanced on measured
per-file durations, kept in ``scripts/ci_test_durations.json``; a file with
no measurement yet (a new one) is dealt round-robin after the measured
ones are placed, so it always lands in exactly one shard.

Groups:

- ``browser``: every module that imports Playwright at module level
  (``pytest.importorskip("playwright...")``), found by reading the files,
  not by a list. Linux runs these in its browser job, with Chromium.
- ``core``: every other test file. Linux runs these in its pytest +
  coverage shards.
- ``all``: every test file. Windows runs these (its browser modules skip,
  and its skip budget counts them).

    python scripts/ci_shards.py list --group core --platform linux --shard 1 --of 4
    python scripts/ci_shards.py verify --group core shard-lists/*.txt
    python scripts/ci_shards.py skips --fail-on playwright junit.xml
    python scripts/ci_shards.py show --group core --platform linux --of 4
    python scripts/ci_shards.py refresh --platform linux junit-*.xml

``refresh`` reads the JUnit XML the CI shards upload (artifacts
``junit-linux-*``, ``junit-browser-*``, ``junit-windows-*``) and rewrites that
platform's durations; download a run's artifacts with ``gh run download
<run-id> -p 'junit-*'`` and run it once per platform (browser files are
measured under ``linux``).
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
DURATIONS = ROOT / "scripts" / "ci_test_durations.json"
PLATFORMS = ("linux", "windows")
GROUPS = ("core", "browser", "all")

# module level only: an import at column 0, as every browser module has it
_BROWSER = re.compile(
    r"""^(?:\w+\s*=\s*)?pytest\.importorskip\(\s*["']playwright\b""", re.M
)


def test_files():
    """Every file pytest collects (pytest.ini: testpaths = tests,
    python_files = test_*.py), as posix paths relative to the checkout."""
    return sorted(p.relative_to(ROOT).as_posix() for p in TESTS.rglob("test_*.py"))


def is_browser(path):
    return bool(_BROWSER.search((ROOT / path).read_text(encoding="utf-8")))


def group_files(group):
    files = test_files()
    if group == "all":
        return files
    browser = [f for f in files if is_browser(f)]
    if group == "browser":
        return browser
    taken = set(browser)
    return [f for f in files if f not in taken]


def load_durations(platform):
    try:
        data = json.loads(DURATIONS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data.get(platform, {})


def split(files, n, durations):
    """n lists of files: the measured ones longest first, each onto the
    lightest shard so far; then the unmeasured ones round-robin."""
    if n < 1:
        raise ValueError("at least one shard")
    shards = [[] for _ in range(n)]
    loads = [0.0] * n
    known = sorted(
        (f for f in files if f in durations), key=lambda f: (-durations[f], f)
    )
    for f in known:
        i = min(range(n), key=lambda k: (loads[k], k))
        shards[i].append(f)
        loads[i] += durations[f]
    unknown = sorted(f for f in files if f not in durations)
    for j, f in enumerate(unknown):
        shards[j % n].append(f)
    return [sorted(s) for s in shards], loads


def plan(group, platform, n):
    return split(group_files(group), n, load_durations(platform))


def _cmd_list(args):
    shards, _ = plan(args.group, args.platform, args.of)
    if not 1 <= args.shard <= args.of:
        sys.exit(f"--shard must be 1..{args.of}")
    files = shards[args.shard - 1]
    text = "\n".join(files) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(
        f"shard {args.shard}/{args.of} ({args.group}, {args.platform}): {len(files)} files",
        file=sys.stderr,
    )


def _cmd_show(args):
    shards, loads = plan(args.group, args.platform, args.of)
    durations = load_durations(args.platform)
    for i, files in enumerate(shards, 1):
        new = sum(1 for f in files if f not in durations)
        print(
            f"shard {i}/{args.of}: {len(files)} files, "
            f"{loads[i - 1]:.0f} s measured, {new} unmeasured"
        )


def _cmd_verify(args):
    """The lists the shards really ran, read back: together they are the
    group's files, each exactly once, so no test file goes unrun."""
    want = group_files(args.group)
    seen = {}
    lists = [Path(p) for p in args.lists]
    for p in lists:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                seen.setdefault(line, []).append(p.name)
    missing = [f for f in want if f not in seen]
    extra = [f for f in seen if f not in set(want)]
    twice = {f: where for f, where in seen.items() if len(where) > 1}
    for label, items in (("never run", missing), ("not in the group", extra)):
        for f in items:
            print(f"{label}: {f}")
    for f, where in twice.items():
        print(f"in more than one shard: {f} ({', '.join(where)})")
    if missing or extra or twice:
        sys.exit(1)
    print(f"{len(want)} {args.group} files, each in exactly one of {len(lists)} shards")


def _junit_durations(paths):
    """Seconds per test file from JUnit XML (setup, call and teardown, so a
    module fixture's cost lands on its file)."""
    known = set(test_files())
    out = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            f = case.get("file")
            if f:
                f = f.replace("\\", "/")
            else:  # xunit2: classname is the dotted module, then any class
                parts = (case.get("classname") or "").split(".")
                f = None
                for k in range(len(parts), 0, -1):
                    cand = "/".join(parts[:k]) + ".py"
                    if cand in known:
                        f = cand
                        break
            if f in known:
                out[f] = out.get(f, 0.0) + float(case.get("time") or 0)
    return out


def _cmd_refresh(args):
    measured = _junit_durations(args.junit)
    if not measured:
        sys.exit("no test cases read from the JUnit files")
    try:
        data = json.loads(DURATIONS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    current = data.get(args.platform, {})
    if not args.merge:
        current = {}
    current.update({f: round(s, 2) for f, s in measured.items()})
    live = set(test_files())
    data[args.platform] = {f: current[f] for f in sorted(current) if f in live}
    DURATIONS.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(
        f"{args.platform}: {len(measured)} files measured, "
        f"{len(data[args.platform])} kept, {sum(measured.values()):.0f} s total"
    )


def _cmd_skips(args):
    """The skips a run's JUnit XML records, with their reasons; with
    --fail-on, a nonzero exit if any reason names the word (the browser
    job: a module that skipped because Playwright or Chromium was missing
    ran nothing, and must not pass)."""
    hits = []
    total = 0
    for path in args.junit:
        for case in ET.parse(path).getroot().iter("testcase"):
            for skip in case.iter("skipped"):
                total += 1
                why = skip.get("message") or skip.text or ""
                where = case.get("file") or case.get("classname") or "?"
                print(f"skipped: {where}::{case.get('name')}: {why.strip()[:160]}")
                if args.fail_on and args.fail_on.lower() in why.lower():
                    hits.append(where)
    print(f"{total} skipped")
    if hits:
        sys.exit(f"{len(hits)} skipped for want of {args.fail_on}: {sorted(set(hits))}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "show", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--group", choices=GROUPS, required=True)
        if name == "verify":
            p.add_argument("lists", nargs="+")
            continue
        p.add_argument("--platform", choices=PLATFORMS, default="linux")
        p.add_argument("--of", type=int, required=True)
        if name == "list":
            p.add_argument("--shard", type=int, required=True)
            p.add_argument("--out")
    p = sub.add_parser("skips")
    p.add_argument(
        "--fail-on",
        metavar="WORD",
        help="fail if any skip's reason contains WORD (case-insensitive)",
    )
    p.add_argument("junit", nargs="+")
    p = sub.add_parser("refresh")
    p.add_argument("--platform", choices=PLATFORMS, required=True)
    p.add_argument(
        "--merge",
        action="store_true",
        help="keep files this run did not measure (default: replace)",
    )
    p.add_argument("junit", nargs="+")
    args = ap.parse_args(argv)
    {
        "list": _cmd_list,
        "show": _cmd_show,
        "verify": _cmd_verify,
        "refresh": _cmd_refresh,
        "skips": _cmd_skips,
    }[args.cmd](args)


if __name__ == "__main__":
    main()

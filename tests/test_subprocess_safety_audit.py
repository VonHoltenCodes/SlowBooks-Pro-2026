"""Static audit: no shell command injection surface in production code.

Walks every .py file under app/ and asserts:

  1. No `shell=True` is ever passed to subprocess.run / call / Popen /
     check_output / check_call.
  2. No `os.system(...)` / `os.popen(...)` / `commands.getoutput(...)`
     calls — these are shell-interpreted by definition.
  3. Every subprocess.* call uses a list-form first argument (sequence
     of args), not a single string. The list form bypasses the shell
     entirely; the string form runs through /bin/sh -c when shell=True
     and is a path-of-least-resistance pitfall.

These are static-source checks. They CANNOT catch every possible
exploitation path (e.g. a route that writes a user string to a config
file that another process later interprets), but they DO pin the
defensive posture of every direct subprocess use so a regression
("a quick `os.system(...)` for this one debug case") fails CI instead
of shipping.

The audit deliberately excludes:
  - tests/  — test fixtures may legitimately exec subprocesses for
              setup/teardown
  - app/static/  — vendored minified JS that grep would otherwise hit
                   on single-letter symbol names
"""

import ast
import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Filename patterns to skip when crawling the tree for Python sources.
# `app/static/` contains vendored Chart.js with letter-name symbols that
# trip naive regex sweeps.
_SKIP_PARTS = {"static", "__pycache__"}


def _python_files() -> list[Path]:
    out = []
    for p in APP_DIR.rglob("*.py"):
        if any(part in _SKIP_PARTS for part in p.parts):
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------
# AST-based audits — more precise than regex, ignore comments and strings
# --------------------------------------------------------------------------


class _SubprocessAuditor(ast.NodeVisitor):
    """Walk a module AST and collect every subprocess.* and os.system call."""

    def __init__(self, path: Path):
        self.path = path
        self.shell_true: list[tuple[int, str]] = []
        self.string_command: list[tuple[int, str]] = []
        self.os_system: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast convention)
        callee = _qualified_name(node.func)

        # subprocess.run / call / Popen / check_output / check_call
        if callee.startswith("subprocess."):
            # 1. shell=True?
            for kw in node.keywords:
                if kw.arg == "shell" and _is_truthy(kw.value):
                    self.shell_true.append((node.lineno, callee))

            # 2. String-form first arg (shell-interpreted if shell=True is
            #    ever later added; brittle either way). list/tuple is safe.
            if node.args:
                first = node.args[0]
                if isinstance(first, (ast.Constant, ast.JoinedStr)) and not isinstance(
                    first.value if isinstance(first, ast.Constant) else None, bytes
                ):
                    # A string literal or f-string as the first arg.
                    self.string_command.append((node.lineno, callee))

        # os.system / os.popen / commands.getoutput
        if callee in ("os.system", "os.popen", "commands.getoutput"):
            self.os_system.append((node.lineno, callee))

        self.generic_visit(node)


def _qualified_name(node: ast.AST) -> str:
    """Resolve `subprocess.run` / `os.system` / `module.x.y` to a dotted str."""
    if isinstance(node, ast.Attribute):
        base = _qualified_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_truthy(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    # Anything not a literal True we can't statically prove false. Be strict:
    # any non-False constant or any non-literal counts as "potentially true."
    return not (isinstance(node, ast.Constant) and node.value is False)


@pytest.fixture(scope="module")
def audited():
    """Run the AST audit once per test module."""
    findings = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as e:
            pytest.fail(f"Cannot parse {path}: {e}")
        a = _SubprocessAuditor(path)
        a.visit(tree)
        findings.append(a)
    return findings


def test_no_shell_true_in_app(audited):
    """No subprocess call may pass shell=True. The list-form arg is safe
    by construction; shell=True turns the call into /bin/sh -c, at which
    point any string interpolation becomes a command-injection vector."""
    hits = [
        (a.path, lineno, callee) for a in audited for lineno, callee in a.shell_true
    ]
    assert (
        not hits
    ), "shell=True forbidden in production code; use list-form args.\n" + "\n".join(
        f"  {p}:{ln}  {c}" for p, ln, c in hits
    )


def test_no_os_system_or_popen_in_app(audited):
    """os.system / os.popen / commands.getoutput are shell-interpreted by
    definition. There is no safe way to interpolate user input into them.
    Use subprocess.run([...]) instead."""
    hits = [(a.path, lineno, callee) for a in audited for lineno, callee in a.os_system]
    assert not hits, (
        "os.system / os.popen / commands.getoutput forbidden in production code.\n"
        + "\n".join(f"  {p}:{ln}  {c}" for p, ln, c in hits)
    )


def test_subprocess_calls_use_list_form(audited):
    """Every subprocess.run / call / Popen / check_output / check_call must
    pass a list (or tuple) as the first arg, not a string. The list form
    routes around the shell — any element can contain arbitrary metachars
    and they get passed as a single argv slot to the spawned binary, no
    shell parsing involved."""
    hits = [
        (a.path, lineno, callee) for a in audited for lineno, callee in a.string_command
    ]
    assert (
        not hits
    ), "subprocess.* called with string first-arg; switch to list form.\n" + "\n".join(
        f"  {p}:{ln}  {c}" for p, ln, c in hits
    )


# --------------------------------------------------------------------------
# Shell-script audit — every variable expansion must be quoted.
# --------------------------------------------------------------------------


def _shell_files() -> list[Path]:
    root = APP_DIR.parent
    return [
        p
        for p in (
            list((root / "scripts").glob("*.sh")) + [root / "docker-entrypoint.sh"]
        )
        if p.exists()
    ]


_UNQUOTED_VAR = re.compile(
    r"""
    (?<![\\'"$])         # not preceded by an escape, quote, or another $
    \$\{?[A-Za-z_][A-Za-z0-9_]*\}?  # $VAR or ${VAR}
    """,
    re.VERBOSE,
)


def _is_inside_double_quotes(line: str, idx: int) -> bool:
    """Crude pairing — does index `idx` sit between an even number of "?
    Good enough for our scripts which never use single-quoted strings to
    contain $vars; if that assumption ever breaks we'll see it as a false
    positive here, not a silent skip."""
    return line.count('"', 0, idx) % 2 == 1


def test_shell_scripts_quote_all_variable_expansions():
    """Every $VAR in our shell scripts must be inside double quotes.
    Unquoted `$VAR` word-splits and glob-expands; if VAR ever comes from
    operator-controlled env (DB_NAME, BACKUP_DIR, etc.), an attacker who
    can set env can inject extra arguments into any command that follows."""
    offenses: list[tuple[Path, int, str]] = []

    for path in _shell_files():
        for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
            # Strip inline comments — `# $VAR` is fine.
            comment_at = raw_line.find("#")
            line = raw_line if comment_at < 0 else raw_line[:comment_at]
            # Skip assignment RHS that are themselves quoted —
            # the regex catches those when they aren't, by definition.
            for m in _UNQUOTED_VAR.finditer(line):
                if _is_inside_double_quotes(line, m.start()):
                    continue
                # Common safe pattern: `$(...)` is captured here only
                # via the leading `$` and our regex matches `\$VAR`, so
                # $( does NOT match. arithmetic $((...)) likewise.
                # Numeric/positional ($1, $#, $?) we never use in these
                # scripts, so we don't special-case them.
                offenses.append((path, lineno, raw_line.strip()))

    assert not offenses, "Unquoted shell variable expansion(s) found:\n" + "\n".join(
        f"  {p}:{ln}  {src}" for p, ln, src in offenses
    )

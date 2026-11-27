# ============================================================================
# Local templates for printed documents (#1235).
#
# A firm that prints invoices and statements on its own layout used to
# bind-mount files over app/templates, which hid every upstream change on
# upgrade without a word. Now the server's operator may name a folder of
# local templates, looked in first by the printed-document environment
# (pdf_service._jinja_env: every PDF and its Print page). A file there
# replaces the built-in template of the same name, and an include or
# extends resolves the same way; anything not there is the built-in one.
#
# Only the operator chooses the folder: the SLOWBOOKS_LOCAL_TEMPLATES_DIR
# environment setting, or, when that is not set, the fixed sub-folder
# local-templates of the desktop app's data folder (SLOWBOOKS_DATA_DIR),
# used only when it exists. Nothing in the web interface, a company file,
# an import or an API call reads or writes either. Unset, or a data-folder
# sub-folder that does not exist, is off, and the environment loads exactly
# what FileSystemLoader(app/templates) loads. A setting that names a folder
# that is missing or cannot be read is off too, and said plainly at startup
# and in the support diagnostics.
#
# Not covered: the app's own pages, the customer portal and the public pay
# page (app/routes/portal.py, public.py), and the invoice email body
# (email_service), which Settings > Email templates already customizes.
#
# docs/local-templates.md is the operator's guide.
# ============================================================================

import logging
import os
from pathlib import Path

from jinja2 import (
    BaseLoader,
    FileSystemLoader,
    TemplateError,
    TemplateNotFound,
    TemplateSyntaxError,
)

logger = logging.getLogger(__name__)

ENV_VAR = "SLOWBOOKS_LOCAL_TEMPLATES_DIR"
DATA_SUBFOLDER = "local-templates"
# Built-in templates the printed-document environment never loads: a local
# file of one of these names replaces nothing, so it is not listed as such.
_NOT_DOCUMENTS = ("portal/", "public_pay.html", "invoice_email.html")


class LocalTemplateError(RuntimeError):
    """A local template could not be loaded, parsed or rendered. It is
    answered as the refusal, never with a fall back to the built-in
    template. ``user_text`` is what the person printing is shown: the
    template's name in the folder, that it is a local template, the line,
    and no server path or raw exception text (safe_errors passes it on).
    ``str()`` adds the path and the error, for the server log."""

    def __init__(self, user_text: str, detail: str = ""):
        super().__init__(f"{user_text} [{detail}]" if detail else user_text)
        self.user_text = user_text


def _configured() -> tuple[Path | None, str]:
    """(folder, where it was named) — the folder is None when nothing names
    one."""
    named = (os.environ.get(ENV_VAR) or "").strip()
    if named:
        return Path(named).expanduser(), "environment"
    data = (os.environ.get("SLOWBOOKS_DATA_DIR") or "").strip()
    if data:
        folder = Path(data) / DATA_SUBFOLDER
        if folder.is_dir():
            return folder, "data folder"
    return None, ""


def _problem(folder: Path, source: str) -> str:
    """Why a named folder cannot be used, or '' when it can. Worded for
    where it was named (the setting, or the desktop data folder)."""
    named = (
        f"{ENV_VAR} names a folder"
        if source == "environment"
        else f"the data folder's {DATA_SUBFOLDER} sub-folder is a folder"
    )
    if not folder.is_dir():
        return f"{named} that does not exist or is not a folder"
    if not os.access(folder, os.R_OK | os.X_OK):
        return f"{named} this server cannot read"
    return ""


def active_folder() -> Path | None:
    """The local templates folder in use, or None (off)."""
    folder, source = _configured()
    if folder is None or _problem(folder, source):
        return None
    return folder.resolve()


def _local_file(folder: Path, name: str) -> Path | None:
    """The file in ``folder`` that stands for template ``name``, or None
    when there is none. A name that could reach outside the folder (an
    absolute path, a drive, a backslash or a ``..`` part) is refused as not
    found, so nothing outside is read. A symbolic link is followed only when
    its target is inside the folder too."""
    parts = name.split("/")
    if (
        name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(p in ("..", "") for p in parts)
    ):
        raise TemplateNotFound(name)
    path = folder.joinpath(*parts)
    # a broken link counts as there: refused below, never the built-in
    if not (path.is_symlink() or path.exists()):
        return None
    real = path.resolve()
    if not real.is_relative_to(folder):
        raise LocalTemplateError(
            f'The local template "{name}" is a link to a file outside the'
            " local templates folder, and was refused.",
            str(path),
        )
    if not real.is_file():
        raise LocalTemplateError(
            f'The local template "{name}" is not a readable file (a folder, or'
            " a link to nothing), and was refused.",
            str(path),
        )
    return real


class DocumentLoader(BaseLoader):
    """The local folder first, then app/templates. Off, it is the built-in
    FileSystemLoader exactly (same source, file name and up-to-date check)."""

    def __init__(self, builtin_dir: str):
        self.builtin = FileSystemLoader(builtin_dir)

    def get_source(self, environment, name):
        folder = active_folder()
        if folder is None:
            return self.builtin.get_source(environment, name)
        path = _local_file(folder, name)
        if path is not None:
            try:
                source = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime
            except (OSError, UnicodeDecodeError) as exc:
                raise LocalTemplateError(
                    f'The local template "{name}" could not be read'
                    f" ({type(exc).__name__}).",
                    f"{path}: {exc}",
                ) from exc

            def uptodate():
                # edited, removed, or the setting changed: load again
                try:
                    return active_folder() == folder and path.stat().st_mtime == mtime
                except OSError:
                    return False

            # the call prints nothing; it marks the page as drawn by a local
            # template (pdf_service guards such a page's scripts)
            return "{{ _sb_local_template() }}" + source, str(path), uptodate
        source, filename, builtin_uptodate = self.builtin.get_source(environment, name)

        def uptodate():
            # a local file of this name added since: load again
            try:
                if active_folder() != folder or _local_file(folder, name):
                    return False
            except (TemplateNotFound, LocalTemplateError):
                return False
            return builtin_uptodate()

        return source, filename, uptodate

    def load(self, environment, name, globals=None):
        try:
            return super().load(environment, name, globals)
        except TemplateSyntaxError as exc:
            if is_local_file(exc.filename):
                raise LocalTemplateError(
                    f'The local template "{name}" has an error on line'
                    f" {exc.lineno}: {exc.message}",
                    str(exc.filename),
                ) from exc
            raise

    def list_templates(self):
        return self.builtin.list_templates()


def is_local_file(filename) -> bool:
    folder = active_folder()
    if folder is None or not filename:
        return False
    try:
        return Path(filename).resolve().is_relative_to(folder)
    except (OSError, ValueError):
        return False


def render_failure(exc: BaseException) -> LocalTemplateError | None:
    """The refusal for an error raised while rendering, when a local
    template's own code was running (its file is in the traceback), else
    None. Names the innermost local file and its line."""
    if isinstance(exc, LocalTemplateError):
        return exc
    folder = active_folder()
    if folder is None:
        return None
    where = None
    tb = exc.__traceback__
    while tb is not None:
        if is_local_file(tb.tb_frame.f_code.co_filename):
            where = (tb.tb_frame.f_code.co_filename, tb.tb_lineno)
        tb = tb.tb_next
    if where is None:
        return None
    name = Path(where[0]).relative_to(folder).as_posix()
    # Jinja's own errors ("'gone' is undefined") are about the template and
    # are shown; any other exception's text may carry data or SQL, so only
    # its kind is shown and the text goes to the log.
    said = f": {exc}" if isinstance(exc, TemplateError) else ""
    return LocalTemplateError(
        f'The local template "{name}" failed on line {where[1]}:'
        f" {type(exc).__name__}{said}",
        f"{where[0]}: {exc}",
    )


def status(builtin_dir: str) -> dict:
    """What the support diagnostics show: whether local templates are on,
    where the folder was named, any problem, and which built-in document
    templates are replaced (and which local files are additions)."""
    folder, source = _configured()
    out = {
        "enabled": False,
        "source": source,
        "problem": "",
        "replaced": [],
        "added": [],
    }
    if folder is None:
        return out
    out["problem"] = _problem(folder, source)
    if out["problem"]:
        return out
    out["enabled"] = True
    folder = folder.resolve()
    builtin = sorted(
        n
        for n in FileSystemLoader(builtin_dir).list_templates()
        if not n.startswith(_NOT_DOCUMENTS)
    )
    try:
        local = sorted(FileSystemLoader(str(folder)).list_templates())
    except OSError as exc:
        out["problem"] = (
            f"the local templates folder could not be listed ({type(exc).__name__})"
        )
        return out
    # Replaced = what the loader itself would draw locally, so a case-
    # insensitive file system (Windows, macOS) and a broken link (refused
    # at print) are reported as they print. Listed by the built-in name.
    taken = set()
    for n in builtin:
        try:
            path = _local_file(folder, n)
        except (LocalTemplateError, TemplateNotFound):
            out["replaced"].append(n)
            taken.add(n)
            continue
        if path is not None:
            out["replaced"].append(n)
            taken.add(_same_file_key(path))
    for n in local:
        key = _same_file_key(folder / n)
        if n not in taken and (key is None or key not in taken):
            out["added"].append(n)
    return out


def _same_file_key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def diagnostics_line(builtin_dir: str) -> str:
    """One line for the support environment block, '' when nothing names a
    folder. Names no path (the block carries no personal details)."""
    s = status(builtin_dir)
    if not s["source"]:
        return ""
    if s["problem"]:
        return f"Local templates: off ({s['problem']})"
    replaced = ", ".join(s["replaced"]) or "none"
    return (
        f"Local templates: on ({s['source']}); replaces: {replaced};"
        f" added files: {len(s['added'])}"
    )


def report_at_startup(builtin_dir: str) -> None:
    """Say once, at boot, what the local templates setting does. Never
    raises: a diagnostic, not a reason not to boot."""
    try:
        folder, _ = _configured()
        if folder is None:
            return
        s = status(builtin_dir)
        if s["problem"]:
            logger.warning("local templates are off: %s (%s)", s["problem"], folder)
        else:
            logger.warning(
                "local templates are on from %s; replacing built-in: %s",
                folder,
                ", ".join(s["replaced"]) or "none",
            )
    except Exception:
        logger.warning("local templates status could not be read", exc_info=True)

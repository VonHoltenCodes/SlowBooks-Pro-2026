# ============================================================================
# Contact support — read-only diagnostics for the in-app support form.
#
# GET  /api/support/diagnostics  the environment block, a redacted tail of
#                                launcher.log, and the company's own reply-to.
#                                Any signed-in role may call it; it reads and
#                                never writes, and makes NO outbound call.
# POST /api/support/reveal-log   native desktop only: open the log's folder in
#                                Finder / Explorer. 404 elsewhere.
#
# The form itself opens the user's mail client (a mailto: link) — the app
# sends nothing. See docs/support.md.
# ============================================================================

import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.models.contacts import Customer, Vendor
from app.models.payroll import Employee
from app.services import settings_service, support_diagnostics
from app.services.company_service import data_dir

router = APIRouter(prefix="/api/support", tags=["support"])


def _log_path():
    """The log the tail comes from: launcher.log in the per-user data dir
    (the launcher writes it in --hidden / frozen mode, and Server Edition's
    headless server too)."""
    return data_dir() / "launcher.log"


def _company_names(db: Session) -> list[str]:
    """Every personal and company name in the file, for the redactor: customer
    and vendor names (and their company names), employee names, and the
    company's own name. Pulled in one pass each; names only, no other columns."""
    names: list[str] = []
    for name, company in db.query(Customer.name, Customer.company).all():
        names += [name, company]
    for name, company in db.query(Vendor.name, Vendor.company).all():
        names += [name, company]
    for first, last in db.query(Employee.first_name, Employee.last_name).all():
        names += [first, last, f"{first} {last}".strip()]
    names.append(settings_service.get_setting_raw(db, "company_name"))
    return [n for n in names if n]


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)):
    """The environment block and redacted log tail the support form places in
    the email. Read-only; never 500s on a missing log (empty tail, flagged)."""
    platform_name = support_diagnostics.detect_platform()
    deployment = support_diagnostics.detect_deployment()
    commit = support_diagnostics.app_commit()
    python = support_diagnostics.python_version()
    env_block = support_diagnostics.environment_block(
        __version__, commit, platform_name, python, deployment
    )

    raw_tail, available = support_diagnostics.read_log_tail(_log_path())
    log_tail = (
        support_diagnostics.redact(raw_tail, _company_names(db)) if available else ""
    )

    reply_to = settings_service.get_setting_raw(db, "company_email") or ""

    return {
        "version": __version__,
        "commit": commit,
        "build_version": f"{__version__}+{commit}" if commit else __version__,
        "platform": platform_name,
        "python": python,
        "deployment": deployment,
        "is_native": support_diagnostics.is_native(),
        "reply_to": reply_to,
        "environment": env_block,
        "log_tail": log_tail,
        "log_available": available,
        "log_note": "" if available else _no_log_note(deployment),
    }


def _no_log_note(deployment: str) -> str:
    """Why there's no log tail. A browser install (Server Edition's
    --serve-lan from source, or self-managed: Docker or a plain source run)
    logs to the console / container logs,
    not to launcher.log, so there is no file to read — say exactly that."""
    if deployment in ("Server Edition", "self-managed"):
        return (
            "This deployment logs to the console (terminal or container logs), "
            "not to a file — copy the recent server output into your email."
        )
    return "No launcher.log was found on this install."


@router.post("/reveal-log")
def reveal_log():
    """Open the log file's folder in the platform file manager. Native desktop
    only — a browser, Server Edition or self-managed client has no local folder to
    open, so the route is a 404 there (and the form hides the button)."""
    if not support_diagnostics.is_native():
        raise HTTPException(status_code=404, detail="Not available on this install.")

    path = _log_path()
    if not path.exists():
        return {
            "revealed": False,
            "reason": "No launcher.log exists yet on this install.",
            "path": str(path),
        }
    try:
        if sys.platform == "darwin":
            # -R reveals (selects) the file in its folder.
            subprocess.run(["open", "-R", str(path)], check=False, timeout=15)
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{path}"], check=False, timeout=15)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=500, detail="Could not open the log folder."
        ) from exc

    return {"revealed": True, "path": str(path)}

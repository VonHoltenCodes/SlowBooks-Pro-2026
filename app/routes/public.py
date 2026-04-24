# ============================================================================
# Public Routes — pages accessible without authentication
# Serves the public invoice payment page at /pay/{token}
# ============================================================================

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.routes.settings import _get_all as get_settings

router = APIRouter(tags=["public"])

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))

@router.get("/pay/{token}")
def public_payment_page(token: str, status: str = None, db: Session = Depends(get_db)):
    """Public invoice payment page — no auth required."""
    invoice = db.query(Invoice).filter(Invoice.payment_token == token).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    settings = get_settings(db)
    stripe_enabled = settings.get("stripe_enabled", "false") == "true"
    stripe_pub_key = settings.get("stripe_publishable_key", "")

    template = _jinja_env.get_template("public_pay.html")
    html = template.render(
        inv=invoice,
        company=settings,
        stripe_enabled=stripe_enabled and bool(stripe_pub_key),
        payment_status=status,
        token=token,
    )
    return HTMLResponse(html)

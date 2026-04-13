# ============================================================================
# CSV Export Service — export entities to CSV format
# Feature 14: Uses Python stdlib csv module
# ============================================================================

import csv
import io

from sqlalchemy.orm import Session

from app.models.contacts import Customer, Vendor
from app.models.items import Item
from app.models.invoices import Invoice
from app.models.accounts import Account


def export_customers(db: Session) -> str:
    customers = db.query(Customer).filter(Customer.is_active == True).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Company", "Email", "Phone", "Address", "City", "Region", "Postcode", "Terms", "Balance"])
    for c in customers:
        writer.writerow([c.id, c.name, c.company or "", c.email or "", c.phone or "",
                         c.bill_address1 or "", c.bill_city or "", c.bill_state or "",
                         c.bill_zip or "", c.terms or "", float(c.balance or 0)])
    return output.getvalue()


def export_vendors(db: Session) -> str:
    vendors = db.query(Vendor).filter(Vendor.is_active == True).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Company", "Email", "Phone", "Address", "City", "Region", "Postcode", "Terms", "Balance"])
    for v in vendors:
        writer.writerow([v.id, v.name, v.company or "", v.email or "", v.phone or "",
                         v.address1 or "", v.city or "", v.state or "",
                         v.zip or "", v.terms or "", float(v.balance or 0)])
    return output.getvalue()


def export_items(db: Session) -> str:
    items = db.query(Item).filter(Item.is_active == True).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Type", "Description", "Rate", "Cost", "Taxable"])
    for i in items:
        writer.writerow([i.id, i.name, i.item_type.value, i.description or "",
                         float(i.rate or 0), float(i.cost or 0), i.is_taxable])
    return output.getvalue()


def export_invoices(db: Session, date_from=None, date_to=None) -> str:
    q = db.query(Invoice)
    if date_from:
        q = q.filter(Invoice.date >= date_from)
    if date_to:
        q = q.filter(Invoice.date <= date_to)
    invoices = q.order_by(Invoice.date).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Invoice #", "Customer", "Date", "Due Date", "Status", "Subtotal", "Tax", "Total", "Paid", "Balance"])
    for inv in invoices:
        writer.writerow([inv.invoice_number, inv.customer.name if inv.customer else "",
                         inv.date.isoformat(), inv.due_date.isoformat() if inv.due_date else "",
                         inv.status.value, float(inv.subtotal), float(inv.tax_amount),
                         float(inv.total), float(inv.amount_paid), float(inv.balance_due)])
    return output.getvalue()


def export_accounts(db: Session) -> str:
    accounts = db.query(Account).order_by(Account.account_number).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Number", "Name", "Type", "Balance", "Active", "System"])
    for a in accounts:
        writer.writerow([a.account_number or "", a.name, a.account_type.value,
                         float(a.balance or 0), a.is_active, a.is_system])
    return output.getvalue()

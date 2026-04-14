"""
Seed Slowbooks with transitional NZ demo data.

Current scope:
- Customer and supplier contacts are derived from Xero Demo Company NZ exports
- Items and transaction examples are still the older temporary sample set and
  will be replaced in a later follow-up slice
"""
import sys
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models.accounts import Account
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.services.accounting import (
    create_journal_entry, get_ar_account_id, get_default_bank_account_id,
    get_default_income_account_id, get_gst_account_id,
)


# ============================================================================
# IRS Pub 583 — Henry Brown's Auto Body Shop
# Monthly Summary of Cash Receipts, January (page 18)
# ============================================================================

JANUARY_DAILY_SALES = [
    # (day, net_sales, sales_tax)
    (3,  263.60, 4.20),
    (4,  212.00, 3.39),
    (5,  194.40, 3.10),
    (6,  222.40, 3.54),
    (7,  231.15, 3.68),
    (8,  137.50, 2.13),
    (10, 187.90, 2.99),
    (11, 207.56, 3.31),
    (12, 128.95, 2.05),
    (13, 231.40, 3.77),
    (14, 201.28, 3.21),
    (15, 88.01,  1.40),
    (17, 210.95, 3.36),
    (18, 221.80, 3.53),
    (19, 225.15, 3.59),
    (20, 221.93, 3.52),
    (21, 133.53, 2.13),
    (22, 130.84, 2.08),
    (24, 216.37, 3.45),
    (25, 220.05, 3.50),
    (26, 197.80, 3.15),
    (27, 272.49, 4.34),
    (28, 150.64, 2.40),
    (29, 224.05, 3.56),
    (31, 133.30, 2.13),
]
# Total: $4,865.05 net sales, $77.51 sales tax (matches Pub 583 page 22)

VENDORS = [
    {"name": "ABC Furniture", "company": "ABC Furniture", "phone": "800 124578", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Bayside Club", "company": "Bayside Club", "phone": "02 2024455", "email": "secretarybob@bsclub.co", "address1": "P O Box 3354", "city": "Ridge Heights", "state": "Madeupville", "zip": "6001", "country": "New Zealand", "terms": "Net 30"},
    {"name": "Bayside Wholesale", "company": "Bayside Wholesale", "phone": "850 55669", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Capital Cab Co", "company": "Capital Cab Holdings", "phone": "800 235689", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Carlton Functions", "company": "Carlton Functions", "phone": "02 9179945", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Central Copiers", "company": "Central Copiers", "phone": "800 244844", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Gateway Motors", "company": "Gateway Motors", "phone": "800 349227", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Hoyt Productions", "company": "Hoyt Productions", "phone": "02 7411234", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "MCO Cleaning Services", "company": "MCO Cleaning Services", "phone": "02 5119119", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "Net Connect", "company": "Net Connect Holdings", "phone": "800 500998", "email": "", "address1": "P O Box 7900", "city": "Oaktown", "state": "", "zip": "1236", "country": "NZ", "terms": "Net 30"},
    {"name": "PC Complete", "company": "PC Complete", "phone": "800 322600", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
    {"name": "PowerDirect", "company": "PowerDirect Holdings", "phone": "800 887612", "email": "", "address1": "P O Box 8900", "city": "Oaktown", "state": "", "zip": "1288", "country": "NZ", "terms": "Net 30"},
    {"name": "SMART Agency", "company": "SMART Agency", "phone": "02 9159889", "email": "", "address1": "", "city": "", "state": "", "zip": "", "country": "NZ", "terms": "Net 30"},
]

CUSTOMERS = [
    {"name": "Basket Case", "company": None, "phone": "02 9176665", "email": "shop@basketcase.co", "terms": "Net 30", "bill_address1": "Shop 14 Ridgeway Mall", "bill_city": "Pinehaven", "bill_state": "", "bill_zip": "9877", "bill_country": "NZ"},
    {"name": "Bayside Club", "company": None, "phone": "02 2024455", "email": "secretarybob@bsclub.co", "terms": "Net 30", "bill_address1": "P O Box 3354", "bill_city": "Ridge Heights", "bill_state": "Madeupville", "bill_zip": "6001", "bill_country": "New Zealand"},
    {"name": "Boom FM", "company": None, "phone": "01 555 9191", "email": "", "terms": "Net 30", "bill_address1": "", "bill_city": "", "bill_state": "", "bill_zip": "", "bill_country": "NZ"},
    {"name": "City Agency", "company": None, "phone": "01 9173555", "email": "", "terms": "Net 30", "bill_address1": "", "bill_city": "", "bill_state": "", "bill_zip": "", "bill_country": "NZ"},
    {"name": "City Limousines", "company": None, "phone": "01 8004001", "email": "", "terms": "Net 30", "bill_address1": "", "bill_city": "", "bill_state": "", "bill_zip": "", "bill_country": "NZ"},
    {"name": "DIISR - Small Business Services", "company": None, "phone": "01 8009001", "email": "cad@diisr.govt", "terms": "Net 30", "bill_address1": "GPO 9566", "bill_city": "Pinehaven", "bill_state": "", "bill_zip": "9862", "bill_country": "NZ"},
    {"name": "Hamilton Smith Ltd", "company": None, "phone": "01 2345678", "email": "", "terms": "Net 30", "bill_address1": "", "bill_city": "", "bill_state": "", "bill_zip": "", "bill_country": "NZ"},
    {"name": "Ridgeway University", "company": None, "phone": "01 8005001", "email": "", "terms": "Net 30", "bill_address1": "", "bill_city": "", "bill_state": "", "bill_zip": "", "bill_country": "NZ"},
]

# ============================================================================
# Items / Services — Body shop service items
# Rates derived from Henry's daily sales averages ($195/day avg)
# ============================================================================

ITEMS = [
    {"name": "Body Repair",        "item_type": "service",  "rate": 85.00,  "description": "Auto body repair labor",         "income_acct": "200"},
    {"name": "Paint & Finish",     "item_type": "service",  "rate": 65.00,  "description": "Paint and finish work",           "income_acct": "200"},
    {"name": "Dent Removal",       "item_type": "service",  "rate": 45.00,  "description": "Dent removal and straightening",  "income_acct": "200"},
    {"name": "Frame Alignment",    "item_type": "service",  "rate": 125.00, "description": "Frame alignment and correction",  "income_acct": "200"},
    {"name": "Auto Parts",         "item_type": "material", "rate": 0.00,   "description": "Parts and materials (at cost)",   "income_acct": "200"},
    {"name": "Paint Supplies",     "item_type": "material", "rate": 0.00,   "description": "Paint, primer, clear coat",       "income_acct": "200"},
    {"name": "Towing Service",     "item_type": "service",  "rate": 75.00,  "description": "Tow truck service",               "income_acct": "200"},
    {"name": "Insurance Estimate", "item_type": "service",  "rate": 0.00,   "description": "Insurance damage assessment",     "income_acct": "200"},
]

# ============================================================================
# Invoices — Based on January daily sales ($4,865.05 total, 1.59% tax rate)
# Split into customer invoices matching the sales volume
# ============================================================================

INVOICES = [
    # (customer_name, invoice_date_offset, lines: [(item_name, qty, rate)], terms)
    ("Basket Case",   3,  [("Body Repair", 2, 85.00), ("Auto Parts", 1, 203.00), ("Paint & Finish", 1, 65.00)], "Net 30"),
    ("Bayside Club",  5,  [("Dent Removal", 3, 45.00), ("Paint & Finish", 2, 65.00)], "Net 30"),
    ("Boom FM",   7,  [("Body Repair", 3, 85.00), ("Frame Alignment", 1, 125.00), ("Auto Parts", 1, 150.00)], "Net 30"),
    ("City Agency", 10, [("Body Repair", 4, 85.00), ("Paint & Finish", 3, 65.00), ("Paint Supplies", 1, 137.50)], "Net 30"),
    ("City Limousines",   12, [("Dent Removal", 2, 45.00), ("Paint & Finish", 1, 65.00)], "Net 30"),
    ("DIISR - Small Business Services",   14, [("Body Repair", 2, 85.00), ("Dent Removal", 1, 45.00), ("Auto Parts", 1, 66.70)], "Net 30"),
    ("Hamilton Smith Ltd", 17, [("Insurance Estimate", 1, 0.00), ("Body Repair", 3, 85.00), ("Paint & Finish", 2, 65.00)], "Net 30"),
    ("Ridgeway University", 20, [("Dent Removal", 1, 45.00), ("Paint & Finish", 1, 65.00)], "Due on Receipt"),
    ("Basket Case",   24, [("Body Repair", 1, 85.00), ("Towing Service", 1, 75.00), ("Auto Parts", 1, 9.80)], "Net 30"),
    ("Boom FM",   27, [("Frame Alignment", 1, 125.00), ("Body Repair", 2, 85.00), ("Auto Parts", 1, 272.49)], "Net 30"),
]

# ============================================================================
# Estimates — Pending work quotes
# ============================================================================

ESTIMATES = [
    ("City Agency", 5,  [("Body Repair", 6, 85.00), ("Frame Alignment", 2, 125.00), ("Paint & Finish", 4, 65.00), ("Auto Parts", 1, 450.00)]),
    ("DIISR - Small Business Services",   8,  [("Body Repair", 3, 85.00), ("Paint & Finish", 3, 65.00), ("Dent Removal", 4, 45.00)]),
    ("Hamilton Smith Ltd", 15, [("Body Repair", 2, 85.00), ("Paint & Finish", 1, 65.00), ("Auto Parts", 1, 320.00)]),
]

# ============================================================================
# Payments — Based on Pub 583 check disbursements
# Transitional sample payments mapped onto the NZ/Xero-derived contacts
# ============================================================================

PAYMENTS = [
    # (customer_name, date_offset, amount, method, reference, invoice_index)
    ("Basket Case",   10, 438.00, "check", "4501", 0),
    ("Bayside Club",  15, 265.00, "check", "4502", 1),
    ("Ridgeway University", 21, 110.00, "cash",  "4503", 7),
    ("City Limousines",   20, 155.00, "check", "4504", 4),
    ("Boom FM",   22, 530.00, "check", "4505", 2),
]


def get_account_by_number(db, num):
    return db.query(Account).filter(Account.account_number == num).first()


def seed():
    db = SessionLocal()
    base_year = date.today().year
    base_date = date(base_year, 1, 1)

    try:
        # Check if mock data already exists
        existing_customers = db.query(Customer).filter(Customer.name == "Basket Case").first()
        if existing_customers:
            print("NZ demo contacts already seeded. Skipping.")
            return

        # Find next available invoice/estimate numbers
        from sqlalchemy import func
        max_inv = db.query(func.max(Invoice.invoice_number)).scalar() or "0"
        try:
            inv_start = max(int(max_inv) + 1, 2001)
        except ValueError:
            inv_start = 2001

        print("Seeding transitional NZ demo data from Xero-derived contacts...")

        # --- Vendors (skip existing) ---
        vendor_map = {}
        created_v = 0
        for v in VENDORS:
            existing = db.query(Vendor).filter(Vendor.name == v["name"]).first()
            if existing:
                vendor_map[v["name"]] = existing
                continue
            vendor = Vendor(
                name=v["name"], company=v["company"],
                email=v.get("email"),
                phone=v["phone"], terms=v["terms"],
                address1=v.get("address1"), city=v.get("city"),
                state=v.get("state"), zip=v.get("zip"),
                country=v.get("country") or "NZ", is_active=True,
            )
            db.add(vendor)
            db.flush()
            vendor_map[v["name"]] = vendor
            created_v += 1
        print(f"  {created_v} vendors created ({len(VENDORS) - created_v} existing)")

        # --- Customers (skip existing) ---
        customer_map = {}
        created_c = 0
        for c in CUSTOMERS:
            existing = db.query(Customer).filter(Customer.name == c["name"]).first()
            if existing:
                customer_map[c["name"]] = existing
                continue
            customer = Customer(
                name=c["name"], company=c.get("company"),
                phone=c["phone"], email=c["email"],
                terms=c["terms"],
                bill_address1=c.get("bill_address1"), bill_city=c.get("bill_city"),
                bill_state=c.get("bill_state"), bill_zip=c.get("bill_zip"),
                bill_country=c.get("bill_country") or "NZ",
                is_active=True, is_taxable=True,
            )
            db.add(customer)
            db.flush()
            customer_map[c["name"]] = customer
            created_c += 1
        print(f"  {created_c} customers created ({len(CUSTOMERS) - created_c} existing)")

        # --- Items (skip existing) ---
        item_map = {}
        created_i = 0
        for it in ITEMS:
            existing = db.query(Item).filter(Item.name == it["name"]).first()
            if existing:
                item_map[it["name"]] = existing
                continue
            income_acct = get_account_by_number(db, it["income_acct"])
            item_type_val = {"service": ItemType.SERVICE, "material": ItemType.MATERIAL,
                             "labor": ItemType.LABOR, "product": ItemType.PRODUCT}[it["item_type"]]
            item = Item(
                name=it["name"], item_type=item_type_val,
                rate=Decimal(str(it["rate"])),
                description=it["description"],
                income_account_id=income_acct.id if income_acct else None,
                is_taxable=True, is_active=True,
            )
            db.add(item)
            db.flush()
            item_map[it["name"]] = item
            created_i += 1
        print(f"  {created_i} items created ({len(ITEMS) - created_i} existing)")

        # --- Invoices ---
        ar_id = get_ar_account_id(db)
        invoice_list = []
        inv_counter = inv_start

        for cust_name, day_offset, lines_data, terms in INVOICES:
            customer = customer_map[cust_name]
            inv_date = base_date + timedelta(days=day_offset - 1)

            # Calculate terms days for due date
            terms_days = {"Net 15": 15, "Net 30": 30, "Net 45": 45,
                          "Due on Receipt": 0}.get(terms, 30)
            due_date = inv_date + timedelta(days=terms_days)

            subtotal = Decimal("0")
            inv_lines = []
            for i, (item_name, qty, rate) in enumerate(lines_data):
                item = item_map[item_name]
                amt = Decimal(str(qty)) * Decimal(str(rate))
                subtotal += amt
                inv_lines.append({
                    "item_id": item.id, "description": item.description,
                    "quantity": Decimal(str(qty)), "rate": Decimal(str(rate)),
                    "amount": amt, "line_order": i,
                })

            # 1.59% sales tax (from Pub 583: $77.51 tax / $4,865.05 sales)
            tax_rate = Decimal("0.0159")
            tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
            total = subtotal + tax_amount

            invoice = Invoice(
                invoice_number=str(inv_counter),
                customer_id=customer.id,
                date=inv_date, due_date=due_date,
                terms=terms, status=InvoiceStatus.SENT,
                subtotal=subtotal, tax_rate=tax_rate,
                tax_amount=tax_amount, total=total,
                amount_paid=Decimal("0"), balance_due=total,
            )
            db.add(invoice)
            db.flush()

            for ld in inv_lines:
                line = InvoiceLine(invoice_id=invoice.id, **ld)
                db.add(line)

            # Journal entry: DR A/R, CR Income, CR Sales Tax
            if ar_id:
                income_acct_id = get_default_income_account_id(db)
                tax_account_id = get_gst_account_id(db)

                journal_lines = [
                    {"account_id": ar_id, "debit": total, "credit": Decimal("0"),
                     "description": f"Invoice {inv_counter}"},
                ]
                if subtotal > 0 and income_acct_id:
                    journal_lines.append({"account_id": income_acct_id, "debit": Decimal("0"),
                                          "credit": subtotal, "description": f"Invoice {inv_counter}"})
                if tax_amount > 0 and tax_account_id:
                    journal_lines.append({"account_id": tax_account_id, "debit": Decimal("0"),
                                          "credit": tax_amount, "description": f"Invoice {inv_counter} tax"})

                txn = create_journal_entry(db, inv_date, f"Invoice {inv_counter}",
                                           journal_lines, source_type="invoice", source_id=invoice.id)
                invoice.transaction_id = txn.id

            invoice_list.append(invoice)
            inv_counter += 1

        db.flush()
        print(f"  {len(INVOICES)} invoices created")

        # --- Estimates ---
        est_counter = 100
        for cust_name, day_offset, lines_data in ESTIMATES:
            customer = customer_map[cust_name]
            est_date = base_date + timedelta(days=day_offset - 1)

            subtotal = Decimal("0")
            est_lines = []
            for i, (item_name, qty, rate) in enumerate(lines_data):
                item = item_map[item_name]
                amt = Decimal(str(qty)) * Decimal(str(rate))
                subtotal += amt
                est_lines.append({
                    "item_id": item.id, "description": item.description,
                    "quantity": Decimal(str(qty)), "rate": Decimal(str(rate)),
                    "amount": amt, "line_order": i,
                })

            tax_amount = (subtotal * Decimal("0.0159")).quantize(Decimal("0.01"))

            estimate = Estimate(
                estimate_number=f"E-{est_counter}",
                customer_id=customer.id,
                date=est_date, expiration_date=est_date + timedelta(days=30),
                status=EstimateStatus.PENDING,
                subtotal=subtotal, tax_rate=Decimal("0.0159"),
                tax_amount=tax_amount, total=subtotal + tax_amount,
            )
            db.add(estimate)
            db.flush()

            for ld in est_lines:
                line = EstimateLine(estimate_id=estimate.id, **ld)
                db.add(line)

            est_counter += 1

        print(f"  {len(ESTIMATES)} estimates created")

        # --- Payments ---
        checking_account_id = get_default_bank_account_id(db)
        for cust_name, day_offset, amount, method, ref, inv_idx in PAYMENTS:
            customer = customer_map[cust_name]
            pmt_date = base_date + timedelta(days=day_offset - 1)
            pmt_amount = Decimal(str(amount))

            payment = Payment(
                customer_id=customer.id,
                date=pmt_date, amount=pmt_amount,
                method=method, reference=ref,
                deposit_to_account_id=checking_account_id,
            )
            db.add(payment)
            db.flush()

            # Allocate to invoice
            inv = invoice_list[inv_idx]
            alloc_amount = min(pmt_amount, inv.balance_due)
            if alloc_amount > 0:
                alloc = PaymentAllocation(
                    payment_id=payment.id,
                    invoice_id=inv.id,
                    amount=alloc_amount,
                )
                db.add(alloc)
                inv.amount_paid += alloc_amount
                inv.balance_due = inv.total - inv.amount_paid
                if inv.balance_due <= 0:
                    inv.status = InvoiceStatus.PAID
                elif inv.amount_paid > 0:
                    inv.status = InvoiceStatus.PARTIAL

            # Journal entry: DR Checking, CR A/R
            if ar_id and checking_account_id:
                journal_lines = [
                    {"account_id": checking_account_id, "debit": pmt_amount, "credit": Decimal("0"),
                     "description": f"Payment from {cust_name}"},
                    {"account_id": ar_id, "debit": Decimal("0"), "credit": pmt_amount,
                     "description": f"Payment from {cust_name}"},
                ]
                txn = create_journal_entry(db, pmt_date, f"Payment from {cust_name}",
                                           journal_lines, source_type="payment", source_id=payment.id)
                payment.transaction_id = txn.id

        db.flush()
        print(f"  {len(PAYMENTS)} payments created")

        db.commit()

        # Summary
        total_invoiced = sum(inv.total for inv in invoice_list)
        total_paid = sum(Decimal(str(p[2])) for p in PAYMENTS)
        print(f"\nNZ demo contacts seeded successfully.")
        print(f"  Total invoiced: ${total_invoiced:,.2f}")
        print(f"  Total paid:     ${total_paid:,.2f}")
        print(f"  Outstanding:    ${total_invoiced - total_paid:,.2f}")

    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()

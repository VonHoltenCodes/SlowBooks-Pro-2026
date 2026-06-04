"""
Seed Slowbooks with mock data from IRS Publication 583 (Rev. December 2024).

Source: "Starting a Business and Keeping Records"
        https://www.irs.gov/pub/irs-pdf/p583.pdf
        Pages 16-23: Henry Brown's Auto Body Shop sample recordkeeping system

Henry Brown is a sole proprietor of a small automobile body shop.
He uses part-time help, has no inventory of items held for sale,
and uses the cash method of accounting.

All dollar amounts, vendor names, expense categories, and daily sales
figures are taken directly from the IRS publication.
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
from app.services.accounting import create_journal_entry, get_ar_account_id

# ============================================================================
# IRS Pub 583 — Henry Brown's Auto Body Shop
# Monthly Summary of Cash Receipts, January (page 18)
# ============================================================================

JANUARY_DAILY_SALES = [
    # (day, net_sales, sales_tax)
    (3, 263.60, 4.20),
    (4, 212.00, 3.39),
    (5, 194.40, 3.10),
    (6, 222.40, 3.54),
    (7, 231.15, 3.68),
    (8, 137.50, 2.13),
    (10, 187.90, 2.99),
    (11, 207.56, 3.31),
    (12, 128.95, 2.05),
    (13, 231.40, 3.77),
    (14, 201.28, 3.21),
    (15, 88.01, 1.40),
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

# ============================================================================
# IRS Pub 583 — Check Disbursements Journal, January (pages 20-21)
# Vendors and check amounts exactly as published
# ============================================================================

VENDORS = [
    {
        "name": "Dale Advertising",
        "company": "Dale Advertising",
        "phone": "555-0101",
        "terms": "Net 30",
    },
    {
        "name": "Auto Parts, Inc.",
        "company": "Auto Parts, Inc.",
        "phone": "555-0102",
        "terms": "Net 30",
    },
    {
        "name": "ABC Auto Paint",
        "company": "ABC Auto Paint",
        "phone": "555-0103",
        "terms": "Net 15",
    },
    {
        "name": "Joe's Service Station",
        "company": "Joe's Service Station",
        "phone": "555-0104",
        "terms": "Net 15",
    },
    {
        "name": "M.B. Ignition",
        "company": "M.B. Ignition",
        "phone": "555-0105",
        "terms": "Net 30",
    },
    {
        "name": "Baker's Fender Co.",
        "company": "Baker's Fender Co.",
        "phone": "555-0106",
        "terms": "Net 30",
    },
    {
        "name": "Enterprise Rentals",
        "company": "Enterprise Rentals",
        "phone": "555-0107",
        "terms": "Due on Receipt",
    },
    {
        "name": "Mike's Deli",
        "company": "Mike's Deli",
        "phone": "555-0108",
        "terms": "Due on Receipt",
    },
    {
        "name": "Telephone Co.",
        "company": "Telephone Co.",
        "phone": "555-0109",
        "terms": "Net 15",
    },
    {
        "name": "National Bank",
        "company": "National Bank",
        "phone": "555-0110",
        "terms": "Due on Receipt",
    },
    {
        "name": "Electric Co.",
        "company": "Electric Co.",
        "phone": "555-0111",
        "terms": "Net 30",
    },
    {
        "name": "City Treasurer",
        "company": "City Treasurer",
        "phone": "555-0112",
        "terms": "Due on Receipt",
    },
    {
        "name": "State Treasurer",
        "company": "State Treasurer",
        "phone": "555-0113",
        "terms": "Due on Receipt",
    },
]

# ============================================================================
# Customers — Derived from IRS Pub 583 context (auto body shop customers)
# Henry's shop does cash and invoice work
# ============================================================================

CUSTOMERS = [
    {
        "name": "John E. Marks",
        "company": None,
        "phone": "555-0201",
        "email": "jmarks@example.com",
        "terms": "Net 30",
    },
    {
        "name": "Patricia Davis",
        "company": None,
        "phone": "555-0202",
        "email": "pdavis@example.com",
        "terms": "Net 30",
    },
    {
        "name": "Robert Garcia",
        "company": "Garcia Trucking",
        "phone": "555-0203",
        "email": "rgarcia@example.com",
        "terms": "Net 15",
    },
    {
        "name": "Thompson & Sons",
        "company": "Thompson & Sons",
        "phone": "555-0204",
        "email": "info@thompson.example",
        "terms": "Net 30",
    },
    {
        "name": "Linda S. Chen",
        "company": None,
        "phone": "555-0205",
        "email": "lchen@example.com",
        "terms": "Net 15",
    },
    {
        "name": "Metro Cab Co.",
        "company": "Metro Cab Co.",
        "phone": "555-0206",
        "email": "fleet@metrocab.example",
        "terms": "Net 30",
    },
    {
        "name": "Wilson Insurance",
        "company": "Wilson Insurance",
        "phone": "555-0207",
        "email": "claims@wilson.example",
        "terms": "Net 30",
    },
    {
        "name": "David R. Ortega",
        "company": None,
        "phone": "555-0208",
        "email": "dortega@example.com",
        "terms": "Due on Receipt",
    },
]

# ============================================================================
# Items / Services — Body shop service items
# Rates derived from Henry's daily sales averages ($195/day avg)
# ============================================================================

ITEMS = [
    {
        "name": "Body Repair",
        "item_type": "service",
        "rate": 85.00,
        "description": "Auto body repair labor",
        "income_acct": "4000",
    },
    {
        "name": "Paint & Finish",
        "item_type": "service",
        "rate": 65.00,
        "description": "Paint and finish work",
        "income_acct": "4000",
    },
    {
        "name": "Dent Removal",
        "item_type": "service",
        "rate": 45.00,
        "description": "Dent removal and straightening",
        "income_acct": "4000",
    },
    {
        "name": "Frame Alignment",
        "item_type": "service",
        "rate": 125.00,
        "description": "Frame alignment and correction",
        "income_acct": "4000",
    },
    {
        "name": "Auto Parts",
        "item_type": "material",
        "rate": 0.00,
        "description": "Parts and materials (at cost)",
        "income_acct": "4100",
    },
    {
        "name": "Paint Supplies",
        "item_type": "material",
        "rate": 0.00,
        "description": "Paint, primer, clear coat",
        "income_acct": "4100",
    },
    {
        "name": "Towing Service",
        "item_type": "service",
        "rate": 75.00,
        "description": "Tow truck service",
        "income_acct": "4000",
    },
    {
        "name": "Insurance Estimate",
        "item_type": "service",
        "rate": 0.00,
        "description": "Insurance damage assessment",
        "income_acct": "4000",
    },
]

# ============================================================================
# Invoices — Based on January daily sales ($4,865.05 total, 1.59% tax rate)
# Split into customer invoices matching the sales volume
# ============================================================================

INVOICES = [
    # (customer_name, invoice_date_offset, lines: [(item_name, qty, rate)], terms)
    (
        "John E. Marks",
        3,
        [
            ("Body Repair", 2, 85.00),
            ("Auto Parts", 1, 203.00),
            ("Paint & Finish", 1, 65.00),
        ],
        "Net 30",
    ),
    (
        "Patricia Davis",
        5,
        [("Dent Removal", 3, 45.00), ("Paint & Finish", 2, 65.00)],
        "Net 30",
    ),
    (
        "Robert Garcia",
        7,
        [
            ("Body Repair", 3, 85.00),
            ("Frame Alignment", 1, 125.00),
            ("Auto Parts", 1, 150.00),
        ],
        "Net 15",
    ),
    (
        "Thompson & Sons",
        10,
        [
            ("Body Repair", 4, 85.00),
            ("Paint & Finish", 3, 65.00),
            ("Paint Supplies", 1, 137.50),
        ],
        "Net 30",
    ),
    (
        "Linda S. Chen",
        12,
        [("Dent Removal", 2, 45.00), ("Paint & Finish", 1, 65.00)],
        "Net 15",
    ),
    (
        "Metro Cab Co.",
        14,
        [
            ("Body Repair", 2, 85.00),
            ("Dent Removal", 1, 45.00),
            ("Auto Parts", 1, 66.70),
        ],
        "Net 30",
    ),
    (
        "Wilson Insurance",
        17,
        [
            ("Insurance Estimate", 1, 0.00),
            ("Body Repair", 3, 85.00),
            ("Paint & Finish", 2, 65.00),
        ],
        "Net 30",
    ),
    (
        "David R. Ortega",
        20,
        [("Dent Removal", 1, 45.00), ("Paint & Finish", 1, 65.00)],
        "Due on Receipt",
    ),
    (
        "John E. Marks",
        24,
        [
            ("Body Repair", 1, 85.00),
            ("Towing Service", 1, 75.00),
            ("Auto Parts", 1, 9.80),
        ],
        "Net 30",
    ),
    (
        "Robert Garcia",
        27,
        [
            ("Frame Alignment", 1, 125.00),
            ("Body Repair", 2, 85.00),
            ("Auto Parts", 1, 272.49),
        ],
        "Net 15",
    ),
]

# ============================================================================
# Estimates — Pending work quotes
# ============================================================================

ESTIMATES = [
    (
        "Thompson & Sons",
        5,
        [
            ("Body Repair", 6, 85.00),
            ("Frame Alignment", 2, 125.00),
            ("Paint & Finish", 4, 65.00),
            ("Auto Parts", 1, 450.00),
        ],
    ),
    (
        "Metro Cab Co.",
        8,
        [
            ("Body Repair", 3, 85.00),
            ("Paint & Finish", 3, 65.00),
            ("Dent Removal", 4, 45.00),
        ],
    ),
    (
        "Wilson Insurance",
        15,
        [
            ("Body Repair", 2, 85.00),
            ("Paint & Finish", 1, 65.00),
            ("Auto Parts", 1, 320.00),
        ],
    ),
]

# ============================================================================
# Payments — Based on Pub 583 check disbursements
# John E. Marks paid check #77 $214.11 and #88 $214.11
# ============================================================================

PAYMENTS = [
    # (customer_name, date_offset, amount, method, reference, invoice_index)
    ("John E. Marks", 10, 438.00, "check", "4501", 0),
    ("Patricia Davis", 15, 265.00, "check", "4502", 1),
    ("David R. Ortega", 21, 110.00, "cash", "4503", 7),
    ("Linda S. Chen", 20, 155.00, "check", "4504", 4),
    ("Robert Garcia", 22, 530.00, "check", "4505", 2),
]


def get_account_by_number(db, num):
    return db.query(Account).filter(Account.account_number == num).first()


def seed():
    db = SessionLocal()
    base_year = date.today().year
    base_date = date(base_year, 1, 1)

    try:
        # Check if mock data already exists
        existing_customers = (
            db.query(Customer).filter(Customer.name == "John E. Marks").first()
        )
        if existing_customers:
            print("IRS mock data already seeded. Skipping.")
            return

        # Find next available invoice/estimate numbers
        from sqlalchemy import func

        max_inv = db.query(func.max(Invoice.invoice_number)).scalar() or "0"
        try:
            inv_start = max(int(max_inv) + 1, 2001)
        except ValueError:
            inv_start = 2001

        print("Seeding IRS Pub 583 mock data — Henry Brown's Auto Body Shop...")

        # --- Vendors (skip existing) ---
        vendor_map = {}
        created_v = 0
        for v in VENDORS:
            existing = db.query(Vendor).filter(Vendor.name == v["name"]).first()
            if existing:
                vendor_map[v["name"]] = existing
                continue
            vendor = Vendor(
                name=v["name"],
                company=v["company"],
                phone=v["phone"],
                terms=v["terms"],
                address1="123 Commerce St",
                city="Anytown",
                state="TX",
                zip="75001",
                is_active=True,
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
                name=c["name"],
                company=c.get("company"),
                phone=c["phone"],
                email=c["email"],
                terms=c["terms"],
                bill_address1="456 Main St",
                bill_city="Anytown",
                bill_state="TX",
                bill_zip="75001",
                is_active=True,
                is_taxable=True,
            )
            db.add(customer)
            db.flush()
            customer_map[c["name"]] = customer
            created_c += 1
        print(
            f"  {created_c} customers created ({len(CUSTOMERS) - created_c} existing)"
        )

        # --- Items (skip existing) ---
        item_map = {}
        created_i = 0
        for it in ITEMS:
            existing = db.query(Item).filter(Item.name == it["name"]).first()
            if existing:
                item_map[it["name"]] = existing
                continue
            income_acct = get_account_by_number(db, it["income_acct"])
            item_type_val = {
                "service": ItemType.SERVICE,
                "material": ItemType.MATERIAL,
                "labor": ItemType.LABOR,
                "product": ItemType.PRODUCT,
            }[it["item_type"]]
            item = Item(
                name=it["name"],
                item_type=item_type_val,
                rate=Decimal(str(it["rate"])),
                description=it["description"],
                income_account_id=income_acct.id if income_acct else None,
                is_taxable=True,
                is_active=True,
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
            terms_days = {
                "Net 15": 15,
                "Net 30": 30,
                "Net 45": 45,
                "Due on Receipt": 0,
            }.get(terms, 30)
            due_date = inv_date + timedelta(days=terms_days)

            subtotal = Decimal("0")
            inv_lines = []
            for i, (item_name, qty, rate) in enumerate(lines_data):
                item = item_map[item_name]
                amt = Decimal(str(qty)) * Decimal(str(rate))
                subtotal += amt
                inv_lines.append(
                    {
                        "item_id": item.id,
                        "description": item.description,
                        "quantity": Decimal(str(qty)),
                        "rate": Decimal(str(rate)),
                        "amount": amt,
                        "line_order": i,
                    }
                )

            # 1.59% sales tax (from Pub 583: $77.51 tax / $4,865.05 sales)
            tax_rate = Decimal("0.0159")
            tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
            total = subtotal + tax_amount

            invoice = Invoice(
                invoice_number=str(inv_counter),
                customer_id=customer.id,
                date=inv_date,
                due_date=due_date,
                terms=terms,
                status=InvoiceStatus.SENT,
                subtotal=subtotal,
                tax_rate=tax_rate,
                tax_amount=tax_amount,
                total=total,
                amount_paid=Decimal("0"),
                balance_due=total,
            )
            db.add(invoice)
            db.flush()

            for ld in inv_lines:
                line = InvoiceLine(invoice_id=invoice.id, **ld)
                db.add(line)

            # Journal entry: DR A/R, CR Income, CR Sales Tax
            if ar_id:
                income_acct = get_account_by_number(db, "4000")
                product_acct = get_account_by_number(db, "4100")
                tax_acct = get_account_by_number(db, "2200")

                # Split income by item type
                service_total = Decimal("0")
                product_total = Decimal("0")
                for ld in inv_lines:
                    itm = db.query(Item).get(ld["item_id"])
                    if itm and itm.item_type in (ItemType.MATERIAL, ItemType.PRODUCT):
                        product_total += ld["amount"]
                    else:
                        service_total += ld["amount"]

                journal_lines = [
                    {
                        "account_id": ar_id,
                        "debit": total,
                        "credit": Decimal("0"),
                        "description": f"Invoice {inv_counter}",
                    },
                ]
                if service_total > 0 and income_acct:
                    journal_lines.append(
                        {
                            "account_id": income_acct.id,
                            "debit": Decimal("0"),
                            "credit": service_total,
                            "description": f"Invoice {inv_counter}",
                        }
                    )
                if product_total > 0 and product_acct:
                    journal_lines.append(
                        {
                            "account_id": product_acct.id,
                            "debit": Decimal("0"),
                            "credit": product_total,
                            "description": f"Invoice {inv_counter}",
                        }
                    )
                if tax_amount > 0 and tax_acct:
                    journal_lines.append(
                        {
                            "account_id": tax_acct.id,
                            "debit": Decimal("0"),
                            "credit": tax_amount,
                            "description": f"Invoice {inv_counter} tax",
                        }
                    )

                txn = create_journal_entry(
                    db,
                    inv_date,
                    f"Invoice {inv_counter}",
                    journal_lines,
                    source_type="invoice",
                    source_id=invoice.id,
                )
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
                est_lines.append(
                    {
                        "item_id": item.id,
                        "description": item.description,
                        "quantity": Decimal(str(qty)),
                        "rate": Decimal(str(rate)),
                        "amount": amt,
                        "line_order": i,
                    }
                )

            tax_amount = (subtotal * Decimal("0.0159")).quantize(Decimal("0.01"))

            estimate = Estimate(
                estimate_number=f"E-{est_counter}",
                customer_id=customer.id,
                date=est_date,
                expiration_date=est_date + timedelta(days=30),
                status=EstimateStatus.PENDING,
                subtotal=subtotal,
                tax_rate=Decimal("0.0159"),
                tax_amount=tax_amount,
                total=subtotal + tax_amount,
            )
            db.add(estimate)
            db.flush()

            for ld in est_lines:
                line = EstimateLine(estimate_id=estimate.id, **ld)
                db.add(line)

            est_counter += 1

        print(f"  {len(ESTIMATES)} estimates created")

        # --- Payments ---
        checking_acct = get_account_by_number(db, "1000")
        for cust_name, day_offset, amount, method, ref, inv_idx in PAYMENTS:
            customer = customer_map[cust_name]
            pmt_date = base_date + timedelta(days=day_offset - 1)
            pmt_amount = Decimal(str(amount))

            payment = Payment(
                customer_id=customer.id,
                date=pmt_date,
                amount=pmt_amount,
                method=method,
                reference=ref,
                deposit_to_account_id=checking_acct.id if checking_acct else None,
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
            if ar_id and checking_acct:
                journal_lines = [
                    {
                        "account_id": checking_acct.id,
                        "debit": pmt_amount,
                        "credit": Decimal("0"),
                        "description": f"Payment from {cust_name}",
                    },
                    {
                        "account_id": ar_id,
                        "debit": Decimal("0"),
                        "credit": pmt_amount,
                        "description": f"Payment from {cust_name}",
                    },
                ]
                txn = create_journal_entry(
                    db,
                    pmt_date,
                    f"Payment from {cust_name}",
                    journal_lines,
                    source_type="payment",
                    source_id=payment.id,
                )
                payment.transaction_id = txn.id

        db.flush()
        print(f"  {len(PAYMENTS)} payments created")

        db.commit()

        # Summary
        total_invoiced = sum(inv.total for inv in invoice_list)
        total_paid = sum(Decimal(str(p[2])) for p in PAYMENTS)
        print("\nIRS Pub 583 mock data seeded successfully.")
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

# Spec: NZ Demo Contacts

## Scope
Replace the demo customer and vendor seed contacts using the supplied Xero NZ example files.

## Rules
- Use `/home/joelmacklow/Downloads/Customers.csv` and `Suppliers.csv` as source material for the in-repo demo contact lists.
- Do not turn this into a generic Xero CSV import feature in this slice.
- Keep the demo seed script runnable by minimally retargeting contact-name references where needed.
- Leave items/invoices/payments/estimates as a later follow-up rewrite.

## Validation
- NZ/Xero-derived customer and vendor names seed correctly.
- Henry Brown / IRS contact markers are removed from the contact layer.
- Seed remains idempotent.

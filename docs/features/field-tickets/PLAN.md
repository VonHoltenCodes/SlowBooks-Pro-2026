# Field tickets: plan

Status: built (construction overhaul, 2026-11-07). The design as drafted for
Keith, the PM persona's additions, and the decisions taken while building it
at the end. `docs/contractor-mode.md` "Field tickets" is the user-facing spec.

## What a field ticket is

A field ticket is a paper record from the job site: what was done or what
arrived, on what day, signed by somebody. It lives on the job. There are three
kinds, stored in one table, with a number per job and kind:

| Kind | Number | Who fills it in | Who signs | What it leads to |
|---|---|---|---|---|
| **T&M ticket** | `<job>-TM-001` | foreman / super | the owner's rep or the GC | a change order, or an invoice |
| **Material slip** | `<job>-MS-001` | yard / super | none (internal) | a job-cost entry: material out of stock onto the job |
| **Delivery ticket** | `<job>-DT-001` | whoever takes the delivery | the receiver | received quantity on a PO line, checked when the bill comes in |

## The code it builds on (all on main)

- `JobCost` / `JobCostLine` (`app/models/job_costing.py`): manual job-cost
  entries with employee, equipment, cost code, cost type, `is_billable`.
- `Equipment` with `hourly_rate`; `TimeEntry` with job and cost code.
- `PurchaseOrderLine.received_qty`: already on the table, "left for the
  goods-receipt path" (#553), never written. Delivery tickets are that path.
- `ChangeOrder` / `ChangeOrderLine` (PCO → owner CO, lines with cost type,
  code, qty, unit cost, markup).
- `Attachment`: file uploads on any record (the photo of the signed paper).

## Model

`field_tickets`: `id`, `job_id` (required), `kind` (`tm` | `material` |
`delivery`), `number`, `date`, `status`, `cost_code_id` (default for its
lines), `description`, `submitted_by`, `signed_by_name`, `signed_at`,
`purchase_order_id` (delivery only), `vendor_ticket_ref` (the supplier's own
number on a delivery), `change_order_id` / `invoice_id` / `job_cost_id`
(what it turned into), `voided_at`, timestamps.

`field_ticket_lines`: `line_type` (`labor` | `equipment` | `material` |
`other`), `employee_id`, `equipment_id`, `item_id`, `po_line_id` (delivery),
`description`, `quantity`, `unit`, `cost_rate`, `bill_rate` (T&M only),
`cost_code_id`, `cost_type`, `line_order`.

Status: `draft` → `signed` → `closed` (billed, posted or received), and
`void` from any of them while nothing downstream is approved.

## Each kind

**T&M ticket.** Labor hours by person or craft, equipment hours, material and
other, each with a cost rate (from the employee, the equipment's rate, the
item cost) and a bill rate. Signing it makes it billable. Two ways out:

- **Make change order** (a lump-sum or GMP job): builds an owner PCO with one
  line per ticket line at cost, with the job's markup, and links back. Several
  signed tickets can go into one PCO. This is the normal path for extra work.
- **Bill it** (a job with no schedule of values, or a T&M / cost-plus job):
  invoice lines at the bill rates.

**A T&M ticket posts no cost.** Labor cost already comes from time entries and
payroll, equipment from job-cost entries, material from bills. If the ticket
posted cost too, the job would count it twice. The ticket is a quantity and
billing record.

**Material slip.** Material taken from the contractor's own stock onto a job
(a return slip is the same with negative quantities). Posting it writes a
`JobCost` entry with `source="material_slip"`: DR job cost (material type,
the line's code) / CR the item's inventory asset account, at the item's
average cost. Voiding it reverses at the booked cost, not today's cost (the
mistake the skeptic keeps finding elsewhere).

**Delivery ticket.** Picked against an open PO: one line per PO line with the
quantity delivered. Saving it adds to `received_qty` and moves the PO to
`partial` or `received`. When the supplier's bill is entered from the PO, the
bill form shows ordered / received / billed per line and **warns** (not
refuses) when a line bills more than was received. A void takes the quantity
back. No posting: received-not-billed accruals are left to the accountant.

## Where it shows

- A **Field tickets** tab on the job (construction mode only), a list with
  kind, number, date, status, signed by, total, and what it turned into.
- A **Field ticket log** report: per job, open (unsigned) T&M tickets and
  signed-but-unbilled T&M at bill rates. This is the money a GC most often
  never bills.
- Commitment Status gains received vs billed per PO line.
- Photos and the signed paper go on the ticket as attachments.

## Milestones (one PR each)

1. **Model, CRUD and the job tab**: all three kinds as records, numbering,
   attachments, void, the log report. Nothing posts. The others build on this.
2. **T&M to change order and invoice**.
3. **Material slip posting**.
4. **Delivery tickets against PO lines**: received_qty, PO status, the bill
   form's received column and warning, Commitment Status column.

2, 3 and 4 depend only on 1 and can run in parallel after it merges.

## From the PM persona (2026-11-07)

- A **sub's** T&M ticket ties to its commitment and becomes a sub change-order
  line under the owner CO it prices, so add `commitment_id` on the header.
- Job Cost Entry already has a **Bill?** checkbox (`is_billable`) that nothing
  reads. Either T&M tickets replace it or "Bill it" reads it too; decide in M2.
- Unmatched delivery tickets (received, not billed) belong next to Pay Bills
  and on Commitment Status.

## Decisions for Keith

1. A T&M ticket posts no cost (recommended; otherwise cost is counted twice).
2. Signature in v1 is a typed name and date plus a photo of the signed paper.
   An on-screen signature pad is a later issue.
3. Billing more than was received is a warning, not a refusal.
4. Not in v1: a mobile/foreman app, daily reports and timesheets on the
   ticket, received-not-billed accruals.

The four decisions above are taken as written.

## Decisions made while building it

1. **Delivery quantities count from save.** A delivery ticket's quantities are
   on the PO lines as soon as it is saved (draft or signed); an edit moves
   them by the change, a delete or void takes them back. A delivery ticket
   never goes to `closed`: "received" is its effect, and it stays open to
   void.
2. **Only a draft is edited.** A signed ticket is what its signer signed; a
   change is a void and a new ticket. A material slip is not signed; it is
   posted.
3. **Default rates.** Cost rate: the employee's loaded rate (the time-entry
   rate), the equipment's hourly rate, the item's average cost (its cost
   when it has none). Bill rate (T&M only): the cost rate plus the job's
   change-order markup.
4. **Make change order prices at cost.** The PCO's lines are quantity × cost
   rate and take the job's change-order markup through the normal fallback;
   the ticket's bill rates are for "Bill it" only. A sub's ticket is
   `subcontract` cost on the PCO and also makes a draft sub change order on
   its commitment, at cost, one per commitment. The sub CO is not linked to
   an owner CO automatically (a PCO cannot back one); link it after the PCO
   is promoted.
5. **The link follows the PCO.** A ticket links to the PCO it was priced on;
   the service follows revisions and the promotion to the owner CO. It
   closes when that owner CO is approved and opens again if it is voided; a
   rejected or voided chain frees it.
6. **Bill it** is refused on a job with a schedule of values (it bills on pay
   applications); the invoice is posted like any invoice, and voiding it
   opens the tickets again.
7. **`is_billable` stays as stored.** Job Cost Entry's Bill? box is a flag
   nothing reads; Bill it does not read it (job-cost lines are at cost, with
   no bill rate, and reading both would bill the work twice). In
   construction mode extra work to bill goes on a T&M ticket.
8. **Material slips post at average cost, void at booked cost.** One Job Cost
   Entry per slip, source `material_slip`; the movement is an inventory
   adjustment with source `material_slip` (no new movement type, so no enum
   migration). The Job Cost Entries void refuses a slip's entry. An item
   with no average cost is refused rather than posting $0.
9. **The received warning** shows only on an order that is being received by
   delivery tickets (some line has a received quantity), comparing billed to
   date plus this period against received quantity × the line's rate.
   Commitment Status's Received column is the delivery tickets dated by the
   as-of date, so it is right back-dated.
10. **The API refuses writes outside construction mode** (409); reads answer
    with an empty list.

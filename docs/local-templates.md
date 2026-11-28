# Local templates for printed documents

SlowBooks Pro draws every printed document (invoices, statements, estimates,
checks and the rest) from a Jinja template in `app/templates`. If your firm
prints on its own layout, put your own copy of a template in a **local
templates folder** instead of mounting a file over the built-in one. A file in
that folder replaces the built-in template of the same name. Anything that is
not in the folder is the built-in template, so an upgrade still brings you
every built-in change you have not replaced (#1235).

It is **off** unless the server's operator turns it on.

## Turning it on

Only the person who runs the server can choose the folder. Nothing in the web
interface, a company file, an import or an API call can name it or change it.

**Docker.** Mount a folder into the container and name it in the
`SLOWBOOKS_LOCAL_TEMPLATES_DIR` environment setting, under the `slowbooks`
service. Put it in a `docker-compose.override.yml` beside the shipped
`docker-compose.yml` (Compose reads both), so an upgrade that replaces the
shipped file does not drop it. With `docker-compose.prod.yml`, add the same
lines to a file you pass after it (`-f docker-compose.prod.yml -f local.yml`).

```yaml
services:
  slowbooks:
    environment:
      SLOWBOOKS_LOCAL_TEMPLATES_DIR: /app/local-templates
    volumes:
      - ./local-templates:/app/local-templates:ro
```

Then `docker compose up -d`. A read-only mount (`:ro`) is enough.

**A source or systemd install.** Put `SLOWBOOKS_LOCAL_TEMPLATES_DIR=/path/to/folder`
in `.env` (or the service's environment) and restart the server. Give an
absolute path: a relative one is read from wherever the server was started.

**The desktop app.** Make a folder named `local-templates` in the app's data
folder and restart SlowBooks Pro:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\SlowBooksPro\data\local-templates\` |
| macOS | `~/Library/Application Support/SlowBooksPro/data/local-templates/` |
| Linux | `~/.slowbookspro/data/local-templates/` |

The folder is used only when it exists. `SLOWBOOKS_LOCAL_TEMPLATES_DIR` in the
desktop app's `.env` (beside the data folder) takes its place if you set one.

**Checking it.** At startup the server log says whether local templates are on
and which built-in templates they replace. Contact support (beside About) shows a line
such as `Local templates: on (environment); replaces: invoice_pdf.html,
statement_pdf.html; added files: 3` in the environment block, so support can
see what is replaced. A setting that names a folder that does not exist, or
that the server cannot read, does not stop the server: local templates are off,
and the startup log and the support line say why.

**When changes take effect.** Once the folder is in use, a file you add, edit
or remove takes effect at the next print, with no restart (the built-in
templates are checked the same way). After turning the setting on or off, or
creating or removing the desktop app's `local-templates` folder, restart:
until then some documents may print from the old choice.

## How names resolve

- Put the file at the same name it has in `app/templates`:
  `local-templates/invoice_pdf.html` replaces `app/templates/invoice_pdf.html`.
- `{% include %}`, `{% extends %}` and `{% import %}` resolve the same way: a
  local template can include its own partial (`{% include '_firm_header.html' %}`,
  a file in your folder) or a built-in one (`{% include '_print_text.html' %}`).
  A local `_print_text.html` would replace the built-in partial in every
  document that includes it.
- Sub-folders work (`{% include 'parts/remit.html' %}`).
- A name with `..`, a leading `/`, a drive letter or a backslash is refused, so
  nothing outside the folder is read. A symbolic link inside the folder is
  followed only when its target is inside the folder too; one that points
  outside is refused, and so is a link that points at nothing or a folder
  with a template's name. A hard link is an ordinary file and is not
  detected; only the person who runs the server can make one.
- A local template cannot `{% extends %}` the built-in template of the same
  name (the name is the local file itself; it is refused). Copy the whole
  built-in file and change what you need.
- Start each page with `<!DOCTYPE html>`, as the built-in ones do.

## What a broken local template does

If a local template, or a partial it includes, does not parse or fails while
it prints, the document is refused with a message that names the local file,
says it is a local template, and gives the line. The built-in template is never
printed in its place, so a layout that broke on upgrade cannot go out looking
like the default one. The message shows the file's name in the folder, not
the server's path; the full error and the path are in the server log. An
emailed document, a Collections statement run or reminder, and a bulk
statement send name the local template in the row that failed.

**A name that is no longer passed prints as nothing, without an error.** If
a release renames or removes a value and your template still uses the old
name (`{{ inv.old_field }}`, `{{ gone_value }}`), that spot prints blank and
the document goes out. Only an error, or reading a field of such a missing
name (`{{ gone_value.amount }}`), is refused. This is why you print a test
document after every upgrade.

## The templates

The ones a local file can replace (the customer portal, the online pay page
and the invoice email body are not printed documents and are not covered; the
email body is customized under Settings → Email → Email Templates):

| Template | What it prints |
|---|---|
| `invoice_pdf.html` | Invoices, sales receipts, pay-application invoices, donation receipts and pledges (one face, worded by the company type) |
| `statement_pdf.html` | Customer statements, including the statements Collections sends |
| `estimate_pdf.html` | Estimates and bids |
| `change_order_pdf.html` | Owner change orders |
| `pay_application_pdf.html` | Owner payment applications |
| `lien_waiver_pdf.html` | Lien waivers |
| `credit_memo_pdf.html` | Credit memos |
| `bill_pdf.html` | Bills |
| `purchase_order_pdf.html` | Purchase orders |
| `deposit_pdf.html` / `deposit_slip_stock.html` | Deposit slips on plain paper / on the bank's pre-printed slip |
| `check_pdf.html` | Checks (voucher, pre-printed and blank stock) |
| `collection_letter.html` | Collection letters |
| `acknowledgment_letter.html` / `giving_statement_pdf.html` | Donor acknowledgment letters / year-end giving statements |
| `paystub_pdf.html` | Pay stubs |
| `labels_5160_pdf.html` | Avery 5160 mailing labels |
| `report_pdf.html`, `_report_theme.html` | Every report's PDF and Print page |
| `analytics_pdf.html` | The analytics snapshot |
| `w2.html`, `w3.html`, `form_940.html`, `form_941.html`, `form_1099nec.html`, `form_1096.html`, `new_hire_report.html` | Tax and payroll forms (replace these only if you know the form's layout rules) |
| `_print_text.html` | A partial: the long-word and line rules every printed page includes |

## What each template receives

Every document drawn through the shared renderer (all of the above except
checks, the pre-printed deposit slip `deposit_slip_stock.html`, labels, the
analytics snapshot and the tax and payroll forms) gets:

| Name | What it is |
|---|---|
| `company` | The company's settings as a dict of strings: `company_name`, `display_name`, `company_address1`, `company_address2`, `company_city`, `company_state`, `company_zip`, `company_phone`, `company_email`, `invoice_footer`, `invoice_logo_only`, `pdf_paper_size` and every other company setting |
| `terms` | The company's words: `terms('Invoice')` prints "Pledge" for a nonprofit |
| `company_logo_data_uri` | The logo as a `data:` URI, or `""` (on invoices, `""` when "show logo" is off) |
| `paper_size` | `"letter"` or `"a4"` |
| `last_line_cut` | How the last line is laid out (`"whole"`, `"kept"` or a cut); pass it through as the built-in templates do |

Filters: `currency`, `rate`, `qty`, `tax_percent`, `fdate`, `desc`, `own_width`,
`word_cap`, `capped`, `css_string`, `pct_label`, `check_amount`. Functions:
`city_line(city, state, zip)`, `country_line(country)`,
`document_currency_code(doc)`, `tail_split`, `line_cut`, `tax_exempt_line`,
`estimate_totals`, `spread_markup_prices`, `presentation_style`,
`deposit_slip_split`. Read the built-in templates for how each is used.

### `invoice_pdf.html`

| Name | What it is |
|---|---|
| `inv` | The invoice record. Used by the built-in template: `invoice_number`, `date`, `due_date`, `terms`, `po_number`, `status`, `is_sales_receipt`, `bill_address1`, `bill_address2`, `bill_city`, `bill_state`, `bill_zip`, `ship_address1`, `ship_address2`, `ship_city`, `ship_state`, `ship_zip`, `customer` (with `name`, `bill_country`, `ship_country`), `customer_name`, `subtotal`, `tax_rate`, `tax_amount`, `total`, `amount_paid`, `balance_due`, `retainage_held`, `retainage_released`, `fair_value_amount`, `fair_value_description`, `notes`, and `lines` (each with `description`, `quantity`, `rate`, `amount`, `is_taxable`) |
| `doc_kind` | Which document it is, as a code: `Invoice`, `SalesReceipt`, `DonationReceipt` or `Pledge` (print `terms(...)` for the company's word) |
| `bill_to_name`, `bill_to_for`, `bill_to_country`, `ship_to_country` | The Bill To name, the "For:" sub-customer line (or none), and the countries frozen with the addresses |
| `irs` | On a donation receipt, the acknowledgment block (`text`, `date`, `amount_received`, `deductible_amount`); otherwise none |
| `release_only` | True on a retainage-release invoice |
| `pay_application_number` | The pay application this invoice bills, or none |
| `retainage_lines` | What a line-less pay-application invoice bills, in place of its lines (each with `description` and `amount`) |
| `discount_line` | The early-payment discount offer, or none |
| `discount_taken` | The discount already taken (`amount`, `number`), or none |

### `statement_pdf.html`

| Name | What it is |
|---|---|
| `customer` | The customer record: `name`, `bill_address1`, `bill_address2`, `bill_city`, `bill_state`, `bill_zip`, `bill_country` and the rest of the customer |
| `as_of_date` | The statement date |
| `activity` | The statement's figures: `lines` (each with `date`, `type`, `number`, `description`, `amount`, `balance`, `currency`), `total_invoiced`, `total_payments`, `total_credits`, `balance_due`, `retainage_held`, `home_currency`, `has_foreign` |

### The other documents

| Template | Its own names (besides the shared ones) |
|---|---|
| `estimate_pdf.html` | `est` (the estimate, with its `lines`) |
| `change_order_pdf.html` | `co` (the change order), `totals` |
| `pay_application_pdf.html` | `app`, `pa` (the application and its line detail), `job`, `header` (`owner`, `job_name`, `net_change`, `contract_sum_original`, `contract_sum_to_date`), `totals` |
| `lien_waiver_pdf.html` | `waiver`, `payment`, `job`, `title`, `conditional`, `final`, `owner`, `project` |
| `credit_memo_pdf.html` | `cm` |
| `bill_pdf.html` | `bill`, `discount_line`, `discount_taken` |
| `purchase_order_pdf.html` | `po` |
| `deposit_pdf.html` | `deposit` (the deposit as `GET /api/deposits/{id}` answers it) |
| `collection_letter.html` | `customer`, `invoices`, `letter_type`, `total_due`, `today` |
| `acknowledgment_letter.html` | `customer`, `gift`, `irs`, `body_html`, `today` |
| `giving_statement_pdf.html` | `statements`, `year`, `opening_message`, `footer_text` |
| `report_pdf.html` | `sections` (each `title`, `period`, `columns`, `rows`), `landscape`, `generated_on` |
| `check_pdf.html` (not the shared renderer) | `checks` (each check's payee, amount, `amount_words`, memo and stub lines), `company`, `position`, `field_css`, `field_wrap`, `no_dollar_sign`, `asterisk_fill` (Fill the amount with asterisks), `stock`, `nudge_x`, `nudge_y`, and on blank stock `blank` and `logo`; the amount in figures prints through `check_amount(no_dollar_sign, asterisk_fill)`, so a check template that leaves `asterisk_fill` out prints no asterisks |
| `labels_5160_pdf.html` (not the shared renderer) | `pages` (lists of 30 labels, each `line1`, `line2`, `address1`, `address2`, `city_line`) |

The pay stub, tax and payroll forms receive the figures their own render calls
pass. The render calls are in `app/services/pdf_service.py` (and `paystub_pdf.py`,
`form_1099.py`, `new_hire_report.py`, `tax_forms/`). Those calls are the
authority: when this table and the code differ, the code is right.

## Before you turn it on: a local template is your own code

- **It runs inside SlowBooks Pro, as the server.** A Jinja template is code.
  The printed-document templates are not sandboxed, built-in or local, and
  this feature does not add a sandbox, so a local template can run any code
  on the server with the server's permissions: every company's data, the
  encryption keys in its environment, and any file the server can read.
  Use only templates you wrote or have read line by line.
- **`company` holds secrets.** On invoices, statements, estimates, bills,
  purchase orders and the other documents of the shared renderer, `company`
  is every company setting, decrypted: the email server's password, the
  payment providers' keys, the ACH bank account, the closing-date password.
  A template that prints `{{ company.smtp_password }}` puts it on a document
  that goes to a customer. Check any template you did not write for this.
- **Its scripts are held back on the Print page.** A page drawn by a local
  template also opens as its Print page in the browser, on SlowBooks Pro's own
  address. Such a page (and a Print all run that holds one) carries a policy
  that lets only SlowBooks Pro's print script run, and allows no fetch, form
  or plugin, so a `<script>` in your template does not run there; PDFs never
  run scripts. The page is not isolated: it can still load images from
  anywhere and can send the window elsewhere, so this is a guard against a
  careless template, not a reason to trust a hostile one.
- **It is not checked on upgrade.** SlowBooks Pro does not compare your
  template with the built-in one it replaces. After an upgrade, compare your
  copy against the new built-in template, and print a test document before
  you send any.
- **A release can change what a template receives.** A value can be renamed,
  removed or added, and a name that is no longer passed prints as nothing.
  The tables above list what this version passes; after an upgrade, the new
  version's built-in templates show what it passes.

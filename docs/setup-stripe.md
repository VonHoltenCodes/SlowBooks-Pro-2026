# Stripe Online Payments Setup

Accept online payments on invoices through Stripe Checkout. Customers open the
**Pay Online** link in an emailed invoice, pay on Stripe's hosted page, and the
payment records in SlowBooks with its journal entry.

---

## Before you start: where the link works

The pay link and the page the customer comes back to are built from the
address you have SlowBooks open at. On the desktop app that is
`http://127.0.0.1:3001`, and on a Server Edition host it is a LAN address, so a
link emailed from either opens only on your own computer or network. **To take
payments from customers, run SlowBooks at a public HTTPS address** (a cloud
server, [cloud-hosting.md](cloud-hosting.md), or any server behind a TLS
proxy, [tls-proxy-setup.md](tls-proxy-setup.md)) and open it at that address
before you email an invoice or copy its link. Online checkout is offered on
invoices in your home currency, USD.

Changing these settings takes an administrator's sign-in. Making a payment
link (**Copy Payment Link**, or emailing an invoice with one) takes a
bookkeeper or administrator; a read-only or accountant sign-in sees no such
button.

---

## Step 1: A Stripe account

Create one at [dashboard.stripe.com/register](https://dashboard.stripe.com/register).
Test first in a **sandbox**: open the account picker at the top left of the
Dashboard and choose **Sandboxes**; a sandbox's keys start with `pk_test_` and
`sk_test_`, and nothing in it moves real money.

## Step 2: API keys

In the sandbox, open **Developers → API keys**
([dashboard.stripe.com/apikeys](https://dashboard.stripe.com/apikeys)) and
copy:

- the **Publishable key** (`pk_test_…`), and
- the **Secret key** (`sk_test_…`, **Reveal** to see it). Use the standard
  secret key; a restricted key has not been tested with SlowBooks.

A live secret key you create yourself is shown once, so paste it into
SlowBooks at the time.

## Step 3: Configure SlowBooks

1. Open **Settings → Online Payments** and find the **Stripe** block.
2. Set **Stripe Payments** to Enabled and paste the Publishable Key and the
   Secret Key.
3. **Post to a clearing account**, optional: see "How it posts" below.
4. **Save**. After saving, the Secret Key and Webhook Secret fields show
   `********` on every later visit; that is the redaction guard, not a save
   failure. Re-saving the page leaves the stored secrets alone; only typing a
   new value over the asterisks replaces them.

## Step 4: A webhook (servers)

Stripe tells SlowBooks a payment completed with a webhook. Without one the
payment still records when the customer returns to the pay page, or from the
invoice's **Check Payment Status** button, so a desktop install can skip this
step. A server should have it.

1. In the Dashboard open **Workbench → Webhooks** and click **Create an event
   destination**.
2. Events from **Your account**; payload **Snapshot**, with the API version
   shown. (Stripe's suggested default is **Thin**; SlowBooks reads the snapshot
   event only, and answers a thin one "ignored" without recording anything.)
3. Select the event `checkout.session.completed`, then **Continue**.
4. Destination type **Webhook endpoint**; **Endpoint URL**
   `https://your-public-address/api/payments/stripe/webhook`. It must be a
   public HTTPS address: Stripe cannot reach `localhost`, and treats a redirect
   (plain HTTP answered with a 307 to HTTPS) as a failure. The older path
   `/api/stripe/webhook` still works.
5. Create it, then **Reveal** the **Signing secret** (`whsec_…`) and paste it
   into SlowBooks' **Webhook Secret**.

To test webhooks on your own machine, run Stripe's CLI:
`stripe listen --forward-to localhost:3001/api/payments/stripe/webhook`, and
paste the `whsec_` it prints as the Webhook Secret while it runs.

## Step 5: Test it

1. Open an unpaid invoice in SlowBooks and click **Copy Payment Link**.
2. Open the link in a private window: the pay page shows the invoice summary
   and a **Pay $amount** button (it reads **Pay with Stripe** only when more
   than one provider is on).
3. Pay with Stripe's test card `4242 4242 4242 4242`, any future expiry, any
   CVC.
4. Back in SlowBooks the invoice reads **Paid** with a payment recorded. If
   not, click **Check Payment Status** on the invoice.

---

## Going live

1. Leave the sandbox: switch the Dashboard to your live account and complete
   Stripe's activation (business details and a bank account).
2. Copy the **live** keys (`pk_live_…`, `sk_live_…`) into SlowBooks.
3. Create the event destination again in the live account, with your
   production URL, and paste its signing secret.
4. Your server must answer at a public HTTPS address (see the top of this
   page).

---

## How it works

1. The customer opens the **Pay Online** link in an emailed invoice, or the
   link you copied.
2. The public payment page at `/pay/{token}` shows the invoice summary.
3. **Pay** creates a Stripe Checkout session and sends the customer to Stripe's
   hosted page; SlowBooks never sees the card number.
4. Stripe sends `checkout.session.completed` to the webhook (or the customer's
   return to the pay page, or Check Payment Status, picks the payment up).
5. SlowBooks verifies the signature, records the payment dated the day it is
   recorded, and marks the invoice paid in the same step.

### How it posts

By default: **DR Undeposited Funds / CR Accounts Receivable** for the gross,
the way any received payment posts.

With **Post to a clearing account** on (Settings → Online Payments, since
2.30.0): the payment posts **DR Stripe Clearing** (1210, or an account you
choose) **/ CR Accounts Receivable**, and Stripe's fee posts **DR Merchant
Account Fees** (6210) **/ CR Stripe Clearing**, so the clearing account holds
what Stripe owes you. When the payout lands in the bank feed, **Payout…** on
that line (or **Record payout** on the invoice's payment) posts DR bank (the
net) / CR clearing (the gross), with the fee already there, and the clearing
account returns to zero. A refund issued from the Stripe Dashboard is
recorded by voiding the online payment in SlowBooks (it reverses through the
clearing account) or, for a partial refund, with a credit memo.

---

## Troubleshooting

| Issue | What to check |
|-------|---------------|
| No Pay Online link on the emailed invoice | Stripe Payments is Enabled and the keys are saved; the invoice is in USD; the sender is a bookkeeper or administrator |
| The customer says the link doesn't open | The link carries the address you had SlowBooks open at: a desktop or LAN address reaches nobody outside. See the top of this page |
| Payment made, invoice not Paid | Click **Check Payment Status** on the invoice. On a server, open the event destination's **Event deliveries** in Workbench: a `400 Invalid webhook signature` means the Webhook Secret in SlowBooks is not this destination's (a test secret in live, or the CLI's); an "ignored" answer means the destination sends thin events, not snapshots |
| Test cards refused | Sandbox keys (`pk_test_`, `sk_test_`) with a sandbox destination; live keys with a live one |

### Stripe test cards

| Card number | Result |
|------------|--------|
| `4242 4242 4242 4242` | Successful payment |
| `4000 0000 0000 3220` | 3D Secure authentication required |
| `4000 0000 0000 9995` | Payment declined |

Any future expiration date and any 3-digit CVC.

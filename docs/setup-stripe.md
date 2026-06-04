# Stripe Online Payments Setup

Accept online payments on invoices via Stripe Checkout. Customers receive a "Pay Online" link in emailed invoices, pay on Stripe's hosted page, and the payment auto-records in Slowbooks with proper journal entries.

---

## Prerequisites

- A Stripe account (free to create at [stripe.com](https://stripe.com))
- Slowbooks Pro running and accessible

---

## Step 1: Create a Stripe Account

1. Go to [https://dashboard.stripe.com/register](https://dashboard.stripe.com/register)
2. Enter your email, full name, and password
3. Verify your email address
4. You'll start in **test mode** — no real charges are made until you activate your account

---

## Step 2: Get Your API Keys

1. Log in to the [Stripe Dashboard](https://dashboard.stripe.com)
2. Make sure **Test mode** is toggled ON (top-right toggle) for initial setup
3. Go to **Developers > API keys**
4. Copy your keys:
   - **Publishable key** — starts with `pk_test_...`
   - **Secret key** — starts with `sk_test_...` (click "Reveal test key" to see it)

---

## Step 3: Set Up a Webhook

Stripe uses webhooks to notify Slowbooks when a payment is completed.

1. In the Stripe Dashboard, go to **Developers > Webhooks**
2. Click **Add endpoint**
3. Set the **Endpoint URL** to: `http://your-server:3001/api/stripe/webhook`
   - For local testing: `http://localhost:3001/api/stripe/webhook`
   - For production: use your actual domain with HTTPS
4. Under **Events to send**, select: `checkout.session.completed`
5. Click **Add endpoint**
6. On the endpoint detail page, click **Reveal** under **Signing secret**
7. Copy the signing secret — starts with `whsec_...`

---

## Step 4: Configure Slowbooks

1. Open Slowbooks and go to **Settings** (sidebar > System > Settings)
2. Scroll down to the **Online Payments (Stripe)** section
3. Fill in:
   - **Enable Online Payments**: `Enabled`
   - **Publishable Key**: paste your `pk_test_...` key
   - **Secret Key**: paste your `sk_test_...` key
   - **Webhook Secret**: paste your `whsec_...` secret
4. Click **Save Settings**

After saving, the **Secret Key** and **Webhook Secret** fields will
display as `********` on every subsequent page load — that's the
redaction guard, not a save failure. Re-opening the page to edit
other settings will not overwrite the stored secrets; only typing a
new value over the `********` actually replaces them.

---

## Step 5: Test It

1. Create an invoice in Slowbooks
2. Click on the invoice to view it
3. Click **Copy Payment Link** — this copies the public payment URL
4. Open the link in a browser — you'll see the invoice summary with a **Pay with Stripe** button
5. Click the button — you'll be redirected to Stripe Checkout
6. Use Stripe's test card: `4242 4242 4242 4242`, any future expiry, any CVC
7. Complete the payment
8. Back in Slowbooks, the invoice should now show as **Paid** with a payment recorded

---

## Going Live

When you're ready to accept real payments:

1. In the Stripe Dashboard, toggle **Test mode** OFF
2. Complete Stripe's account activation (requires business details and bank account)
3. Copy your **live** API keys (`pk_live_...`, `sk_live_...`)
4. Create a new webhook endpoint with your production URL and copy the live signing secret
5. Update Slowbooks Settings with the live keys
6. **Important**: Your server must be accessible via HTTPS for production Stripe webhooks

---

## How It Works

1. Customer clicks "Pay Online" link in an emailed invoice (or you copy the payment link)
2. The public payment page at `/pay/{token}` shows the invoice summary
3. Customer clicks "Pay with Stripe" — Slowbooks creates a Stripe Checkout session
4. Customer pays on Stripe's hosted page (Slowbooks never sees card numbers)
5. Stripe sends a `checkout.session.completed` webhook to Slowbooks
6. Slowbooks verifies the webhook signature, records the payment, and updates the invoice status
7. Journal entry: DR Undeposited Funds, CR Accounts Receivable

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Pay Online" button doesn't appear | Make sure Stripe is set to `Enabled` in Settings and keys are saved |
| Webhook not firing | Check that the endpoint URL is reachable from the internet (localhost won't work for production) |
| Payment recorded but invoice still shows "Sent" | Check the webhook secret matches — a signature mismatch silently fails |
| Test cards not working | Make sure you're using test mode keys (`pk_test_`, `sk_test_`), not live keys |

### Stripe Test Cards

| Card Number | Result |
|------------|--------|
| `4242 4242 4242 4242` | Successful payment |
| `4000 0000 0000 3220` | 3D Secure authentication required |
| `4000 0000 0000 9995` | Payment declined |

Use any future expiration date and any 3-digit CVC.

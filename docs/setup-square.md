# Square Online Payments Setup

Accept Square payments on invoices alongside Stripe and/or PayPal. When Square is enabled, the public invoice pay page shows a **Pay $amount** button (**Pay with Square** when more than one provider is on); the customer pays on Square's hosted page and the payment records in SlowBooks with the standard journal entries (DR Undeposited Funds / CR Accounts Receivable, or the clearing account and fee when **Post to a clearing account** is on, see [setup-stripe.md](setup-stripe.md), "How it posts").

See [setup-stripe.md](setup-stripe.md) and [setup-paypal.md](setup-paypal.md) for the other providers — any combination can be enabled. The notes there on who can change these settings, where the pay link works and USD-only invoices apply to Square too.

---

## Prerequisites

- A Square account ([squareup.com](https://squareup.com))
- Slowbooks Pro running and accessible

---

## Step 1: Get sandbox credentials

1. Log in to the [Square Developer Console](https://developer.squareup.com/apps)
2. Create an application (e.g. "SlowBooks")
3. On the app's **Credentials** page, with **Sandbox** selected at the top, copy the **Sandbox Access Token**; on its **Locations** page, copy a sandbox **Location ID**

## Step 2: Configure Slowbooks

1. Open **Settings → Online Payments → Square** (an administrator's setting)
2. Set **Square Payments** to Enabled and **Environment** to `Sandbox`
3. Paste the **Access Token** and **Location ID**
4. Save

## Step 3 (server installs): Add a webhook

Skip this on desktop installs — webhooks can't reach `127.0.0.1`; payments record when the customer returns from Square, or via the **Check Payment Status** button on the invoice. (On a desktop or LAN install the pay link itself only opens on your own network, see the Stripe guide.)

1. In the Developer Console: your app → **Webhooks → Subscriptions → Add subscription**
2. URL: `https://your-server/api/payments/square/webhook` (HTTPS; Square refuses plain HTTP)
3. Choose an API version and subscribe to **payment.updated**
4. Copy the subscription's **Signature Key** into Slowbooks
5. **Important:** paste the exact same URL into Slowbooks' **Webhook Notification URL** field — Square signs each delivery over that exact URL string, so verification fails if they differ (e.g. behind a proxy).

## Step 4: Test in sandbox

1. In Slowbooks, open an unpaid invoice → **Copy Payment Link** → open in a private window
2. Click **Pay $…** (or **Pay with Square**) and pay with the sandbox test card `4111 1111 1111 1111`, CVV `111`, any future expiry and a valid ZIP such as `94103` (a USD sandbox payment needs a real-looking postal code; CVV `911` forces a decline)
3. On return you should see "Payment received — thank you!"
4. Verify: invoice Paid, payment row (method `square`), journal entry posted

## Going live

Switch to the app's **Production** tab, copy the production Access Token and a real Location ID, set **Environment** to `Production`, and (server installs) recreate the webhook subscription in production mode with its own signature key.

## Notes

- Amounts are charged in USD for the invoice's balance due at checkout time.
- Square payment links carry no metadata, so SlowBooks matches payments to invoices by the **order id** stored when the checkout was created. Each press of Pay makes a new Square checkout and only the latest order id is kept, so a payment made on an older Square page is answered "invoice not found" and not recorded: record that one by hand.
- Refunds are issued from the Square dashboard. In SlowBooks, void the online payment for a full refund (the posting reverses, through the clearing account when that is on) or enter a credit memo for a partial one.

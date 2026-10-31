# PayPal Online Payments Setup

Accept PayPal payments on invoices alongside (or instead of) Stripe. When PayPal is enabled, the public invoice pay page shows a **Pay $amount** button (**Pay with PayPal** when more than one provider is on); the customer approves on PayPal's hosted page, the payment is captured on return, and it records in SlowBooks with the same journal entries as any online payment (DR Undeposited Funds / CR Accounts Receivable, or the clearing account and fee when **Post to a clearing account** is on).

See [setup-stripe.md](setup-stripe.md) for the Stripe equivalent — both can be enabled at once. The notes there on who can change these settings, where the pay link works and USD-only invoices apply to PayPal too.

---

## Prerequisites

- A PayPal Business account ([paypal.com/business](https://www.paypal.com/business))
- Slowbooks Pro running and accessible

---

## Step 1: Create a REST API app

1. Log in to the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/)
2. Go to **Apps & Credentials**
3. Start in **Sandbox** mode (toggle at the top) for initial testing
4. Click **Create App**, name it (e.g. "Slowbooks"), and create it
5. Copy the **Client ID** and **Secret** for the app

## Step 2: Configure Slowbooks

1. Open **Settings → Online Payments → PayPal** (an administrator's setting)
2. Set **PayPal Payments** to Enabled
3. Set **Environment** to `Sandbox` (switch to `Live` after testing)
4. Paste the **Client ID** and **Client Secret**
5. Save

## Step 3 (server installs): Add a webhook

Skip this on desktop installs — webhooks can't reach `127.0.0.1`; payments record automatically when the customer returns from PayPal, or via the **Check Payment Status** button on the invoice. (On a desktop or LAN install the pay link itself only opens on your own network, see the Stripe guide.)

1. In the developer dashboard, open your app → **Webhooks** → **Add Webhook**
2. URL: `https://your-server/api/payments/paypal/webhook` (PayPal delivers to HTTPS on port 443 only)
3. Subscribe to the event **Payment capture completed** (`PAYMENT.CAPTURE.COMPLETED`)
4. Save, then copy the **Webhook ID** into Slowbooks settings

## Step 4: Test in sandbox

1. Use the default sandbox **personal** (buyer) account PayPal made for you under **Testing Tools → Sandbox Accounts**, or create one
2. In Slowbooks, open an unpaid invoice → **Copy Payment Link** → open it in a private window
3. Click **Pay with PayPal**, log in with the sandbox buyer account, approve
4. On return you should see "Payment received — thank you!"
5. Verify in Slowbooks: the invoice is Paid, a payment row exists (method `paypal`), and the journal entry posted

## Going live

Switch the app credentials to the **Live** tab in the developer dashboard, paste the live Client ID/Secret into Slowbooks, set **Environment** to `Live`, and (server installs) recreate the webhook against the live app.

## Notes

- Amounts are charged in USD for the invoice's balance due at checkout time.
- Approval and capture are separate steps at PayPal; Slowbooks captures on the customer's return (idempotently), and the webhook is a second, redundant recording path on server installs.
- Refunds are issued from the PayPal dashboard. In SlowBooks, void the online payment for a full refund (the posting reverses, through the clearing account when that is on) or enter a credit memo for a partial one.

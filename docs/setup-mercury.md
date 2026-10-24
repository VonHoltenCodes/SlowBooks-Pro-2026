# Mercury bank feed

SlowBooks reads posted deposits and withdrawals from Mercury into Banking's
**To review** list. This tracks incoming payments to your business alongside
its other bank activity. The connector only reads Mercury; it cannot send money.

## Connect

1. In Mercury, open **Settings → API Tokens**, create a **Read Only** token,
   and copy it. See [Mercury's token guide](https://docs.mercury.com/docs/getting-started).
2. In SlowBooks, sign in as an administrator and open **Banking**. In
   **Bank Feed (Mercury)**, paste the token and click **Connect Mercury**.
3. Create a bank account with **+ New Bank Account** if needed. Map each Mercury
   account to its corresponding SlowBooks bank account. The company books must
   use USD; each Mercury account needs a separate bank feed linked to a bank
   ledger account.
4. Choose **Import transactions posted since** and click **Sync Mercury**.
   The initial date is 90 days ago. Choose an earlier date for older history.
5. Open the bank account's **To review** list. Match deposits to payments you
   have already recorded, or review their category and add them to the books.
   Importing a deposit does not automatically mark a customer invoice paid.

Sync runs when you click **Sync Mercury**. Each sync rechecks the selected date
range, follows every page, and skips Mercury transaction IDs already imported.
It filters by posting date so a transaction created earlier can still appear
when it posts. Pending, failed, cancelled and blocked transactions are not
imported. Reversed transactions produce a warning for manual reconciliation;
the connector does not change existing ledger entries or remove imported rows.

Use one source for an account: Mercury IDs do not identify duplicates from
SimpleFIN or a CSV/OFX export. **Refresh accounts** discovers newly available
Mercury accounts and updates displayed bank balances. The register balance
comes from your ledger; connecting does not create an opening balance.

## Credentials and connectivity

The token is encrypted using the company's settings encryption key and masked
in settings responses. Connecting and disconnecting require an administrator;
bookkeepers can map and sync, and read-only users can view the feed status.
Disconnecting forgets the token and mapping while keeping imported transactions.
Reconnect and map to the same bank feed to continue without duplicate imports.

Manual sync sends requests from the SlowBooks server to `https://api.mercury.com`.
It needs no inbound webhook, public server address, IP allowlist, or Compose
environment variable. Keep the browser-to-SlowBooks connection protected when
entering the token, as for other credentials in the app.

The connector uses Mercury's [accounts endpoint](https://docs.mercury.com/reference/getaccounts)
and [transaction feed](https://docs.mercury.com/reference/listtransactions).
If Mercury refuses a token, check its permissions or disconnect and connect
with a fresh read-only token. For a rate limit or temporary network failure,
wait and sync again; repeated successful syncs skip previously imported IDs.

## Automatic balance updates with webhooks

Open SlowBooks at your public HTTPS address. Under **Banking → Bank Feed
(Mercury) → Balance webhooks**, the app shows your webhook URL and registration
JSON. The endpoint is:

```text
https://your-slowbooks-host/api/mercury/webhook
```

Register that URL with Mercury for `checkingAccount.balance.updated` and
`savingsAccount.balance.updated`. Webhook registration requires Mercury
permission to manage webhooks; the token saved for bank sync can stay read-only.
For example, use a separately authorized token in your shell:

```bash
curl --request POST 'https://api.mercury.com/api/v1/webhooks' \
  --header "Authorization: Bearer $MERCURY_WEBHOOK_ADMIN_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{
    "url": "https://your-slowbooks-host/api/mercury/webhook",
    "eventTypes": [
      "checkingAccount.balance.updated",
      "savingsAccount.balance.updated"
    ]
  }'
```

Save the returned `secret` in **Webhook signing secret** in SlowBooks. Mercury
returns it when creating the endpoint; SlowBooks encrypts it and never displays
it again. Use the returned endpoint `id` to send a verification event:

```bash
curl --request POST "https://api.mercury.com/api/v1/webhooks/$MERCURY_WEBHOOK_ID/verify" \
  --header "Authorization: Bearer $MERCURY_WEBHOOK_ADMIN_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"eventType":"checkingAccount.balance.updated"}'
```

**Last webhook received** reports the most recent verified delivery, including
test events. Only events for connected checking and savings accounts update
balances. The Banking table shows **Current balance**, **Available**, and
**Incoming transfers**; a dash means an incoming-transfer balance has not been
reported. While Banking is open, it reads the locally stored balances every 15
seconds without replacing your form inputs or calling Mercury.

The receiver verifies Mercury's HMAC-SHA256 signature over the raw request body,
rejects signatures outside a five-minute window, and tracks balance versions per
field. Repeated or out-of-order events cannot overwrite newer values. Manual
sync and refresh preserve updates that arrive after their API snapshot starts.
Keep the server clock synchronized for signature verification.

These events update bank balance snapshots only. **Sync Mercury** still imports
transactions for review; webhook delivery does not post income, change the
ledger balance, or mark invoices paid. The endpoint serves the company currently
open in this SlowBooks instance, using that company's signing secret and known
Mercury accounts.

**Disable webhooks** clears the local signing secret; also pause or delete the
endpoint in Mercury to stop deliveries. Disconnecting the Mercury feed clears
its webhook configuration as well. Mercury does not offer webhook delivery in
its sandbox. See [Mercury's webhook guide](https://docs.mercury.com/reference/webhooks)
for registration, verification, and delivery behavior.

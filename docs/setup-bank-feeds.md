# Bank Feeds (SimpleFIN) — pull transactions straight from your bank

SlowBooks can sync bank transactions automatically using
[SimpleFIN](https://www.simplefin.org/), an open protocol where **you**
hold the bank credential — not SlowBooks, and not any middleman server we
run. There is no SlowBooks cloud in the loop: your machine talks directly
to your SimpleFIN bridge over HTTPS.

## How it works

1. You sign up for a SimpleFIN bridge — the reference one is
   [bridge.simplefin.org](https://bridge.simplefin.org/) (about **$1.50/month**,
   paid by you to them; SlowBooks takes nothing). Any other service that
   issues SimpleFIN setup tokens works the same way: SlowBooks follows the
   claim URL inside the token, whatever server it names, as long as it is
   HTTPS and not a private or local address.
2. On the bridge's site, you connect your bank(s). The bridge handles the
   bank login, MFA, and aggregation.
3. The bridge gives you a one-time **setup token**. Paste it into
   **Banking → Bank Feeds (SimpleFIN) → Connect**. Connecting, mapping and
   disconnecting are an administrator's; a bookkeeper runs the syncs.
4. SlowBooks exchanges the token once for a permanent read-only access
   credential, stored encrypted-at-rest in your company file and never
   shown again, anywhere.
5. Map each bridge account: the **Imports into** list offers every bank and
   credit-card account in your chart, and picking one makes the feed for it
   when you save the mapping (since 2.31.1). Tick **Reverse signs** on a feed
   whose bridge reports money out as positive. Then click **Sync Now**
   whenever you want fresh transactions.

Synced transactions land in the same review flow as OFX/CSV imports:

- **Duplicates are skipped automatically** (dedup on the bridge's stable
  transaction id, same mechanism as OFX FITIDs) — syncing twice never
  double-imports.
- **Matches are found on arrival** — a line whose amount the ledger already
  has (same side, within five days) is linked to that posting and cleared.
- **Bank rules apply on arrival** — anything your rules match gets a suggested
  category. Nothing posts by itself: the account's *To review* list is where
  you **Add** a line (posts it with the category), **Match** it, or **Exclude**
  it; *Add all categorised* posts the rule-suggested ones in one click.
- Pending transactions are ignored until they post (their ids aren't
  stable before that).

The first sync reaches back roughly three months; every later sync re-checks a 7-day
overlap window before your last sync so late-posting transactions are
never missed.

**Older history.** **Fetch older history…** reaches back 3, 6 or 12 months
(the API takes `POST /api/simplefin/sync {"history_months": N}`, up to 24).
How much there is depends on your SimpleFIN provider: the SimpleFIN Bridge
keeps about 90 days, while others, such as BankSync, keep up to a year. The
Bridge refuses a request spanning more than 90 days, so SlowBooks asks for
the range in slices of 85 days, oldest first; transactions already imported
are skipped, and the rest wait in *To review* like any other line, where
anything from before your books began can be excluded.

## Trying it without a bank

SimpleFIN publishes a public demo. Generate a demo setup token at
[beta-bridge.simplefin.org/info/developers](https://beta-bridge.simplefin.org/info/developers)
and paste it in — you'll get a fake "SimpleFIN Savings" account with
sample transactions to play with. Disconnect afterward to clear it.

## Notes & troubleshooting

- **Setup tokens are single-use.** If a connect attempt fails and the
  token was consumed, generate a fresh token on the bridge site.
- **Desktop installs work fully** — the sync is outbound HTTPS polling;
  no webhooks, no ports to open, no server required.
- **One source per account.** A feed's transaction ids don't match an OFX
  or CSV export of the same account, so importing both doubles the lines;
  pick the feed or the file.
- **Not too often.** The SimpleFIN Bridge asks for 24 or fewer requests a
  day per token and can disable a token that goes well past that. Each Sync
  Now is one request; fetching 12 months of older history is about five.
- **Payouts from Stripe, PayPal or Square** show a **Payout…** button in To
  review when the clearing-account option is on ([setup-stripe.md](setup-stripe.md)).
- **Disconnecting** (Banking → Bank Feeds → Disconnect) forgets the
  credential, mapping, and cache. Transactions already imported stay in
  your registers.
- **Coverage** is whatever your bridge supports — the reference bridge
  covers most US and Canadian institutions.
- If a mapped account stops appearing in syncs, the bank connection
  usually needs re-authentication **on the bridge's site**, not in
  SlowBooks.

## Why SimpleFIN and not Plaid?

Plaid requires the application developer to hold API credentials and run
a server that proxies every user's bank data. That's the opposite of the
SlowBooks design (your data, your machine, no phone-home). SimpleFIN
inverts the relationship: each user brings their own credential, and the
data flows straight to their computer.

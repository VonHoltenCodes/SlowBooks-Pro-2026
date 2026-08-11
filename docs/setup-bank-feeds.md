# Bank Feeds (SimpleFIN) — pull transactions straight from your bank

SlowBooks can sync bank transactions automatically using
[SimpleFIN](https://www.simplefin.org/), an open protocol where **you**
hold the bank credential — not SlowBooks, and not any middleman server we
run. There is no SlowBooks cloud in the loop: your machine talks directly
to your SimpleFIN bridge over HTTPS.

## How it works

1. You sign up for a SimpleFIN bridge — the reference one is
   [bridge.simplefin.org](https://bridge.simplefin.org/) (about **$1.50/month**,
   paid by you to them; SlowBooks takes nothing).
2. On the bridge's site, you connect your bank(s). The bridge handles the
   bank login, MFA, and aggregation.
3. The bridge gives you a one-time **setup token**. Paste it into
   **Banking → Bank Feeds (SimpleFIN) → Connect**.
4. SlowBooks exchanges the token once for a permanent read-only access
   credential, stored encrypted-at-rest in your company file and never
   shown again (Settings displays `********`).
5. Map each bridge account to a SlowBooks bank account, then click
   **Sync Now** whenever you want fresh transactions.

Synced transactions land in the same review flow as OFX/CSV imports:

- **Duplicates are skipped automatically** (dedup on the bridge's stable
  transaction id, same mechanism as OFX FITIDs) — syncing twice never
  double-imports.
- **Bank rules apply on arrival** — anything your rules match is
  auto-categorized, the rest waits in the register as unmatched.
- Pending transactions are ignored until they post (their ids aren't
  stable before that).

The first sync reaches back roughly three months; every later sync re-checks a 7-day
overlap window before your last sync so late-posting transactions are
never missed.

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

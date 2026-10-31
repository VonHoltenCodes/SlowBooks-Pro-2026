# Contact support from inside the app

SlowBooks Pro has a built-in **Contact support** form. Open it from the top
bar (**Contact support**, beside **About**), from the **About** dialog, or
from the error dialog the desktop launcher shows when the app can't start.

## How it works

The form has three fields: *What happened*, *What you expected*, and an
optional *Can we reply to you at this address?* (prefilled from the company
file's email when one is set). **Send** opens **your own mail program** with
the message already written, addressed to `support@slowbookspro.com`.

**The support form makes no network call of its own.** It never emails
anything, and ships no credentials — it only hands a ready-to-send message to
the mail client you already use. Nothing leaves your machine unless you press
Send in your mail program. (The desktop app's one call of its own is the
update check against dl.slowbookspro.com, which Settings can turn off.) Every
sign-in can use the form, a read-only or accountant one included.

Because a `mailto:` link can't attach files, attach screenshots or the log
file in your mail program if they help.

## What is sent

The body carries your text plus:

- an **environment block** — version and commit, platform, Python version,
  deployment kind (native / Server Edition / self-managed, which covers
  Docker and a plain source run), and the browser when
  relevant; and
- the **last ~50 lines of the log** (`launcher.log` on a native desktop
  install), **redacted before it is placed**: emails, personal and company
  names, dollar amounts, account and routing numbers, street addresses, phone
  numbers and tax IDs become bracketed tokens like `[email]`, `[name]` and
  `[amount]`. Timestamps, log levels and stack traces stay readable.

> Redaction is **best-effort** — it catches the common shapes of private data,
> not every possible one. **Read the message before you send it** and remove
> anything sensitive it missed.

A **Server Edition started from the source tree, and self-managed installs**
(Docker, or a plain source run) log to the console (your terminal or the
container logs), so there is no file for the form to read; the form says so
and asks you to copy the recent server output into your email yourself. The
installed Windows app's `--serve-lan` writes `launcher.log` like the desktop
app, and the form reads it.

Some mail clients truncate a long `mailto:` body, so the log tail is trimmed
to keep the body short. Use **Copy diagnostics** to copy the full,
untrimmed block to the clipboard and paste it into your email yourself.

On a native desktop install, **Reveal log file** opens the log's folder in
Finder, Explorer or your Linux file manager. The button is hidden where there
is no local folder to open.

> Support emails, including any logs you attach, are stored in our private
> project tracker (Basecamp).

## Security issues

Do **not** use this form for security vulnerabilities. Report those privately
through
[GitHub Security Advisories](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/security/advisories/new)
(see [SECURITY.md](../SECURITY.md)).

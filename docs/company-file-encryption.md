# Encrypting the company file

A desktop company is one file. Plain, it is a SQLite database anyone with
the file can read: a stolen laptop, a shared drive, a backup copied to a
stick. Encrypted, nothing readable is ever written to disk: every page is
encrypted under the database driver (SQLCipher), the file opens only with
its key, and every backup of it is a copy of the encrypted bytes.

It is **off by default**. For a company whose customer list is private, a
clinic or a law office, turn it on the first day.

## Turning it on

**Settings → Company File Encryption → Turn on…**, or the *Encrypt this
company file* box on first-run setup. An administrator types the company's
name to confirm, and their own password. SlowBooks Pro then:

1. makes a **recovery key**: eight groups of four letters and digits, shown
   once, with *Copy* and *Save as a text file*;
2. encrypts the company file under the key that recovery key derives;
3. wraps that key for the administrator's password, in a small keyring
   file beside the company file (`<company>.db.keyring`, which holds
   nothing secret: each wrapping opens only with its password).

Nobody else should be using the company while the file is rewritten; it
takes a few seconds for most books.

**Keep the recovery key where the backups are not**: a safe, a password
manager, a sealed envelope. It opens the file, and every backup of it, on
any machine, if every administrator's password is lost; and anyone holding
it can read the books. SlowBooks Pro keeps no copy.

**Backups made before you turned it on are still plain**, readable by anyone
with the file. Settings counts them and says so; delete them once a newer,
encrypted backup exists.

## Opening the books from then on

The company picker asks for an **administrator's sign-in** when it opens
an encrypted company, or takes the **recovery key** instead (*Use the
recovery key instead*). The administrator who turned encryption on unlocks
it at once. Every other administrator unlocks it after their next sign-in
inside the books, which wraps the key for their password too; Settings
lists who can unlock. Other roles never hold the key: a bookkeeper or an
accountant signs in once an administrator has opened the books.

An administrator who is removed, demoted, or whose password an
administrator reset, no longer unlocks the file; a reset one does again
once they have set a password of their own and signed in.

## A new recovery key

**Settings → Company File Encryption → New recovery key…** re-keys the
file in place under a new recovery key, shown once, and retires the old
one. The administrator who asked unlocks the file at once; every other
administrator after their next sign-in (until then the picker tells them
so, and the recovery key or another administrator opens the books).
Backups made before the change open with the old recovery key only, so
keep it until those backups are gone.

## Backups and restores

A backup of an encrypted company is encrypted under the same key; the
backups page works as before. Restoring one of this company's own
backups needs nothing more. Restoring a backup made under **another
key** (another recovery key, or another install's secret) asks for the
recovery key it was made under; the backup file itself is never changed.

## Turning it off

**Turn off…** (an administrator, the company's name typed, and their own
password) rewrites the file as plain SQLite and retires the recovery key.
Backups made while encryption was on still open with that retired recovery
key only, so keep it until those backups are gone.

## Server Edition

A server has nobody at the picker, so it unlocks from an operator-set
credential at startup. **Set the secret first, then turn encryption on**:
put `COMPANY_FILE_ENCRYPTION_SECRET` in the server's `.env`
(`C:\ProgramData\SlowBooksPro\.env` for a Server Edition install, the
place `PAYROLL_ENCRYPTION_SECRET` lives; the Docker `.env` otherwise), stop
and start the server, and only then turn encryption on under Settings: the
file is keyed from the secret and no recovery key is made. Turned on before
the secret is set, the company gets a recovery key instead, and the next
server start refuses the file. A file encrypted on a desktop under a
recovery key does not open headless either: open it at the window, turn
encryption off, and turn it on again under the secret.

Companies on **PostgreSQL** are the database host's job (PostgreSQL's own
disk encryption, or the volume it sits on), and the page says so.

## What is and is not covered

Covered: the company file on disk, its WAL and sidecars, every backup of
it. Not covered: a report or export you saved (a PDF, a CSV, an IIF file)
is a plain file where you put it, and the books in a running SlowBooks
Pro are readable by whoever is signed in. The technical notes are in
[security-hardening.md](security-hardening.md).

# QuickBooks Online export with account numbers, as a sample

Invented books in the layout of QuickBooks Online's Export data bundle for a
company that has **account numbers** switched on (Settings → Advanced →
Chart of accounts → Enable account numbers), beside `qbo_export/`, which has
none. The statements put each account's number on its own row ("4000
Memberships", then "Family Memberships" indented under it); the Journal, the
Trial Balance and the General Ledger write the account's own number in front
of the whole path and leave the parents' off ("Memberships:Family
Memberships", "1010 Operating:Operating Checking", "2201 Long-term loans:Bank
loan"). A card the company deleted, "2101 Old Store Card (deleted)", has
history that nets to nothing: it is off the Balance Sheet and the Trial
Balance, and only the General Ledger lists it, between the other
liabilities.

`General_ledger.csv` is in the export's real layout: the account headings in
the first column, sub-accounts' indented under their parent's, a "Total for"
row after each (and "with subs" after a parent's children), and the
transactions in the Date column onward with the first column blank.

`Pinecrest_Account_List.csv` is the Account List report (Reports → Account
List → Export to Excel), a download of its own beside the zip: Account #,
Account Name as the full path, Type, Detail type, Description and the
balance; parents may follow their children; a deleted account is not on it,
and "Prepaid rent" has no history anywhere else.

The sample company is **Pinecrest Studio Rentals**; the real export the
numbering was built against is a user's books and is not here.

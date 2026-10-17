# QuickBooks Desktop Transaction Detail by Account, as a sample

`Transaction_Detail_by_Account.csv` is invented books in the exact layout of QuickBooks Desktop's Transaction
Detail by Account saved as CSV: 28 columns, the labels before Type, an empty column between every field, a heading
row per account with sub-accounts one column in, QuickBooks' Total rows and the TOTAL row, the Clr check glyph in
the Windows code page, CRLF line ends. `make_ledger_fixture.py` writes it. The real report the importer was built
against is a bookkeeper's client's books and is not here.

The sample company is **Morden Bookkeeping**, named for Kim Morden Bookkeeping, whose client's export this
importer was built on and who said yes to the name.

`Time_by_Name_by_Job.xlsx` and `Time_by_Job_by_Name.xlsx` (written by `make_time_fixture.py --both`) are invented
timesheets in the exact layout of QuickBooks Desktop's Time by Name by Job detail report saved as Excel: four label
columns (the person in the second, the customer:job in the third; the other way round in the by-Job file), then Date,
Item, Billing Status, Duration and Notes, a "Total <label>" row under each group at both levels and a TOTAL row. The
importer was built against a real consultancy's report (half a year, 6,277 entries, every total matched); that file
stays private. The sample company is Northline Surveying; its people, customers, jobs and items are invented.

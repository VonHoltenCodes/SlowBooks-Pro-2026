"""Writes Transaction_Detail_by_Account.csv: invented books in the exact layout
of QuickBooks Desktop's Transaction Detail by Account exported to CSV (28
columns, the labels before Type, an empty column between every field, a
heading row per account with sub-accounts one column in, QuickBooks' "Total"
rows and the TOTAL row, the Clr check glyph in the Windows code page, CRLF).
Run it again when the transactions below change. The sample company is Morden
Bookkeeping, named for Kim Morden Bookkeeping, who brought the report."""

from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

D = Decimal
# (type, date, num, [(account path, name, memo, debit, credit)]) ; a line's
# num may be overridden per line (QuickBooks prints a journal entry's number
# on one line only now and then)
TXNS = [
    (
        "Transfer",
        "01/01/2024",
        "",
        [
            (
                "Operating Checking 4321",
                "",
                "Beginning balance for this acct on 1-1-24",
                D("1000.00"),
                D(0),
            ),
            (
                "Retained Earnings",
                "",
                "Beginning balance for this acct on 1-1-24",
                D(0),
                D("1000.00"),
            ),
        ],
    ),
    (
        "Deposit",
        "01/05/2024",
        "",
        [
            ("Operating Checking 4321", "Card Processor", "Deposit", D("300.00"), D(0)),
            ("Card Clearing", "Card Processor", "Deposit", D(0), D("300.00")),
        ],
    ),
    (
        "Deposit",
        "01/05/2024",
        "",
        [
            ("Operating Checking 4321", "Walk-in", "Deposit", D("120.00"), D(0)),
            ("Service Income", "Walk-in", "Deposit", D(0), D("120.00")),
        ],
    ),
    (
        "Check",
        "01/08/2024",
        "1001",
        [
            ("Operating Checking 4321", "Office Depot", "", D(0), D("75.00")),
            ("Office Expense:Supplies", "Office Depot", "Paper", D("45.00"), D(0)),
            (
                "Office Expense:Software",
                "Office Depot",
                "Scheduling app",
                D("30.00"),
                D(0),
            ),
        ],
    ),
    (
        "General Journal",
        "01/10/2024",
        "7",
        [
            ("Card Clearing", "", "Card sales, week 2", D("485.00"), D(0)),
            ("Card Fees", "", "Card sales, week 2", D("15.00"), D(0)),
            ("Service Income", "", "Card sales, week 2", D(0), D("500.00")),
        ],
    ),
    (
        "Transfer",
        "01/15/2024",
        "",
        [
            ("Owner Draw", "", "Funds Transfer", D("200.00"), D(0)),
            ("Operating Checking 4321", "", "Funds Transfer", D(0), D("200.00")),
        ],
    ),
    (
        "Check",
        "01/31/2024",
        "1002",
        [
            (
                "Rent",
                "Harbor Properties",
                "January rent",
                D("1,000.00".replace(",", "")),
                D(0),
            ),
            (
                "Operating Checking 4321",
                "Harbor Properties",
                "January rent",
                D(0),
                D("1000.00"),
            ),
        ],
    ),
    (
        "General Journal",
        "02/27/2024",
        "8",
        [
            ("Card Fees", "", "Chargeback fee", D("10.00"), D(0)),
            (
                "Card Clearing",
                "",
                "Chargeback fee",
                D(0),
                D("10.00"),
                "",
            ),  # number on one line only
        ],
    ),
]
ORDER = [
    "Operating Checking 4321",
    "Card Clearing",
    "Owner Draw",
    "Retained Earnings",
    "Service Income",
    "Card Fees",
    "Office Expense",
    "Office Expense:Software",
    "Office Expense:Supplies",
    "Rent",
]
COLS = 28
TYPE, DATE, NUM, ADJ, NAME, MEMO, CLR, SPLIT, DEBIT, CREDIT, ORIG, BAL = range(5, 28, 2)


def money(v: Decimal) -> str:
    return "" if v == 0 else f"{v:,.2f}"


def row(**cells) -> list[str]:
    r = [""] * COLS
    for k, v in cells.items():
        r[int(k[1:])] = v
    return r


lines = OrderedDict((path, []) for path in ORDER)
for ttype, day, num, parts in TXNS:
    for part in parts:
        path, name, memo, dr, cr = part[:5]
        line_num = part[5] if len(part) > 5 else num
        others = [p[0] for p in parts if p[0] != path]
        split = others[0].split(":")[-1] if len(others) == 1 else "-SPLIT-"
        lines[path].append(
            (
                ttype,
                day,
                line_num,
                name,
                memo,
                split,
                dr,
                cr,
                sum(p[3] - p[4] for p in parts if p[0] == path),
            )
        )

out = [
    row(
        **{
            f"c{TYPE}": "Type",
            f"c{DATE}": "Date",
            f"c{NUM}": "Num",
            f"c{ADJ}": "Adj",
            f"c{NAME}": "Name",
            f"c{MEMO}": "Memo",
            f"c{CLR}": "Clr",
            f"c{SPLIT}": "Split",
            f"c{DEBIT}": "Debit",
            f"c{CREDIT}": "Credit",
            f"c{ORIG}": "Original Amount",
            f"c{BAL}": "Balance",
        }
    )
]
grand_d = grand_c = D(0)


def section(path: str, depth: int) -> tuple[Decimal, Decimal]:
    out.append(row(**{f"c{depth}": path.split(":")[-1]}))
    tot_d = tot_c = D(0)
    balance = D(0)
    for ttype, day, num, name, memo, split, dr, cr, orig in lines.get(path, []):
        balance += dr - cr
        tot_d, tot_c = tot_d + dr, tot_c + cr
        out.append(
            row(
                **{
                    f"c{TYPE}": ttype,
                    f"c{DATE}": day,
                    f"c{NUM}": num,
                    f"c{NAME}": name,
                    f"c{MEMO}": memo,
                    f"c{CLR}": "Ö",
                    f"c{SPLIT}": split,
                    f"c{DEBIT}": money(dr),
                    f"c{CREDIT}": money(cr),
                    f"c{ORIG}": f"{orig:,.2f}",
                    f"c{BAL}": f"{balance:,.2f}",
                }
            )
        )
    for child in [
        p
        for p in ORDER
        if p.startswith(path + ":") and p.count(":") == path.count(":") + 1
    ]:
        d, c = section(child, depth + 1)
        tot_d, tot_c = tot_d + d, tot_c + c
    out.append(
        row(
            **{
                f"c{depth}": "Total " + path.split(":")[-1],
                f"c{DEBIT}": f"{tot_d:,.2f}",
                f"c{CREDIT}": f"{tot_c:,.2f}",
                f"c{BAL}": f"{tot_d - tot_c:,.2f}",
            }
        )
    )
    return tot_d, tot_c


for path in [p for p in ORDER if ":" not in p]:
    d, c = section(path, 1)
    grand_d, grand_c = grand_d + d, grand_c + c
out.append(
    row(
        c0="TOTAL",
        **{
            f"c{DEBIT}": f"{grand_d:,.2f}",
            f"c{CREDIT}": f"{grand_c:,.2f}",
            f"c{BAL}": "0.00",
        },
    )
)
out.append(row())


def cell(v: str) -> str:
    return f'"{v}"' if ("," in v or '"' in v) else v


text = "\r\n".join(",".join(cell(v) for v in r) for r in out) + "\r\n"
Path(__file__).with_name("Transaction_Detail_by_Account.csv").write_bytes(
    text.encode("cp1252")
)
print(len(out), "rows")

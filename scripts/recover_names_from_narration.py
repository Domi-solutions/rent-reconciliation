#!/usr/bin/env python3
"""
Recover payer names from the narration already stored on each transaction.

When a scanned statement is read, the payer is often inside the description
rather than in a column of its own. The vision parser took the empty column at
face value and stored no name, but it kept the description — so the name was
never lost, only left unread. That matters because the statement PDF itself can
be gone: the rows, and their narrations, are still in the database.

A transaction with no name cannot be matched to a tenant, so the payment sits
unassigned and the tenant reads as owing money they have already paid.

Only ever improves a name: fills a blank, or replaces a narration dump — the
whole description stored where a name belongs — with the name read out of it.
A row that already holds a clean name is left alone.

    ./venv/bin/python scripts/recover_names_from_narration.py
    ./venv/bin/python scripts/recover_names_from_narration.py --execute
    ./venv/bin/python scripts/recover_names_from_narration.py --statement STMT-2D0CBE02
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection  # noqa: E402
from src.parsers.pdf_parser import _NOT_A_NAME, name_from_narration  # noqa: E402


def looks_like_a_dump(value):
    """True when a whole narration has been stored where a name belongs."""
    if not value:
        return False
    words = value.upper().split()
    if len(words) > 5:
        return True
    # Routing words never appear in a person's name.
    return any(w.strip(",.:;/-'") in _NOT_A_NAME for w in words)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--statement', help='limit to one statement id')
    ap.add_argument('--execute', action='store_true', help='apply (default is a preview)')
    ap.add_argument('--limit', type=int, default=60, help='rows to show in the preview')
    args = ap.parse_args()

    query = """
        SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name,
               bt.raw_text, bs.id AS statement_id, bs.filename, bs.bank_format
        FROM bank_transactions bt
        JOIN bank_statements bs ON bt.statement_id = bs.id
        WHERE bt.txn_type = 'PAYBILL_CREDIT'
    """
    params = ()
    if args.statement:
        query += " AND bs.id = ?"
        params = (args.statement,)

    filled, cleaned, no_source, already_good = [], [], 0, 0
    with get_connection() as conn:
        for row in conn.execute(query, params):
            current = (row['sender_name'] or '').strip()
            # The description is the source. Some rows kept it only in the name
            # column, so fall back to that rather than give up on the row.
            source = (row['raw_text'] or '').strip() or (current if looks_like_a_dump(current) else '')
            if not source:
                if not current:
                    no_source += 1
                else:
                    already_good += 1
                continue

            recovered = name_from_narration(source, row['mpesa_ref'])
            if not recovered:
                if not current:
                    no_source += 1
                else:
                    already_good += 1
                continue

            if not current:
                filled.append((row, current, recovered))
            elif looks_like_a_dump(current) and recovered.upper() != current.upper():
                cleaned.append((row, current, recovered))
            else:
                already_good += 1

        print(f"\n  {len(filled)} blank name(s) can be filled")
        print(f"  {len(cleaned)} narration dump(s) can be reduced to a name")
        print(f"  {already_good} row(s) already hold a usable name")
        print(f"  {no_source} row(s) have nothing to read a name from\n")

        for label, group in (('FILL ', filled), ('CLEAN', cleaned)):
            for row, current, recovered in group[:args.limit]:
                shown = (current[:44] + '…') if len(current) > 45 else (current or '(blank)')
                print(f'    {label} {row["txn_date"] or "—":<12}{str(row["mpesa_ref"] or "—"):<13}'
                      f'{float(row["amount"] or 0):>10,.0f}  {shown:<46} ->  {recovered}')
            if len(group) > args.limit:
                print(f'    ... and {len(group) - args.limit} more')

        if not args.execute:
            print('\n  Preview only — nothing written. Re-run with --execute to apply.')
            return

        for row, _, recovered in filled + cleaned:
            conn.execute("UPDATE bank_transactions SET sender_name = ? WHERE id = ?",
                         (recovered, row['id']))
        total = len(filled) + len(cleaned)
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ('sender_names_recovered', 'bank_transaction', args.statement or '-',
             f'Recovered {total} payer name(s) from stored narrations', 'admin')
        )
        print(f'\n  Updated {total} transaction(s).')
        print('  Re-open the worksheet — named payers can now be matched to tenants.')


if __name__ == '__main__':
    main()

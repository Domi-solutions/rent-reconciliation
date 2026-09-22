#!/usr/bin/env python3
"""
Repair sender names on already-stored bank transactions.

Transactions parsed before the sender fix hold truncated names — "BRIAN
LEMAYIAN" for "BRIAN LEMAYIAN LOLKERRA" — or no name at all. A truncated name
loses the match, because enrich_with_suggestions() needs two name tokens to
overlap, so the payment sits unassigned and the tenant reads as owing money
they have paid.

Re-parsing the statement through the app would fix it, but that discards and
rebuilds the rows and, for a scanned statement, spends another vision call.
This updates the sender name in place, matched on the M-Pesa reference, and
touches nothing else.

    ./venv/bin/python scripts/fix_sender_names.py --pdf statement.pdf
    ./venv/bin/python scripts/fix_sender_names.py --pdf statement.pdf --execute

A names file may be given instead of a PDF, for a statement this machine
cannot parse — one "REFERENCE,NAME" per line.
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection  # noqa: E402


def names_from_pdf(path):
    from src.parsers.pdf_parser import parse_bank_statement
    result = parse_bank_statement(path)
    names = {}
    for txn in result['transactions']:
        if txn.reference and txn.sender:
            names[txn.reference.strip().upper()] = txn.sender.strip()
    print(f"  parsed {path}: {len(result['transactions'])} transactions, "
          f"{len(names)} with a reference and a name")
    return names


def names_from_file(path):
    names = {}
    with open(path, newline='') as handle:
        for row in csv.reader(handle):
            if len(row) >= 2 and row[0].strip():
                names[row[0].strip().upper()] = row[1].strip()
    print(f'  read {path}: {len(names)} reference/name pairs')
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pdf', action='append', default=[], help='statement PDF; repeat for several')
    ap.add_argument('--names', action='append', default=[], help='CSV of REFERENCE,NAME')
    ap.add_argument('--execute', action='store_true', help='apply (default is a preview)')
    args = ap.parse_args()

    if not args.pdf and not args.names:
        sys.exit('Give at least one --pdf or --names file.')

    names = {}
    for path in args.pdf:
        names.update(names_from_pdf(path))
    for path in args.names:
        names.update(names_from_file(path))
    if not names:
        sys.exit('No reference/name pairs found.')

    improved, unchanged, missing = [], 0, 0
    with get_connection() as conn:
        for ref, name in sorted(names.items()):
            row = conn.execute(
                "SELECT id, sender_name FROM bank_transactions WHERE UPPER(mpesa_ref) = ?", (ref,)
            ).fetchone()
            if not row:
                missing += 1
                continue
            current = (row['sender_name'] or '').strip()
            # Only ever lengthen a name: never replace a fuller record with a
            # shorter one, whatever order the sources are given in.
            if current == name:
                unchanged += 1
            elif len(name) > len(current):
                improved.append((row['id'], ref, current, name))
            else:
                unchanged += 1

        print(f'\n  {len(improved)} name(s) would improve · {unchanged} already good · '
              f'{missing} reference(s) not in the database\n')
        for _, ref, old, new in improved[:40]:
            print(f'    {ref:<14}{(old or "(blank)"):<26} ->  {new}')
        if len(improved) > 40:
            print(f'    ... and {len(improved) - 40} more')

        if not args.execute:
            print('\n  Preview only — nothing written. Re-run with --execute to apply.')
            return

        for txn_id, _, _, new in improved:
            conn.execute("UPDATE bank_transactions SET sender_name = ? WHERE id = ?", (new, txn_id))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ('sender_names_repaired', 'bank_transaction', '-',
             f'Repaired {len(improved)} sender name(s) from statement re-read', 'admin')
        )
        print(f'\n  Updated {len(improved)} transaction(s).')
        print('  Re-open the worksheet — names now match tenants and suggestions appear.')


if __name__ == '__main__':
    main()

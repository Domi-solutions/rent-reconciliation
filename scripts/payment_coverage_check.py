#!/usr/bin/env python3
"""
Read-only check: who is this payer, and which months does the database hold payments for?

Answers two questions before balances are rebuilt from bank statements:

  1. Is a given payer known anywhere — as a tenant (any status), a bank
     transaction sender, or in a reported M-Pesa message? Search by any part
     of a name or phone number.
  2. Month by month, per property: how many payments were recorded, how much,
     and which bank statements were uploaded covering that month. A month with
     tenants but no payments and no statement is a month the database never saw.
     Then, per tenant, which months in the range have no payment recorded.

Opens the database mode=ro and never imports the app, so the app's startup
migrations do not run against it. The same checks are on the admin page
/reports/lookup for the live database.

    ./scripts/download_prod_db.sh
    ./venv/bin/python scripts/payment_coverage_check.py --db data/dev.db \\
        --find "RUTH KANDUMA" --find 254759761866 \\
        --find "NICHOLAS MATHEKA" --find 254724780761 \\
        --from 2026-01 --to 2026-05
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.reports.payment_lookup import find_payer, month_coverage  # noqa: E402


def print_find(conn, term):
    print(f'\n── "{term}" ──')
    hits = find_payer(conn, term, all_orgs=True)
    for r in hits['tenants']:
        print(f"  TENANT   {r['name']} | {r['phone'] or '-'} | {r['property']} unit {r['unit_number']} | "
              f"{r['status']} | in {r['move_in_date'] or '?'} out {r['move_out_date'] or '-'}")
    for r in hits['bank']:
        print(f"  BANK     {r['sender_name']} | {r['n']} txns KES {r['total']:,.0f} | "
              f"{r['first']} → {r['last']} | assigned {r['assigned']} to unit(s) {r['units'] or '-'}")
    for r in hits['claims']:
        print(f"  CLAIM    {r['mpesa_ref']} KES {r['claimed_amount'] or 0:,.0f} | {r['status']} | "
              f"{(r['created_at'] or '')[:10]} | unit {r['unit_number']}")
    if not any(hits.values()):
        print('  not found anywhere — never a tenant, never seen on an uploaded statement, never reported')


def print_coverage(conn, first, last):
    for prop in conn.execute("SELECT id FROM properties ORDER BY name").fetchall():
        cov = month_coverage(conn, prop['id'], first, last)
        print(f"\n══ {cov['property']['name']} ({prop['id']}) ══")
        print(f"{'MONTH':<9}{'PAYMENTS':>9}{'KES':>13}{'PAYERS':>8}{'CHARGED':>13}  STATEMENTS UPLOADED COVERING THIS MONTH")
        for r in cov['months']:
            label = '; '.join(f"{s['filename']} ({s['period_start']}→{s['period_end']})"
                              for s in r['statements']) or 'NONE'
            print(f"{r['month']:<9}{r['payments']:>9}{r['total']:>13,.0f}{r['units_paid']:>8}"
                  f"{r['charged']:>13,.0f}  {label}")
        if cov['tenants']:
            print(f"\n  Per tenant — months in {first}..{last} with NO payment recorded on their unit")
        for t in cov['tenants']:
            gaps = t['missing']
            print(f"  {t['unit_number']:<6}{(t['name'] or '')[:28]:<29}{t['status']:<9}"
                  f"{'all months paid' if not gaps else 'missing: ' + ', '.join(g[5:] for g in gaps)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--db', required=True)
    ap.add_argument('--find', action='append', default=[], help='name fragment or phone; repeatable')
    ap.add_argument('--from', dest='first', default='2026-01')
    ap.add_argument('--to', dest='last', default='2026-05')
    a = ap.parse_args()
    try:
        conn = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f'cannot open {a.db}: {e}')
    conn.row_factory = sqlite3.Row
    for term in a.find:
        print_find(conn, term)
    print_coverage(conn, a.first, a.last)


if __name__ == '__main__':
    main()

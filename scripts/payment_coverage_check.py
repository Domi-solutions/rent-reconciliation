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
migrations do not run against it.

    ./scripts/download_prod_db.sh
    ./venv/bin/python scripts/payment_coverage_check.py --db data/dev.db \\
        --find "RUTH KANDUMA" --find 254759761866 \\
        --find "NICHOLAS MATHEKA" --find 254724780761 \\
        --from 2026-01 --to 2026-05
"""

import argparse
import re
import sqlite3
import sys


def months(start, end):
    y, m = map(int, start.split('-'))
    out = []
    while f'{y:04d}-{m:02d}' <= end:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def phone_digits(term):
    """Last 9 digits of anything phone-like, so 0759…, 254759… and +254759… all match."""
    d = re.sub(r'\D', '', term)
    return d[-9:] if len(d) >= 9 else None


def find(conn, term):
    print(f'\n── "{term}" ──')
    digits = phone_digits(term)
    like = f'%{term.upper()}%'
    hits = 0

    rows = conn.execute("""
        SELECT t.name, t.phone, t.status, t.move_in_date, t.move_out_date,
               u.unit_number, p.name AS property
        FROM tenants t
        LEFT JOIN units u ON u.id = t.unit_id
        LEFT JOIN properties p ON p.id = t.property_id
        WHERE UPPER(t.name) LIKE ? OR (? IS NOT NULL AND REPLACE(t.phone, ' ', '') LIKE '%' || ? )
    """, (like, digits, digits)).fetchall()
    for r in rows:
        hits += 1
        print(f"  TENANT   {r['name']} | {r['phone'] or '-'} | {r['property']} unit {r['unit_number']} | "
              f"{r['status']} | in {r['move_in_date'] or '?'} out {r['move_out_date'] or '-'}")

    rows = conn.execute("""
        SELECT bt.sender_name, MIN(bt.txn_date) AS first, MAX(bt.txn_date) AS last,
               COUNT(*) AS n, SUM(bt.amount) AS total,
               SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS assigned,
               GROUP_CONCAT(DISTINCT u.unit_number) AS units
        FROM bank_transactions bt
        LEFT JOIN payments p ON p.bank_txn_id = bt.id
        LEFT JOIN units u ON u.id = p.unit_id
        WHERE UPPER(bt.sender_name) LIKE ? OR (? IS NOT NULL AND bt.raw_text LIKE '%' || ? || '%')
        GROUP BY bt.sender_name
    """, (like, digits, digits)).fetchall()
    for r in rows:
        hits += 1
        print(f"  BANK     {r['sender_name']} | {r['n']} txns KES {r['total'] or 0:,.0f} | "
              f"{r['first']} → {r['last']} | assigned {r['assigned']} to unit(s) {r['units'] or '-'}")

    rows = conn.execute("""
        SELECT pc.mpesa_ref, pc.claimed_amount, pc.status, pc.created_at, u.unit_number
        FROM payment_claims pc LEFT JOIN units u ON u.id = pc.unit_id
        WHERE UPPER(pc.raw_message) LIKE ? OR (? IS NOT NULL AND pc.raw_message LIKE '%' || ? || '%')
    """, (like, digits, digits)).fetchall()
    for r in rows:
        hits += 1
        print(f"  CLAIM    {r['mpesa_ref']} KES {r['claimed_amount'] or 0:,.0f} | {r['status']} | "
              f"{(r['created_at'] or '')[:10]} | unit {r['unit_number']}")

    if not hits:
        print('  not found anywhere — never a tenant, never seen on an uploaded statement, never reported')


def coverage(conn, first, last):
    span = months(first, last)
    props = conn.execute("SELECT id, name, organization_id FROM properties ORDER BY name").fetchall()
    for prop in props:
        print(f"\n══ {prop['name']} ({prop['id']}) ══")
        print(f"{'MONTH':<9}{'PAYMENTS':>9}{'KES':>13}{'PAYERS':>8}{'CHARGED':>13}  STATEMENTS UPLOADED COVERING THIS MONTH")
        for m in span:
            pay = conn.execute("""
                SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, COUNT(DISTINCT unit_id) AS units
                FROM payments WHERE property_id = ? AND substr(payment_date, 1, 7) = ?
            """, (prop['id'], m)).fetchone()
            charged = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM rent_charges WHERE property_id = ? AND period = ?",
                (prop['id'], m)).fetchone()[0]
            stmts = conn.execute("""
                SELECT filename, bank_format, period_start, period_end FROM bank_statements
                WHERE (property_id = ? OR (property_id IS NULL AND org_id = ?))
                  AND substr(period_start, 1, 7) <= ? AND substr(period_end, 1, 7) >= ?
                ORDER BY period_start
            """, (prop['id'], prop['organization_id'], m, m)).fetchall()
            label = '; '.join(f"{s['filename']} ({s['period_start']}→{s['period_end']})" for s in stmts) or 'NONE'
            print(f"{m:<9}{pay['n']:>9}{pay['total']:>13,.0f}{pay['units']:>8}{charged:>13,.0f}  {label}")

        tenants = conn.execute("""
            SELECT t.id, t.name, t.status, t.move_in_date, t.move_out_date, u.unit_number
            FROM tenants t JOIN units u ON u.id = t.unit_id
            WHERE t.property_id = ?
              AND COALESCE(substr(t.move_in_date, 1, 7), '0000-00') <= ?
              AND (t.move_out_date IS NULL OR substr(t.move_out_date, 1, 7) >= ?)
            ORDER BY u.unit_number
        """, (prop['id'], last, first)).fetchall()
        if not tenants:
            continue
        print(f"\n  Per tenant — months in {first}..{last} with NO payment recorded on their unit")
        for t in tenants:
            paid = {r[0] for r in conn.execute(
                "SELECT DISTINCT substr(payment_date, 1, 7) FROM payments p "
                "JOIN tenants x ON x.id = ? WHERE p.unit_id = x.unit_id", (t['id'],))}
            lo = max(first, (t['move_in_date'] or '0000-00')[:7])
            hi = min(last, (t['move_out_date'] or '9999-99')[:7])
            gaps = [m for m in span if lo <= m <= hi and m not in paid]
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
        find(conn, term)
    coverage(conn, a.first, a.last)


if __name__ == '__main__':
    main()

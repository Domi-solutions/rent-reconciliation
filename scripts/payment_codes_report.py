#!/usr/bin/env python3
"""
Read-only: M-Pesa codes recorded as more than one payment (rent counted twice).

Since migrate_add_payment_ref the database refuses a second payment for a code, but
duplicates made before that stay until a person decides. Typical cause: the same bank
statement uploaded twice and the same line assigned on each copy. For each code this lists
every payment — when, by whom, from which statement, which tenant — so management can
delete the extra one (Payments › Confirmed › delete) after checking.

Opens the database mode=ro and never imports the app, so no migration runs against it.
Reads the code from payments.mpesa_ref when the column exists, else from the payment's
bank line / claim / M-Pesa receipt (an older database).

    ./scripts/download_prod_db.sh
    ./venv/bin/python scripts/payment_codes_report.py --db data/dev.db
"""

import argparse
import sqlite3
import sys

REF = """COALESCE(
    NULLIF(UPPER(TRIM(bt.mpesa_ref)), ''),
    NULLIF(UPPER(TRIM(pc.mpesa_ref)), ''),
    CASE WHEN p.source = 'daraja' THEN NULLIF(UPPER(TRIM(p.assignment_reason)), '') END)"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', required=True)
    args = ap.parse_args()
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(payments)")}
    ref = f"COALESCE(NULLIF(UPPER(TRIM(p.mpesa_ref)), ''), {REF})" if 'mpesa_ref' in cols else REF
    rows = conn.execute(f"""
        WITH coded AS (
            SELECT p.*, {ref} AS code FROM payments p
            LEFT JOIN bank_transactions bt ON bt.id = p.bank_txn_id
            LEFT JOIN payment_claims pc ON pc.id = p.claim_id)
        SELECT c.code, c.id, c.amount, c.payment_date, c.created_at, c.assigned_by, c.assignment_type, c.source,
               u.unit_number, t.name AS tenant_name, bs.filename
        FROM coded c
        LEFT JOIN units u ON u.id = c.unit_id
        LEFT JOIN tenants t ON t.id = c.tenant_id
        LEFT JOIN bank_transactions bt ON bt.id = c.bank_txn_id
        LEFT JOIN bank_statements bs ON bs.id = bt.statement_id
        WHERE c.code IN (SELECT code FROM coded WHERE code IS NOT NULL GROUP BY code HAVING COUNT(*) > 1)
        ORDER BY c.code, c.created_at""").fetchall()
    if not rows:
        print('No M-Pesa code is recorded as more than one payment.')
        return 0
    codes = {}
    for r in rows:
        codes.setdefault(r['code'], []).append(r)
    extra = sum(float(rs[0]['amount'] or 0) * (len(rs) - 1) for rs in codes.values())
    print(f'{len(codes)} code(s) recorded more than once — about KES {extra:,.0f} counted extra.\n')
    for code, rs in codes.items():
        print(f'{code}: {len(rs)} payments')
        for r in rs:
            print(f"  {r['id']}  KES {float(r['amount'] or 0):,.0f}  paid {r['payment_date']}  "
                  f"house {r['unit_number'] or '?'} ({r['tenant_name'] or 'no tenant'})  "
                  f"recorded {r['created_at']} by {r['assigned_by'] or r['source'] or '?'} "
                  f"[{r['assignment_type'] or '?'}]  from {r['filename'] or r['source'] or '-'}")
        print('  → keep the first, delete the others after checking.\n')
    return 1


if __name__ == '__main__':
    sys.exit(main())

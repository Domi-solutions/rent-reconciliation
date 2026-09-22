#!/usr/bin/env python3
"""
Delete duplicate or test copies of a property.

One building can end up in the database several times — a demo property, a
copy made while building the multi-org layer — each carrying its own units,
tenants and balances. The same tenant then shows three different amounts owed
and no figure can be trusted.

This deletes the copies you name. It does not guess which one is real: that
decision is yours, and getting it wrong destroys a live ledger. Nothing is
deleted without --execute, and --execute takes a snapshot of the database first.

    # see what is there
    ./venv/bin/python scripts/cleanup_duplicate_properties.py --list

    # see exactly what deleting these would remove
    ./venv/bin/python scripts/cleanup_duplicate_properties.py --delete PROP-7CA50F2A --delete PROP-CB42A055

    # do it
    ./venv/bin/python scripts/cleanup_duplicate_properties.py --delete PROP-7CA50F2A --delete PROP-CB42A055 --execute
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection  # noqa: E402
from src.platform.property_purge import count_property_data, purge_property_data  # noqa: E402


def list_properties(conn):
    rows = conn.execute("""
        SELECT p.id, p.name, p.status, p.organization_id, o.name AS org_name,
               (SELECT COUNT(*) FROM units    WHERE property_id = p.id) AS units,
               (SELECT COUNT(*) FROM tenants  WHERE property_id = p.id AND status='active') AS tenants,
               (SELECT COUNT(*) FROM payments WHERE property_id = p.id) AS payments,
               (SELECT MAX(payment_date) FROM payments WHERE property_id = p.id) AS last_payment,
               (SELECT COUNT(*) FROM bank_statements WHERE property_id = p.id) AS statements
        FROM properties p
        LEFT JOIN organizations o ON o.id = p.organization_id
        ORDER BY p.name
    """).fetchall()

    print(f"\n{'PROPERTY':<18}{'NAME':<22}{'ORGANIZATION':<26}"
          f"{'UNITS':>6}{'TENANTS':>8}{'PAYMTS':>8}{'STMTS':>7}  LAST PAYMENT")
    print('-' * 118)
    for r in rows:
        print(f"{r['id']:<18}{(r['name'] or '')[:21]:<22}"
              f"{(r['org_name'] or r['organization_id'] or '—')[:25]:<26}"
              f"{r['units']:>6}{r['tenants']:>8}{r['payments']:>8}{r['statements']:>7}"
              f"  {r['last_payment'] or '—'}")
    print("\nThe live property is normally the one with statements attached and "
          "recent payments.\nCheck the organization column: a property under a "
          "test or demo organization is not your ledger.")
    return rows


def backup_database(conn):
    """Snapshot via sqlite3's own backup API, which is consistent under writes.

    The snapshot is written beside the database itself. On Fly that is the
    mounted volume, so it survives the next deploy — writing it next to the
    code instead would put it on the container's ephemeral layer, where a
    restore point quietly disappears the moment anything is redeployed.
    """
    source = os.environ.get('DATABASE_PATH') or os.path.join('data', 'rent.db')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(source)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(
        backup_dir, f"pre_cleanup_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db")
    try:
        dest = sqlite3.connect(target)
        conn.backup(dest)
        dest.close()
    except Exception:
        if not os.path.exists(source):
            raise
        shutil.copy2(source, target)
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true', help='show every property and stop')
    ap.add_argument('--delete', action='append', default=[], metavar='PROPERTY_ID',
                    help='property to delete; repeat for several')
    ap.add_argument('--execute', action='store_true',
                    help='actually delete (default is a preview)')
    args = ap.parse_args()

    with get_connection() as conn:
        rows = list_properties(conn)
        if args.list or not args.delete:
            if not args.delete and not args.list:
                print('\nNothing named with --delete, so nothing to preview.')
            return

        known = {r['id']: r for r in rows}
        targets = []
        for pid in args.delete:
            if pid not in known:
                sys.exit(f'\nNo property {pid}. Nothing was deleted.')
            targets.append(known[pid])

        if len(targets) >= len(rows):
            sys.exit('\nThat would delete every property. Refusing.')

        survivors = [r['id'] for r in rows if r['id'] not in args.delete]
        print(f"\n{'WOULD DELETE' if not args.execute else 'DELETING'}:")
        for prop in targets:
            counts = count_property_data(conn, prop['id'])
            print(f"\n  {prop['name']}  ({prop['id']})  org={prop['organization_id']}")
            for table, n in sorted(counts.items()):
                if n:
                    print(f"      {table:<24}{n:>7} rows")
            if not any(counts.values()):
                print('      (no attached data)')
        print(f"\nKEEPING: {', '.join(survivors)}")

        if not args.execute:
            print('\nPreview only — nothing was written.')
            print('Re-run with --execute to apply. A snapshot is taken first.')
            return

        snapshot = backup_database(conn)
        print(f'\nSnapshot written to {snapshot}')

        for prop in targets:
            removed = purge_property_data(conn, prop['id'])
            conn.execute("DELETE FROM properties WHERE id = ?", (prop['id'],))
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ('property_deleted', 'property', prop['id'],
                 f"Duplicate property \"{prop['name']}\" removed via cleanup script "
                 f"({sum(removed.values())} rows)", 'admin')
            )
            print(f"  deleted {prop['name']} ({sum(removed.values())} rows)")

        print('\nDone. Confirm the surviving property still reads correctly '
              'before deleting the snapshot.')


if __name__ == '__main__':
    main()

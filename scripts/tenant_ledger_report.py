#!/usr/bin/env python3
"""
Show what moving to a per-tenant ledger changes, and repair old allocations.

Charges and payments used to belong to the unit, with move-in dates deciding
whose they were. Now each row carries its tenant. Run this against a copy of
the live database before deploying, to see exactly whose balance moves and why:

    ./scripts/download_prod_db.sh
    DATABASE_PATH=data/dev.db ./venv/bin/python scripts/tenant_ledger_report.py

It reports:
  - balance changes  active tenants whose balance differs under the new rule
  - cross-tenant     allocations where one tenant's payment settled another's charge
  - unattributed     rows no tenant could be matched to — each needs a person
  - departed         former tenants who still owe (or are owed) money

Nothing is written without --execute. --execute takes a snapshot, then redoes
allocations on every unit with a cross-tenant allocation. Balances do not change;
only which charge each payment settled.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection, migrate_add_ledger_tenant_id  # noqa: E402
from src.reconciliation.tenant_ledger import (  # noqa: E402
    balance_changes, cross_tenant_allocations, departed_balances,
    reallocate_unit, unattributed_rows,
)


def backup_database(conn):
    """Snapshot beside the database (the mounted volume on Fly), via sqlite3's backup API."""
    source = os.environ.get('DATABASE_PATH') or os.path.join('data', 'rent.db')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(source)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(
        backup_dir, f"pre_ledger_rebuild_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db")
    try:
        dest = sqlite3.connect(target)
        conn.backup(dest)
        dest.close()
    except Exception:
        if not os.path.exists(source):
            raise
        shutil.copy2(source, target)
    return target


def kes(v):
    return f"{float(v or 0):>12,.0f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--property', help='limit to one property id')
    parser.add_argument('--execute', action='store_true',
                        help='snapshot, then redo allocations on units with cross-tenant allocations')
    args = parser.parse_args()
    pid = args.property

    migrate_add_ledger_tenant_id()

    with get_connection() as conn:
        changes = balance_changes(conn, pid)
        print(f"\nBALANCE CHANGES — {len(changes)} active tenant(s)")
        if changes:
            print(f"{'UNIT':<8}{'TENANT':<28}{'MOVE-IN':<12}{'OLD':>12}{'NEW':>12}{'DIFF':>12}")
            for r in changes:
                diff = float(r['new_balance']) - float(r['old_balance'])
                print(f"{r['unit_number']:<8}{(r['name'] or '')[:27]:<28}{(r['move_in_date'] or '-'):<12}"
                      f"{kes(r['old_balance'])}{kes(r['new_balance'])}{kes(diff)}")

        cross = cross_tenant_allocations(conn, pid)
        units = sorted({r['unit_id'] for r in cross})
        print(f"\nCROSS-TENANT ALLOCATIONS — {len(cross)} on {len(units)} unit(s)")
        for r in cross:
            print(f"  {r['unit_number']:<8}{r['payment_date'] or '-':<12}KES{kes(r['amount'])}  "
                  f"paid by {r['payer_name'] or '(unattributed)'} → "
                  f"{r['charge_type']} {r['period']} of {r['charge_owner_name'] or '(unattributed)'}")

        loose = unattributed_rows(conn, pid)
        print(f"\nUNATTRIBUTED — {len(loose['charges'])} charge(s), {len(loose['payments'])} payment(s), "
              f"{len(loose['claims'])} claim(s)")
        for r in loose['charges']:
            print(f"  charge   {r['unit_number']:<8}{r['period']:<12}{r['charge_type']:<9}KES{kes(r['amount'])}")
        for r in loose['payments']:
            print(f"  payment  {r['unit_number']:<8}{r['payment_date'] or '-':<12}{'':<9}KES{kes(r['amount'])}")
        for r in loose['claims']:
            print(f"  claim    {r['unit_number']:<8}{(r['created_at'] or '-')[:10]:<12}{r['status']:<9}"
                  f"KES{kes(r['claimed_amount'])}")

        gone = departed_balances(conn, pid)
        print(f"\nDEPARTED WITH A BALANCE — {len(gone)}")
        for r in gone:
            print(f"  {r['unit_number']:<8}{(r['name'] or '')[:27]:<28}{r['status']:<10}"
                  f"left {r['move_out_date'] or '?':<12}KES{kes(r['balance'])}  {r['phone'] or ''}")

        if not args.execute:
            if units:
                print('\nRe-run with --execute to redo allocations on those units. A snapshot is taken first.')
            return

        if not units:
            print('\nNo cross-tenant allocations. Nothing to do.')
            return
        snapshot = backup_database(conn)
        print(f'\nSnapshot written to {snapshot}')
        for unit_id in units:
            n = reallocate_unit(conn, unit_id)
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('ledger_reallocated', 'unit', unit_id,
                 f'Allocations redone per tenant for {n} payment(s)', 'system'),
            )
        print(f'Allocations redone on {len(units)} unit(s).')


if __name__ == '__main__':
    main()

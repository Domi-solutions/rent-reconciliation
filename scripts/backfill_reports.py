#!/usr/bin/env python3
"""
Generate the monthly landlord reports that were never created.

Reports are only written when somebody asks for one, so a property can have
months of collections with nothing to look back at. This fills the gaps — one
report per calendar month that had activity — so the history is complete.

Safe to re-run: a month that already has a report is skipped.

    ./venv/bin/python scripts/backfill_reports.py --dry-run
    ./venv/bin/python scripts/backfill_reports.py
    ./venv/bin/python scripts/backfill_reports.py --property PROP-666759B2
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.db import get_connection  # noqa: E402
from src.reports.backfill import (  # noqa: E402
    backfill_reports,
    find_activity_months,
    find_missing_report_months,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--property', help='limit to one property id')
    ap.add_argument('--dry-run', action='store_true',
                    help='list what would be generated, write nothing')
    args = ap.parse_args()

    with get_connection() as conn:
        if args.property:
            properties = conn.execute(
                "SELECT id, name FROM properties WHERE id = ?", (args.property,)
            ).fetchall()
            if not properties:
                sys.exit(f'No property {args.property}')
        else:
            properties = conn.execute(
                "SELECT id, name FROM properties WHERE status = 'active' ORDER BY name"
            ).fetchall()

        total_created = 0
        for prop in properties:
            activity = find_activity_months(conn, prop['id'])
            missing = find_missing_report_months(conn, prop['id'])
            print(f"\n{prop['name']}  ({prop['id']})")
            print(f"  months with activity : {len(activity)}"
                  + (f"  [{activity[0]} … {activity[-1]}]" if activity else ''))
            print(f"  already reported     : {len(activity) - len(missing)}")

            if not missing:
                print('  nothing to generate')
                continue

            result = backfill_reports(conn, prop['id'], dry_run=args.dry_run)
            verb = 'would generate' if args.dry_run else 'generated'
            print(f"  {verb:<20} : {len(result['created'])}  "
                  f"({', '.join(result['created'])})")
            total_created += len(result['created'])
            for month, error in result['failed']:
                print(f"  FAILED {month}: {error}")

        print(f"\n{'Would generate' if args.dry_run else 'Generated'} {total_created} report(s).")
        if args.dry_run:
            print('Nothing was written. Re-run without --dry-run to apply.')


if __name__ == '__main__':
    main()

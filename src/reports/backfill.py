"""
Fill in the monthly landlord reports that were never generated.

Reports are only created when somebody asks for one, so a property that has
been collecting rent for months can have no history to look at. This walks the
months that actually have activity and generates the ones that are missing, so
the history is already there when the page is opened rather than being built a
month at a time by hand.

Each report is bounded to its own calendar month — first day to last day — so
it describes that month and no other. That is what generate_landlord_report()
already does with a period: collections filter on payment_date within the
range, charges on period within the month range. Arrears remain a snapshot as
at the close of the month, which is the useful reading of "what was still owed
when this month ended".

Idempotent: a month that already has a report is left alone, so this is safe to
run repeatedly, on every page load or from cron.
"""

import calendar
import json
import logging
import re

from src.database.db import generate_id
from src.reports.landlord_report import generate_landlord_report

logger = logging.getLogger(__name__)

MONTH_RE = re.compile(r'^\d{4}-\d{2}$')


def _month_bounds(month):
    """'2026-03' -> ('2026-03-01', '2026-03-31')."""
    year, mon = int(month[:4]), int(month[5:7])
    return f'{year:04d}-{mon:02d}-01', f'{year:04d}-{mon:02d}-{calendar.monthrange(year, mon)[1]:02d}'


def find_activity_months(conn, property_id):
    """Months where anything happened: a charge raised, a payment recorded, or
    a bank transaction on one of this property's statements."""
    months = set()

    # rent_charges.period is usually 'YYYY-MM' but 'ARREARS' is also a valid
    # value, so anything that is not a month is filtered out rather than parsed.
    for row in conn.execute(
        "SELECT DISTINCT period FROM rent_charges WHERE property_id = ?", (property_id,)
    ):
        period = (row['period'] or '').strip()
        if MONTH_RE.match(period):
            months.add(period)

    for row in conn.execute(
        "SELECT DISTINCT substr(payment_date, 1, 7) AS m FROM payments "
        "WHERE property_id = ? AND payment_date IS NOT NULL AND payment_date != ''",
        (property_id,)
    ):
        if row['m'] and MONTH_RE.match(row['m']):
            months.add(row['m'])

    for row in conn.execute(
        "SELECT DISTINCT substr(bt.txn_date, 1, 7) AS m "
        "FROM bank_transactions bt JOIN bank_statements bs ON bt.statement_id = bs.id "
        "WHERE bs.property_id = ? AND bt.txn_date IS NOT NULL AND bt.txn_date != ''",
        (property_id,)
    ):
        if row['m'] and MONTH_RE.match(row['m']):
            months.add(row['m'])

    return sorted(months)


def find_missing_report_months(conn, property_id):
    """Activity months with no stored report for that exact month."""
    covered = set()
    for row in conn.execute(
        "SELECT period_start, period_end FROM landlord_reports WHERE property_id = ?",
        (property_id,)
    ):
        start, end = row['period_start'] or '', row['period_end'] or ''
        if len(start) >= 7 and start[:7] == end[:7]:
            covered.add(start[:7])

    return [m for m in find_activity_months(conn, property_id) if m not in covered]


def backfill_reports(conn, property_id, dry_run=False, limit=None):
    """Generate a report for each activity month that lacks one.

    Returns {'created': [...], 'skipped_existing': int, 'failed': [(month, error)]}.
    A month that fails is recorded and the rest continue, so one bad month
    cannot stop the backfill.
    """
    missing = find_missing_report_months(conn, property_id)
    if limit:
        missing = missing[:limit]

    result = {'property_id': property_id, 'created': [], 'failed': [],
              'considered': len(missing), 'dry_run': dry_run}

    for month in missing:
        period_start, period_end = _month_bounds(month)
        if dry_run:
            result['created'].append(month)
            continue
        try:
            report_data = generate_landlord_report(conn, property_id, period_start, period_end)
            report_id = generate_id('RPT')
            conn.execute(
                "INSERT INTO landlord_reports "
                "(id, property_id, period_start, period_end, report_type, report_data) "
                "VALUES (?, ?, ?, ?, 'backfill', ?)",
                (report_id, property_id, period_start, period_end, json.dumps(report_data))
            )
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ('report_generated', 'report', report_id,
                 f'Backfilled report for {period_start} to {period_end}', 'system')
            )
            result['created'].append(month)
        except Exception as exc:  # noqa: BLE001 — one bad month must not stop the rest
            logger.warning('Report backfill failed for %s %s: %s', property_id, month, exc)
            result['failed'].append((month, str(exc)))

    return result

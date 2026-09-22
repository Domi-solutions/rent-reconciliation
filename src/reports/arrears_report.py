"""
Who owes what, and which statements are missing.

The landlord report describes a month that has closed. This one describes the
position right now: the outstanding balance broken down by tenant, and — just
as important — how much of the period that balance covers has actually been
checked against a bank statement.

The two belong in one report because the second decides how much the first can
be trusted. A tenant with no payments recorded may be in arrears, or may be
paying into an account whose statement was never uploaded. Reporting the debt
without reporting the gaps invites chasing someone who already paid.
"""

from datetime import date, datetime
from decimal import Decimal

from src.utils.metrics import get_expected_monthly_income, get_months_behind


def _month_range(start_month, end_month):
    """Inclusive list of 'YYYY-MM' from start to end."""
    months = []
    year, mon = int(start_month[:4]), int(start_month[5:7])
    end_year, end_mon = int(end_month[:4]), int(end_month[5:7])
    while (year, mon) <= (end_year, end_mon):
        months.append(f'{year:04d}-{mon:02d}')
        mon += 1
        if mon > 12:
            mon, year = 1, year + 1
    return months


def _aging_bucket(months_behind):
    if months_behind <= 1:
        return 'current'
    if months_behind == 2:
        return '1-2 months'
    if months_behind == 3:
        return '2-3 months'
    return '3+ months'


def _statement_coverage(conn, property_id, org_id, first_month, last_month):
    """Months between first_month and last_month with no statement covering them.

    Statements may be attached to a property or left org-wide (property_id
    NULL), so both count as coverage. A month is covered when any statement's
    period overlaps it at all.
    """
    statements = conn.execute("""
        SELECT id, filename, bank_format, period_start, period_end, status,
               total_transactions, property_id
        FROM bank_statements
        WHERE (property_id = ? OR (property_id IS NULL AND org_id = ?))
          AND status != 'superseded'
        ORDER BY period_start
    """, (property_id, org_id)).fetchall()

    covered = set()
    rows = []
    for stmt in statements:
        start, end = stmt['period_start'] or '', stmt['period_end'] or ''
        rows.append({
            'id': stmt['id'],
            'filename': stmt['filename'],
            'bank_format': stmt['bank_format'],
            'period_start': start,
            'period_end': end,
            'status': stmt['status'],
            'total_transactions': stmt['total_transactions'],
            # A period_start the parser could not read — the year-extraction
            # defect produced dates like '0034-02-02' — cannot be trusted to
            # mark anything as covered.
            'period_suspect': not (start[:2] == '20' and end[:2] == '20'),
        })
        if start[:2] == '20' and end[:2] == '20':
            covered.update(_month_range(start[:7], end[:7]))

    all_months = _month_range(first_month, last_month)
    return {
        'statements': rows,
        'covered_months': sorted(covered & set(all_months)),
        'gap_months': [m for m in all_months if m not in covered],
        'checked_through': max(covered) if covered else None,
    }


def generate_arrears_report(conn, property_id):
    """Outstanding balances by tenant plus statement coverage. Read-only."""
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        raise ValueError(f'Property {property_id} not found')
    org_id = prop['organization_id']
    today = date.today()

    balances = conn.execute("""
        SELECT unit_number, tenant_id, tenant_name, tenant_phone, monthly_rent,
               total_charged, total_paid, balance
        FROM unit_balances
        WHERE property_id = ?
        ORDER BY balance DESC
    """, (property_id,)).fetchall()

    # Receipts per unit: the actual payments behind the balance, newest first,
    # with the M-Pesa reference so a disputed figure can be checked against the
    # bank rather than argued about. The reference lives on the bank
    # transaction when the payment came from a statement, and on the claim when
    # it was reported by a caretaker or tenant.
    receipts = {}
    for row in conn.execute("""
        SELECT p.unit_id, p.payment_date, p.amount, p.assignment_type, p.source,
               COALESCE(bt.mpesa_ref, pc.mpesa_ref) AS mpesa_ref,
               bt.sender_name
        FROM payments p
        LEFT JOIN bank_transactions bt ON p.bank_txn_id = bt.id
        LEFT JOIN payment_claims pc ON p.claim_id = pc.id
        WHERE p.property_id = ?
        ORDER BY p.payment_date DESC, p.created_at DESC
    """, (property_id,)):
        receipts.setdefault(row['unit_id'], []).append({
            'date': row['payment_date'],
            'amount': float(row['amount'] or 0),
            'mpesa_ref': row['mpesa_ref'],
            'sender_name': row['sender_name'],
            'source': row['source'] or row['assignment_type'],
        })

    last_payments = dict(conn.execute("""
        SELECT unit_id, MAX(payment_date) FROM payments
        WHERE property_id = ? GROUP BY unit_id
    """, (property_id,)).fetchall())
    unit_ids = dict(conn.execute(
        "SELECT unit_number, id FROM units WHERE property_id = ?", (property_id,)
    ).fetchall())

    debtors, credits = [], []
    buckets = {'current': 0.0, '1-2 months': 0.0, '2-3 months': 0.0, '3+ months': 0.0}
    for row in balances:
        balance = float(row['balance'] or 0)
        if balance == 0:
            continue
        unit_id = unit_ids.get(row['unit_number'])
        unit_receipts = receipts.get(unit_id, [])
        last_paid = last_payments.get(unit_id)
        days_since = None
        if last_paid:
            try:
                days_since = (today - datetime.strptime(last_paid[:10], '%Y-%m-%d').date()).days
            except (ValueError, TypeError):
                days_since = None
        entry = {
            'unit_number': row['unit_number'],
            'tenant_id': row['tenant_id'],
            'tenant_name': row['tenant_name'],
            'tenant_phone': row['tenant_phone'],
            'monthly_rent': float(row['monthly_rent'] or 0),
            'total_charged': float(row['total_charged'] or 0),
            'total_paid': float(row['total_paid'] or 0),
            'balance': balance,
            'last_payment_date': last_paid,
            'days_since_last_payment': days_since,
            'last_payment_amount': unit_receipts[0]['amount'] if unit_receipts else None,
            'last_payment_ref': unit_receipts[0]['mpesa_ref'] if unit_receipts else None,
            'payment_count': len(unit_receipts),
            'receipts': unit_receipts[:6],
            'never_paid': float(row['total_paid'] or 0) == 0,
        }
        if balance > 0:
            entry['months_behind'] = get_months_behind(balance, row['monthly_rent'])
            entry['bucket'] = _aging_bucket(entry['months_behind'])
            buckets[entry['bucket']] += balance
            debtors.append(entry)
        else:
            credits.append(entry)

    # The period the debt spans: earliest charge to this month.
    first_period = conn.execute("""
        SELECT MIN(period) FROM rent_charges
        WHERE property_id = ? AND period LIKE '20%-%'
    """, (property_id,)).fetchone()[0]
    first_month = (first_period or today.strftime('%Y-%m'))[:7]
    coverage = _statement_coverage(conn, property_id, org_id,
                                  first_month, today.strftime('%Y-%m'))

    unassigned = conn.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(bt.amount), 0) AS total
        FROM bank_transactions bt
        JOIN bank_statements bs ON bt.statement_id = bs.id
        WHERE (bs.property_id = ? OR (bs.property_id IS NULL AND bs.org_id = ?))
          AND bt.txn_type = 'PAYBILL_CREDIT'
          AND (bt.ignored IS NULL OR bt.ignored = 0)
          AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
    """, (property_id, org_id)).fetchone()

    flagged = conn.execute("""
        SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.flag_reason, pc.created_at,
               u.unit_number, t.name AS tenant_name
        FROM payment_claims pc
        LEFT JOIN units u ON pc.unit_id = u.id
        LEFT JOIN tenants t ON t.unit_id = pc.unit_id AND t.status = 'active'
        WHERE pc.property_id = ? AND pc.status = 'flagged'
        ORDER BY pc.created_at DESC
    """, (property_id,)).fetchall()

    total_outstanding = sum(d['balance'] for d in debtors)
    expected_monthly = get_expected_monthly_income(conn, property_id)

    return {
        'property_name': prop['name'],
        'property_id': property_id,
        'generated_at': datetime.now().isoformat(),
        'as_at': today.isoformat(),
        'summary': {
            'total_outstanding': total_outstanding,
            'units_in_arrears': len(debtors),
            'total_credit': abs(sum(c['balance'] for c in credits)),
            'units_in_credit': len(credits),
            'never_paid_count': len([d for d in debtors if d['never_paid']]),
            'never_paid_total': sum(d['balance'] for d in debtors if d['never_paid']),
            'expected_monthly_income': expected_monthly,
            'months_of_income_outstanding': (
                round(total_outstanding / expected_monthly, 1) if expected_monthly else 0
            ),
        },
        'aging': buckets,
        'debtors': debtors,
        'credits': sorted(credits, key=lambda c: c['balance']),
        'never_paid': [d for d in debtors if d['never_paid']],
        'gone_quiet': sorted(
            [d for d in debtors
             if d['days_since_last_payment'] is not None and d['days_since_last_payment'] >= 45],
            key=lambda d: -d['days_since_last_payment']
        ),
        'coverage': coverage,
        'unassigned': {'count': unassigned['n'], 'total': float(unassigned['total'] or 0)},
        'flagged_claims': [dict(f) for f in flagged],
    }

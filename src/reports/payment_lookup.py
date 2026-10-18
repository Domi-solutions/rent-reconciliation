"""
Payer lookup and month-by-month payment coverage. Read-only.

Two questions asked before balances are rebuilt from bank statements:
  find_payer      — is this name or phone known anywhere: as a tenant (any status),
                    a bank transaction sender, or in a reported M-Pesa message?
  month_coverage  — per month: payments recorded, amount, units that paid, what was
                    charged, and which uploaded statements cover the month. A month
                    with no payments and no statement is a month the database never
                    saw, not a month nobody paid.

Used by the admin page /reports/lookup (scoped to the session's organisation)
and by scripts/payment_coverage_check.py (whole database, opened read-only).
"""

import calendar
import re


def _months(first, last):
    y, m = map(int, first.split('-'))
    out = []
    while f'{y:04d}-{m:02d}' <= last:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _phone_digits(term):
    """Last 9 digits of anything phone-like, so 0759…, 254759… and +254759… all match."""
    d = re.sub(r'\D', '', term or '')
    return d[-9:] if len(d) >= 9 else None


def find_payer(conn, term, org_id=None, all_orgs=False):
    """Every trace of a name fragment or phone number, within one organisation.
    all_orgs=True (the offline script only) searches the whole database."""
    term = (term or '').strip()
    if not term:
        return {'tenants': [], 'bank': [], 'claims': []}
    digits = _phone_digits(term)
    like = f'%{term.upper()}%'
    # IS rather than = so a property with no organisation only matches others with none.
    org = "" if all_orgs else "AND p.organization_id IS ?"
    oargs = () if all_orgs else (org_id,)

    tenants = conn.execute(f"""
        SELECT t.id, t.name, t.phone, t.status, t.move_in_date, t.move_out_date,
               u.unit_number, p.name AS property, p.id AS property_id
        FROM tenants t
        LEFT JOIN units u ON u.id = t.unit_id
        LEFT JOIN properties p ON p.id = t.property_id
        WHERE (UPPER(t.name) LIKE ? OR (? IS NOT NULL AND REPLACE(t.phone, ' ', '') LIKE '%' || ?)) {org}
        ORDER BY t.status, t.name
    """, (like, digits, digits) + oargs).fetchall()

    borg = "" if all_orgs else "AND bs.org_id IS ?"
    bank = conn.execute(f"""
        SELECT bt.sender_name, MIN(bt.txn_date) AS first, MAX(bt.txn_date) AS last,
               COUNT(*) AS n, COALESCE(SUM(bt.amount), 0) AS total,
               SUM(CASE WHEN pay.id IS NOT NULL THEN 1 ELSE 0 END) AS assigned,
               GROUP_CONCAT(DISTINCT u.unit_number) AS units
        FROM bank_transactions bt
        JOIN bank_statements bs ON bs.id = bt.statement_id
        LEFT JOIN payments pay ON pay.bank_txn_id = bt.id
        LEFT JOIN units u ON u.id = pay.unit_id
        WHERE (UPPER(bt.sender_name) LIKE ? OR (? IS NOT NULL AND bt.raw_text LIKE '%' || ? || '%')) {borg}
        GROUP BY bt.sender_name
        ORDER BY total DESC
    """, (like, digits, digits) + oargs).fetchall()

    claims = conn.execute(f"""
        SELECT pc.mpesa_ref, pc.claimed_amount, pc.status, pc.created_at, u.unit_number
        FROM payment_claims pc
        LEFT JOIN units u ON u.id = pc.unit_id
        LEFT JOIN properties p ON p.id = pc.property_id
        WHERE (UPPER(pc.raw_message) LIKE ? OR (? IS NOT NULL AND pc.raw_message LIKE '%' || ? || '%')) {org}
        ORDER BY pc.created_at DESC
    """, (like, digits, digits) + oargs).fetchall()

    return {
        'tenants': [dict(r) for r in tenants],
        'bank': [dict(r) for r in bank],
        'claims': [dict(r) for r in claims],
    }


def month_coverage(conn, property_id, first, last):
    """Month rows for one property, plus per-tenant months with no payment recorded."""
    prop = conn.execute("SELECT id, name, organization_id FROM properties WHERE id = ?",
                        (property_id,)).fetchone()
    span = _months(first, last)
    rows = []
    for m in span:
        pay = conn.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, COUNT(DISTINCT unit_id) AS units
            FROM payments WHERE property_id = ? AND substr(payment_date, 1, 7) = ?
        """, (property_id, m)).fetchone()
        charged = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM rent_charges WHERE property_id = ? AND period = ?",
            (property_id, m)).fetchone()[0]
        stmts = conn.execute("""
            SELECT filename, period_start, period_end FROM bank_statements
            WHERE (property_id = ? OR (property_id IS NULL AND org_id = ?))
              AND substr(period_start, 1, 7) <= ? AND substr(period_end, 1, 7) >= ?
            ORDER BY period_start
        """, (property_id, prop['organization_id'], m, m)).fetchall()
        rows.append({'month': m, 'label': f"{calendar.month_abbr[int(m[5:])]} {m[:4]}",
                     'payments': pay['n'], 'total': float(pay['total']),
                     'units_paid': pay['units'], 'charged': float(charged),
                     'statements': [dict(s) for s in stmts]})

    # Tenants whose tenancy overlaps the range. A payment on their unit within
    # their own tenancy counts as theirs — this works on databases from before
    # and after rows carried tenant_id.
    tenants = conn.execute("""
        SELECT t.id, t.name, t.status, t.move_in_date, t.move_out_date, t.unit_id, u.unit_number
        FROM tenants t JOIN units u ON u.id = t.unit_id
        WHERE t.property_id = ?
          AND COALESCE(substr(t.move_in_date, 1, 7), '0000-00') <= ?
          AND (t.move_out_date IS NULL OR substr(t.move_out_date, 1, 7) >= ?)
        ORDER BY u.unit_number
    """, (property_id, last, first)).fetchall()
    gaps = []
    for t in tenants:
        paid = {r[0] for r in conn.execute(
            "SELECT DISTINCT substr(payment_date, 1, 7) FROM payments WHERE unit_id = ?", (t['unit_id'],))}
        lo = max(first, (t['move_in_date'] or '0000-00')[:7])
        hi = min(last, (t['move_out_date'] or '9999-99')[:7])
        missing = [m for m in span if lo <= m <= hi and m not in paid]
        gaps.append({'unit_number': t['unit_number'], 'name': t['name'], 'status': t['status'],
                     'tenant_id': t['id'], 'missing': missing})

    return {'property': dict(prop), 'months': rows, 'tenants': gaps}

"""Who is behind on rent — the list admins and the caretaker work from.

Reactive, not proactive: nothing here sends anything. It answers "who should we
call today?" the same way on every page.

"Behind" is OVERDUE money, not the whole balance: only charges whose due date
has passed (opening arrears / brought forward always count). November's rent,
generated on the 1st and due on the 5th, does not make anyone behind on the 3rd.
Months behind = overdue ÷ (monthly rent + service charge).

Each tenant's own ledger (rent_charges.tenant_id) — never the unit's history.
"""

import calendar
from datetime import date

FOLLOW_UP_MONTHS = 1.0            # the stakeholders' line: more than one month behind


def _month_label(period):
    if not period or period == '0000-00':
        return 'Opening arrears'
    try:
        y, m = period.split('-')
        return f'{calendar.month_abbr[int(m)]} {y}'
    except (ValueError, IndexError):
        return period


def behind_list(conn, property_id, min_months=FOLLOW_UP_MONTHS, today=None, include_departed=True):
    """Tenants of this property with overdue money, most months behind first.

    min_months=FOLLOW_UP_MONTHS → only those more than a month behind ("Follow up");
    min_months=0 → everyone with anything overdue.
    """
    today = (today or date.today()).isoformat()
    status_list = "'active', 'departed'" if include_departed else "'active'"   # constants, not input
    rows = conn.execute(f"""
        SELECT t.id AS tenant_id, t.name, t.phone, t.status, u.unit_number,
               COALESCE(u.monthly_rent, 0) + COALESCE(u.service_charge, 0) AS monthly,
               COALESCE(tb.balance, 0) AS balance,
               COALESCE(od.overdue, 0) AS overdue, od.oldest,
               lp.payment_date AS last_payment_date, lp.amount AS last_payment_amount,
               COALESCE(pc.pending_amount, 0) AS pending_amount,
               COALESCE(pc.flagged_count, 0) AS flagged_count
        FROM tenants t
        JOIN units u ON u.id = t.unit_id
        LEFT JOIN tenant_balances tb ON tb.tenant_id = t.id
        LEFT JOIN (
            -- this property's charges only, each with what is still unpaid on it
            SELECT tenant_id, SUM(remaining) AS overdue,
                   MIN(CASE WHEN remaining > 0.5
                            THEN CASE WHEN period = 'ARREARS' THEN '0000-00' ELSE period END END) AS oldest
            FROM (
                SELECT rc.tenant_id, rc.period,
                       rc.amount - COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa
                                             WHERE pa.charge_id = rc.id), 0) AS remaining
                FROM rent_charges rc
                WHERE rc.property_id = :pid AND rc.tenant_id IS NOT NULL
                  AND (rc.period = 'ARREARS' OR COALESCE(rc.due_date, rc.period || '-28') <= :today)
            )
            GROUP BY tenant_id
        ) od ON od.tenant_id = t.id
        LEFT JOIN (
            SELECT p.tenant_id, p.payment_date, p.amount FROM payments p
            WHERE p.property_id = :pid
              AND p.id = (SELECT p2.id FROM payments p2 WHERE p2.tenant_id = p.tenant_id
                          ORDER BY p2.payment_date DESC, p2.created_at DESC LIMIT 1)
        ) lp ON lp.tenant_id = t.id
        LEFT JOIN (
            SELECT tenant_id,
                   SUM(CASE WHEN status = 'pending' THEN claimed_amount ELSE 0 END) AS pending_amount,
                   SUM(CASE WHEN status = 'flagged' AND COALESCE(admin_cleared, 0) = 0 THEN 1 ELSE 0 END) AS flagged_count
            FROM payment_claims WHERE property_id = :pid GROUP BY tenant_id
        ) pc ON pc.tenant_id = t.id
        WHERE t.property_id = :pid AND t.status IN ({status_list})
    """, {'pid': property_id, 'today': today}).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        # A payment not yet applied to a charge is still money in: never show more
        # overdue than the account balance.
        d['overdue'] = max(0.0, min(float(d['overdue'] or 0), float(d['balance'] or 0)))
        monthly = float(d['monthly'] or 0)
        d['months_behind'] = round(d['overdue'] / monthly, 1) if monthly > 0 else 0.0
        d['oldest_unpaid'] = _month_label(d['oldest']) if d['overdue'] > 0 else None
        d['follow_up'] = d['months_behind'] > FOLLOW_UP_MONTHS
        if d['overdue'] <= 0:
            continue
        if min_months and d['months_behind'] <= min_months:
            continue
        out.append(d)
    out.sort(key=lambda d: (d['status'] != 'active', -d['months_behind'], -d['overdue']))
    return out


def summary(rows):
    active = [r for r in rows if r['status'] == 'active']
    return {
        'count': len(active),
        'overdue': sum(r['overdue'] for r in active),
        'departed_count': len(rows) - len(active),
        'departed_overdue': sum(r['overdue'] for r in rows if r['status'] != 'active'),
    }

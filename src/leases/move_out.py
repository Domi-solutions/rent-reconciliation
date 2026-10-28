"""
Moving out, from the tenant's side: giving notice, seeing the deposit, asking for the refund.

A tenant gives notice in their portal (a date, an optional reason, the M-Pesa number
for the deposit refund). That opens a conversation with management (topic 'other', so
management is texted and the caretaker is not) and a move_out_notices row management
can mark as seen. The money side — deposit set against anything still owed, the
refund, any debt left — is the existing admin move-out (app.move_out_tenant), which
marks the notice completed. The lease asks for one month's notice; shorter notice is
recorded as given, never refused (a tenant who leaves early still has to be handled).
"""

from datetime import date, datetime, timedelta

from src.database.db import generate_id
from src.messaging import threads
from src.platform.guardian import platform_log
from src.utils.phone import normalize_to_e164

NOTICE_DAYS = 30
REASONS = ('Moving to another house', 'Work or school', 'The rent', 'A problem with the house', 'Other')
OPEN = ('given', 'acknowledged')


def current(conn, tenant_id):
    """The tenant's notice that is still open, or None."""
    row = conn.execute(f"""
        SELECT * FROM move_out_notices WHERE tenant_id = ? AND status IN {OPEN}
        ORDER BY rowid DESC LIMIT 1
    """, (tenant_id,)).fetchone()
    return dict(row) if row else None


def estimate(deposit, still_owed):
    """What the deposit covers if nothing else is owed at the inspection."""
    deposit = float(deposit or 0)
    owed = max(float(still_owed or 0), 0)
    return {'deposit': deposit, 'owed': owed,
            'refund': max(deposit - owed, 0), 'left_owing': max(owed - deposit, 0)}


def lease_terms_for(conn, tenant_id):
    """Notice days, and the fixed term's end and early-exit terms, from the tenant's lease."""
    row = conn.execute("""SELECT notice_days, term_type, end_date, early_exit_terms FROM leases
                           WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 1""", (tenant_id,)).fetchone()
    if not row:
        return {'notice_days': NOTICE_DAYS, 'end_date': None, 'early_exit_terms': None}
    return {'notice_days': row['notice_days'] if row['notice_days'] is not None else NOTICE_DAYS,
            'end_date': row['end_date'] if row['term_type'] == 'fixed' else None,
            'early_exit_terms': row['early_exit_terms']}


def earliest_with_full_notice(today=None, days=NOTICE_DAYS):
    return (today or date.today()) + timedelta(days=days)


def give_notice(conn, tenant, unit, prop, planned, reason, refund_phone, today=None):
    """Record the notice and open the conversation. Returns (notice_id, conversation_id) or
    raises ValueError with a message for the tenant."""
    today = today or date.today()
    try:
        planned_date = datetime.strptime(planned or '', '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('Choose the date you plan to move out.')
    if planned_date <= today:
        raise ValueError('Choose a date after today.')
    phone = normalize_to_e164(refund_phone or '') if refund_phone else None
    if refund_phone and not phone:
        raise ValueError('That M-Pesa number is not a full phone number — use the 10 digits.')
    if current(conn, tenant['id']):
        raise ValueError('You have already given notice. Withdraw it first to give a new date.')
    days = (planned_date - today).days
    lease = lease_terms_for(conn, tenant['id'])
    short = days < lease['notice_days']
    early = bool(lease['end_date']) and planned_date.isoformat() < lease['end_date'][:10]
    reason = (reason or '').strip()[:300]
    on_record = normalize_to_e164(tenant.get('phone') or '')
    body = (f"I am giving notice to move out of unit {unit['unit_number']} on {planned_date:%-d %B %Y} "
            f"({days} days from today{', less than the notice my lease asks for' if short else ''}).")
    if early:
        body += f"\nThis is before my fixed-term lease ends on {lease['end_date'][:10]}." 
    if reason:
        body += f"\nReason: {reason}"
    if phone:
        body += f"\nPlease send my deposit refund to M-Pesa {phone}"
        body += "." if phone == on_record else " (not the number on my tenancy)."
    conv_id, _, _ = threads.start_conversation(
        conn, tenant, unit, prop, 'other', f'Notice to move out — {planned_date:%-d %b %Y}', body)
    notice_id = generate_id('MOVE')
    conn.execute("""
        INSERT INTO move_out_notices (id, tenant_id, property_id, unit_id, planned_date, given_on, notice_days,
                                      reason, refund_phone, conversation_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (notice_id, tenant['id'], prop['id'], unit['id'], planned_date.isoformat(), today.isoformat(), days,
          reason or None, phone, conv_id))
    details = (f"Unit {unit['unit_number']} | moving out {planned_date} | {days} days' notice"
               f"{' (short)' if short else ''} | refund to {'number on record' if phone == on_record else 'another number' if phone else 'not given'}")
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('move_out_notice_given', 'tenant', tenant['id'], details, 'tenant'))
    platform_log(conn, 'move_out_notice_given', 'tenant', tenant['id'], details,
                 org_id=prop.get('organization_id'), property_id=prop['id'], actor='tenant')
    return notice_id, conv_id


def withdraw(conn, notice, tenant, unit):
    """The tenant takes the notice back (only before the move-out is recorded)."""
    conn.execute("UPDATE move_out_notices SET status = 'withdrawn', withdrawn_at = CURRENT_TIMESTAMP WHERE id = ?",
                 (notice['id'],))
    if notice['conversation_id']:
        threads.add_message(conn, notice['conversation_id'], 'tenant', tenant['name'],
                            'I am withdrawing my notice to move out. I am staying.')
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('move_out_notice_withdrawn', 'tenant', tenant['id'], f"Unit {unit['unit_number']}", 'tenant'))


def acknowledge(conn, notice_id, property_id, actor):
    """Management has seen the notice."""
    conn.execute("""UPDATE move_out_notices SET status = 'acknowledged', acknowledged_at = CURRENT_TIMESTAMP,
                    acknowledged_by = ? WHERE id = ? AND property_id = ? AND status = 'given'""",
                 (actor, notice_id, property_id))


def complete_for_tenant(conn, tenant_id):
    """The move-out was recorded (app.move_out_tenant): close any open notice."""
    conn.execute(f"""UPDATE move_out_notices SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                     WHERE tenant_id = ? AND status IN {OPEN}""", (tenant_id,))


def for_property(conn, property_id):
    """Open notices first (soonest move-out first), then the last few closed ones."""
    return [dict(r) for r in conn.execute("""
        SELECT n.*, t.name AS tenant_name, t.phone AS tenant_phone, t.deposit_paid, u.unit_number,
               COALESCE(tb.balance, 0) AS balance
        FROM move_out_notices n JOIN tenants t ON t.id = n.tenant_id
        LEFT JOIN units u ON u.id = n.unit_id
        LEFT JOIN tenant_balances tb ON tb.tenant_id = n.tenant_id
        WHERE n.property_id = ?
        ORDER BY CASE WHEN n.status IN ('given', 'acknowledged') THEN 0 ELSE 1 END, n.planned_date
        LIMIT 50
    """, (property_id,))]


def open_count(conn, property_id):
    return conn.execute(f"SELECT COUNT(*) FROM move_out_notices WHERE property_id = ? AND status IN {OPEN}",
                        (property_id,)).fetchone()[0]

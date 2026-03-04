"""Automatic due-date reminder generation. Idempotent per property per day per template_key."""

from datetime import date

from src.database.db import generate_id


def _substitute(body_or_subject, variables):
    out = body_or_subject
    for k, v in variables.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def generate_due_reminders(conn, property_id):
    """
    For the current property, check enabled reminder_settings; for each setting where
    today == due_date - days_before_due for some charge, create one message per
    active tenant (with unit_id and balance > 0) that hasn't already received this
    reminder today. Uses batch_id 'reminder-{date}-{template_key}' for idempotency.
    """
    today = date.today().isoformat()

    settings = conn.execute("""
        SELECT rs.id, rs.template_key, rs.days_before_due
        FROM reminder_settings rs
        WHERE rs.property_id = ? AND rs.enabled = 1
    """, (property_id,)).fetchall()

    for row in settings:
        template_key = row['template_key']
        days_before = int(row['days_before_due']) if row['days_before_due'] is not None else 0
        batch_id = f"reminder-{today}-{template_key}"

        existing = conn.execute(
            "SELECT 1 FROM messages WHERE property_id = ? AND batch_id = ? LIMIT 1",
            (property_id, batch_id),
        ).fetchone()
        if existing:
            continue

        template = conn.execute("""
            SELECT id, subject, body FROM message_templates
            WHERE (property_id = ? OR property_id IS NULL) AND template_key = ? AND enabled = 1
            ORDER BY property_id DESC NULLS LAST
            LIMIT 1
        """, (property_id, template_key)).fetchone()
        if not template:
            continue

        target_due = conn.execute("""
            SELECT 1 FROM rent_charges rc
            WHERE rc.property_id = ? AND rc.due_date IS NOT NULL
            AND rc.due_date = date(?, '+' || ? || ' days')
            LIMIT 1
        """, (property_id, today, days_before)).fetchone()
        if not target_due:
            continue

        tenants_with_balance = conn.execute("""
            SELECT
                t.id AS tenant_id,
                t.name AS tenant_name,
                t.unit_id,
                u.unit_number,
                u.monthly_rent,
                u.service_charge,
                ub.balance,
                ub.total_paid,
                rc.period,
                rc.amount AS charge_amount,
                rc.due_date AS charge_due_date
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            JOIN unit_balances ub ON ub.unit_id = t.unit_id
            JOIN rent_charges rc ON rc.unit_id = t.unit_id AND rc.property_id = ?
                 AND rc.due_date IS NOT NULL
                 AND rc.due_date = date(?, '+' || ? || ' days')
            WHERE t.property_id = ? AND t.status = 'active'
              AND t.unit_id IS NOT NULL
              AND ub.balance > 0
        """, (property_id, today, days_before, property_id)).fetchall()

        seen_tenants = set()
        for t in tenants_with_balance:
            if t['tenant_id'] in seen_tenants:
                continue
            seen_tenants.add(t['tenant_id'])
            variables = {
                'tenant_name': t['tenant_name'] or '',
                'unit_number': t['unit_number'] or '',
                'balance': f"{float(t['balance']):,.0f}",
                'month': t['period'],
                'amount': f"{float(t['charge_amount']):,.0f}",
                'due_date': t['charge_due_date'] or '',
                'monthly_rent': f"{float(t['monthly_rent'] or 0):,.0f}",
                'service_charge': f"{float(t['service_charge'] or 0):,.0f}",
                'total_paid': f"{float(t['total_paid'] or 0):,.0f}",
            }
            subject = _substitute(template['subject'], variables)
            body = _substitute(template['body'], variables)
            msg_id = generate_id('MSG')
            conn.execute("""
                INSERT INTO messages (id, property_id, tenant_id, batch_id, subject, body, message_type, delivery_channel, delivery_status)
                VALUES (?, ?, ?, ?, ?, ?, 'reminder', 'portal', 'delivered')
            """, (msg_id, property_id, t['tenant_id'], batch_id, subject, body))

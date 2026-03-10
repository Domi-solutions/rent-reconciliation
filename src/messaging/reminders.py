"""Automatic due-date reminder generation. Idempotent per property per day per template_key."""

import os
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
                t.phone,
                t.access_token,
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

            if t['phone']:
                try:
                    from src.messaging.delivery import send_sms
                    app_base = os.environ.get('APP_BASE_URL', '').rstrip('/')
                    sms_text = body
                    if app_base and t['access_token']:
                        sms_text += f"\n\nView account: {app_base}/tenant/{t['access_token']}"
                    send_sms([{'phone': t['phone']}], sms_text)
                except Exception:
                    pass

    # --- Process reminder_schedules (flexible system) ---
    prop_row = conn.execute("SELECT name FROM properties WHERE id = ?", (property_id,)).fetchone()
    property_name = prop_row['name'] if prop_row else property_id

    schedules = conn.execute("""
        SELECT id, label, template_key, days_before_due, send_to
        FROM reminder_schedules
        WHERE property_id = ? AND enabled = 1
    """, (property_id,)).fetchall()

    for sched in schedules:
        days_before = int(sched['days_before_due'] or 0)
        batch_id = f"rschedule-{today}-{sched['id']}"

        if conn.execute(
            "SELECT 1 FROM messages WHERE property_id = ? AND batch_id = ? LIMIT 1",
            (property_id, batch_id),
        ).fetchone():
            continue

        template = conn.execute("""
            SELECT id, subject, body FROM message_templates
            WHERE (property_id = ? OR property_id IS NULL) AND template_key = ? AND enabled = 1
            ORDER BY property_id DESC NULLS LAST LIMIT 1
        """, (property_id, sched['template_key'])).fetchone()
        if not template:
            continue

        due_row = conn.execute("""
            SELECT MIN(rc.due_date) AS due_date FROM rent_charges rc
            WHERE rc.property_id = ? AND rc.due_date IS NOT NULL
            AND rc.due_date = date(?, '+' || ? || ' days')
        """, (property_id, today, days_before)).fetchone()
        if not due_row or not due_row['due_date']:
            continue

        due_date_val = due_row['due_date']
        send_to = (sched['send_to'] or 'arrears').strip()
        balance_filter = "AND ub.balance > 0" if send_to == 'arrears' else ""

        recipients = conn.execute(f"""
            SELECT t.id AS tenant_id, t.name AS tenant_name, t.phone, t.access_token,
                   t.unit_id, u.unit_number, u.monthly_rent, u.service_charge,
                   ub.balance, ub.total_paid,
                   rc.period, rc.amount AS charge_amount
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            JOIN unit_balances ub ON ub.unit_id = t.unit_id
            JOIN rent_charges rc ON rc.unit_id = t.unit_id AND rc.property_id = ?
                 AND rc.due_date = ?
            WHERE t.property_id = ? AND t.status = 'active' AND t.unit_id IS NOT NULL
            {balance_filter}
        """, (property_id, due_date_val, property_id)).fetchall()

        seen = set()
        sent_count = 0
        for t in recipients:
            if t['tenant_id'] in seen:
                continue
            seen.add(t['tenant_id'])
            variables = {
                'tenant_name': t['tenant_name'] or '',
                'unit_number': t['unit_number'] or '',
                'balance': f"{float(t['balance'] or 0):,.0f}",
                'month': t['period'] or '',
                'amount': f"{float(t['charge_amount'] or 0):,.0f}",
                'due_date': due_date_val,
                'monthly_rent': f"{float(t['monthly_rent'] or 0):,.0f}",
                'service_charge': f"{float(t['service_charge'] or 0):,.0f}",
                'total_paid': f"{float(t['total_paid'] or 0):,.0f}",
                'days_to_due': str(days_before),
            }
            subj = _substitute(template['subject'], variables)
            body = _substitute(template['body'], variables)
            msg_id = generate_id('MSG')
            conn.execute("""
                INSERT INTO messages (id, property_id, tenant_id, batch_id, subject, body,
                                      message_type, delivery_channel, delivery_status)
                VALUES (?, ?, ?, ?, ?, ?, 'reminder', 'portal', 'delivered')
            """, (msg_id, property_id, t['tenant_id'], batch_id, subj, body))

            if t['phone']:
                try:
                    from src.messaging.delivery import send_sms
                    app_base = os.environ.get('APP_BASE_URL', '').rstrip('/')
                    sms_text = body
                    if app_base and t['access_token']:
                        sms_text += f"\n\nView account: {app_base}/tenant/{t['access_token']}"
                    send_sms([{'phone': t['phone']}], sms_text)
                except Exception:
                    pass
            sent_count += 1

        if sent_count > 0:
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('reminder_sent', 'reminder', batch_id,
                 f"{sched['label']} ({days_before}d before due) — {sent_count} tenants notified | {send_to}", 'system')
            )
            try:
                from src.messaging.owner_notify import notify_property_owners
                owner_msg = (
                    f"Reminder sent: {sent_count} tenant(s) notified — {property_name}.\n"
                    f"'{sched['label']}' — {days_before} days before {due_date_val}."
                )
                notify_property_owners(conn, property_id, owner_msg, sent_by='System')
            except Exception:
                pass

"""Admin messaging: broadcasts, templates, and reminder settings. Scoped to current property."""

import os
from datetime import date, timedelta
import calendar

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database.db import get_connection, generate_id

messaging_bp = Blueprint('messaging', __name__, url_prefix='/messages')


def get_current_property(conn):
    """Current property from session, scoped to session org if set."""
    pid = session.get('property_id')
    if pid:
        org_id = session.get('org_id')
        if org_id:
            prop = conn.execute(
                "SELECT * FROM properties WHERE id = ? AND status = 'active' AND organization_id = ?",
                (pid, org_id),
            ).fetchone()
        else:
            prop = conn.execute(
                "SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)
            ).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


def _substitute(text, variables):
    if not text:
        return text
    out = text
    for k, v in variables.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _next_due_date(rent_due_day):
    """Return next due date string given property.rent_due_day (0 = last day of month)."""
    today = date.today()
    if rent_due_day == 0:
        last = calendar.monthrange(today.year, today.month)[1]
        d = date(today.year, today.month, last)
        if d < today:
            if today.month == 12:
                d = date(today.year + 1, 1, 31)
            else:
                nm = today.month + 1
                d = date(today.year, nm, calendar.monthrange(today.year, nm)[1])
    else:
        d = date(today.year, today.month, min(rent_due_day, calendar.monthrange(today.year, today.month)[1]))
        if d < today:
            if today.month == 12:
                d = date(today.year + 1, 1, min(rent_due_day, 31))
            else:
                nm = today.month + 1
                d = date(today.year, nm, min(rent_due_day, calendar.monthrange(today.year, nm)[1]))
    return d.strftime('%-d %b %Y')


def _build_variables(conn, tenant, prop, base_url=None):
    """Build substitution variables for a single tenant."""
    unit_info = conn.execute(
        "SELECT monthly_rent, service_charge FROM units WHERE id = ?",
        (tenant['unit_id'],)
    ).fetchone() if tenant['unit_id'] else None

    bal_row = conn.execute(
        "SELECT balance, total_paid FROM unit_balances WHERE unit_id = ?",
        (tenant['unit_id'],)
    ).fetchone() if tenant['unit_id'] else None

    caretaker = conn.execute(
        "SELECT phone FROM caretakers WHERE property_id = ? AND phone IS NOT NULL ORDER BY created_at LIMIT 1",
        (prop['id'],)
    ).fetchone()

    base = (base_url or os.environ.get('APP_BASE_URL', request.host_url)).rstrip('/')
    short_code = tenant['short_code'] if 'short_code' in tenant.keys() else None
    portal_link = f"{base}/t/{short_code}" if short_code else ''

    rent_due_day = prop['rent_due_day'] if prop and 'rent_due_day' in prop.keys() else 1

    return {
        'tenant_name': tenant['name'] or '',
        'unit_number': tenant['unit_number'] or '',
        'balance': f"{float(bal_row['balance'] or 0):,.0f}" if bal_row else '0',
        'monthly_rent': f"{float(unit_info['monthly_rent'] or 0):,.0f}" if unit_info else '0',
        'service_charge': f"{float(unit_info['service_charge'] or 0):,.0f}" if unit_info else '0',
        'total_paid': f"{float(bal_row['total_paid'] or 0):,.0f}" if bal_row else '0',
        'due_date': _next_due_date(rent_due_day),
        'caretaker_phone': caretaker['phone'] if caretaker else '',
        'portal_link': portal_link,
        'month': '',
        'amount': '',
    }


@messaging_bp.route('/')
def dashboard():
    """Messaging dashboard: counts, recent activity, and quick-send search."""
    import json
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        total_messages = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE property_id = ?", (pid,)
        ).fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages m WHERE m.property_id = ? AND m.read_at IS NULL",
            (pid,),
        ).fetchone()[0]
        recent = conn.execute("""
            SELECT m.id, m.tenant_id, m.subject, m.message_type, m.created_at, t.name AS tenant_name
            FROM messages m
            JOIN tenants t ON m.tenant_id = t.id
            WHERE m.property_id = ?
            ORDER BY m.created_at DESC LIMIT 10
        """, (pid,)).fetchall()

        reminder_count = conn.execute(
            "SELECT COUNT(*) FROM reminder_settings WHERE property_id = ? AND enabled = 1",
            (pid,),
        ).fetchone()[0]

        tenant_rows = conn.execute("""
            SELECT t.id, t.name, t.phone, u.unit_number,
                   COALESCE(ub.balance, 0) AS balance
            FROM tenants t
            LEFT JOIN units u ON t.unit_id = u.id
            LEFT JOIN unit_balances ub ON ub.unit_id = u.id
            WHERE t.property_id = ? AND t.status = 'active'
            ORDER BY u.unit_number
        """, (pid,)).fetchall()
        tenants_json = json.dumps([dict(r) for r in tenant_rows])

    return render_template(
        'messaging/dashboard.html',
        property=prop,
        total_messages=total_messages,
        unread_count=unread,
        recent_messages=recent,
        reminder_count=reminder_count,
        tenants_json=tenants_json,
    )


@messaging_bp.route('/thread/<tenant_id>', methods=['GET', 'POST'])
def thread(tenant_id):
    """Chat-style message thread for a single tenant."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        tenant = conn.execute("""
            SELECT t.*, u.unit_number FROM tenants t
            LEFT JOIN units u ON t.unit_id = u.id
            WHERE t.id = ? AND t.property_id = ?
        """, (tenant_id, pid)).fetchone()
        if not tenant:
            flash('Tenant not found.', 'error')
            return redirect(url_for('messaging.dashboard'))

        if request.method == 'POST':
            subject = request.form.get('subject', '').strip()
            body = request.form.get('body', '').strip()
            channel = request.form.get('channel', 'portal')
            if subject:
                variables = _build_variables(conn, tenant, prop)
                subj = _substitute(subject, variables)
                b = _substitute(body, variables)
                msg_id = generate_id('MSG')
                conn.execute("""
                    INSERT INTO messages
                        (id, property_id, tenant_id, subject, body, template_body,
                         message_type, delivery_channel, delivery_status)
                    VALUES (?, ?, ?, ?, ?, ?, 'broadcast', ?, 'delivered')
                """, (msg_id, pid, tenant_id, subj, b, body, channel))
                if channel == 'sms' and tenant['phone']:
                    from src.messaging.delivery import send_sms_async
                    sms_text = f"{subj}\n\n{b}" if b else subj
                    send_sms_async([{'phone': tenant['phone']}], sms_text)
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)"
                    " VALUES (?, ?, ?, ?, ?)",
                    ('message_sent', 'tenant', tenant_id,
                     f"Subject: {subject[:50]} | Channel: {channel}", 'admin'),
                )
            return redirect(url_for('messaging.thread', tenant_id=tenant_id))

        # Outbound messages (admin → tenant)
        outbound = conn.execute("""
            SELECT subject, body, delivery_channel AS channel, created_at, 'out' AS direction
            FROM messages
            WHERE tenant_id = ? AND property_id = ?
            ORDER BY created_at
        """, (tenant_id, pid)).fetchall()

        # Inbound messages from tenant's phone
        inbound = []
        if tenant['phone']:
            from src.utils.phone import normalize_to_e164
            phone_e164 = normalize_to_e164(tenant['phone'])
            if phone_e164:
                inbound = conn.execute("""
                    SELECT NULL AS subject, raw_body AS body, channel,
                           received_at AS created_at, 'in' AS direction
                    FROM inbound_messages
                    WHERE sender_phone = ?
                    ORDER BY received_at
                """, (phone_e164,)).fetchall()

        messages = sorted(
            [dict(r) for r in outbound] + [dict(r) for r in inbound],
            key=lambda x: x['created_at'] or ''
        )

        templates = conn.execute("""
            SELECT id, template_key, subject, body
            FROM message_templates
            WHERE (property_id = ? OR property_id IS NULL) AND enabled = 1
            ORDER BY property_id DESC NULLS LAST
        """, (pid,)).fetchall()

    return render_template('messaging/thread.html',
        property=prop,
        tenant=tenant,
        messages=messages,
        templates=templates,
    )


@messaging_bp.route('/broadcast', methods=['GET', 'POST'])
def broadcast():
    """Send broadcast: select recipients, pick template, preview, send."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        tenants = conn.execute("""
            SELECT t.id, t.name, t.phone, t.unit_id, t.short_code, u.unit_number
            FROM tenants t
            LEFT JOIN units u ON t.unit_id = u.id
            WHERE t.property_id = ?
            ORDER BY t.name
        """, (pid,)).fetchall()

        templates = conn.execute("""
            SELECT id, template_key, subject, body
            FROM message_templates
            WHERE (property_id = ? OR property_id IS NULL) AND enabled = 1
            ORDER BY property_id DESC NULLS LAST
        """, (pid,)).fetchall()

        # Per-unit financial context for variables
        unit_data = conn.execute(
            "SELECT id, monthly_rent, service_charge FROM units WHERE property_id = ?",
            (pid,),
        ).fetchall()
        rent_by_unit = {u['id']: u for u in unit_data}

        balance_rows = conn.execute(
            "SELECT unit_id, total_paid, balance FROM unit_balances WHERE property_id = ?",
            (pid,),
        ).fetchall()
        balance_by_unit = {r['unit_id']: float(r['balance'] or 0) for r in balance_rows}
        paid_by_unit = {r['unit_id']: float(r['total_paid'] or 0) for r in balance_rows}

        if request.method == 'POST':
            recipient_ids = request.form.getlist('tenant_ids')
            if not recipient_ids:
                flash('Select at least one recipient.', 'error')
                return redirect(url_for('messaging.broadcast'))
            subject = request.form.get('subject', '').strip()
            body = request.form.get('body', '').strip()
            channel = request.form.get('channel', 'portal').strip()
            if not subject:
                flash('Subject is required.', 'error')
                return redirect(url_for('messaging.broadcast'))

            delivery_channel = 'sms' if channel == 'sms' else 'portal'
            batch_id = generate_id('BATCH')
            selected_tenants = []
            for tenant_id in recipient_ids:
                t = next((x for x in tenants if x['id'] == tenant_id), None)
                if not t:
                    continue
                selected_tenants.append(t)
                variables = _build_variables(conn, t, prop)
                subj = _substitute(subject, variables)
                b = _substitute(body, variables)
                msg_id = generate_id('MSG')
                conn.execute("""
                    INSERT INTO messages (id, property_id, tenant_id, batch_id, subject, body, template_body, message_type, delivery_channel, delivery_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'broadcast', ?, 'delivered')
                """, (msg_id, pid, tenant_id, batch_id, subj, b, body, delivery_channel))

                if delivery_channel == 'portal' and t['phone'] and t['short_code']:
                    try:
                        from src.messaging.delivery import send_sms
                        _base = (os.environ.get('APP_BASE_URL', request.host_url)).rstrip('/')
                        _ping = f"Hi {t['name']}, you have a new message. View: {_base}/t/{t['short_code']}"
                        send_sms([{'phone': t['phone']}], _ping)
                    except Exception:
                        pass

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('broadcast_sent', 'message', batch_id,
                 f"Subject: {subject[:50]} | Recipients: {len(recipient_ids)} | Channel: {delivery_channel}", 'admin'),
            )

            try:
                from src.messaging.owner_notify import notify_property_owners
                _base = request.host_url.rstrip('/')
                _sms = (
                    f"Broadcast sent to {len(recipient_ids)} tenant(s) — {prop['name']}.\n"
                    f"'{subject[:50]}'.\n"
                    f"View: {_base}/view/{pid}"
                )
                notify_property_owners(
                    conn, pid, _sms,
                    portal_subject=subject[:100],
                    portal_body=body,
                    template_body=body,
                    channel=delivery_channel,
                    recipient_count=len(recipient_ids),
                    message_type='broadcast',
                    sent_by='Admin',
                )
            except Exception:
                pass

            if channel == 'sms':
                from src.messaging.delivery import send_sms
                # Send per-tenant so each gets their own substituted body
                sent, failed, errors = 0, 0, []
                for t in selected_tenants:
                    if not t['phone']:
                        failed += 1
                        continue
                    v = _build_variables(conn, t, prop)
                    sms_text = _substitute(f"{subject}\n\n{body}", v)
                    s, f, e = send_sms([{'phone': t['phone']}], sms_text)
                    sent += s
                    failed += f
                    errors += e
                if failed == 0:
                    flash(f'SMS sent to {sent} tenant(s).', 'success')
                elif sent > 0:
                    flash(f'SMS sent to {sent} tenant(s); {failed} failed.', 'success')
                else:
                    flash(f'SMS delivery failed: {errors[0] if errors else "unknown error"}', 'error')
            else:
                flash(f'Broadcast sent to {len(recipient_ids)} tenant(s).', 'success')
            return redirect(url_for('messaging.dashboard'))

    return render_template(
        'messaging/broadcast.html',
        property=prop,
        tenants=tenants,
        templates=templates,
    )


@messaging_bp.route('/templates')
def templates_list():
    """List all templates (system defaults + property overrides)."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        template_list = conn.execute("""
            SELECT id, property_id, template_key, subject, body, enabled, updated_at
            FROM message_templates
            WHERE property_id = ? OR property_id IS NULL
            ORDER BY property_id DESC NULLS LAST, template_key
        """, (pid,)).fetchall()

    return render_template(
        'messaging/templates.html',
        property=prop,
        templates=template_list,
    )


@messaging_bp.route('/templates/<template_id>/edit', methods=['GET', 'POST'])
def edit_template(template_id):
    """Edit template subject, body, enabled."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))

        row = conn.execute(
            "SELECT id, property_id, template_key, subject, body, enabled FROM message_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if not row:
            flash('Template not found.', 'error')
            return redirect(url_for('messaging.templates_list'))

        if request.method == 'POST':
            subject = request.form.get('subject', '').strip()
            body = request.form.get('body', '')
            enabled = 1 if request.form.get('enabled') == '1' else 0
            conn.execute(
                "UPDATE message_templates SET subject = ?, body = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (subject, body, enabled, template_id),
            )
            flash('Template updated.', 'success')
            return redirect(url_for('messaging.templates_list'))

    return render_template(
        'messaging/edit_template.html',
        property=prop,
        template=row,
    )


@messaging_bp.route('/reminders', methods=['GET', 'POST'])
def reminders():
    """Toggle and configure reminder types (days_before_due) for current property."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        default_keys = ['rent_due_10d', 'rent_due_5d', 'rent_due_today', 'water_cutoff']
        default_days = {'rent_due_10d': 10, 'rent_due_5d': 5, 'rent_due_today': 0, 'water_cutoff': 0}

        if request.method == 'POST':
            for key in default_keys:
                enabled = 1 if request.form.get(f'enabled_{key}') == '1' else 0
                try:
                    days = int(request.form.get(f'days_{key}', default_days[key]))
                except (TypeError, ValueError):
                    days = default_days[key]
                existing = conn.execute(
                    "SELECT id FROM reminder_settings WHERE property_id = ? AND template_key = ?",
                    (pid, key),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE reminder_settings SET days_before_due = ?, enabled = ? WHERE id = ?",
                        (days, enabled, existing['id']),
                    )
                else:
                    rid = generate_id('RSET')
                    conn.execute(
                        "INSERT INTO reminder_settings (id, property_id, template_key, days_before_due, enabled) VALUES (?, ?, ?, ?, ?)",
                        (rid, pid, key, days, enabled),
                    )
            flash('Reminder settings saved.', 'success')
            return redirect(url_for('messaging.reminders'))

        settings = conn.execute(
            "SELECT template_key, days_before_due, enabled FROM reminder_settings WHERE property_id = ?",
            (pid,),
        ).fetchall()
        by_key = {r['template_key']: r for r in settings}
        rows = []
        for key in default_keys:
            r = by_key.get(key)
            rows.append({
                'template_key': key,
                'days_before_due': r['days_before_due'] if r else default_days[key],
                'enabled': r['enabled'] if r else 0,
            })

    return render_template(
        'messaging/reminders.html',
        property=prop,
        settings=rows,
    )


@messaging_bp.route('/schedules', methods=['GET', 'POST'])
def reminder_schedules():
    """Manage flexible reminder schedules for current property."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                label = (request.form.get('label') or 'Rent reminder').strip()
                template_key = (request.form.get('template_key') or 'rent_due_reminder').strip()
                try:
                    days = int(request.form.get('days_before_due', 5))
                    days = max(0, days)
                except (TypeError, ValueError):
                    days = 5
                send_to = request.form.get('send_to', 'arrears')
                if send_to not in ('all', 'arrears'):
                    send_to = 'arrears'
                sched_id = generate_id('RSCH')
                conn.execute("""
                    INSERT INTO reminder_schedules (id, property_id, label, template_key, days_before_due, send_to, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (sched_id, pid, label, template_key, days, send_to))
                flash('Reminder schedule added.', 'success')
            elif action == 'delete':
                sched_id = request.form.get('sched_id', '').strip()
                conn.execute(
                    "DELETE FROM reminder_schedules WHERE id = ? AND property_id = ?", (sched_id, pid)
                )
                flash('Reminder schedule removed.', 'success')
            elif action == 'toggle':
                sched_id = request.form.get('sched_id', '').strip()
                conn.execute("""
                    UPDATE reminder_schedules
                    SET enabled = CASE WHEN enabled = 1 THEN 0 ELSE 1 END
                    WHERE id = ? AND property_id = ?
                """, (sched_id, pid))
                flash('Schedule updated.', 'success')
            return redirect(url_for('messaging.reminder_schedules'))

        schedules = conn.execute(
            "SELECT * FROM reminder_schedules WHERE property_id = ? ORDER BY days_before_due DESC",
            (pid,)
        ).fetchall()
        templates = conn.execute(
            "SELECT DISTINCT template_key FROM message_templates WHERE (property_id = ? OR property_id IS NULL) AND enabled = 1 ORDER BY template_key",
            (pid,)
        ).fetchall()

    return render_template('messaging/schedules.html', property=prop, schedules=schedules, templates=templates)

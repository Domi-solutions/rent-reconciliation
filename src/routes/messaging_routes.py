"""Admin messaging: broadcasts, templates, and reminder settings. Scoped to current property."""

import os
from datetime import date, timedelta
import calendar

from flask import Blueprint, abort, render_template, request, redirect, url_for, flash, session, send_from_directory
from src.utils import staff
from src.database.db import get_connection, generate_id
from src.messaging import threads
from src.utils.phone import normalize_to_e164 as _normalize_phone

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
    """Replace {variable} placeholders; the money line is worded by src/messaging/wording.py."""
    from src.messaging.wording import fill
    return fill(text, variables)


def _sms_lead(prop):
    """How this property's SMS start: "Kijani Court: ", or nothing when it has its own sender
    name (the phone already shows it — src/messaging/delivery.sender_for)."""
    from src.messaging.delivery import sender_for
    lead = prop['name'].title() + ': '
    return '' if sender_for(lead + 'x', prop['id'])[1] == 'X' else lead


def _link_sms():
    """A sign-in link goes by SMS only once tenant outreach is approved (src/messaging/outreach.py)."""
    from src.messaging.outreach import enabled
    return enabled()


def _tenant_sms(prop, tenant, subject, body):
    """The SMS for one message to one tenant: a note and their sign-in link — the message is
    read (and answered) in the portal. Before sign-in links are switched on, the title and
    message themselves. Broadcasts and rent reminders are texted in full instead."""
    name = prop['name'].title()
    code = tenant['short_code'] if 'short_code' in tenant.keys() else None
    if _link_sms() and code:
        base = (os.environ.get('APP_BASE_URL') or request.host_url).rstrip('/')
        title = '' if subject == 'Message from management' else f': "{subject[:60]}"'
        return f"{name}: new message from management{title}. Read and reply in Domi: {base}/t/{code}"
    text = body if subject == 'Message from management' else f"{subject}\n{body}"
    return text if text.lower().startswith(prop['name'].lower()) else f"{name}: {text}"


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

    # The person's own balance — a departed tenant must not be shown the new occupant's.
    bal_row = conn.execute(
        "SELECT balance, total_paid FROM tenant_balances WHERE tenant_id = ?",
        (tenant['id'],)
    ).fetchone()

    caretaker = conn.execute(
        "SELECT phone FROM caretakers WHERE property_id = ? AND phone IS NOT NULL ORDER BY created_at LIMIT 1",
        (prop['id'],)
    ).fetchone()

    base = (base_url or os.environ.get('APP_BASE_URL', request.host_url)).rstrip('/')
    short_code = tenant['short_code'] if 'short_code' in tenant.keys() else None
    # A portal link is a sign-in invitation: none until tenant outreach is approved.
    from src.messaging.outreach import enabled as _outreach_enabled
    portal_link = f"{base}/t/{short_code}" if short_code and _outreach_enabled() else ''

    rent_due_day = prop['rent_due_day'] if prop and 'rent_due_day' in prop.keys() else 1

    return {
        'tenant_name': tenant['name'] or '',
        'unit_number': tenant['unit_number'] or '',
        'balance': f"{float(bal_row['balance'] or 0):,.0f}" if bal_row else '0',
        'balance_value': float(bal_row['balance'] or 0) if bal_row else 0.0,
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
            # One box, one Send. The message is kept in the tenant's portal and the tenant is
            # texted: a short note with their sign-in link (they read and act in the portal).
            # Until sign-in links are switched on (TENANT_OUTREACH_ENABLED), the text carries
            # the message itself so it still reaches them.
            body = request.form.get('body', '').strip()
            subject = request.form.get('subject', '').strip() or 'Message from management'
            if not body:
                flash('Write a message first.', 'error')
                return redirect(url_for('messaging.thread', tenant_id=tenant_id))
            variables = _build_variables(conn, tenant, prop)
            subj, b = _substitute(subject, variables), _substitute(body, variables)
            sms_text = _tenant_sms(prop, tenant, subj, b) if tenant['phone'] else None
            channel = 'sms' if sms_text else 'portal'
            conn.execute("""
                INSERT INTO messages
                    (id, property_id, tenant_id, subject, body, template_body,
                     message_type, delivery_channel, delivery_status)
                VALUES (?, ?, ?, ?, ?, ?, 'broadcast', ?, 'delivered')
            """, (generate_id('MSG'), pid, tenant_id, subj, b, body, channel))
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)"
                " VALUES (?, ?, ?, ?, ?)",
                ('message_sent', 'tenant', tenant_id,
                 f"Subject: {subj[:50]} | Channel: {channel}", staff.actor()),
            )
    if request.method == 'POST':
        if not sms_text:
            flash('Posted in the tenant\'s portal. No phone number on record, so no SMS went out.', 'warning')
        else:                                    # after the message is saved, so the record never waits on the SMS
            from src.messaging.delivery import send_sms
            sent, _, errors = send_sms([{'phone': tenant['phone']}], sms_text)
            if sent:
                flash(f"Sent. {tenant['phone']} got an SMS"
                      + (" with a link to read it." if _link_sms() else " with the message."), 'success')
            elif errors and 'disabled' in str(errors[0]):
                flash('Saved in the tenant\'s portal. SMS sending is switched off on this system, so no SMS went out.', 'warning')
            else:
                flash(f"Saved in the tenant's portal, but the SMS did not go out: {errors[0] if errors else 'unknown error'}", 'error')
        return redirect(url_for('messaging.thread', tenant_id=tenant_id))

    with get_connection() as conn:

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
        # Quick messages show this tenant's real figures, so what is in the box is what goes out.
        # Skipped: the blank broadcast starter, and water notices ({month}/{amount} are only
        # known at water upload).
        variables = _build_variables(conn, tenant, prop)
        names = {'rent_due_10d': 'Rent due in 10 days', 'rent_due_5d': 'Rent due in 5 days',
                 'rent_due_reminder': 'Rent reminder', 'rent_due_today': 'Rent due today'}
        templates = [{'label': names.get(t['template_key'], t['template_key'].replace('_', ' ').capitalize()),
                      'subject': _substitute(t['subject'], variables),
                      'body': _substitute(t['body'], variables)}
                     for t in templates
                     if t['template_key'] != 'custom_broadcast'
                     and '{month}' not in (t['body'] or '') + (t['subject'] or '')
                     and '{amount}' not in (t['body'] or '')]

    from src.messaging import delivery
    sms_on = delivery.configured() and os.environ.get('AT_SMS_ENABLED', 'true').lower() != 'false'
    return render_template('messaging/thread.html',
        property=prop,
        tenant=tenant,
        messages=messages,
        templates=templates,
        sms_on=sms_on,
        link_sms=_link_sms() and bool(tenant['short_code']),
        sms_preview=delivery.sender_for(_tenant_sms(prop, tenant, 'Message from management', ''), pid)[1],
        sms_prefix=_sms_lead(prop),
    )


@messaging_bp.route('/broadcast', methods=['GET', 'POST'])
def broadcast():
    """Send broadcast: select recipients, pick template, preview, send."""
    broadcast_done = None
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        pid = prop['id']

        # Current tenants only: a re-leased tenant's frozen old record is 'inactive' and
        # would otherwise appear (and be messaged) twice.
        tenants = conn.execute("""
            SELECT t.id, t.name, t.phone, t.unit_id, t.short_code, u.unit_number
            FROM tenants t
            LEFT JOIN units u ON t.unit_id = u.id
            WHERE t.property_id = ? AND t.status = 'active'
            ORDER BY u.unit_number, t.name
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
            if not subject:
                flash('Subject is required.', 'error')
                return redirect(url_for('messaging.broadcast'))

            # A broadcast notice is kept in each tenant's portal and texted in full (title and
            # message), ending with their portal link once sign-in links are approved.
            channel, delivery_channel = 'both', 'sms'
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

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('broadcast_sent', 'message', batch_id,
                 f"Subject: {subject[:50]} | Recipients: {len(recipient_ids)} | Channel: {delivery_channel}", staff.actor()),
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

            # The texts go out after the save, from a background thread: the page never waits on
            # the SMS provider, and the database is never held while a text is in flight.
            from src.messaging.outreach import portal_footer
            _prefix = prop['name'].title() + ': '
            texts, no_phone = [], 0
            for t in selected_tenants:
                if not t['phone']:
                    no_phone += 1
                    continue
                v = _build_variables(conn, t, prop)
                sms_text = _substitute(f"{subject}\n{body}", v)
                if not sms_text.lower().startswith(prop['name'].lower()):
                    sms_text = _prefix + sms_text       # every SMS says which property it is about
                sms_text = portal_footer(sms_text, os.environ.get('APP_BASE_URL') or request.host_url, t['short_code'])
                texts.append(([{'phone': t['phone']}], sms_text, pid))
            broadcast_done = (len(selected_tenants), texts, no_phone)

    if broadcast_done:
        from src.messaging import delivery
        posted, texts, no_phone = broadcast_done
        sms_on = delivery.configured() and os.environ.get('AT_SMS_ENABLED', 'true').lower() != 'false'
        delivery.send_many_async(texts)
        message = f'Posted to {posted} tenant portal(s). '
        if not sms_on:
            message += 'SMS sending is switched off on this system, so no SMS went out.'
        else:
            message += f'Texts are going out now to {len(texts)} tenant(s)'
            message += f'; {no_phone} have no phone number.' if no_phone else '.'
        flash(message, 'success' if sms_on else 'warning')
        return redirect(url_for('messaging.dashboard'))

    return render_template(
        'messaging/broadcast.html',
        property=prop,
        tenants=tenants,
        templates=templates,
        outreach_enabled=__import__('src.messaging.outreach', fromlist=['enabled']).enabled(),
        sms_on=os.environ.get('AT_SMS_ENABLED', 'true').lower() != 'false',
        sms_prefix=_sms_lead(prop),
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


# ── Tenant conversations (inbox) ─────────────────────────────────────────────

@messaging_bp.route('/inbox')
def inbox():
    """Tenant conversations for the current property — awaiting a reply first, longest-waiting first."""
    show = request.args.get('show', 'open')
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        rows = threads.list_for_staff(conn, prop['id'])
        from src.messaging import staff_alerts
        alerts = {
            'route': staff_alerts.who(prop, 'repair'),
            'hours': prop['repair_escalate_hours'] if prop['repair_escalate_hours'] is not None else 24,
            'admin_phones': conn.execute(
                "SELECT COUNT(*) FROM admin_users WHERE org_id = ? AND is_active = 1 AND phone IS NOT NULL",
                (prop['organization_id'],)).fetchone()[0],
            'caretaker_phones': conn.execute(
                "SELECT COUNT(*) FROM caretakers WHERE property_id = ? AND phone IS NOT NULL", (prop['id'],)).fetchone()[0],
            'who': staff_alerts.WHO, 'hour_choices': staff_alerts.ESCALATE_HOURS,
        }
    if show == 'open':
        rows = [r for r in rows if r['status'] != 'closed']
    return render_template('messaging/inbox.html', property=prop, rows=rows, show=show,
                           topics=threads.TOPICS, active_nav='inbox', alerts=alerts)


@messaging_bp.route('/inbox/alerts', methods=['POST'])
def inbox_alert_settings():
    """Who is texted when a tenant reports a repair, and when management is texted
    about a repair nobody has answered (src/messaging/staff_alerts.py)."""
    from src.messaging import staff_alerts
    from src.platform.guardian import platform_log
    from src.utils import staff
    route = request.form.get('repair_alerts_to')
    try:
        hours = int(request.form.get('repair_escalate_hours', 24))
    except ValueError:
        hours = 24
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        if route not in staff_alerts.WHO or hours not in staff_alerts.ESCALATE_HOURS:
            flash('Choose from the options shown.', 'error')
            return redirect(url_for('messaging.inbox'))
        conn.execute("UPDATE properties SET repair_alerts_to = ?, repair_escalate_hours = ? WHERE id = ?",
                     (route, hours, prop['id']))
        details = f"Repair texts: {staff_alerts.WHO[route]} | unanswered after: {hours or 'off'} h"
        conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                     ('alert_routing_changed', 'property', prop['id'], details, staff.actor()))
        platform_log(conn, 'alert_routing_changed', 'property', prop['id'], details,
                     org_id=prop['organization_id'], property_id=prop['id'], actor=staff.actor())
    flash('Saved. ' + details + '.', 'success')
    return redirect(url_for('messaging.inbox'))


@messaging_bp.route('/inbox/<conv_id>', methods=['GET', 'POST'])
def inbox_thread(conv_id):
    """Read and reply to one tenant conversation."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        conv, msgs = threads.thread(conn, conv_id)
        if not conv or conv['property_id'] != prop['id']:
            flash('Conversation not found.', 'warning')
            return redirect(url_for('messaging.inbox'))
        if request.method == 'POST':
            body = (request.form.get('body') or '').strip()
            if body:
                threads.add_message(conn, conv_id, 'admin', threads.STAFF_LABEL, body, request.files.getlist('photos'),
                                    account_id=session.get('admin_user_id'))
                if request.form.get('close'):
                    threads.close(conn, conv_id, staff.actor())
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('conversation_reply', 'conversation', conv_id,
                     f"Unit {conv['unit_number']} | {conv['subject'][:60]}", staff.actor()))
                threads.notify_tenant_of_reply(conv, os.environ.get('APP_BASE_URL') or request.host_url)
            return redirect(url_for('messaging.inbox_thread', conv_id=conv_id))
        threads.mark_read(conn, conv_id, 'staff')
        ref_text = threads.ref_summary(conn, conv['tenant_id'], conv['ref_type'], conv['ref_id']) if conv['ref_id'] else None
        from src.utils import tenant_contacts
        phone_change = tenant_contacts.pending_for_conversation(conn, conv_id)
    return render_template('messaging/inbox_thread.html', property=prop, conv=conv, msgs=msgs,
                           topics=threads.TOPICS, ref_text=ref_text, active_nav='inbox', phone_change=phone_change)


@messaging_bp.route('/inbox/<conv_id>/phone-change', methods=['POST'])
def inbox_phone_change(conv_id):
    """Approve or decline a tenant's request to change their phone number (src/utils/tenant_contacts.py)."""
    from src.utils import tenant_contacts
    from src.messaging.delivery import send_sms_async
    texts = []
    with get_connection() as conn:
        prop = get_current_property(conn)
        conv, _ = threads.thread(conn, conv_id)
        req = tenant_contacts.pending_for_conversation(conn, conv_id) if conv else None
        if prop and conv and req and conv['property_id'] == prop['id']:
            approve = request.form.get('decision') == 'approve'
            texts = tenant_contacts.decide(conn, req['id'], approve, staff.actor(), org_id=prop['organization_id'])
            flash('Number changed. The tenant and the old number get a text.' if approve
                  else 'Declined. The number stays as it was.', 'success')
    for phone, text in texts:                     # after commit
        send_sms_async([phone], text)
    return redirect(url_for('messaging.inbox_thread', conv_id=conv_id))


@messaging_bp.route('/inbox/<conv_id>/close', methods=['POST'])
def inbox_close(conv_id):
    with get_connection() as conn:
        prop = get_current_property(conn)
        conv, _ = threads.thread(conn, conv_id)
        if prop and conv and conv['property_id'] == prop['id']:
            threads.add_message(conn, conv_id, 'system', None, 'Conversation closed by management.',
                                account_id=session.get('admin_user_id'))
            threads.close(conn, conv_id, staff.actor())
    return redirect(url_for('messaging.inbox'))


@messaging_bp.route('/inbox/photo/<photo_id>')
def inbox_photo(photo_id):
    with get_connection() as conn:
        prop = get_current_property(conn)
        row = conn.execute("""
            SELECT ph.file_name FROM conversation_photos ph JOIN conversations c ON c.id = ph.conversation_id
            WHERE ph.id = ? AND c.property_id = ?
        """, (photo_id, prop['id'] if prop else None)).fetchone()
    if not row:
        abort(404)
    return threads.send_photo(row['file_name'])


# ── Sign-in invitations ──────────────────────────────────────────────────────

INVITE_TEXT = {
    'en': ("{property}: your rent account is now online. See your balance and receipts, "
           "and message management, at {link} - sign in with this phone number."),
    'sw': ("{property}: akaunti yako ya kodi sasa iko mtandaoni. Angalia salio na risiti, "
           "na uwasiliane na usimamizi, hapa {link} - ingia kwa nambari hii ya simu."),
}


def _invite_rows(conn, pid):
    return [dict(r) for r in conn.execute("""
        SELECT t.id, t.name, t.phone, t.short_code, t.access_token, t.status,
               t.language_preference, u.unit_number,
               (p.pin_hash IS NOT NULL) AS signed_up,
               (SELECT MAX(m.created_at) FROM messages m
                WHERE m.tenant_id = t.id AND m.message_type = 'invite') AS invited_at
        FROM tenants t
        JOIN units u ON u.id = t.unit_id
        LEFT JOIN persons p ON p.id = t.person_id
        WHERE t.property_id = ? AND t.status IN ('active', 'departed')
        ORDER BY t.status, u.unit_number
    """, (pid,)).fetchall()]


@messaging_bp.route('/invite', methods=['GET', 'POST'])
def invite():
    """SMS tenants their sign-in link. Each tenant signs up with their own phone
    (SMS code, then a PIN), so the link alone opens nothing."""
    from src.messaging.delivery import plain_sms, sms_parts, send_sms_async
    from src.messaging.outreach import enabled as outreach_enabled
    can_send = outreach_enabled()
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        base = (os.environ.get('APP_BASE_URL') or request.host_url).rstrip('/')
        rows = _invite_rows(conn, prop['id'])
        for r in rows:
            r['link_ready'] = bool(r['access_token'] and r['short_code'])
            r['phone_ok'] = bool(_normalize_phone(r['phone']))
            if not r['link_ready']:
                r['text'] = ''
                r['parts'] = 0
                continue
            lang = 'sw' if (r['language_preference'] or '').lower() in ('sw', 'kiswahili', 'swahili') else 'en'
            r['text'] = plain_sms(INVITE_TEXT[lang].format(property=prop['name'].title(), link=f"{base}/t/{r['short_code']}"))
            r['parts'] = sms_parts(r['text'])
        if request.method == 'POST':
            if not can_send:
                flash('Tenant invitations are paused until onboarding is approved.', 'warning')
                return redirect(url_for('messaging.invite'))
            chosen = set(request.form.getlist('tenant_id'))
            sent = 0
            for r in rows:
                if r['id'] not in chosen or not r['phone_ok'] or not r['link_ready']:
                    continue
                conn.execute("""
                    INSERT INTO messages (id, property_id, tenant_id, subject, body, message_type, delivery_channel, delivery_status)
                    VALUES (?, ?, ?, 'Sign-in link', ?, 'invite', 'sms', 'sent')
                """, (generate_id('MSG'), prop['id'], r['id'], r['text']))
                send_sms_async([r['phone']], r['text'])
                sent += 1
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('tenants_invited', 'property', prop['id'], f'Sign-in link sent to {sent} tenant(s)', staff.actor()))
            flash(f'Sign-in link sent to {sent} tenant(s).', 'success')
            return redirect(url_for('messaging.invite'))
    return render_template('messaging/invite.html', property=prop, rows=rows,
                           signed_up=sum(1 for r in rows if r['signed_up']),
                           missing_links=sum(1 for r in rows if not r['link_ready']),
                           outreach_enabled=can_send, active_nav='messages')


# ── Management ↔ caretaker chat ──────────────────────────────────────────────

def _caretaker_for_prop(conn, caretaker_id, property_id):
    """The caretaker's chat belongs to this property (current account, or history here)."""
    return conn.execute("""
        SELECT 1 FROM caretakers WHERE id = ? AND property_id = ?
        UNION SELECT 1 FROM staff_messages WHERE caretaker_id = ? AND property_id = ?
    """, (caretaker_id, property_id, caretaker_id, property_id)).fetchone() is not None


@messaging_bp.route('/caretakers')
def caretaker_chats():
    from src.messaging import staff_chat
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        chats = staff_chat.chats_for_admin(conn, prop['id'])
    if len(chats) == 1:
        return redirect(url_for('messaging.caretaker_chat', caretaker_id=chats[0]['caretaker_id']))
    return render_template('messaging/caretaker_chats.html', property=prop, chats=chats,
                           active_nav='caretaker_chat')


@messaging_bp.route('/caretakers/<caretaker_id>', methods=['GET', 'POST'])
def caretaker_chat(caretaker_id):
    from src.messaging import staff_chat
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        if not _caretaker_for_prop(conn, caretaker_id, prop['id']):
            abort(404)
        ct = conn.execute("SELECT * FROM caretakers WHERE id = ?", (caretaker_id,)).fetchone()
        if request.method == 'POST':
            if not ct:
                flash('This caretaker account no longer exists, so they cannot read new messages.', 'warning')
                return redirect(url_for('messaging.caretaker_chat', caretaker_id=caretaker_id))
            body = (request.form.get('body') or '').strip()
            if body:
                _, saved, rejected = staff_chat.post(
                    conn, prop['id'], caretaker_id, 'admin', session.get('admin_name') or 'Management', body,
                    photos=request.files.getlist('photos'), account_id=session.get('admin_user_id'))
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('caretaker_chat_message', 'caretaker', caretaker_id,
                     f"To {ct['name']} | {body[:60]}", staff.actor()))
                staff_chat.notify_caretaker(conn, caretaker_id, prop['id'],
                                            os.environ.get('APP_BASE_URL') or request.host_url)
                if rejected:
                    flash(f'{rejected} file(s) were not photos and were not attached.', 'warning')
            return redirect(url_for('messaging.caretaker_chat', caretaker_id=caretaker_id))
        msgs = staff_chat.thread(conn, caretaker_id)
        staff_chat.mark_read(conn, caretaker_id, 'admin')
        chats = staff_chat.chats_for_admin(conn, prop['id'])
    name = ct['name'] if ct else next((m['sender_name'] for m in reversed(msgs) if m['sender_type'] == 'caretaker'),
                                      'Former caretaker')
    return render_template('messaging/caretaker_chat.html', property=prop, caretaker=ct, name=name,
                           msgs=msgs, chats=chats, active_nav='caretaker_chat')


@messaging_bp.route('/caretakers/photo/<photo_id>')
def caretaker_chat_photo(photo_id):
    from src.messaging import staff_chat
    with get_connection() as conn:
        prop = get_current_property(conn)
        p, m = staff_chat.photo(conn, photo_id)
        if not prop or not p or m['property_id'] != prop['id']:
            abort(404)
    return threads.send_photo(p['file_name'])

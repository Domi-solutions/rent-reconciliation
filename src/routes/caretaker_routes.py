"""
Caretaker portal routes — live operational view.
Auth: CARETAKER_PASSWORD env var; session key 'caretaker_authenticated'.
"""
import os
import re
from math import ceil

from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash

from src.database.db import get_connection, generate_id

caretaker_bp = Blueprint('caretaker', __name__, url_prefix='/caretaker')


def _has_db_caretakers():
    """Return True if any caretaker accounts exist in the database."""
    with get_connection() as conn:
        return bool(conn.execute("SELECT 1 FROM caretakers LIMIT 1").fetchone())


@caretaker_bp.before_request
def require_caretaker_auth():
    exempt = ('caretaker.login', 'caretaker.logout')
    if request.endpoint in exempt:
        return None

    # DB-based auth: verify caretaker_id still exists in DB on every request.
    # This means deleting an account immediately revokes access even for active sessions.
    caretaker_id = session.get('caretaker_id')
    if caretaker_id:
        with get_connection() as conn:
            still_exists = conn.execute(
                "SELECT 1 FROM caretakers WHERE id = ?", (caretaker_id,)
            ).fetchone()
        if not still_exists:
            # Account was deleted — clear session and force re-login
            session.pop('caretaker_id', None)
            session.pop('caretaker_name', None)
            session.pop('caretaker_property_id', None)
            return redirect(url_for('caretaker.login'))
        return None

    # Legacy shared-password mode
    if session.get('caretaker_authenticated'):
        return None

    # Dev mode: open if no CARETAKER_PASSWORD env var and no DB accounts exist
    if not os.environ.get('CARETAKER_PASSWORD') and not _has_db_caretakers():
        return None

    return redirect(url_for('caretaker.login', next=request.url))


@caretaker_bp.route('/login', methods=['GET', 'POST'])
def login():
    from werkzeug.security import check_password_hash
    use_db = _has_db_caretakers()
    error = None

    if request.method == 'POST':
        if use_db:
            name = (request.form.get('name') or '').strip()
            password = (request.form.get('password') or '').strip()
            with get_connection() as conn:
                caretaker = conn.execute(
                    "SELECT id, name, property_id, password_hash FROM caretakers WHERE name = ?", (name,)
                ).fetchone()
            if caretaker and caretaker['password_hash'] and check_password_hash(caretaker['password_hash'], password):
                session['caretaker_id'] = caretaker['id']
                session['caretaker_name'] = caretaker['name']
                session['caretaker_property_id'] = caretaker['property_id']
                next_url = request.args.get('next') or url_for('caretaker.dashboard', property_id=caretaker['property_id'])
                return redirect(next_url)
            error = 'Incorrect name or password.'
        else:
            # Legacy shared-password mode
            password = os.environ.get('CARETAKER_PASSWORD')
            if not password:
                return redirect(url_for('caretaker.index'))
            if request.form.get('password') == password:
                session['caretaker_authenticated'] = True
                next_url = request.args.get('next') or url_for('caretaker.index')
                return redirect(next_url)
            error = 'Incorrect password.'

    return render_template('caretaker/login.html', error=error, use_db=use_db)


@caretaker_bp.route('/logout')
def logout():
    session.pop('caretaker_id', None)
    session.pop('caretaker_name', None)
    session.pop('caretaker_property_id', None)
    session.pop('caretaker_authenticated', None)
    return redirect(url_for('caretaker.login'))


@caretaker_bp.route('/')
def index():
    """Auto-select property if only one, else show picker."""
    with get_connection() as conn:
        properties = conn.execute(
            "SELECT id, name FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()
    if len(properties) == 1:
        return redirect(url_for('caretaker.dashboard', property_id=properties[0]['id']))
    return render_template('caretaker/property_list.html', properties=properties)


def _occupancy_data(conn, property_id):
    total = conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)).fetchone()[0]
    occupied = conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'occupied'", (property_id,)).fetchone()[0]
    vacant = conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'vacant'", (property_id,)).fetchone()[0]
    office = conn.execute("SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'office'", (property_id,)).fetchone()[0]
    rentable = total - office
    occ_rate = round(occupied / rentable * 100, 1) if rentable > 0 else 0
    return {
        'total': total, 'occupied': occupied, 'vacant': vacant,
        'office': office, 'rentable': rentable, 'occ_rate': occ_rate,
    }


def _arrears_rows(conn, property_id):
    rows = conn.execute("""
        SELECT
            u.id as unit_id,
            u.unit_number,
            u.monthly_rent,
            t.name as tenant_name,
            t.phone as tenant_phone,
            COALESCE(ch.total, 0) - COALESCE(py.total, 0) as balance
        FROM units u
        LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) ch ON ch.unit_id = u.id
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) py ON py.unit_id = u.id
        WHERE u.property_id = ?
          AND COALESCE(ch.total, 0) - COALESCE(py.total, 0) > 0
        ORDER BY balance DESC
    """, (property_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['months_behind'] = ceil(d['balance'] / d['monthly_rent']) if d.get('monthly_rent') and d['monthly_rent'] > 0 else 0
        result.append(d)
    return result


@caretaker_bp.route('/<property_id>')
def dashboard(property_id):
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        occ = _occupancy_data(conn, property_id)
        arrears = _arrears_rows(conn, property_id)

        vacant_units = conn.execute("""
            SELECT unit_number, apartment_size FROM units
            WHERE property_id = ? AND status = 'vacant'
            ORDER BY unit_number
        """, (property_id,)).fetchall()

        from datetime import date as _date
        _today_str = _date.today().isoformat()
        _due_row = conn.execute("""
            SELECT MIN(rc.due_date) AS due_date
            FROM rent_charges rc
            JOIN units u ON rc.unit_id = u.id
            WHERE u.property_id = ? AND rc.due_date >= ?
        """, (property_id, _today_str)).fetchone()
        next_due_date = _due_row['due_date'] if _due_row else None
        days_to_due = (
            (_date.fromisoformat(next_due_date) - _date.today()).days
            if next_due_date else None
        )
        recent_reminder = conn.execute("""
            SELECT details FROM audit_log
            WHERE action = 'reminder_sent' AND date(timestamp) = date('now')
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()

    return render_template('caretaker/dashboard.html',
                           property=prop,
                           occ=occ,
                           arrears=arrears[:5],        # top 5 on dashboard
                           arrears_total=len(arrears),
                           vacant_units=vacant_units,
                           next_due_date=next_due_date,
                           days_to_due=days_to_due,
                           recent_reminder=recent_reminder,
                           active_tab='overview')


@caretaker_bp.route('/<property_id>/arrears')
def arrears(property_id):
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        arrears = _arrears_rows(conn, property_id)
        total_balance = sum(float(a['balance']) for a in arrears)

    return render_template('caretaker/arrears.html',
                           property=prop,
                           arrears=arrears,
                           total_balance=total_balance,
                           active_tab='arrears')


@caretaker_bp.route('/<property_id>/tenants')
def tenants(property_id):
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        rows = conn.execute("""
            SELECT
                u.unit_number,
                u.apartment_size,
                t.name as tenant_name,
                t.phone as tenant_phone,
                t.move_in_date,
                COALESCE(ch.total, 0) - COALESCE(py.total, 0) as balance
            FROM units u
            JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) ch ON ch.unit_id = u.id
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) py ON py.unit_id = u.id
            WHERE u.property_id = ?
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

    return render_template('caretaker/tenants.html',
                           property=prop,
                           tenants=rows,
                           active_tab='tenants')


def _substitute(text, variables):
    """Replace {variable} placeholders in text with values dict."""
    if not text:
        return text
    out = text
    for k, v in variables.items():
        out = out.replace("{" + k + "}", str(v))
    return out


@caretaker_bp.route('/<property_id>/messages')
def messages(property_id):
    """Caretaker messaging — compose + sent history."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        tenants = conn.execute("""
            SELECT t.id, t.name, t.phone, t.access_token, t.unit_id, u.unit_number,
                   u.monthly_rent, u.service_charge,
                   COALESCE(ub.balance, 0) as balance
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            LEFT JOIN unit_balances ub ON ub.unit_id = u.id
            WHERE t.property_id = ? AND t.status = 'active'
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

        templates = conn.execute("""
            SELECT id, template_key, subject, body
            FROM message_templates
            WHERE (property_id = ? OR property_id IS NULL) AND enabled = 1
            ORDER BY property_id DESC NULLS LAST
        """, (property_id,)).fetchall()

        sent = conn.execute("""
            SELECT m.subject, m.delivery_channel, m.created_at,
                   t.name AS tenant_name, u.unit_number
            FROM messages m
            JOIN tenants t ON m.tenant_id = t.id
            JOIN units u ON t.unit_id = u.id
            WHERE m.property_id = ?
            ORDER BY m.created_at DESC LIMIT 20
        """, (property_id,)).fetchall()

        due_row = conn.execute("""
            SELECT MIN(rc.due_date) AS due_date
            FROM rent_charges rc
            JOIN units u ON rc.unit_id = u.id
            WHERE u.property_id = ? AND rc.due_date >= date('now')
        """, (property_id,)).fetchone()
        next_due_date = (due_row['due_date'] if due_row else None) or ''

    return render_template('caretaker/messages.html',
                           property=prop,
                           tenants=tenants,
                           templates=templates,
                           sent=sent,
                           next_due_date=next_due_date,
                           active_tab='messages')


_KNOWN_MSG_VARS = {
    'tenant_name', 'unit_number', 'balance', 'monthly_rent',
    'service_charge', 'total_paid', 'due_date',
}


@caretaker_bp.route('/<property_id>/messages/send', methods=['POST'])
def send_message(property_id):
    """Send a message to one or all tenants with per-tenant variable substitution."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        reference = (request.form.get('reference') or '').strip() or 'Direct message'
        body_template = (request.form.get('body') or '').strip()
        recipient = (request.form.get('recipient') or '').strip()
        channel = (request.form.get('channel') or 'portal').strip()

        if not body_template:
            flash('Message body is required.', 'error')
            return redirect(url_for('caretaker.messages', property_id=property_id))

        # Reject any {variable} not in the known server-filled set.
        # (Client should have already substituted custom ones before submitting.)
        found_vars = set(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', body_template))
        unknown_vars = found_vars - _KNOWN_MSG_VARS
        if unknown_vars:
            names = ', '.join('{' + v + '}' for v in sorted(unknown_vars))
            flash(
                f"Message contains unresolved placeholder(s): {names}. "
                "Fill in all placeholders before sending.",
                'error',
            )
            return redirect(url_for('caretaker.messages', property_id=property_id))

        due_row = conn.execute("""
            SELECT MIN(rc.due_date) AS due_date
            FROM rent_charges rc
            JOIN units u ON rc.unit_id = u.id
            WHERE u.property_id = ? AND rc.due_date >= date('now')
        """, (property_id,)).fetchone()
        next_due_date = (due_row['due_date'] if due_row else None) or ''

        tenants = conn.execute("""
            SELECT t.id, t.name, t.phone, t.access_token, t.unit_id, u.unit_number,
                   u.monthly_rent, u.service_charge,
                   COALESCE(ub.balance, 0) as balance,
                   COALESCE(ub.total_paid, 0) as total_paid
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            LEFT JOIN unit_balances ub ON ub.unit_id = u.id
            WHERE t.property_id = ? AND t.status = 'active'
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

        if recipient == 'all':
            recipients = tenants
            batch_id = generate_id('BATCH')
        elif recipient == 'arrears':
            recipients = [t for t in tenants if float(t['balance'] or 0) > 0]
            batch_id = generate_id('BATCH')
        else:
            recipients = [t for t in tenants if t['id'] == recipient]
            batch_id = None

        if not recipients:
            no_match_msg = 'No tenants with outstanding balance found.' if recipient == 'arrears' else 'No valid recipients found.'
            flash(no_match_msg, 'error')
            return redirect(url_for('caretaker.messages', property_id=property_id))

        delivery_channel = 'sms' if channel == 'sms' else 'portal'

        # Build personalised message per tenant and store
        personalised = []
        for t in recipients:
            variables = {
                'tenant_name': t['name'] or '',
                'unit_number': t['unit_number'] or '',
                'balance': f"{float(t['balance'] or 0):,.0f}",
                'monthly_rent': f"{float(t['monthly_rent'] or 0):,.0f}",
                'service_charge': f"{float(t['service_charge'] or 0):,.0f}",
                'total_paid': f"{float(t['total_paid'] or 0):,.0f}",
                'due_date': next_due_date,
            }
            body = _substitute(body_template, variables)
            subject = _substitute(reference, variables)
            personalised.append({'tenant': t, 'body': body})

            msg_id = generate_id('MSG')
            conn.execute("""
                INSERT INTO messages (id, property_id, tenant_id, batch_id, subject, body,
                                      template_body, message_type, delivery_channel, delivery_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'broadcast', ?, 'delivered')
            """, (msg_id, property_id, t['id'], batch_id, subject, body, body_template, delivery_channel))

            if delivery_channel == 'portal' and t['phone'] and t['access_token']:
                try:
                    from src.messaging.delivery import send_sms
                    _base = request.host_url.rstrip('/')
                    _link = f"{_base}/tenant/{t['access_token']}"
                    _ping = f"Hi {t['name']}, you have a new message. View: {_link}"
                    send_sms([{'phone': t['phone']}], _ping)
                except Exception:
                    pass

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('broadcast_sent', 'message', batch_id or generate_id('BATCH'),
             f"Ref: {reference[:50]} | Recipients: {len(recipients)} | Channel: {delivery_channel} | caretaker", 'caretaker'),
        )

        try:
            from src.messaging.owner_notify import notify_property_owners
            _base = request.host_url.rstrip('/')
            _actor = session.get('caretaker_name', 'Caretaker')
            _sms = (
                f"Broadcast sent to {len(recipients)} tenant(s) — {prop['name']}.\n"
                f"'{reference[:50]}'.\n"
                f"View: {_base}/view/{property_id}"
            )
            notify_property_owners(
                conn, property_id, _sms,
                portal_subject=reference[:100],
                portal_body=body_template,
                template_body=body_template,
                channel=delivery_channel,
                recipient_count=len(recipients),
                message_type='broadcast',
                sent_by=_actor,
            )
        except Exception:
            pass

    if channel == 'sms':
        from src.messaging.delivery import send_sms
        sent_count = failed_count = 0
        first_error = None
        for p in personalised:
            s, f, errors = send_sms([{'phone': p['tenant']['phone']}], p['body'])
            sent_count += s
            failed_count += f
            if errors and not first_error:
                first_error = errors[0]
        if failed_count == 0:
            flash(f"SMS sent to {sent_count} tenant{'s' if sent_count != 1 else ''}.", 'success')
        elif sent_count > 0:
            flash(f"SMS sent to {sent_count} tenant{'s' if sent_count != 1 else ''}; {failed_count} failed.", 'success')
        else:
            flash(f"SMS delivery failed: {first_error or 'unknown error'}", 'error')
    else:
        flash(f"Message sent to {len(recipients)} tenant{'s' if len(recipients) != 1 else ''}.", 'success')

    return redirect(url_for('caretaker.messages', property_id=property_id))


@caretaker_bp.route('/<property_id>/log-payment', methods=['GET', 'POST'])
def log_payment(property_id):
    """Log a tenant payment claim from an M-Pesa message."""
    from src.parsers.sms_parser import parse_mpesa_message

    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        units = conn.execute("""
            SELECT u.id, u.unit_number, t.name AS tenant_name
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE u.property_id = ? AND u.status = 'occupied'
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

        recent_claims = conn.execute("""
            SELECT pc.mpesa_ref, pc.claimed_amount, pc.status, pc.created_at,
                   u.unit_number, t.name AS tenant_name
            FROM payment_claims pc
            JOIN units u ON pc.unit_id = u.id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE pc.property_id = ?
            ORDER BY pc.created_at DESC LIMIT 15
        """, (property_id,)).fetchall()

        if request.method == 'POST':
            message = request.form.get('message', '').strip()
            unit_id = request.form.get('unit_id', '').strip()

            if not message:
                flash('Paste the M-Pesa message or enter the reference code.', 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, recent_claims=recent_claims, active_tab='log_payment')
            if not unit_id:
                flash('Select a unit.', 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, recent_claims=recent_claims, active_tab='log_payment')

            parsed = parse_mpesa_message(message)
            if not parsed.get('success'):
                flash(parsed.get('error', 'Could not read M-Pesa reference from message.'), 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, recent_claims=recent_claims, active_tab='log_payment')

            reference = parsed['reference']
            amount = parsed.get('amount')
            if amount is not None:
                try:
                    amount = float(amount)
                except (TypeError, ValueError):
                    amount = None

            existing = conn.execute(
                "SELECT id FROM payment_claims WHERE property_id = ? AND mpesa_ref = ?",
                (property_id, reference),
            ).fetchone()
            if existing:
                flash(f'Reference {reference} has already been logged.', 'warning')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, recent_claims=recent_claims, active_tab='log_payment')

            claim_id = generate_id('CLM')
            conn.execute("""
                INSERT INTO payment_claims
                (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (claim_id, property_id, reference, unit_id, amount, message, 'caretaker'))

            unit_row = conn.execute("SELECT unit_number FROM units WHERE id = ?", (unit_id,)).fetchone()
            unit_number = unit_row['unit_number'] if unit_row else unit_id
            amt_str = f'{amount:,.0f}' if amount is not None else '-'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('claim_created', 'claim', claim_id,
                 f'Ref: {reference} | Unit: {unit_number} | Amount: {amt_str} | source: caretaker', 'caretaker'),
            )

            flash(f'Payment logged — {reference}, Unit {unit_number}, KES {amt_str}.', 'success')
            return redirect(url_for('caretaker.log_payment', property_id=property_id))

    return render_template('caretaker/log_payment.html', property=prop,
                           units=units, recent_claims=recent_claims, active_tab='log_payment')


@caretaker_bp.route('/<property_id>/issues')
def issues(property_id):
    """View all maintenance issues for a property (open first, then recent resolved)."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        open_issues = conn.execute(
            """
            SELECT m.*, u.unit_number
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            WHERE m.property_id = ? AND m.status = 'open'
            ORDER BY m.created_at DESC
            """,
            (property_id,),
        ).fetchall()

        resolved_issues = conn.execute(
            """
            SELECT m.*, u.unit_number
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            WHERE m.property_id = ? AND m.status = 'resolved'
            ORDER BY m.resolved_at DESC, m.created_at DESC
            """,
            (property_id,),
        ).fetchall()

        units = conn.execute(
            """
            SELECT id, unit_number
            FROM units
            WHERE property_id = ?
            ORDER BY unit_number
            """,
            (property_id,),
        ).fetchall()

    return render_template(
        'caretaker/issues.html',
        property=prop,
        open_issues=open_issues,
        resolved_issues=resolved_issues,
        units=units,
        active_tab='issues',
    )


@caretaker_bp.route('/<property_id>/issues/new', methods=['POST'])
def new_issue(property_id):
    """Caretaker-raised maintenance issue for a property or common area."""
    category = (request.form.get('category') or 'general').strip() or 'general'
    title = (request.form.get('title') or '').strip()
    description = (request.form.get('description') or '').strip()
    raw_unit = (request.form.get('unit_id') or '').strip()

    if not title:
        flash('Please enter a short title for the issue.', 'error')
        return redirect(url_for('caretaker.issues', property_id=property_id))

    unit_id = raw_unit or None
    if unit_id == 'common':
        unit_id = None

    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        issue_id = generate_id('MAINT')
        conn.execute(
            """
            INSERT INTO maintenance_issues
                (id, property_id, unit_id, source, raised_by_tenant_id, category, title, description)
            VALUES
                (?, ?, ?, 'caretaker', NULL, ?, ?, ?)
            """,
            (issue_id, property_id, unit_id, category, title, description),
        )

    flash('Maintenance issue recorded.', 'success')
    return redirect(url_for('caretaker.issues', property_id=property_id))


@caretaker_bp.route('/<property_id>/issues/<issue_id>/resolve', methods=['POST'])
def resolve_issue(property_id, issue_id):
    """Mark an issue as resolved and optionally notify the tenant."""
    resolved_note = (request.form.get('resolved_note') or '').strip()

    with get_connection() as conn:
        issue = conn.execute(
            """
            SELECT *
            FROM maintenance_issues
            WHERE id = ? AND property_id = ?
            """,
            (issue_id, property_id),
        ).fetchone()
        if not issue:
            abort(404)

        conn.execute(
            """
            UPDATE maintenance_issues
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                resolved_note = ?
            WHERE id = ? AND property_id = ?
            """,
            (resolved_note, issue_id, property_id),
        )

        if issue['source'] == 'tenant' and issue['raised_by_tenant_id']:
            msg_id = generate_id('MSG')
            conn.execute(
                """
                INSERT INTO messages
                    (id, property_id, tenant_id, batch_id, subject, body, message_type, delivery_channel, delivery_status)
                VALUES
                    (?, ?, ?, NULL, ?, ?, 'notice', 'portal', 'delivered')
                """,
                (
                    msg_id,
                    property_id,
                    issue['raised_by_tenant_id'],
                    f"Maintenance update: {issue['title']}",
                    (
                        "Your maintenance issue — {title} — has been marked as resolved.".format(
                            title=issue['title']
                        )
                        + (f"\n\nNote: {resolved_note}" if resolved_note else "")
                    ),
                ),
            )

    flash('Issue marked as resolved.', 'success')
    return redirect(url_for('caretaker.issues', property_id=property_id))

"""
Caretaker portal routes — live operational view.
Auth: CARETAKER_PASSWORD env var; session key 'caretaker_authenticated'.
"""
import json
import os
import re

from flask import Blueprint, render_template, request, redirect, url_for, session, abort, flash

from src.database.db import get_connection, generate_id, allocate_payment
from src.reconciliation.matcher import enrich_with_suggestions
from src.reports.landlord_report import enrich_report_data
from src.utils.metrics import get_property_occupancy, get_months_behind

caretaker_bp = Blueprint('caretaker', __name__, url_prefix='/caretaker')


@caretaker_bp.context_processor
def inject_caretaker_counts():
    """Inject flagged claims count and pending tenant claim count into all caretaker templates."""
    property_id = request.view_args.get('property_id') if request.view_args else None
    if not property_id:
        return {'ct_flagged_count': 0, 'ct_tenant_pending_count': 0}
    try:
        with get_connection() as conn:
            flagged = conn.execute(
                "SELECT COUNT(*) FROM payment_claims WHERE property_id = ? AND status = 'flagged' AND caretaker_confirmed = 0 AND admin_cleared = 0",
                (property_id,)
            ).fetchone()[0]
            tenant_pending = conn.execute(
                "SELECT COUNT(*) FROM payment_claims WHERE property_id = ? AND source = 'tenant' AND status = 'pending'",
                (property_id,)
            ).fetchone()[0]
        return {'ct_flagged_count': flagged, 'ct_tenant_pending_count': tenant_pending}
    except Exception:
        return {'ct_flagged_count': 0, 'ct_tenant_pending_count': 0}


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
    occ = get_property_occupancy(conn, property_id)
    occ['occ_rate'] = occ['occupancy_rate']  # alias for templates that use occ_rate
    return occ


def _unassigned_credits(conn, property_id, limit=None):
    """Money that arrived in the bank but is not yet attributed to any unit.

    This is the gap that leaves a tenant looking unpaid while their money sits
    in the account, so the caretaker — who knows who actually lives where —
    needs to see it rather than only the admin. Each row carries the matcher's
    suggestion so the common case is one click, not a hunt.

    Mirrors the admin 'unreported' query, including the ignored filter: an
    ignored transaction stays assignable but must not be counted as outstanding.
    """
    rows = conn.execute("""
        SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name, bt.unit_hint,
               bs.id AS statement_id, bs.filename AS statement_filename
        FROM bank_transactions bt
        JOIN bank_statements bs ON bt.statement_id = bs.id
        WHERE bs.property_id = ?
          AND bt.txn_type = 'PAYBILL_CREDIT'
          AND (bt.ignored IS NULL OR bt.ignored = 0)
          AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
        ORDER BY bt.txn_date DESC
    """, (property_id,)).fetchall()

    unassigned = [dict(row) for row in rows]
    org_row = conn.execute(
        "SELECT organization_id FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    enrich_with_suggestions(
        unassigned, conn, property_id,
        org_row['organization_id'] if org_row else None
    )

    # Attach the tenant sitting in the suggested unit so the caretaker sees a
    # name, not a unit code, before committing someone's money to it.
    for row in unassigned:
        row['suggested_tenant_id'] = None
        if row.get('suggested_unit_id'):
            tenant = conn.execute(
                "SELECT id, name FROM tenants WHERE unit_id = ? AND status = 'active'",
                (row['suggested_unit_id'],)
            ).fetchone()
            if tenant:
                row['suggested_tenant_id'] = tenant['id']
                row.setdefault('suggested_tenant_name', None)
                row['suggested_tenant_name'] = row.get('suggested_tenant_name') or tenant['name']
    return unassigned[:limit] if limit else unassigned


def _assignable_tenants(conn, property_id):
    """Active tenants, for the assign dropdown."""
    return conn.execute("""
        SELECT t.id, t.name, u.unit_number
        FROM tenants t JOIN units u ON t.unit_id = u.id
        WHERE t.property_id = ? AND t.status = 'active'
        ORDER BY u.unit_number
    """, (property_id,)).fetchall()


def _arrears_rows(conn, property_id):
    rows = conn.execute("""
        SELECT
            u.id as unit_id,
            u.unit_number,
            u.monthly_rent,
            t.id as tenant_id,
            t.name as tenant_name,
            t.phone as tenant_phone,
            COALESCE(ch.total, 0) - COALESCE(py.total, 0) as balance,
            COALESCE(pc_pending.pending_amount, 0) as pending_amount,
            COALESCE(pc_pending.pending_count, 0) as pending_count,
            COALESCE(pc_flagged.flagged_amount, 0) as flagged_amount,
            COALESCE(pc_flagged.flagged_count, 0) as flagged_count
        FROM units u
        LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) ch ON ch.unit_id = u.id
        LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) py ON py.unit_id = u.id
        LEFT JOIN (
            SELECT unit_id, SUM(claimed_amount) as pending_amount, COUNT(*) as pending_count
            FROM payment_claims WHERE property_id = ? AND status = 'pending' GROUP BY unit_id
        ) pc_pending ON pc_pending.unit_id = u.id
        LEFT JOIN (
            SELECT unit_id, SUM(claimed_amount) as flagged_amount, COUNT(*) as flagged_count
            FROM payment_claims WHERE property_id = ? AND status = 'flagged' GROUP BY unit_id
        ) pc_flagged ON pc_flagged.unit_id = u.id
        WHERE u.property_id = ?
          AND COALESCE(ch.total, 0) - COALESCE(py.total, 0) > 0
        ORDER BY
            CASE WHEN COALESCE(pc_flagged.flagged_count, 0) > 0 THEN 0 ELSE 1 END ASC,
            (COALESCE(ch.total, 0) - COALESCE(py.total, 0) - COALESCE(pc_pending.pending_amount, 0)) DESC
    """, (property_id, property_id, property_id)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Flagged claims do NOT reduce display balance — the full amount is still owed
        d['display_balance'] = max(float(d['balance']) - float(d['pending_amount']), 0)
        # months_behind reflects what the caretaker actually needs to follow up on
        d['months_behind'] = get_months_behind(d['display_balance'], d.get('monthly_rent'))
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

        # Payments where bank amount differs from what caretaker reported and no note yet added
        mismatches = conn.execute("""
            SELECT p.id, p.amount AS bank_amount, p.caretaker_note,
                   pc.claimed_amount, pc.mpesa_ref,
                   u.unit_number, t.name AS tenant_name
            FROM payments p
            JOIN payment_claims pc ON pc.id = p.claim_id
            JOIN units u ON p.unit_id = u.id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE p.property_id = ?
              AND pc.source = 'caretaker'
              AND pc.claimed_amount IS NOT NULL
              AND ABS(p.amount - pc.claimed_amount) > 1
              AND (p.caretaker_note IS NULL OR TRIM(p.caretaker_note) = '')
            ORDER BY p.payment_date DESC
        """, (property_id,)).fetchall()

        unassigned = _unassigned_credits(conn, property_id)
        unassigned_total = sum(float(u['amount'] or 0) for u in unassigned)
        assignable_tenants = _assignable_tenants(conn, property_id)

    return render_template('caretaker/dashboard.html',
                           unassigned=unassigned[:5],
                           unassigned_count=len(unassigned),
                           unassigned_total=unassigned_total,
                           assignable_tenants=assignable_tenants,
                           property=prop,
                           occ=occ,
                           arrears=arrears,
                           arrears_total=len(arrears),
                           arrears_sum=sum(float(a['display_balance']) for a in arrears),
                           vacant_units=vacant_units,
                           next_due_date=next_due_date,
                           days_to_due=days_to_due,
                           recent_reminder=recent_reminder,
                           mismatches=mismatches,
                           active_tab='overview')


@caretaker_bp.route('/<property_id>/unassigned')
def unassigned(property_id):
    """Every payment received but not yet matched to a tenant."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        rows = _unassigned_credits(conn, property_id)
        tenants_list = _assignable_tenants(conn, property_id)

    return render_template('caretaker/unassigned.html',
                           property=prop,
                           unassigned=rows,
                           unassigned_total=sum(float(r['amount'] or 0) for r in rows),
                           assignable_tenants=tenants_list,
                           active_tab='unassigned')


@caretaker_bp.route('/<property_id>/arrears')
def arrears(property_id):
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        arrears = _arrears_rows(conn, property_id)
        total_balance = sum(float(a['balance']) for a in arrears)
        total_display_balance = sum(float(a['display_balance']) for a in arrears)
        total_pending = sum(float(a['pending_amount']) for a in arrears)
        total_flagged = sum(float(a['flagged_amount']) for a in arrears)

    return render_template('caretaker/arrears.html',
                           property=prop,
                           arrears=arrears,
                           total_balance=total_balance,
                           total_display_balance=total_display_balance,
                           total_pending=total_pending,
                           total_flagged=total_flagged,
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
                t.id as tenant_id,
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

        departed_tenants = conn.execute("""
            SELECT td.id AS departure_id, td.tenant_id, td.unit_id, td.remaining_debt,
                   t.name AS tenant_name, u.unit_number
            FROM tenant_departures td
            JOIN tenants t ON t.id = td.tenant_id
            JOIN units u ON u.id = td.unit_id
            WHERE td.property_id = ? AND td.debt_status = 'active'
            ORDER BY t.name
        """, (property_id,)).fetchall()

        if request.method == 'POST':
            payment_mode = request.form.get('payment_mode', 'active')
            message = request.form.get('message', '').strip()

            # Departed tenant payment — resolve unit_id from departure record
            if payment_mode == 'departed':
                departed_tenant_id = request.form.get('departed_tenant_id', '').strip()
                if not departed_tenant_id:
                    flash('Select a departed tenant.', 'error')
                    return render_template('caretaker/log_payment.html', property=prop,
                                           units=units, departed_tenants=departed_tenants,
                                           active_tab='log_payment')
                dep_row = conn.execute(
                    "SELECT td.unit_id, t.id AS tenant_id, t.name AS tenant_name, u.unit_number "
                    "FROM tenant_departures td "
                    "JOIN tenants t ON t.id = td.tenant_id "
                    "JOIN units u ON u.id = td.unit_id "
                    "WHERE td.tenant_id = ? AND td.property_id = ? AND td.debt_status = 'active'",
                    (departed_tenant_id, property_id)
                ).fetchone()
                if not dep_row:
                    flash('Departed tenant not found or debt already settled.', 'error')
                    return render_template('caretaker/log_payment.html', property=prop,
                                           units=units, departed_tenants=departed_tenants,
                                           active_tab='log_payment')
                unit_id = dep_row['unit_id']
                unit_number = dep_row['unit_number']
                _departed_tenant_id = dep_row['tenant_id']
            else:
                departed_tenant_id = None
                _departed_tenant_id = None
                unit_id = request.form.get('unit_id', '').strip()

            if not message:
                flash('Paste the M-Pesa message or enter the reference code.', 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, departed_tenants=departed_tenants,
                                       active_tab='log_payment')
            if not unit_id:
                flash('Select a unit.', 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, departed_tenants=departed_tenants,
                                       active_tab='log_payment')

            parsed = parse_mpesa_message(message)
            if not parsed.get('success'):
                flash(parsed.get('error', 'Could not read M-Pesa reference from message.'), 'error')
                return render_template('caretaker/log_payment.html', property=prop,
                                       units=units, active_tab='log_payment')

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
                                       units=units, departed_tenants=departed_tenants,
                                       active_tab='log_payment')

            # For active-tenant path, resolve unit_number here
            if payment_mode != 'departed':
                unit_row = conn.execute("SELECT unit_number FROM units WHERE id = ?", (unit_id,)).fetchone()
                unit_number = unit_row['unit_number'] if unit_row else unit_id

            # Extract M-Pesa transaction date for period-based flagging
            _mpesa_ts = parsed.get('timestamp')
            _has_real_ts = not parsed.get('parse_warnings') or not any(
                'Timestamp not found' in w for w in parsed.get('parse_warnings', [])
            )
            mpesa_date = _mpesa_ts.date().isoformat() if (_mpesa_ts and _has_real_ts) else None
            mpesa_period = _mpesa_ts.strftime('%Y-%m') if (_mpesa_ts and _has_real_ts) else None

            claim_id = generate_id('CLM')
            conn.execute("""
                INSERT INTO payment_claims
                (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source, mpesa_date, mpesa_period, departed_tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (claim_id, property_id, reference, unit_id, amount, message, 'caretaker', mpesa_date, mpesa_period, _departed_tenant_id))

            amt_str = f'{amount:,.0f}' if amount is not None else '-'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('claim_created', 'claim', claim_id,
                 f'Ref: {reference} | Unit: {unit_number} | Amount: {amt_str} | source: caretaker', 'caretaker'),
            )

            # At-submission bank check: cross-reference against any bank data already uploaded.
            # Three outcomes: auto-verify (ref found), flag (ref absent), or leave pending (no data yet).
            _claim_flagged = False
            _claim_verified = False
            if reference:
                _org_id = prop['organization_id'] if prop['organization_id'] else None

                if mpesa_period:
                    # Compute date range (avoids strftime on indexed txn_date column)
                    _mp_y, _mp_m = int(mpesa_period[:4]), int(mpesa_period[5:7])
                    _mp_start = f"{mpesa_period}-01"
                    _mp_end = f"{_mp_y + 1}-01-01" if _mp_m == 12 else f"{_mp_y}-{_mp_m + 1:02d}-01"

                    # Check if a statement covering this specific period exists
                    if _org_id:
                        _has_bank_data = bool(conn.execute("""
                            SELECT 1 FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            WHERE bs.org_id = ? AND bt.txn_date >= ? AND bt.txn_date < ?
                              AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                        """, (_org_id, _mp_start, _mp_end)).fetchone())
                    else:
                        _has_bank_data = bool(conn.execute("""
                            SELECT 1 FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            JOIN properties p ON p.id = bs.property_id
                            WHERE p.id = ? AND bt.txn_date >= ? AND bt.txn_date < ?
                              AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                        """, (property_id, _mp_start, _mp_end)).fetchone())
                    _period_label = mpesa_period
                else:
                    # No parsed timestamp — check across all available bank data for the org
                    if _org_id:
                        _has_bank_data = bool(conn.execute("""
                            SELECT 1 FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            WHERE bs.org_id = ? AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                        """, (_org_id,)).fetchone())
                    else:
                        _has_bank_data = bool(conn.execute("""
                            SELECT 1 FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            JOIN properties p ON p.id = bs.property_id
                            WHERE p.id = ? AND bt.txn_type = 'PAYBILL_CREDIT' LIMIT 1
                        """, (property_id,)).fetchone())
                    _period_label = 'any uploaded period'

                if _has_bank_data:
                    # Look up the actual bank transaction row for this ref
                    if _org_id:
                        _bank_txn = conn.execute("""
                            SELECT bt.* FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            WHERE bs.org_id = ? AND bt.mpesa_ref = ? LIMIT 1
                        """, (_org_id, reference)).fetchone()
                    else:
                        _bank_txn = conn.execute("""
                            SELECT bt.* FROM bank_transactions bt
                            JOIN bank_statements bs ON bs.id = bt.statement_id
                            JOIN properties p ON p.id = bs.property_id
                            WHERE p.id = ? AND bt.mpesa_ref = ? LIMIT 1
                        """, (property_id, reference)).fetchone()

                    if _bank_txn:
                        # Ref exists in bank — auto-verify the claim now
                        _existing_pay = conn.execute(
                            "SELECT id, claim_id FROM payments WHERE bank_txn_id = ?",
                            (_bank_txn['id'],)
                        ).fetchone()

                        if _existing_pay:
                            # Payment already exists (was manually assigned) — link this claim to it
                            if not _existing_pay['claim_id']:
                                conn.execute(
                                    "UPDATE payments SET claim_id = ? WHERE id = ?",
                                    (claim_id, _existing_pay['id'])
                                )
                            conn.execute(
                                "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (claim_id,)
                            )
                            conn.execute(
                                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                                ('claim_auto_verified', 'claim', claim_id,
                                 f'Ref: {reference} | Unit: {unit_number} | KES {amt_str} | '
                                 f'matched existing payment {_existing_pay["id"]} at submission', 'system'),
                            )
                        else:
                            # Bank transaction unassigned — create payment and verify immediately
                            payment_id = generate_id('PAY')
                            conn.execute("""
                                INSERT INTO payments
                                (id, property_id, unit_id, claim_id, bank_txn_id, statement_id, amount, payment_date, assignment_type)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto')
                            """, (
                                payment_id, property_id, unit_id, claim_id,
                                _bank_txn['id'], _bank_txn['statement_id'],
                                _bank_txn['amount'], _bank_txn['txn_date'] or '',
                            ))
                            allocate_payment(conn, payment_id, unit_id, _bank_txn['amount'])
                            conn.execute(
                                "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (claim_id,)
                            )
                            conn.execute(
                                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                                ('payment_verified', 'payment', payment_id,
                                 f'Ref: {reference} | Unit: {unit_number} | KES {amt_str} | '
                                 f'auto-verified at caretaker submission', 'system'),
                            )
                        # If this transaction was previously ignored, restore it
                        if _bank_txn['ignored']:
                            conn.execute(
                                "UPDATE bank_transactions SET ignored=0, ignored_at=NULL, ignored_reason=NULL WHERE id=?",
                                (_bank_txn['id'],)
                            )
                            conn.execute(
                                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?,?,?,?,?)",
                                ('txn_unignored', 'bank_transaction', _bank_txn['id'],
                                 f'Auto-restored — matched by payment claim {claim_id} (ref {reference})', 'system'),
                            )

                        _claim_verified = True

                    else:
                        # Ref absent from all available bank data — flag as fake
                        conn.execute("""
                            UPDATE payment_claims
                            SET status = 'flagged', flag_reason = 'ref_not_found', flagged_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (claim_id,))

                        _active_tenant = conn.execute(
                            "SELECT id FROM tenants WHERE unit_id = ? AND status = 'active'", (unit_id,)
                        ).fetchone()
                        if _active_tenant:
                            conn.execute("UPDATE tenants SET flagged = 1 WHERE id = ?", (_active_tenant['id'],))

                        conn.execute(
                            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                            ('claim_flagged', 'claim', claim_id,
                             f'Ref: {reference} | Unit: {unit_number} | KES {amt_str} | '
                             f'not found in bank records for {_period_label} — flagged at submission', 'system'),
                        )
                        try:
                            from src.platform.guardian import raise_alert as _raise_alert
                            _raise_alert(conn, 'fake_payment_claim',
                                f'Ref {reference} | Unit {unit_number} | KES {amt_str} | '
                                f'Submitted by caretaker — ref absent from bank records ({_period_label}).',
                                org_id=_org_id, property_id=property_id, severity='critical')
                        except Exception:
                            pass
                        _claim_flagged = True

            if _claim_verified:
                flash(
                    f'Payment logged and verified — {reference}, Unit {unit_number}, KES {amt_str}. '
                    f'Reference confirmed in bank records.',
                    'success'
                )
            elif _claim_flagged:
                flash(
                    f'WARNING: Reference {reference} was not found in bank records for {_period_label}. '
                    f'This claim has been flagged for review.',
                    'error'
                )
            else:
                flash(f'Payment logged — {reference}, Unit {unit_number}, KES {amt_str}.', 'success')
            return redirect(url_for('caretaker.payment_activity', property_id=property_id))

    return render_template('caretaker/log_payment.html', property=prop,
                           units=units, departed_tenants=departed_tenants,
                           active_tab='log_payment')


@caretaker_bp.route('/<property_id>/payment-activity')
def payment_activity(property_id):
    """Full history of payment claims logged by this caretaker."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        claims = conn.execute("""
            SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.status, pc.created_at,
                   pc.flag_reason, pc.mpesa_period, pc.source,
                   u.unit_number, t.name AS tenant_name, t.short_code AS tenant_short_code,
                   p.id AS payment_id, p.amount AS bank_amount,
                   p.caretaker_note, p.caretaker_note_at
            FROM payment_claims pc
            JOIN units u ON pc.unit_id = u.id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN payments p ON p.claim_id = pc.id
            WHERE pc.property_id = ?
            ORDER BY pc.created_at DESC
        """, (property_id,)).fetchall()

    return render_template('caretaker/payment_activity.html',
                           property=prop,
                           claims=claims,
                           active_tab='payment_activity')


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


@caretaker_bp.route('/<property_id>/reports')
def reports_list(property_id):
    """List all monthly reports for this property."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        rows = conn.execute("""
            SELECT id, period_start, period_end, created_at, report_data
            FROM landlord_reports
            WHERE property_id = ?
            ORDER BY created_at DESC
        """, (property_id,)).fetchall()

        reports = []
        for r in rows:
            data = json.loads(r['report_data'])
            reports.append({
                'id': r['id'],
                'period_start': r['period_start'],
                'period_end': r['period_end'],
                'created_at': r['created_at'],
                'units_behind': data.get('arrears', {}).get('units_in_arrears', 0),
                'occupied': data.get('occupancy', {}).get('occupied_units', 0),
                'total_units': data.get('occupancy', {}).get('total_units', 0),
            })

    return render_template('caretaker/reports.html',
                           property=prop, reports=reports, active_tab='reports')


@caretaker_bp.route('/<property_id>/reports/<report_id>')
def report_detail(property_id, report_id):
    """Caretaker view of a monthly report — operational only, no financials."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        report_row = conn.execute(
            "SELECT * FROM landlord_reports WHERE id = ? AND property_id = ?",
            (report_id, property_id)
        ).fetchone()
        if not report_row:
            abort(404)

        report_data = json.loads(report_row['report_data'])
        enrich_report_data(report_data, conn, property_id,
                           report_row['period_start'], report_row['period_end'])

        vacant_units = conn.execute("""
            SELECT unit_number, apartment_size FROM units
            WHERE property_id = ? AND status = 'vacant'
            ORDER BY unit_number
        """, (property_id,)).fetchall()

    return render_template('caretaker/report_detail.html',
                           property=prop,
                           report=report_data,
                           report_id=report_id,
                           period_start=report_row['period_start'],
                           period_end=report_row['period_end'],
                           created_at=report_row['created_at'],
                           vacant_units=vacant_units,
                           active_tab='reports')


@caretaker_bp.route('/<property_id>/water')
def water_list(property_id):
    """List past water reading uploads for this property."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        uploads = conn.execute("""
            SELECT wu.*
            FROM water_uploads wu
            WHERE wu.property_id = ?
            ORDER BY wu.submitted_at DESC
        """, (property_id,)).fetchall()

    return render_template('caretaker/water.html',
                           property=prop, uploads=uploads, active_tab='water')


@caretaker_bp.route('/<property_id>/water/new', methods=['GET', 'POST'])
def water_new(property_id):
    """Record meter readings for all units — creates water charges for next billing period."""
    from datetime import date as _date

    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        today = _date.today()
        reading_period = today.strftime('%Y-%m')
        if today.month == 12:
            charge_period = f"{today.year + 1}-01"
        else:
            charge_period = f"{today.year}-{today.month + 1:02d}"

        rate = float(prop['water_rate']) if prop['water_rate'] else 300.0

        units = conn.execute("""
            SELECT
                u.id AS unit_id,
                u.unit_number,
                u.status AS unit_status,
                COALESCE(t.name, '') AS tenant_name,
                (SELECT wr.current_reading
                 FROM water_readings wr
                 JOIN water_uploads wu ON wr.upload_id = wu.id
                 WHERE wr.unit_id = u.id AND wu.property_id = u.property_id
                 ORDER BY wu.submitted_at DESC LIMIT 1) AS last_reading
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE u.property_id = ? AND u.status != 'vacant'
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

        if request.method == 'POST':
            rp = request.form.get('reading_period', reading_period).strip()
            cp = request.form.get('charge_period', charge_period).strip()
            submitter = session.get('caretaker_name', 'Caretaker')

            existing_upload = conn.execute(
                "SELECT id FROM water_uploads WHERE property_id = ? AND charge_period = ?",
                (property_id, cp)
            ).fetchone()
            if existing_upload:
                flash(f'Water readings for {cp} billing have already been recorded.', 'error')
                return render_template('caretaker/water_new.html',
                                       property=prop, units=units, rate=rate,
                                       reading_period=rp, charge_period=cp, active_tab='water')

            readings = []
            errors = []
            for u in units:
                uid = u['unit_id']
                prev_str = request.form.get(f'prev_{uid}', '').strip()
                curr_str = request.form.get(f'curr_{uid}', '').strip()

                if not curr_str:
                    continue

                try:
                    prev = float(prev_str) if prev_str else 0.0
                    curr = float(curr_str)
                except ValueError:
                    errors.append(f"Unit {u['unit_number']}: invalid reading.")
                    continue

                if curr < prev:
                    errors.append(f"Unit {u['unit_number']}: current ({curr:.0f}) is less than previous ({prev:.0f}).")
                    continue

                consumed = curr - prev
                readings.append({
                    'unit_id': uid,
                    'unit_number': u['unit_number'],
                    'prev': prev, 'curr': curr,
                    'consumed': consumed,
                    'amount': consumed * rate,
                })

            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('caretaker/water_new.html',
                                       property=prop, units=units, rate=rate,
                                       reading_period=rp, charge_period=cp, active_tab='water')

            if not readings:
                flash('No readings entered.', 'error')
                return render_template('caretaker/water_new.html',
                                       property=prop, units=units, rate=rate,
                                       reading_period=rp, charge_period=cp, active_tab='water')

            total_amount = sum(r['amount'] for r in readings)

            upload_id = generate_id('WU')
            conn.execute("""
                INSERT INTO water_uploads
                    (id, property_id, reading_period, charge_period, unit_count, total_amount, source, submitted_by)
                VALUES (?, ?, ?, ?, ?, ?, 'caretaker_web', ?)
            """, (upload_id, property_id, rp, cp, len(readings), total_amount, submitter))

            created = skipped = 0
            for r in readings:
                wr_id = generate_id('WR')
                conn.execute("""
                    INSERT INTO water_readings
                        (id, upload_id, unit_id, previous_reading, current_reading, units_consumed, rate, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (wr_id, upload_id, r['unit_id'], r['prev'], r['curr'], r['consumed'], rate, r['amount']))

                existing_charge = conn.execute(
                    "SELECT id FROM rent_charges WHERE unit_id = ? AND period = ? AND charge_type = 'water'",
                    (r['unit_id'], cp)
                ).fetchone()
                if not existing_charge:
                    charge_id = generate_id('CHG')
                    conn.execute(
                        "INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) VALUES (?, ?, ?, ?, 'water', ?)",
                        (charge_id, property_id, r['unit_id'], cp, r['amount'])
                    )
                    created += 1
                else:
                    skipped += 1

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('water_readings_recorded', 'water_upload', upload_id,
                 f'Reading period: {rp} | Charge period: {cp} | {len(readings)} units | {created} charges created | Total: KES {total_amount:,.0f} | by: {submitter}',
                 'caretaker')
            )

            flash(f'Water readings recorded — {len(readings)} units, KES {total_amount:,.0f} total. Billed to {cp}.', 'success')
            return redirect(url_for('caretaker.water_list', property_id=property_id))

    return render_template('caretaker/water_new.html',
                           property=prop, units=units, rate=rate,
                           reading_period=reading_period, charge_period=charge_period,
                           active_tab='water')


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


@caretaker_bp.route('/<property_id>/flagged-claims')
def flagged_claims(property_id):
    """Caretaker view of flagged payment claims — shows status and allows confirmation."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)

        claims = conn.execute("""
            SELECT pc.*,
                   u.unit_number,
                   t.name  AS tenant_name,
                   t.phone AS tenant_phone
            FROM payment_claims pc
            JOIN units u ON u.id = pc.unit_id
            LEFT JOIN tenants t ON t.unit_id = pc.unit_id AND t.status = 'active'
            WHERE pc.property_id = ? AND pc.status = 'flagged'
            ORDER BY pc.admin_cleared ASC, pc.caretaker_confirmed ASC, pc.flagged_at DESC
        """, (property_id,)).fetchall()

    return render_template('caretaker/flagged_claims.html',
                           property=prop,
                           claims=claims,
                           active_tab='log_payment')


@caretaker_bp.route('/<property_id>/flagged-claims/<claim_id>/confirm', methods=['POST'])
def confirm_flagged_claim(property_id, claim_id):
    """Caretaker confirms they have re-verified the payment with the tenant."""
    note = request.form.get('note', '').strip()
    if len(note) < 5:
        flash('Please describe what the tenant confirmed (min 5 characters).', 'error')
        return redirect(url_for('caretaker.flagged_claims', property_id=property_id))

    with get_connection() as conn:
        claim = conn.execute(
            "SELECT * FROM payment_claims WHERE id = ? AND property_id = ? AND status = 'flagged'",
            (claim_id, property_id)
        ).fetchone()
        if not claim:
            flash('Claim not found or not flagged.', 'error')
            return redirect(url_for('caretaker.flagged_claims', property_id=property_id))

        conn.execute("""
            UPDATE payment_claims
            SET caretaker_confirmed = 1, caretaker_note = ?, caretaker_confirmed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (note, claim_id))

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('claim_caretaker_confirmed', 'claim', claim_id,
             f"Ref: {claim['mpesa_ref']} | Caretaker note: {note}",
             session.get('caretaker_name', 'caretaker'))
        )

    flash('Confirmation recorded. This claim is still under admin review.', 'success')
    return redirect(url_for('caretaker.flagged_claims', property_id=property_id))


@caretaker_bp.route('/<property_id>/payments/<payment_id>/note', methods=['POST'])
def payment_note(property_id, payment_id):
    """Caretaker adds a note to a verified payment with an amount mismatch."""
    note = request.form.get('note', '').strip()
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        payment = conn.execute(
            "SELECT p.*, pc.mpesa_ref, u.unit_number FROM payments p "
            "LEFT JOIN payment_claims pc ON pc.id = p.claim_id "
            "LEFT JOIN units u ON u.id = p.unit_id "
            "WHERE p.id = ? AND p.property_id = ?",
            (payment_id, property_id)
        ).fetchone()
        if not payment:
            flash('Payment not found.', 'error')
            return redirect(url_for('caretaker.log_payment', property_id=property_id))

        conn.execute(
            "UPDATE payments SET caretaker_note = ?, caretaker_note_at = CURRENT_TIMESTAMP WHERE id = ?",
            (note or None, payment_id)
        )
        actor = session.get('caretaker_name', 'caretaker')
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('caretaker_payment_note', 'payment', payment_id,
             f"Ref: {payment['mpesa_ref'] or '-'} | Unit: {payment['unit_number'] or '-'} | "
             f"Note: {note}" if note else "Note cleared",
             actor)
        )
        from src.platform.guardian import platform_log
        platform_log(conn, 'caretaker_payment_note', 'payment', payment_id,
                     f"Caretaker '{actor}' added note on ref {payment['mpesa_ref'] or '-'}: {note}",
                     property_id=property_id)

    flash('Note saved.', 'success')
    return redirect(url_for('caretaker.payment_activity', property_id=property_id))


@caretaker_bp.route('/<property_id>/tenant/<tenant_id>/statement')
def tenant_statement(property_id, tenant_id):
    """Caretaker view: tenant charge + payment ledger for dispute resolution."""
    from src.utils.statement import get_tenant_statement
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        tenant, ledger, open_claims = get_tenant_statement(conn, tenant_id, property_id)
        if not tenant:
            abort(404)
    from_page = request.args.get('from', 'tenants')
    return render_template('caretaker/statement.html',
                           property=prop,
                           tenant=tenant,
                           ledger=ledger,
                           open_claims=open_claims,
                           active_tab='arrears' if from_page == 'arrears' else 'tenants')


@caretaker_bp.route('/<property_id>/tenant/<tenant_id>/quick-verify', methods=['POST'])
def tenant_quick_verify(property_id, tenant_id):
    """AJAX: parse M-Pesa message/ref and check bank records."""
    from flask import jsonify
    from src.utils.statement import quick_verify_ref
    text = request.form.get('text', '').strip()
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            return jsonify({'status': 'parse_error', 'message': 'Property not found'}), 404
        org_id = conn.execute(
            "SELECT organization_id FROM properties WHERE id = ?", (property_id,)
        ).fetchone()['organization_id']
        tenant = conn.execute(
            "SELECT unit_id FROM tenants WHERE id = ? AND property_id = ?",
            (tenant_id, property_id)
        ).fetchone()
        if not tenant:
            return jsonify({'status': 'parse_error', 'message': 'Tenant not found'}), 404
        result = quick_verify_ref(conn, text, tenant['unit_id'], org_id)
    return jsonify(result)


@caretaker_bp.route('/<property_id>/tenant/<tenant_id>/quick-assign/<txn_id>', methods=['POST'])
def tenant_quick_assign(property_id, tenant_id, txn_id):
    """Assign a found bank transaction to this tenant directly from the statement page."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        tenant = conn.execute(
            "SELECT unit_id, name FROM tenants WHERE id = ? AND property_id = ?",
            (tenant_id, property_id)
        ).fetchone()
        if not tenant:
            abort(404)
        unit_id = tenant['unit_id']

        txn = conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not txn:
            flash('Transaction not found.', 'error')
            return redirect(url_for('caretaker.tenant_statement', property_id=property_id, tenant_id=tenant_id))
        if conn.execute("SELECT id FROM payments WHERE bank_txn_id = ?", (txn_id,)).fetchone():
            flash('Transaction has already been assigned.', 'warning')
            return redirect(url_for('caretaker.tenant_statement', property_id=property_id, tenant_id=tenant_id))

        caretaker_name = session.get('caretaker_name', 'Caretaker')
        payment_id = generate_id('PAY')
        conn.execute("""
            INSERT INTO payments
            (id, property_id, unit_id, bank_txn_id, statement_id, amount, payment_date,
             assignment_type, assignment_reason, assigned_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'Assigned from caretaker statement view', ?)
        """, (payment_id, property_id, unit_id, txn_id, txn['statement_id'],
              txn['amount'], txn['txn_date'] or '', caretaker_name))
        allocate_payment(conn, payment_id, unit_id, txn['amount'])

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('payment_manual_assigned', 'payment', payment_id,
             f"Ref: {txn['mpesa_ref'] or '-'} | KES {float(txn['amount']):,.0f} | "
             f"Assigned to {tenant['name']} by {caretaker_name} from statement view",
             caretaker_name)
        )

    flash(f"KES {float(txn['amount']):,.0f} (ref {txn['mpesa_ref']}) assigned and allocated.", 'success')
    # Assigning now also happens from the dashboard and the unassigned list, so
    # send the caretaker back where they were instead of always to the statement.
    if request.form.get('return_to') == 'unassigned':
        return redirect(url_for('caretaker.unassigned', property_id=property_id))
    if request.form.get('return_to') == 'dashboard':
        return redirect(url_for('caretaker.dashboard', property_id=property_id))
    return redirect(url_for('caretaker.tenant_statement', property_id=property_id, tenant_id=tenant_id))

"""Tenant portal: token URL access (no session) + optional persons-based holistic login."""

import os
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from src.database.db import get_connection, generate_id
from src.payments.daraja import stk_push
from src.utils.statement import get_tenant_statement
from src.utils.tenant_documents import (account_coverage, build_invoice, build_receipt, invoice_periods,
                                         period_label, unpaid_bills)
from src.utils.water_breakdown import water_breakdowns
from src.messaging import staff_alerts, threads
from src.reconciliation import claim_owner
from src.leases import move_out
from src.utils import tenant_contacts
from src.messaging.delivery import send_sms_async
from src.utils.phone import normalize_to_e164 as _normalize_phone, normalize_to_daraja
from src.utils.tenant_auth import (
    check_otp, check_pin, issue_otp, mask_phone, person_for_phone, set_pin,
    tenancies_for_phone, valid_pin,
)

tenant_bp = Blueprint('tenant', __name__, url_prefix='/tenant')


def _get_tenant_by_token(conn, token):
    """Return (tenant, unit, property) row dicts or None if invalid/revoked."""
    if not token or not token.strip():
        return None
    row = conn.execute("""
        SELECT t.id AS tenant_id, t.name AS tenant_name, t.phone AS tenant_phone, t.unit_id,
               u.id AS unit_id, u.unit_number, u.property_id,
               p.id AS property_id, p.name AS property_name, p.organization_id,
               p.pay_paybill, p.pay_account, p.pay_account_name, p.repair_alerts_to, t.short_code,
               t.status AS tenant_status, t.deposit_paid, t.welcomed_at
        FROM tenants t
        JOIN units u ON t.unit_id = u.id
        JOIN properties p ON u.property_id = p.id
        WHERE t.access_token = ? AND t.unit_id IS NOT NULL
    """, (token.strip(),)).fetchone()
    if not row:
        return None
    tenant = {'id': row['tenant_id'], 'name': row['tenant_name'], 'phone': row['tenant_phone'], 'unit_id': row['unit_id'],
              'short_code': row['short_code'], 'status': row['tenant_status'], 'deposit_paid': row['deposit_paid'],
              'welcomed_at': row['welcomed_at']}
    unit = {'id': row['unit_id'], 'unit_number': row['unit_number'], 'property_id': row['property_id']}
    # organization_id: report_payment's bank check is org-scoped (it used to be missing here,
    # so the check silently fell back to property-scoped statements only).
    prop = {'id': row['property_id'], 'name': row['property_name'], 'organization_id': row['organization_id'],
            'pay_paybill': row['pay_paybill'], 'pay_account': row['pay_account'],
            'pay_account_name': row['pay_account_name'], 'repair_alerts_to': row['repair_alerts_to']}
    return (tenant, unit, prop)


# ── Sign-in ──────────────────────────────────────────────────────────────────
#
# Every page behind /tenant/<token> needs the tenant signed in as the person the
# tenancy belongs to. The token alone (an SMS link that may have been forwarded)
# only tells us who they claim to be; the SMS code or their PIN proves it.

_PUBLIC = {'tenant.login', 'tenant.logout', 'tenant.verify', 'tenant.verify_code'}
_JSON = {'tenant.pay_stk', 'tenant.pay_status'}


def _sign_in(person_id, name):
    session.permanent = True                      # stay signed in on their own phone
    session['tenant_person_id'] = person_id
    session['tenant_person_name'] = name
    with get_connection() as conn:                # "Sign out everywhere" raises this (tenant_contacts)
        session['tenant_session_version'] = tenant_contacts.session_version(conn, person_id)


@tenant_bp.before_request
def require_tenant_sign_in():
    if request.endpoint in _PUBLIC or not request.endpoint:
        return None
    token = (request.view_args or {}).get('token')
    if not token:
        return None if session.get('tenant_person_id') else redirect(url_for('tenant.login'))
    with get_connection() as conn:
        row = conn.execute("""
            SELECT t.person_id, p.pin_hash, COALESCE(p.session_version, 0) AS session_version FROM tenants t
            LEFT JOIN persons p ON p.id = t.person_id
            WHERE t.access_token = ? AND t.unit_id IS NOT NULL
        """, (token.strip(),)).fetchone()
    if not row:
        return None                               # the route renders invalid_token
    if (row['person_id'] and session.get('tenant_person_id') == row['person_id']
            and session.get('tenant_session_version', 0) != row['session_version']):
        for key in ('tenant_person_id', 'tenant_person_name', 'tenant_session_version'):
            session.pop(key, None)                # signed out everywhere since this phone signed in
    if row['person_id'] and session.get('tenant_person_id') == row['person_id']:
        session[f'pay_auth_{token}'] = True       # signed in counts as PIN-verified for paying
        return None
    if request.endpoint in _JSON:
        return jsonify({'success': False, 'status': 'error', 'error': 'Please sign in again'}), 401
    session['tenant_entry_token'] = token
    session['tenant_next'] = request.path
    return redirect(url_for('tenant.login' if row['pin_hash'] else 'tenant.verify'))


def _entry_tenancy(conn):
    """The tenancy the tenant arrived through via their SMS link, if any."""
    token = session.get('tenant_entry_token')
    if not token:
        return None
    row = conn.execute("""
        SELECT t.id, t.name, t.phone, t.person_id, u.unit_number, p.name AS property_name
        FROM tenants t JOIN units u ON u.id = t.unit_id JOIN properties p ON p.id = t.property_id
        WHERE t.access_token = ?
    """, (token,)).fetchone()
    return dict(row) if row else None


def _after_sign_in(conn, person_id):
    """Where to go once signed in: the page they asked for, their one tenancy, or the list."""
    nxt = session.pop('tenant_next', None)
    session.pop('tenant_entry_token', None)
    tenancies = conn.execute("""
        SELECT access_token FROM tenants
        WHERE person_id = ? AND status IN ('active', 'departed') AND access_token IS NOT NULL
    """, (person_id,)).fetchall()
    tokens = {t['access_token'] for t in tenancies}
    if nxt and any(nxt.startswith(f'/tenant/{t}') for t in tokens):
        return redirect(nxt)
    if len(tokens) == 1:
        return redirect(url_for('tenant.portal', token=tokens.pop()))
    return redirect(url_for('tenant.dashboard'))


@tenant_bp.route("/login", methods=["GET", "POST"])
def login():
    """Phone + 4-digit PIN. Arriving from an SMS link, only the PIN is asked for."""
    error = None
    with get_connection() as conn:
        entry = _entry_tenancy(conn)
        if request.method == "POST":
            phone = entry['phone'] if entry else (request.form.get("phone") or "").strip()
            pin = (request.form.get("pin") or "").strip()
            tenancies = tenancies_for_phone(conn, phone)
            person_id = next((t['person_id'] for t in tenancies if t['person_id']), None)
            result, minutes = check_pin(conn, person_id, pin) if person_id else ('no_pin', 0)
            if result == 'ok':
                person = conn.execute("SELECT name FROM persons WHERE id = ?", (person_id,)).fetchone()
                _sign_in(person_id, person['name'])
                return _after_sign_in(conn, person_id)
            if result == 'locked':
                error = f"Too many wrong PINs. Try again in {minutes} minutes, or reset your PIN."
            elif result == 'no_pin':
                error = "This number has no PIN yet. Tap \"First time or forgot PIN\" below."
            else:
                error = "That PIN is not correct."
    return render_template("tenant/login.html", error=error, entry=entry,
                           masked=mask_phone(entry['phone']) if entry else None)


@tenant_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Send a 6-digit code to the phone on record — first-time setup and forgotten PINs."""
    from src.utils.runtime import is_demo
    code_sms = phone = None
    with get_connection() as conn:
        entry = _entry_tenancy(conn)
        if request.method != "POST":
            return render_template("tenant/verify.html", entry=entry,
                                   masked=mask_phone(entry['phone']) if entry else None)
        phone = entry['phone'] if entry else (request.form.get("phone") or "").strip()
        if not entry and not _normalize_phone(phone):
            # Only the format is checked here, so this says nothing about who rents where.
            return render_template("tenant/verify.html", entry=None, phone=phone,
                                   error="That is not a full phone number. Use the 10 digits, e.g. 0712 345 678.")
        if is_demo():
            # Demo: your own number becomes the new tenant's, so the code really reaches you.
            from src.demo.phones import claim_first_time
            claim_first_time(conn, phone)
        tenancies = tenancies_for_phone(conn, phone)
        session['tenant_verify_person'] = None
        session['demo_no_tenant'] = bool(is_demo() and not tenancies)
        if is_demo() and (not tenancies or session.get('demo_sms_person') not in
                          {t['person_id'] for t in tenancies if t['person_id']}):
            # A code from a different phone's earlier attempt must never appear here.
            session.pop('demo_sms_code', None)
            session.pop('demo_sms_status', None)
        if tenancies:
            person_id = person_for_phone(conn, phone, tenancies)
            session['tenant_verify_person'] = person_id
            code = issue_otp(conn, person_id)
            if code and is_demo():
                session['demo_sms_code'] = code          # also shown on screen in the demo
                session['demo_sms_person'] = person_id
            elif not code and is_demo() and session.get('demo_sms_status') == 'sent':
                session['demo_sms_status'] = 'recent'    # do not turn a failed delivery into "sent"
            if code:
                code_sms = (f"Your {(tenancies[0]['property_name'] or '').title()} tenant code is {code}. "
                            f"It expires in 10 minutes. Do not share it.")
    # The code is saved (the block above committed) before the SMS goes: sending
    # from inside the transaction raced the write and could fail without a trace.
    if code_sms:
        session.pop('demo_sms_status', None)
    if code_sms:
        if is_demo():
            from src.messaging.delivery import send_sms
            sent, _, errors = send_sms([phone], code_sms)     # wait, so the presenter sees the outcome
            session['demo_sms_status'] = 'sent' if sent else (errors[0] if errors else 'not sent')
        else:
            send_sms_async([phone], code_sms)
    # Same answer whether or not the number is a tenant's, so the page
    # cannot be used to find out who rents where.
    session['tenant_verify_masked'] = mask_phone(phone)
    return redirect(url_for('tenant.verify_code'))


@tenant_bp.route("/verify/code", methods=["GET", "POST"])
def verify_code():
    """Enter the SMS code and choose a PIN."""
    error = None
    masked = session.get('tenant_verify_masked')
    if not masked:
        return redirect(url_for('tenant.verify'))
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        pin = (request.form.get("pin") or "").strip()
        pin2 = (request.form.get("pin_confirm") or "").strip()
        person_id = session.get('tenant_verify_person')
        if not valid_pin(pin):
            error = "Your PIN must be exactly 4 digits."
        elif pin != pin2:
            error = "The two PINs do not match."
        elif not person_id:
            error = "That code is not correct."
        else:
            with get_connection() as conn:
                result = check_otp(conn, person_id, code)
                if result == 'ok':
                    set_pin(conn, person_id, pin)
                    person = conn.execute("SELECT name FROM persons WHERE id = ?", (person_id,)).fetchone()
                    session.pop('tenant_verify_person', None)
                    session.pop('tenant_verify_masked', None)
                    _sign_in(person_id, person['name'])
                    return _after_sign_in(conn, person_id)
            error = {'wrong': "That code is not correct.",
                     'expired': "That code has expired. Request a new one.",
                     'too_many': "Too many wrong codes. Request a new one."}[result]
    return render_template("tenant/verify_code.html", error=error, masked=masked)


@tenant_bp.route("/logout")
def logout():
    """Sign out of this phone."""
    for key in ('tenant_person_id', 'tenant_person_name', 'tenant_entry_token', 'tenant_next'):
        session.pop(key, None)
    for key in [k for k in session.keys() if k.startswith('pay_auth_')]:
        session.pop(key, None)
    return redirect(url_for("tenant.login"))


@tenant_bp.route("/dashboard")
def dashboard():
    """Holistic tenant dashboard: all units for the logged-in person."""
    person_id = session.get("tenant_person_id")
    if not person_id:
        return redirect(url_for("tenant.login"))

    from datetime import datetime
    with get_connection() as conn:
        units = conn.execute(
            """SELECT t.id AS tenant_id, t.name AS tenant_name, t.access_token,
                      u.id AS unit_id, u.unit_number,
                      p.name AS property_name, p.id AS property_id,
                      COALESCE(tb.total_charged, 0) AS total_charged,
                      COALESCE(tb.total_paid, 0) AS total_paid,
                      COALESCE(tb.balance, 0) AS balance
               FROM tenants t
               JOIN units u ON t.unit_id = u.id
               JOIN properties p ON u.property_id = p.id
               LEFT JOIN tenant_balances tb ON tb.tenant_id = t.id
               WHERE t.person_id = ? AND t.status IN ('active', 'departed')
               ORDER BY p.name, u.unit_number""",
            (person_id,),
        ).fetchall()

        unit_details = []
        for row in units:
            last_payment = conn.execute(
                "SELECT amount, payment_date FROM payments WHERE tenant_id = ? ORDER BY payment_date DESC LIMIT 1",
                (row["tenant_id"],),
            ).fetchone()

            pending = float(conn.execute(
                "SELECT COALESCE(SUM(claimed_amount), 0) FROM payment_claims WHERE tenant_id = ? AND status = 'pending'",
                (row["tenant_id"],),
            ).fetchone()[0])

            unit_details.append({
                "tenant_id": row["tenant_id"],
                "tenant_name": row["tenant_name"],
                "access_token": row["access_token"],
                "unit_number": row["unit_number"],
                "property_name": row["property_name"],
                "property_id": row["property_id"],
                "total_charged": float(row["total_charged"]),
                "total_paid": float(row["total_paid"]),
                "balance": max(0.0, float(row["balance"]) - pending),
                "pending": pending,
                "last_payment": last_payment,
            })

    return render_template(
        "tenant/dashboard.html",
        units=unit_details,
        person_name=session.get("tenant_person_name", ""),
    )


@tenant_bp.route('/<token>')
def portal(token):
    """Balance summary and recent messages."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        if not tenant['welcomed_at'] and tenant['status'] == 'active':
            return redirect(url_for('tenant.welcome', token=token))     # first visit: how it all works
        notice = move_out.current(conn, tenant['id'])
        latest = threads.latest_for_tenant(conn, tenant['id'])
        balance_row = conn.execute("""
            SELECT total_charged, total_paid, balance
            FROM tenant_balances WHERE tenant_id = ?
        """, (tenant['id'],)).fetchone()
        total_charged = float(balance_row['total_charged']) if balance_row else 0
        total_paid = float(balance_row['total_paid']) if balance_row else 0
        verified_balance = float(balance_row['balance']) if balance_row else 0
        coverage = account_coverage(conn, tenant['id'])
        _, ledger, _ = get_tenant_statement(conn, tenant['id'], prop['id'])
        owed = unpaid_bills(ledger, verified_balance)

        # Sum pending claims not yet verified — subtract from balance for tenant view only
        pending_claims_total = conn.execute("""
            SELECT COALESCE(SUM(claimed_amount), 0) AS total
            FROM payment_claims
            WHERE tenant_id = ? AND status = 'pending'
        """, (tenant['id'],)).fetchone()['total']
        pending_claims_total = float(pending_claims_total)

        # Same figure as the statement: what the bank-matched ledger says. Payments
        # reported but not yet matched are shown beside it, never netted into it.
        balance = verified_balance
        total_paid_display = total_paid

        recent_messages = conn.execute("""
            SELECT id, subject, body, message_type, read_at, created_at
            FROM messages WHERE tenant_id = ?
            ORDER BY created_at DESC LIMIT 10
        """, (tenant['id'],)).fetchall()

        caretaker = conn.execute("""
            SELECT name, phone FROM caretakers
            WHERE property_id = ? AND phone IS NOT NULL AND TRIM(phone) != ''
            ORDER BY created_at LIMIT 1
        """, (prop['id'],)).fetchone()

    return render_template(
        'tenant/portal.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        total_charged=total_charged,
        total_paid=total_paid_display,
        balance=balance,
        pending_total=pending_claims_total,
        recent_messages=recent_messages,
        caretaker=caretaker,
        coverage=coverage,
        owed=owed,
        notice=notice,
        latest=latest,
    )


@tenant_bp.route('/<token>/welcome')
def welcome(token):
    """First visit: what every part of the account does, in plain words (English / Kiswahili).
    Seen once automatically; open again any time from "How to use Domi"."""
    from src.utils.guides import current_lang, welcome as welcome_text
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        if not tenant['welcomed_at']:
            conn.execute("UPDATE tenants SET welcomed_at = CURRENT_TIMESTAMP WHERE id = ?", (tenant['id'],))
    lang = current_lang()
    return render_template('tenant/welcome.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='portal', w=welcome_text(lang), lang=lang)


@tenant_bp.route('/<token>/move-out', methods=['GET', 'POST'])
def move_out_page(token):
    """Give notice to move out; see the deposit and roughly what comes back."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        balance_row = conn.execute("SELECT balance FROM tenant_balances WHERE tenant_id = ?", (tenant['id'],)).fetchone()
        balance = float(balance_row['balance']) if balance_row else 0.0
        error, alerts, notice_given = None, [], False
        if request.method == 'POST' and tenant['status'] == 'active':
            try:
                _, conv_id = move_out.give_notice(conn, tenant, unit, prop, request.form.get('planned_date'),
                                                  request.form.get('reason_detail') or request.form.get('reason'),
                                                  request.form.get('refund_phone'))
                alerts = staff_alerts.due(conn, conv_id, os.environ.get('APP_BASE_URL') or request.host_url)
                notice_given = True
            except ValueError as e:
                error = str(e)
        notice = move_out.current(conn, tenant['id'])
        departure = conn.execute("""SELECT * FROM tenant_departures WHERE tenant_id = ?
                                     ORDER BY created_at DESC LIMIT 1""", (tenant['id'],)).fetchone()
    if notice_given:
        staff_alerts.send(alerts)          # after commit
        return redirect(url_for('tenant.move_out_page', token=token, given=1))
    return render_template('tenant/move_out.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='move_out', notice=notice, departure=departure, error=error,
                           est=move_out.estimate(tenant['deposit_paid'], balance), balance=balance,
                           reasons=move_out.REASONS, full_notice=move_out.earliest_with_full_notice(),
                           tomorrow=(datetime.now().date() + timedelta(days=1)).isoformat(),
                           form=request.form, given=request.args.get('given'))


@tenant_bp.route('/<token>/settings', methods=['GET', 'POST'])
def settings(token):
    """Your details (read-only), change your PIN, and your language."""
    from src.utils.guides import LANGS
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        person_id = session.get('tenant_person_id')
        done = error = send_code = None
        alerts, signed_out = [], False
        if request.method == 'POST' and request.form.get('action') == 'pin':
            current, new, again = (request.form.get(k, '').strip() for k in ('current_pin', 'new_pin', 'new_pin_again'))
            result, minutes = check_pin(conn, person_id, current)
            if result == 'locked':
                error = f'Too many wrong PINs. Try again in {minutes} minutes.'
            elif result != 'ok':
                error = 'Your current PIN is not correct.'
            elif not valid_pin(new):
                error = 'Your new PIN must be exactly 4 digits.'
            elif new != again:
                error = 'The two new PINs do not match.'
            else:
                set_pin(conn, person_id, new)
                conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                             ('tenant_pin_changed', 'person', person_id, f"Unit {unit['unit_number']}", 'tenant'))
                done = 'Your PIN is changed. Use the new one next time you sign in.'
        elif request.method == 'POST' and request.form.get('action') == 'language':
            lang = request.form.get('lang')
            if lang in LANGS:
                conn.execute("UPDATE tenants SET language_preference = ? WHERE id = ?", (lang, tenant['id']))
                session['lang'] = lang
                done = 'Saved.' if lang == 'en' else 'Imehifadhiwa.'
        elif request.method == 'POST' and request.form.get('action') == 'add_contact':
            try:
                tenant_contacts.add_contact(conn, tenant, request.form.get('kind'), request.form.get('phone'),
                                            request.form.get('name'), request.form.get('relationship'))
                done = 'Saved.'
            except ValueError as e:
                error = str(e)
        elif request.method == 'POST' and request.form.get('action') == 'remove_contact':
            tenant_contacts.remove_contact(conn, tenant, request.form.get('contact_id'))
            done = 'Removed.'
        elif request.method == 'POST' and request.form.get('action') == 'phone_request':
            try:
                _, code = tenant_contacts.request_change(conn, tenant, person_id, prop, request.form.get('new_phone'))
                send_code = (_normalize_phone(request.form.get('new_phone')), code)
                done = 'A 6-digit code is on its way to your new number. Type it below.'
            except ValueError as e:
                error = str(e)
        elif request.method == 'POST' and request.form.get('action') == 'phone_confirm':
            try:
                conv_id = tenant_contacts.confirm_code(conn, tenant, unit, prop, request.form.get('code'))
                alerts = staff_alerts.due(conn, conv_id, os.environ.get('APP_BASE_URL') or request.host_url)
                done = 'Sent to management. Your number changes when they approve it; you get an SMS.'
            except ValueError as e:
                error = str(e)
        elif request.method == 'POST' and request.form.get('action') == 'phone_cancel':
            tenant_contacts.cancel(conn, tenant)
            done = 'Cancelled. Your number stays as it is.'
        elif request.method == 'POST' and request.form.get('action') == 'signout_all':
            tenant_contacts.sign_out_everywhere(conn, person_id)
            signed_out = True
        details = conn.execute("""
            SELECT t.move_in_date, t.deposit_paid, t.language_preference, u.monthly_rent, u.service_charge,
                   (SELECT l.start_date FROM leases l WHERE l.tenant_id = t.id ORDER BY l.created_at DESC LIMIT 1) AS lease_start
            FROM tenants t JOIN units u ON u.id = t.unit_id WHERE t.id = ?
        """, (tenant['id'],)).fetchone()
        contacts = tenant_contacts.contacts(conn, tenant['id'])
        phone_request = tenant_contacts.open_request(conn, tenant['id'])
    # Texts go after the save (an SMS sent inside the transaction can fail without a trace).
    if signed_out:
        for key in ('tenant_person_id', 'tenant_person_name', 'tenant_session_version'):
            session.pop(key, None)
        return redirect(url_for('tenant.login'))
    staff_alerts.send(alerts)
    from src.utils.runtime import is_demo
    if send_code:
        if is_demo():
            session['demo_phone_code'] = send_code[1]          # demo: shown on screen as well
        from src.messaging.delivery import send_sms
        send_sms([send_code[0]], f"Your code to change your phone number on {(prop['name'] or '').title()} "
                                 f"is {send_code[1]}. It expires in 10 minutes. Do not share it.")
    return render_template('tenant/settings.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='settings', details=details, done=done, error=error,
                           lang=session.get('lang') or details['language_preference'] or 'en',
                           contacts=contacts, kinds=tenant_contacts.KINDS, limits=tenant_contacts.LIMITS,
                           phone_request=phone_request,
                           demo_code=session.get('demo_phone_code') if is_demo() and phone_request else None)


@tenant_bp.route('/<token>/move-out/withdraw', methods=['POST'])
def move_out_withdraw(token):
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        notice = move_out.current(conn, tenant['id'])
        if notice:
            move_out.withdraw(conn, notice, tenant, unit)
    return redirect(url_for('tenant.move_out_page', token=token))


@tenant_bp.route('/<token>/charges')
def charges(token):
    """All charges by period with paid/unpaid status per charge."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        charge_rows = conn.execute("""
            SELECT rc.id, rc.period, rc.charge_type, rc.amount, rc.due_date,
                   COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.charge_id = rc.id), 0) AS paid
            FROM rent_charges rc
            WHERE rc.tenant_id = ?
            ORDER BY rc.period DESC, rc.charge_type
        """, (tenant['id'],)).fetchall()
        water = water_breakdowns(conn, tenant['id'], prop['id'])

        raw_by_period = {}
        for r in charge_rows:
            period = r['period']
            if period not in raw_by_period:
                raw_by_period[period] = []
            amt = float(r['amount'])
            paid = float(r['paid'])
            raw_by_period[period].append({
                'id': r['id'],
                'charge_type': r['charge_type'],
                'amount': amt,
                'paid': paid,
                'outstanding': amt - paid,
                'due_date': r['due_date'],
                'water_breakdown': water.get(r['id']),
            })

        charges_by_period = {}
        for period, items in raw_by_period.items():
            period_total = sum(c['amount'] for c in items)
            period_paid = sum(c['paid'] for c in items)
            period_outstanding = sum(c['outstanding'] for c in items)
            charges_by_period[period] = {
                'charges': items,
                'period_total': period_total,
                'period_paid': period_paid,
                'period_outstanding': period_outstanding,
            }

    return render_template(
        'tenant/charges.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        charges_by_period=charges_by_period,
    )


@tenant_bp.route('/<token>/payments')
def payments(token):
    """Payment history with M-Pesa ref and allocation trail."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        # Verified payments with allocations
        # Every payment on this tenancy: from bank statements, reported claims,
        # and M-Pesa/card payments made in the app (ref in assignment_reason).
        payment_rows = conn.execute("""
            SELECT p.id, p.amount, p.payment_date,
                   COALESCE(bt.mpesa_ref, pc.mpesa_ref,
                            CASE WHEN p.source IN ('daraja', 'pesapal') THEN p.assignment_reason END) AS mpesa_ref,
                   bt.sender_name
            FROM payments p
            LEFT JOIN bank_transactions bt ON p.bank_txn_id = bt.id
            LEFT JOIN payment_claims pc ON p.claim_id = pc.id
            WHERE p.tenant_id = ?
            ORDER BY p.payment_date DESC, p.id
        """, (tenant['id'],)).fetchall()

        payments_with_allocations = []
        for p in payment_rows:
            allocs = conn.execute("""
                SELECT pa.amount AS alloc_amount, rc.charge_type, rc.period
                FROM payment_allocations pa
                JOIN rent_charges rc ON pa.charge_id = rc.id
                WHERE pa.payment_id = ?
                ORDER BY rc.created_at ASC
            """, (p['id'],)).fetchall()
            payments_with_allocations.append({
                'payment_id': p['id'],
                'date': p['payment_date'],
                'amount': float(p['amount']),
                'mpesa_ref': p['mpesa_ref'] or '-',
                'sender_name': p['sender_name'] or '-',
                'allocations': allocs,
                'status': 'verified',
            })

        # Unverified claims (pending + flagged) — skip refs already in verified payments
        verified_refs = {p['mpesa_ref'] for p in payment_rows if p['mpesa_ref']}
        claim_rows = conn.execute("""
            SELECT mpesa_ref, claimed_amount, status, flag_reason, created_at
            FROM payment_claims
            WHERE tenant_id = ? AND status IN ('pending', 'flagged')
            ORDER BY created_at DESC
        """, (tenant['id'],)).fetchall()

        for c in claim_rows:
            if c['mpesa_ref'] in verified_refs:
                continue
            payments_with_allocations.append({
                'date': c['created_at'][:10] if c['created_at'] else '',
                'amount': float(c['claimed_amount'] or 0),
                'mpesa_ref': c['mpesa_ref'] or '-',
                'sender_name': '-',
                'allocations': [],
                'status': c['status'],
                'flag_reason': c['flag_reason'],
            })

        # Sort combined list by date descending
        payments_with_allocations.sort(key=lambda x: x['date'], reverse=True)

    return render_template(
        'tenant/payments.html',
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        payments_with_allocations=payments_with_allocations,
        held_text=claim_owner.TENANT_TEXT,
    )


@tenant_bp.route('/<token>/statement')
def statement(token):
    """Every charge and payment on this tenancy, with the running balance."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        _, rows, open_claims = get_tenant_statement(conn, tenant['id'], prop['id'])
        coverage = account_coverage(conn, tenant['id'])
    balance = rows[-1]['running_balance'] if rows else 0.0
    return render_template('tenant/statement.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='statement', rows=list(reversed(rows)), balance=balance,
                           owed=unpaid_bills(rows, balance),
                           periods=invoice_periods(rows), period_label=period_label,
                           coverage=coverage,
                           pending=[c for c in open_claims if c['status'] == 'pending'])


@tenant_bp.route('/<token>/invoice/<period>')
def invoice(token, period):
    """One month's invoice — printable."""
    if not re.fullmatch(r'\d{4}-\d{2}', period):
        return redirect(url_for('tenant.statement', token=token))
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        inv = build_invoice(conn, tenant['id'], prop['id'], period)
    if not inv:
        return redirect(url_for('tenant.statement', token=token))
    return render_template('tenant/invoice.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='statement', inv=inv)


@tenant_bp.route('/<token>/receipt/<payment_id>')
def receipt(token, payment_id):
    """Receipt for one payment — printable. Only the tenant's own payments."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        rec = build_receipt(conn, tenant['id'], prop['id'], payment_id)
    if not rec:
        return redirect(url_for('tenant.statement', token=token))
    return render_template('tenant/receipt.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='payments', rec=rec)


@tenant_bp.route('/<token>/messages')
def messages(token):
    """Conversations with management, plus notices sent to this tenant."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        conversations = threads.list_for_tenant(conn, tenant['id'])
        # Repair requests from before every repair had a conversation
        earlier_repairs = conn.execute("""
            SELECT m.title, m.category, m.status, m.created_at, m.resolved_at FROM maintenance_issues m
            WHERE m.raised_by_tenant_id = ? AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.issue_id = m.id)
            ORDER BY m.created_at DESC
        """, (tenant['id'],)).fetchall()
        notices = conn.execute("""
            SELECT id, subject, body, message_type, read_at, created_at
            FROM messages WHERE tenant_id = ?
            ORDER BY created_at DESC LIMIT 30
        """, (tenant['id'],)).fetchall()
        conn.executemany(
            "UPDATE messages SET read_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ? AND read_at IS NULL",
            [(tenant['id'], m['id']) for m in notices],
        )
    return render_template('tenant/messages.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='messages', conversations=conversations, notices=notices,
                           earlier_repairs=earlier_repairs,
                           topics=threads.TOPICS)


@tenant_bp.route('/<token>/messages/new', methods=['GET', 'POST'])
def message_new(token):
    """Start a conversation. ?topic=repair|money|other; ?ref=payment:<id>|charge:<id> links it to a ledger line."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        topic = request.values.get('topic') or 'money'
        ref_type, _, ref_id = (request.values.get('ref') or '').partition(':')
        ref_text = threads.ref_summary(conn, tenant['id'], ref_type, ref_id) if ref_id else None
        if not ref_text:
            ref_type = ref_id = None
        error = conv_id = None
        if request.method == 'POST':
            subject = (request.form.get('subject') or '').strip() or ref_text or ''
            body = (request.form.get('body') or '').strip()
            if not subject or not body:
                error = 'Please write a short title and your message.'
            else:
                conv_id, saved, rejected = threads.start_conversation(
                    conn, tenant, unit, prop, topic, subject[:120], body,
                    photos=request.files.getlist('photos'), ref_type=ref_type, ref_id=ref_id,
                    category=request.form.get('category') or 'general')
                alerts = staff_alerts.due(conn, conv_id, os.environ.get('APP_BASE_URL') or request.host_url)
    if conv_id:
        staff_alerts.send(alerts)          # after commit
        return redirect(url_for('tenant.conversation', token=token, conv_id=conv_id, rejected=rejected or None))
    return render_template('tenant/message_new.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='messages', topic=topic if topic in threads.TOPICS else 'money',
                           topics=threads.TOPICS, categories=threads.REPAIR_CATEGORIES,
                           ref=f'{ref_type}:{ref_id}' if ref_id else '', ref_text=ref_text,
                           error=error, max_photos=threads.MAX_PHOTOS,
                           alert_line=staff_alerts.tenant_line(prop, topic if topic in threads.TOPICS else 'money'))


@tenant_bp.route('/<token>/messages/<conv_id>', methods=['GET', 'POST'])
def conversation(token, conv_id):
    """One conversation: read it, reply, add photos."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        conv, msgs = threads.thread(conn, conv_id)
        if not conv or conv['tenant_id'] != tenant['id']:
            return redirect(url_for('tenant.messages', token=token))
        sent = False
        if request.method == 'POST':
            body = (request.form.get('body') or '').strip()
            if body:
                _, _, rejected = threads.add_message(conn, conv_id, 'tenant', tenant['name'], body,
                                                     request.files.getlist('photos'))
                alerts = staff_alerts.due(conn, conv_id, os.environ.get('APP_BASE_URL') or request.host_url)
                sent = True
        if not sent:
            threads.mark_read(conn, conv_id, 'tenant')
    if sent:
        staff_alerts.send(alerts)          # after commit
        return redirect(url_for('tenant.conversation', token=token, conv_id=conv_id, rejected=rejected or None))
    return render_template('tenant/conversation.html', token=token, tenant=tenant, unit=unit, property=prop,
                           active_tab='messages', conv=conv, msgs=msgs, topics=threads.TOPICS,
                           rejected=request.args.get('rejected'), max_photos=threads.MAX_PHOTOS)


@tenant_bp.route('/<token>/messages/<conv_id>/photo/<photo_id>')
def conversation_photo(token, conv_id, photo_id):
    """A photo in one of this tenant's own conversations."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            abort(404)
        tenant, _, _ = ctx
        row = conn.execute("""
            SELECT ph.file_name FROM conversation_photos ph JOIN conversations c ON c.id = ph.conversation_id
            WHERE ph.id = ? AND c.id = ? AND c.tenant_id = ?
        """, (photo_id, conv_id, tenant['id'])).fetchone()
    if not row:
        abort(404)
    return send_from_directory(threads.photos_dir(), row['file_name'], mimetype='image/jpeg')


@tenant_bp.route('/<token>/maintenance')
def maintenance(token):
    """Repairs live in Messages (each repair is a conversation, with its open/fixed status).
    Kept so old links still land somewhere useful."""
    return redirect(url_for('tenant.messages', token=token))


@tenant_bp.route('/<token>/pay', methods=['GET'])
def pay(token):
    """Payment tab — tenant self-reports M-Pesa payment."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        balance_row = conn.execute(
            "SELECT balance FROM tenant_balances WHERE tenant_id = ?", (tenant['id'],)
        ).fetchone()
        balance = float(balance_row['balance']) if balance_row else 0.0
        _, ledger, _ = get_tenant_statement(conn, tenant['id'], prop['id'])
        owed = unpaid_bills(ledger, balance)
        bill = request.args.get('bill')
        chosen = next((b for b in owed['bills'] if b['charge_id'] == bill), None) if bill else None

        claims_raw = conn.execute("""
            SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.status, pc.flag_reason, pc.created_at,
                   p.amount AS verified_amount, p.id AS payment_id
            FROM payment_claims pc
            LEFT JOIN payments p ON p.claim_id = pc.id
            WHERE pc.tenant_id = ?
            ORDER BY pc.created_at DESC LIMIT 20
        """, (tenant['id'],)).fetchall()

        claims = []
        for c in claims_raw:
            allocs = []
            if c['status'] == 'verified' and c['payment_id']:
                allocs = conn.execute("""
                    SELECT pa.amount AS alloc_amount, rc.charge_type, rc.period,
                           (SELECT COALESCE(SUM(pa2.amount), 0) FROM payment_allocations pa2
                            WHERE pa2.charge_id = rc.id) >= rc.amount AS charge_settled
                    FROM payment_allocations pa
                    JOIN rent_charges rc ON pa.charge_id = rc.id
                    WHERE pa.payment_id = ?
                    ORDER BY rc.period, rc.charge_type
                """, (c['payment_id'],)).fetchall()
            claims.append({**dict(c), 'allocations': [dict(a) for a in allocs]})

        caretaker = conn.execute("""
            SELECT name, phone FROM caretakers
            WHERE property_id = ? AND phone IS NOT NULL AND TRIM(phone) != ''
            ORDER BY created_at LIMIT 1
        """, (prop['id'],)).fetchone()

    success = request.args.get('success')
    error = request.args.get('error')
    notice = request.args.get('notice')
    from src.payments import daraja
    how = None
    if prop.get('pay_paybill') and prop.get('pay_account'):
        how = {'paybill': prop['pay_paybill'],
               'account': prop['pay_account'].replace('{unit}', unit['unit_number']),
               'name': prop.get('pay_account_name') or ''}

    return render_template(
        'tenant/pay.html',
        how=how,
        stk_enabled=daraja.configured() or __import__('src.utils.runtime', fromlist=['is_demo']).is_demo(),
        token=token,
        tenant=tenant,
        unit=unit,
        property=prop,
        active_tab='pay',
        balance=balance,
        owed=owed,
        chosen=chosen,
        claims=claims,
        caretaker=caretaker,
        success=success,
        error=error,
        notice=notice,
        held_text=claim_owner.TENANT_TEXT,
    )


def _chosen_bill(conn, tenant, prop, charge_id):
    """One of this tenant's own unpaid bills ("Pay this bill"), or None."""
    if not charge_id:
        return None
    _, rows, _ = get_tenant_statement(conn, tenant['id'], prop['id'])
    balance = rows[-1]['running_balance'] if rows else 0.0
    return next((b for b in unpaid_bills(rows, balance)['bills'] if b['charge_id'] == charge_id), None)


@tenant_bp.route('/<token>/report-payment', methods=['POST'])
def report_payment(token):
    """Tenant self-reports an M-Pesa payment by pasting their confirmation SMS."""
    from src.parsers.sms_parser import parse_mpesa_message
    from src.database.db import allocate_payment

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx

        message = (request.form.get('mpesa_sms') or '').strip()
        if not message:
            return redirect(url_for('tenant.pay', token=token, error='Please paste your M-Pesa SMS.'))

        parsed = parse_mpesa_message(message)
        if not parsed.get('success'):
            return redirect(url_for('tenant.pay', token=token,
                                    error='Could not read an M-Pesa reference from that message. Please paste the full SMS.'))

        reference = parsed['reference']
        try:
            amount = float(parsed['amount']) if parsed.get('amount') is not None else None
        except (TypeError, ValueError):
            amount = None

        # Already received through the property's own paybill: confirmed if it is this
        # tenant's; on someone else's account it is held for management (claim_owner).
        from src.payments.mpesa import paid_through_mpesa
        _held = None
        _booked = paid_through_mpesa(conn, reference)
        if _booked:
            _owner = conn.execute("SELECT tenant_id FROM payments WHERE id = ?", (_booked['payment_id'],)).fetchone()
            if _owner and _owner['tenant_id'] == tenant['id']:
                return redirect(url_for('tenant.pay', token=token,
                                        success=f'Payment {reference} is already confirmed — see your statement.'))
            _held = claim_owner.ON_OTHER_ACCOUNT

        # Duplicate check
        existing = conn.execute(
            "SELECT id FROM payment_claims WHERE property_id = ? AND mpesa_ref = ?",
            (prop['id'], reference),
        ).fetchone()
        if existing:
            return redirect(url_for('tenant.pay', token=token,
                                    error=f'Reference {reference} has already been reported.'))

        _mpesa_ts = parsed.get('timestamp')
        _has_real_ts = not parsed.get('parse_warnings') or not any(
            'Timestamp not found' in w for w in parsed.get('parse_warnings', [])
        )
        mpesa_date = _mpesa_ts.date().isoformat() if (_mpesa_ts and _has_real_ts) else None
        mpesa_period = _mpesa_ts.strftime('%Y-%m') if (_mpesa_ts and _has_real_ts) else None

        claim_id = generate_id('CLM')
        _chosen = _chosen_bill(conn, tenant, prop, request.form.get('charge_id'))
        conn.execute("""
            INSERT INTO payment_claims
            (id, property_id, mpesa_ref, unit_id, tenant_id, claimed_amount, raw_message, source, mpesa_date, mpesa_period,
             target_charge_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'tenant', ?, ?, ?)
        """, (claim_id, prop['id'], reference, unit['id'], tenant['id'], amount, message, mpesa_date, mpesa_period,
              _chosen['charge_id'] if _chosen else None))

        amt_str = f'{amount:,.0f}' if amount is not None else '-'
        unit_number = unit['unit_number']
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('claim_created', 'claim', claim_id,
             f'Ref: {reference} | Unit: {unit_number} | Amount: KES {amt_str} | source: tenant', 'tenant'),
        )

        # At-submission bank check — same logic as caretaker flow
        _org_id = prop['organization_id'] if prop.get('organization_id') else None
        property_id = prop['id']
        _claim = {'id': claim_id, 'mpesa_ref': reference, 'property_id': property_id}

        if mpesa_period:
            _mp_y, _mp_m = int(mpesa_period[:4]), int(mpesa_period[5:7])
            _mp_start = f"{mpesa_period}-01"
            _mp_end = f"{_mp_y + 1}-01-01" if _mp_m == 12 else f"{_mp_y}-{_mp_m + 1:02d}-01"
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
        else:
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

        _notify = None  # ('verified'|'flagged', sms_body) — sent after commit

        if _held:
            claim_owner.hold(conn, _claim, _held, org_id=_org_id)
        elif _has_bank_data:
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
                # Book it to this tenant only if it is recognisably theirs (claim_owner).
                _existing_pay = conn.execute(
                    "SELECT id, claim_id, tenant_id FROM payments WHERE bank_txn_id = ?",
                    (_bank_txn['id'],)
                ).fetchone()
                if _existing_pay and _existing_pay['tenant_id'] != tenant['id']:
                    _held = claim_owner.ON_OTHER_ACCOUNT
                elif not _existing_pay and not claim_owner.sender_is_tenant(conn, _bank_txn, tenant):
                    _held = claim_owner.SENDER_NOT_TENANT
            if _bank_txn and _held:
                claim_owner.hold(conn, _claim, _held, org_id=_org_id)
            elif _bank_txn:
                if _existing_pay:
                    if not _existing_pay['claim_id']:
                        conn.execute("UPDATE payments SET claim_id = ? WHERE id = ?",
                                     (claim_id, _existing_pay['id']))
                    conn.execute(
                        "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (claim_id,)
                    )
                    _pay_id_for_sms = _existing_pay['id']
                else:
                    payment_id = generate_id('PAY')
                    conn.execute("""
                        INSERT INTO payments
                        (id, property_id, unit_id, claim_id, bank_txn_id, statement_id, amount, payment_date, assignment_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto')
                    """, (payment_id, property_id, unit['id'], claim_id,
                          _bank_txn['id'], _bank_txn['statement_id'],
                          _bank_txn['amount'], _bank_txn['txn_date'] or ''))
                    allocate_payment(conn, payment_id, unit['id'], _bank_txn['amount'])
                    conn.execute(
                        "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (claim_id,)
                    )
                    conn.execute(
                        "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                        ('payment_verified', 'payment', payment_id,
                         f'Ref: {reference} | Unit: {unit_number} | KES {amt_str} | auto-verified at tenant submission',
                         'system'),
                    )
                    _pay_id_for_sms = payment_id
                if _bank_txn['ignored']:
                    conn.execute(
                        "UPDATE bank_transactions SET ignored=0, ignored_at=NULL, ignored_reason=NULL WHERE id=?",
                        (_bank_txn['id'],)
                    )

                from src.messaging.tenant_payment import receipt_sms
                _receipt = receipt_sms(conn, _pay_id_for_sms,
                                       os.environ.get('APP_BASE_URL') or request.host_url)
                _notify = ('verified', _receipt[1]) if _receipt else ('verified', None)
            else:
                # Ref absent — flag claim and flag tenant
                conn.execute("""
                    UPDATE payment_claims
                    SET status = 'flagged', flag_reason = 'ref_not_found', flagged_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (claim_id,))
                conn.execute("UPDATE tenants SET flagged = 1 WHERE id = ?", (tenant['id'],))
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('claim_flagged', 'claim', claim_id,
                     f'Ref: {reference} | Unit: {unit_number} | ref not in bank data | source: tenant', 'system'),
                )
                _notify = ('flagged',
                    f"Hi {tenant['name']}, we could not verify payment ref {reference} in bank records. "
                    "Please contact your caretaker to resolve this.")

    # Send notifications outside the DB transaction
    if _notify and _notify[1] and tenant.get('phone'):
        try:
            from src.messaging.delivery import send_sms_async
            send_sms_async([{'phone': tenant['phone']}], _notify[1])
        except Exception:
            pass

    if _notify and _notify[0] == 'verified':
        return redirect(url_for('tenant.pay', token=token,
                                success=f'Payment {reference} confirmed and allocated to your charges.'))
    if _held:
        return redirect(url_for('tenant.pay', token=token,
                                notice=f'Code {reference} received. {claim_owner.TENANT_TEXT[_held]}'))
    return redirect(url_for('tenant.pay', token=token,
                            success=f'Payment reference {reference} recorded. We will verify it against bank records within 1–5 days.'))


@tenant_bp.route('/<token>/pay/pin/create', methods=['POST'])
def pay_pin_create(token):
    """Create the 4-digit payment PIN."""
    pin = (request.form.get('pin') or '').strip()
    pin_confirm = (request.form.get('pin_confirm') or '').strip()

    if not pin.isdigit() or len(pin) != 4:
        return redirect(url_for('tenant.pay', token=token, error='PIN must be exactly 4 digits'))
    if pin != pin_confirm:
        return redirect(url_for('tenant.pay', token=token, error='PINs do not match'))

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        existing = conn.execute(
            "SELECT portal_pin_hash FROM tenants WHERE id = ?", (tenant['id'],)
        ).fetchone()
        if existing and existing['portal_pin_hash']:
            return redirect(url_for('tenant.pay', token=token, error='PIN already set'))
        pin_hash = generate_password_hash(pin)
        conn.execute(
            "UPDATE tenants SET portal_pin_hash = ? WHERE id = ?",
            (pin_hash, tenant['id']),
        )

    session[f'pay_auth_{token}'] = True
    return redirect(url_for('tenant.pay', token=token))


@tenant_bp.route('/<token>/pay/pin/verify', methods=['POST'])
def pay_pin_verify(token):
    """Verify the 4-digit payment PIN."""
    pin = (request.form.get('pin') or '').strip()

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        row = conn.execute(
            "SELECT portal_pin_hash FROM tenants WHERE id = ?", (tenant['id'],)
        ).fetchone()
        pin_hash = row['portal_pin_hash'] if row else None

    if not pin_hash or not check_password_hash(pin_hash, pin):
        return redirect(url_for('tenant.pay', token=token, error='Incorrect PIN'))

    session[f'pay_auth_{token}'] = True
    return redirect(url_for('tenant.pay', token=token))


@tenant_bp.route('/<token>/pay/stk', methods=['POST'])
def pay_stk(token):
    """Initiate M-Pesa STK Push. Returns JSON. Requires PIN verified in session."""
    if not session.get(f'pay_auth_{token}'):
        return jsonify({'success': False, 'error': 'PIN verification required'}), 403

    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return jsonify({'success': False, 'error': 'Invalid token'}), 400
        tenant, unit, prop = ctx

    phone = (request.form.get('phone') or tenant.get('phone') or '').strip()
    try:
        amount = int(float(request.form.get('amount') or 0))
    except (ValueError, TypeError):
        amount = 0

    if amount <= 0:
        return jsonify({'success': False, 'error': 'Enter a valid amount'})
    if amount > 250000:
        return jsonify({'success': False, 'error': 'M-Pesa allows up to KES 250,000 per payment'})
    if not phone or not normalize_to_daraja(phone):
        return jsonify({'success': False, 'error': 'Enter a Safaricom number, e.g. 0712 345 678'})
    with get_connection() as conn:
        recent = conn.execute("""
            SELECT 1 FROM payment_transactions
            WHERE tenant_id = ? AND source = 'daraja' AND processing_status = 'pending'
              AND received_at > datetime('now', '-60 seconds')
        """, (tenant['id'],)).fetchone()
    if recent:
        return jsonify({'success': False, 'error': 'A payment prompt was just sent. Check your phone, or try again in a minute.'})
    with get_connection() as conn:
        chosen = _chosen_bill(conn, tenant, prop, request.form.get('charge_id'))
    target = chosen['charge_id'] if chosen else None

    description = f"Rent — Unit {unit['unit_number']}"
    from src.utils.runtime import is_demo
    if is_demo():
        return _demo_stk(tenant, unit, prop, phone, amount, target)
    result = stk_push(phone, amount, unit['unit_number'], description)

    if result['success']:
        checkout_request_id = result['checkout_request_id']
        from src.payments import mpesa
        with get_connection() as conn:
            if not mpesa.link_stk_request(conn, checkout_request_id, prop['organization_id'],
                                          prop['id'], unit['id'], tenant['id'], phone, amount, target):
                return jsonify({'success': False, 'error': 'Payment request could not be recorded'}), 409
        return jsonify({'success': True, 'checkout_request_id': checkout_request_id})

    return jsonify({'success': False, 'error': result.get('error', 'Payment initiation failed')})


def _demo_stk(tenant, unit, prop, phone, amount, target=None):
    """Demo site only: what Safaricom would do, done here. The request is recorded and
    'paid' through the same booking path as a real M-Pesa payment, so the receipt,
    allocations and balance are the real thing — only the money is pretend."""
    import secrets as _secrets
    from datetime import datetime
    from src.payments import mpesa
    checkout_id = 'DEMO-' + _secrets.token_hex(8)
    receipt = 'TK' + ''.join(_secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
    with get_connection() as conn:
        from src.demo.phones import claim_for_tenant
        claim_for_tenant(conn, tenant['id'], phone)          # the receipt goes to the phone typed here
        mpesa.link_stk_request(conn, checkout_id, prop['organization_id'], prop['id'], unit['id'],
                               tenant['id'], phone, amount, target)
        row = conn.execute("SELECT id FROM payment_transactions WHERE external_reference = ?", (checkout_id,)).fetchone()
        conn.execute("""UPDATE payment_transactions SET callback_amount = ?, mpesa_ref = ?, paid_at = ?, raw_callback = ?
                        WHERE id = ?""", (amount, receipt, datetime.now().isoformat(timespec='seconds'),
                                          '{"demo": "simulated M-Pesa result"}', row['id']))
    receipt = mpesa.book(row['id'], os.environ.get('APP_BASE_URL') or request.host_url)
    if receipt and receipt.get('phone'):
        send_sms_async([{'phone': receipt['phone']}], receipt['body'])   # the real receipt SMS
    return jsonify({'success': True, 'checkout_request_id': checkout_id})


@tenant_bp.route('/<token>/pay/status/<checkout_request_id>')
def pay_status(token, checkout_request_id):
    """Poll payment status. Returns JSON {status, message}."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return jsonify({'status': 'error', 'message': 'Invalid token'}), 400

        tenant = ctx[0]
        row = conn.execute(
            """
            SELECT processing_status, error_detail, amount, payment_id, mpesa_ref
            FROM payment_transactions
            WHERE external_reference = ? AND tenant_id = ?
            """,
            (checkout_request_id, tenant['id']),
        ).fetchone()

    if not row:
        return jsonify({'status': 'not_found', 'message': 'Payment not found'})

    status = row['processing_status']
    if status in ('completed', 'duplicate'):
        amount_fmt = f"{float(row['amount']):,.0f}"
        return jsonify({
            'status': 'completed',
            'message': f"KES {amount_fmt} payment confirmed" + (f" (M-Pesa {row['mpesa_ref']})." if row['mpesa_ref'] else "."),
            'receipt_url': url_for('tenant.receipt', token=token, payment_id=row['payment_id']) if row['payment_id'] else None,
        })
    if status == 'failed':
        return jsonify({
            'status': 'failed',
            'message': row['error_detail'] or 'Payment was not completed',
        })
    return jsonify({'status': 'pending', 'message': 'Waiting for confirmation...'})


@tenant_bp.route('/<token>/maintenance/new', methods=['POST'])
def new_maintenance(token):
    """Older repair form — now opens a repair conversation, so every repair has a thread."""
    title = (request.form.get('title') or '').strip()
    if not title:
        return redirect(url_for('tenant.message_new', token=token, topic='repair'))
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        conv_id, _, _ = threads.start_conversation(
            conn, tenant, unit, prop, 'repair', title[:120],
            (request.form.get('description') or '').strip() or title,
            category=(request.form.get('category') or 'general').strip() or 'general')
        alerts = staff_alerts.due(conn, conv_id, os.environ.get('APP_BASE_URL') or request.host_url)
    staff_alerts.send(alerts)          # after commit
    return redirect(url_for('tenant.conversation', token=token, conv_id=conv_id))


@tenant_bp.route('/<token>/dispute', methods=['POST'])
def raise_dispute(token):
    """Tenant flags a discrepancy — goes directly to platform, not the agency."""
    with get_connection() as conn:
        ctx = _get_tenant_by_token(conn, token)
        if not ctx:
            return render_template('tenant/invalid_token.html')
        tenant, unit, prop = ctx
        subject = request.form.get('subject', '').strip() or 'Dispute'
        message = request.form.get('message', '').strip()
        if not message:
            from flask import flash
            flash('Please describe the issue.', 'error')
            return redirect(url_for('tenant.portal', token=token))
        org = conn.execute(
            "SELECT organization_id FROM properties WHERE id = ?", (prop['id'],)
        ).fetchone()
        dispute_id = generate_id('DISP')
        conn.execute(
            """INSERT INTO tenant_disputes
                   (id, tenant_id, property_id, org_id, subject, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (dispute_id, tenant['id'], prop['id'],
             org['organization_id'] if org else None, subject, message),
        )
    from flask import flash
    flash('Your concern has been recorded and will be reviewed by Domi.', 'success')
    return redirect(url_for('tenant.portal', token=token))

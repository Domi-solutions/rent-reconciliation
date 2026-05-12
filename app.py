"""
Rent Reconciliation - Flask Web Application (Phase 3).
Uses existing parsers (pdf_parser, sms_parser); state in SQLite.
"""

import os
import re
import secrets
import sqlite3
import io
import atexit
from datetime import datetime
from decimal import Decimal
from apscheduler.schedulers.background import BackgroundScheduler

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

from src.database.db import get_connection, init_database, generate_id, allocate_payment
from src.parsers.pdf_parser import parse_bank_statement
from src.parsers.sms_parser import parse_mpesa_message
from src.parsers.excel_parser import parse_tenant_excel
from src.parsers.water_parser import parse_water_excel
from src.routes.test_routes import test_bp
from src.routes.viewer_routes import viewer_bp
from src.routes.report_routes import report_bp
from src.routes.caretaker_routes import caretaker_bp
from src.routes.agent_routes import agent_bp
from src.routes.payment_routes import payment_bp
from src.routes.inbound_routes import inbound_bp
from src.agent.coordinator import (
    daily_snapshot_job,
    morning_briefings_job,
    weekly_digest_job,
    anomaly_check_job,
    monthly_checkins_job,
    process_payment_queue,
)
from src.payments.disbursements import scheduled_disbursement_job

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
if os.environ.get('ADMIN_PASSWORD') and app.secret_key == 'dev-secret-change-in-production':
    import warnings
    warnings.warn(
        "SECRET_KEY is using the default dev value while ADMIN_PASSWORD is set. Set SECRET_KEY env var for production security.",
        stacklevel=1,
    )
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'data/statements')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_database()
from src.database.db import (
    migrate_add_charge_type,
    migrate_add_apartment_size,
    migrate_add_payment_allocations,
    migrate_allocate_existing_payments,
    migrate_add_status_changed_at,
    migrate_add_landlord_reports,
    migrate_add_tenant_access_token,
    migrate_add_messaging,
    migrate_set_rent_charge_due_dates,
    migrate_update_template_wording,
    migrate_add_owners,
    migrate_add_property_owners,
    migrate_add_rent_due_day,
    migrate_add_reminder_schedules,
    migrate_add_unit_hint,
    migrate_add_maintenance,
    migrate_add_sms_delivery,
    migrate_add_template_body,
    migrate_add_owner_messages,
    migrate_add_caretakers,
    migrate_add_balance_snapshots,
    migrate_add_inbound_messages,
    migrate_add_inbound_sessions,
    migrate_add_checkin_responses,
    migrate_add_payment_transactions,
    migrate_add_language_preference,
    migrate_add_organizations,
    migrate_add_persons,
    migrate_add_platform_errors,
)
migrate_add_charge_type()
migrate_add_apartment_size()
migrate_add_payment_allocations()
migrate_allocate_existing_payments()
migrate_add_status_changed_at()
migrate_add_landlord_reports()
migrate_add_tenant_access_token()
migrate_add_messaging()
migrate_set_rent_charge_due_dates()
migrate_update_template_wording()
migrate_add_owners()
migrate_add_property_owners()
migrate_add_rent_due_day()
migrate_add_reminder_schedules()
migrate_add_unit_hint()
migrate_add_maintenance()
migrate_add_sms_delivery()
migrate_add_template_body()
migrate_add_owner_messages()
migrate_add_caretakers()
migrate_add_balance_snapshots()
migrate_add_inbound_messages()
migrate_add_inbound_sessions()
migrate_add_checkin_responses()
migrate_add_payment_transactions()
migrate_add_language_preference()
migrate_add_organizations()
migrate_add_persons()
migrate_add_platform_errors()

from src.routes.tenant_routes import tenant_bp
from src.routes.messaging_routes import messaging_bp
from src.messaging.reminders import generate_due_reminders

app.register_blueprint(test_bp)
app.register_blueprint(viewer_bp)
app.register_blueprint(report_bp)
app.register_blueprint(tenant_bp)
app.register_blueprint(messaging_bp)
app.register_blueprint(caretaker_bp)
app.register_blueprint(agent_bp)
app.register_blueprint(payment_bp)
app.register_blueprint(inbound_bp)


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=daily_snapshot_job, trigger='cron', hour=1, minute=0, id='daily_snapshot_job', replace_existing=True)
scheduler.add_job(func=morning_briefings_job, trigger='cron', hour=7, minute=0, id='morning_briefings_job', replace_existing=True)
scheduler.add_job(func=weekly_digest_job, trigger='cron', day_of_week='mon', hour=8, minute=0, id='weekly_digest_job', replace_existing=True)
scheduler.add_job(func=anomaly_check_job, trigger='cron', hour=6, minute=0, id='anomaly_check_job', replace_existing=True)
scheduler.add_job(func=monthly_checkins_job, trigger='cron', day=1, hour=9, minute=0, id='monthly_checkins_job', replace_existing=True)
scheduler.add_job(func=process_payment_queue, trigger='interval', seconds=60, id='process_payment_queue', replace_existing=True)
scheduler.add_job(func=scheduled_disbursement_job, trigger='cron', day=10, hour=9, minute=0, id='disbursement_job', replace_existing=True)

_running_via_flask_cli = os.environ.get("FLASK_RUN_FROM_CLI") == "true"
_is_werkzeug_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
_should_start_scheduler = (not _running_via_flask_cli) or _is_werkzeug_child

if _should_start_scheduler and not scheduler.running:
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

# #endregion


@app.before_request
def require_admin_auth():
    """Require admin password for all admin routes. Bypass if ADMIN_PASSWORD unset (dev mode)."""
    if request.endpoint is None:
        return None
    exempt = ('admin_login', 'admin_logout', 'static')
    if request.endpoint in exempt:
        return None
    if request.path.startswith('/view'):
        return None
    if request.path.startswith('/tenant'):
        return None
    if request.path.startswith('/caretaker'):
        return None
    if request.path.startswith('/inbound'):
        return None
    if not os.environ.get('ADMIN_PASSWORD'):
        return None
    if session.get('admin_authenticated'):
        return None
    return redirect(url_for('admin_login', next=request.url))


def _parse_txn_date_to_iso(date_str):
    """Convert parser date like 30-DEC-2025 to YYYY-MM-DD or None."""
    if not date_str or not date_str.strip():
        return None
    try:
        # Try DD-MMM-YYYY
        m = re.match(r'(\d{2})-([A-Z]{3})-(\d{4})', date_str.strip().upper())
        if m:
            day, mon_str, year = m.groups()
            months = 'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split()
            if mon_str in months:
                month = months.index(mon_str) + 1
                return f"{year}-{month:02d}-{day}"
    except Exception:
        pass
    return None


def _audit_display_details(conn, row):
    """Build display string for an audit row; use stored details if already rich, else look up from related tables (for old entries)."""
    details = row['details'] if row['details'] else ''
    # Consider details "rich" if it has our pipe format and at least one label
    if details and ' | ' in details and any(x in details for x in ('Unit:', 'Amount:', 'Ref:', 'File:', 'Tenant:')):
        return details
    action = row['action']
    entity_id = row['entity_id']
    if not entity_id:
        return details
    try:
        if action == 'payment_manual_assigned':
            p = conn.execute(
                "SELECT p.amount, p.unit_id, p.assignment_reason, bt.mpesa_ref FROM payments p "
                "JOIN bank_transactions bt ON p.bank_txn_id = bt.id WHERE p.id = ?", (entity_id,)
            ).fetchone()
            if not p:
                return details
            u = conn.execute("SELECT unit_number FROM units WHERE id = ?", (p['unit_id'],)).fetchone()
            unit_number = u['unit_number'] if u else p['unit_id']
            amt = float(p['amount']) if p['amount'] is not None else 0
            ref = p['mpesa_ref'] or '-'
            reason = p['assignment_reason'] or '-'
            return f'Ref: {ref} | Amount: KES {amt:.2f} | Unit: {unit_number} | Reason: {reason}'
        if action == 'tenant_created':
            t = conn.execute("SELECT name, unit_id FROM tenants WHERE id = ?", (entity_id,)).fetchone()
            if not t:
                return details
            u = conn.execute("SELECT unit_number FROM units WHERE id = ?", (t['unit_id'],)).fetchone()
            unit_number = u['unit_number'] if u else t['unit_id']
            return f'Tenant: {t["name"]} | Unit: {unit_number}'
        if action == 'payment_verified':
            p = conn.execute(
                "SELECT p.amount, p.unit_id, p.bank_txn_id FROM payments p WHERE p.id = ?", (entity_id,)
            ).fetchone()
            if not p:
                return details
            bt = conn.execute("SELECT mpesa_ref FROM bank_transactions WHERE id = ?", (p['bank_txn_id'],)).fetchone()
            ref = bt['mpesa_ref'] if bt and bt['mpesa_ref'] else '-'
            u = conn.execute("SELECT unit_number FROM units WHERE id = ?", (p['unit_id'],)).fetchone()
            unit_number = u['unit_number'] if u else p['unit_id']
            t = conn.execute(
                "SELECT name FROM tenants WHERE unit_id = ? AND status = 'active'", (p['unit_id'],)
            ).fetchone()
            tenant_name = t['name'] if t and t['name'] else '-'
            amt = float(p['amount']) if p['amount'] is not None else 0
            return f'Ref: {ref} | Amount: KES {amt:.2f} | Unit: {unit_number} | Tenant: {tenant_name}'
        if action == 'statement_uploaded':
            s = conn.execute(
                "SELECT filename, rent_transactions FROM bank_statements WHERE id = ?", (entity_id,)
            ).fetchone()
            if not s:
                return details
            n = conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE statement_id = ?", (entity_id,)).fetchone()[0]
            return f'File: {s["filename"]} | {n} transactions | {s["rent_transactions"] or 0} rent'
        if action == 'claim_created':
            c = conn.execute(
                "SELECT mpesa_ref, unit_id, claimed_amount FROM payment_claims WHERE id = ?", (entity_id,)
            ).fetchone()
            if not c:
                return details
            u = conn.execute("SELECT unit_number FROM units WHERE id = ?", (c['unit_id'],)).fetchone()
            unit_number = u['unit_number'] if u else c['unit_id']
            amt = c['claimed_amount']
            amt_str = f'{float(amt):.2f}' if amt is not None else '-'
            return f'Ref: {c["mpesa_ref"]} | Unit: {unit_number} | Amount: {amt_str}'
    except Exception:
        pass
    return details


def get_current_property(conn):
    """Get the currently selected property from session. Returns row or None."""
    pid = session.get('property_id')
    if pid:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)
        ).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


@app.context_processor
def inject_property_context():
    """Make current property and counts available to all admin templates."""
    try:
        with get_connection() as conn:
            prop = get_current_property(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM properties WHERE status = 'active'"
            ).fetchone()[0]

            unit_count = None
            if prop:
                unit_count = conn.execute(
                    "SELECT COUNT(*) FROM units WHERE property_id = ?", (prop["id"],)
                ).fetchone()[0]

            return {
                "current_property": prop,
                "property_count": count,
                "unit_count": unit_count,
            }
    except Exception:
        return {"current_property": None, "property_count": 0, "unit_count": None}


@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin password login. Shown when ADMIN_PASSWORD is set."""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == os.environ.get('ADMIN_PASSWORD'):
            session['admin_authenticated'] = True
            next_url = request.form.get('next') or request.args.get('next') or url_for('dashboard')
            return redirect(next_url)
        return render_template('login.html', error='Invalid password.')
    return render_template('login.html')


@app.route('/logout')
def admin_logout():
    """Clear admin session and redirect to login."""
    session.pop('admin_authenticated', None)
    return redirect(url_for('admin_login'))


@app.route('/properties')
def property_list():
    """List all properties for admin to select."""
    with get_connection() as conn:
        properties = conn.execute(
            "SELECT * FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()
    if len(properties) == 1:
        session['property_id'] = properties[0]['id']
        return redirect(url_for('dashboard'))
    return render_template('property_list.html', properties=properties)


@app.route('/properties/select/<property_id>')
def select_property(property_id):
    """Set the active property in session and redirect to dashboard."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            flash('Property not found.', 'error')
            return redirect(url_for('property_list'))
    session['property_id'] = property_id
    flash(f'Switched to {prop["name"]}', 'info')
    return redirect(url_for('dashboard'))


@app.route('/properties/delete/<property_id>', methods=['POST'])
def delete_property(property_id):
    """Soft-delete a property (set status=inactive)."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            flash('Property not found.', 'error')
            return redirect(url_for('property_list'))

        conn.execute("UPDATE properties SET status = 'inactive' WHERE id = ?", (property_id,))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('property_deleted', 'property', property_id, f'Deactivated: {prop["name"]}', 'admin'),
        )

        if session.get('property_id') == property_id:
            session.pop('property_id', None)

    flash(f'Property "{prop["name"]}" has been deactivated.', 'success')
    return redirect(url_for('property_list'))


@app.route('/')
def dashboard():
    """Main dashboard: current property, stats, arrears, pending claims, unassigned txns, recent audit."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            any_prop = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
            if any_prop == 0:
                return redirect(url_for('setup_property'))
            return redirect(url_for('property_list'))

        property_id = property_row['id']

        generate_due_reminders(conn, property_id)

        # Arrears list first so we can derive units_in_arrears and total_arrears from it
        arrears = conn.execute("""
            SELECT * FROM unit_balances
            WHERE property_id = ? AND balance > 0
            ORDER BY balance DESC
        """, (property_id,)).fetchall()

        _today = datetime.today()
        _month_start = _today.replace(day=1).strftime('%Y-%m-%d')   # e.g. 2026-03-01
        _month_period = _today.strftime('%Y-%m')                     # e.g. 2026-03

        stats = {
            'total_units': conn.execute(
                "SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)
            ).fetchone()[0],
            'occupied_units': conn.execute(
                "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'occupied'", (property_id,)
            ).fetchone()[0],
            'pending_claims': conn.execute(
                "SELECT COUNT(*) FROM payment_claims WHERE property_id = ? AND status = 'pending'", (property_id,)
            ).fetchone()[0],
            'total_collected': conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ? AND payment_date >= ?",
                (property_id, _month_start)
            ).fetchone()[0],
            'total_charged': conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM rent_charges WHERE property_id = ? AND period = ?",
                (property_id, _month_period)
            ).fetchone()[0],
        }

        # Derived from existing arrears list (no extra query)
        stats['units_in_arrears'] = len(arrears)
        stats['total_arrears'] = sum(float(row['balance']) for row in arrears)

        office_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'office'", (property_id,)
        ).fetchone()[0]
        vacant_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'vacant'", (property_id,)
        ).fetchone()[0]
        income_row = conn.execute(
            "SELECT COALESCE(SUM(monthly_rent), 0) as rent, COALESCE(SUM(service_charge), 0) as svc FROM units WHERE property_id = ? AND status = 'occupied'",
            (property_id,),
        ).fetchone()
        expected_monthly = float(income_row['rent']) + float(income_row['svc'])

        stats['office_units'] = office_units
        stats['vacant_units'] = vacant_units
        rentable = stats['total_units'] - office_units
        stats['occupancy_rate'] = round(stats['occupied_units'] / rentable * 100, 1) if rentable > 0 else 0
        stats['expected_monthly'] = expected_monthly
        # Collection rate: this month's verified vs expected monthly income (per CLAUDE.md)
        stats['collection_rate'] = (
            round(float(stats['total_collected']) / expected_monthly * 100, 1)
            if expected_monthly > 0 else 0
        )
        # Arrears health: <= 0.5 = Healthy, <= 1.0 = Watch, > 1.0 = Critical
        stats['arrears_ratio'] = round(stats['total_arrears'] / expected_monthly, 1) if expected_monthly > 0 else 0

        pending = conn.execute("""
            SELECT pc.*, u.unit_number
            FROM payment_claims pc
            JOIN units u ON pc.unit_id = u.id
            WHERE pc.property_id = ? AND pc.status = 'pending'
            ORDER BY pc.created_at DESC
        """, (property_id,)).fetchall()

        unassigned = conn.execute("""
            SELECT bt.*
            FROM bank_transactions bt
            JOIN bank_statements bs ON bt.statement_id = bs.id
            WHERE bs.property_id = ?
            AND bt.txn_type = 'PAYBILL_CREDIT'
            AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
            ORDER BY bt.txn_date DESC
        """, (property_id,)).fetchall()

        unassigned_stats = conn.execute("""
            SELECT
                COUNT(*) as total_count,
                COALESCE(SUM(bt.amount), 0) as total_amount,
                COUNT(CASE WHEN bt.unit_hint IS NOT NULL AND bt.unit_hint != '' THEN 1 END) as hint_count,
                COALESCE(SUM(CASE WHEN bt.unit_hint IS NOT NULL AND bt.unit_hint != '' THEN bt.amount ELSE 0 END), 0) as hint_amount
            FROM bank_transactions bt
            JOIN bank_statements bs ON bt.statement_id = bs.id
            WHERE bs.property_id = ?
            AND bt.txn_type = 'PAYBILL_CREDIT'
            AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
        """, (property_id,)).fetchone()

        recent = conn.execute("""
            SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 10
        """).fetchall()

        current_month = datetime.now().strftime('%Y-%m')
        charges_this_month = conn.execute(
            "SELECT COUNT(*) FROM rent_charges WHERE property_id = ? AND period = ? AND charge_type = 'rent'",
            (property_id, current_month),
        ).fetchone()[0]
        show_generate_charges_hint = charges_this_month == 0

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

        return render_template(
            'dashboard.html',
            property=property_row,
            stats=stats,
            arrears=arrears,
            pending=pending,
            unassigned=unassigned,
            unassigned_stats=unassigned_stats,
            recent=recent,
            show_generate_charges_hint=show_generate_charges_hint,
            current_month=current_month,
            next_due_date=next_due_date,
            days_to_due=days_to_due,
            recent_reminder=recent_reminder,
        )


@app.route('/setup', methods=['GET', 'POST'])
def setup_property():
    """Initial property setup."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        if not name:
            flash('Property name is required.', 'error')
            return redirect(url_for('setup_property'))

        property_id = generate_id('PROP')
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO properties (id, name, address) VALUES (?, ?, ?)",
                (property_id, name, address),
            )
            details = f'Name: {name}'
            if address:
                details += f' | Address: {address}'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('property_created', 'property', property_id, details, 'admin'),
            )
        session['property_id'] = property_id
        flash(f'Property "{name}" created successfully!', 'success')
        return redirect(url_for('manage_units'))

    return render_template('setup_property.html')


@app.route('/units')
def manage_units():
    """List units with tenant and balance. Optional filters: status=occupied, pending_claims=1."""
    status_filter = request.args.get('status', '').strip()
    pending_claims_filter = request.args.get('pending_claims', '').strip()

    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        property_id = property_row['id']
        conditions = ["u.property_id = ?"]
        params = [property_id]

        if status_filter == 'occupied':
            conditions.append("u.status = 'occupied'")
        if pending_claims_filter:
            conditions.append(
                "u.id IN (SELECT unit_id FROM payment_claims WHERE property_id = ? AND status = 'pending')"
            )
            params.append(property_id)

        where_clause = " AND ".join(conditions)
        units = conn.execute(f"""
            SELECT u.*, t.name AS tenant_name, t.phone AS tenant_phone, ub.balance
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN unit_balances ub ON ub.unit_id = u.id
            WHERE {where_clause}
            ORDER BY u.unit_number
        """, tuple(params)).fetchall()

        return render_template(
            'units.html',
            property=property_row,
            units=units,
            filter_status=status_filter or None,
            filter_pending_claims=bool(pending_claims_filter),
        )


@app.route('/units/<unit_id>/set-status', methods=['POST'])
def set_unit_status(unit_id):
    """Change a unit's status between occupied, vacant, and office."""
    new_status = request.form.get('status', '').strip()
    if new_status not in ('occupied', 'vacant', 'office'):
        flash('Invalid status value.', 'error')
        return redirect(url_for('manage_units'))
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
        unit = conn.execute(
            "SELECT id, unit_number, status FROM units WHERE id = ? AND property_id = ?",
            (unit_id, property_row['id']),
        ).fetchone()
        if not unit:
            flash('Unit not found.', 'error')
            return redirect(url_for('manage_units'))
        old_status = unit['status']
        conn.execute(
            "UPDATE units SET status = ?, status_changed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, unit_id),
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('unit_status_changed', 'unit', unit_id,
             f"Unit: {unit['unit_number']} | {old_status} → {new_status}", 'admin'),
        )
    flash(f"Unit {unit['unit_number']} is now {new_status}.", 'success')
    return redirect(url_for('manage_units'))


@app.route('/units/add', methods=['GET', 'POST'])
def add_unit():
    """Add a new unit."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        if request.method == 'POST':
            unit_number = request.form.get('unit_number', '').strip()
            try:
                monthly_rent = float(request.form.get('monthly_rent', 0))
            except (TypeError, ValueError):
                monthly_rent = 0
            if not unit_number:
                flash('Unit number is required.', 'error')
                return redirect(url_for('add_unit'))
            if monthly_rent <= 0:
                flash('Monthly rent must be greater than 0.', 'error')
                return redirect(url_for('add_unit'))

            unit_id = f"{property_row['id']}-{unit_number}"
            try:
                conn.execute(
                    "INSERT INTO units (id, property_id, unit_number, monthly_rent, status_changed_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (unit_id, property_row['id'], unit_number, monthly_rent),
                )
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('unit_created', 'unit', unit_id, f'Unit {unit_number}, Rent: {monthly_rent}', 'admin'),
                )
            except sqlite3.IntegrityError:
                flash(f'Unit {unit_number} already exists for this property.', 'error')
                return redirect(url_for('add_unit'))
            flash(f'Unit {unit_number} added successfully!', 'success')
            return redirect(url_for('manage_units'))

        return render_template('add_unit.html', property=property_row)


@app.route('/tenants')
def manage_tenants():
    """List tenants with unit."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        show_inactive = request.args.get('show_inactive') == '1'
        status_filter = "IN ('active', 'inactive')" if show_inactive else "= 'active'"
        sort = request.args.get('sort', 'unit')
        only_arrears = request.args.get('arrears') == '1'

        arrears_join = "LEFT JOIN unit_balances ub ON ub.unit_id = t.unit_id"
        arrears_clause = "AND COALESCE(ub.balance, 0) > 0" if only_arrears else ""
        order_clause = "CAST(REPLACE(u.unit_number, ' ', '') AS TEXT)" if sort == 'unit' else "t.name"

        tenants = conn.execute(f"""
            SELECT t.*, u.unit_number, COALESCE(ub.balance, 0) as balance
            FROM tenants t
            LEFT JOIN units u ON t.unit_id = u.id
            {arrears_join}
            WHERE t.property_id = ? AND t.status {status_filter}
            {arrears_clause}
            ORDER BY {order_clause}
        """, (property_row['id'],)).fetchall()

        return render_template('tenants.html', property=property_row, tenants=tenants, show_inactive=show_inactive,
                               sort=sort, only_arrears=only_arrears)


@app.route('/tenants/add', methods=['GET', 'POST'])
def add_tenant():
    """Add tenant; only show units without an active tenant."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        units = conn.execute("""
            SELECT * FROM units
            WHERE property_id = ? AND status IN ('occupied', 'vacant')
            AND id NOT IN (SELECT unit_id FROM tenants WHERE status = 'active' AND unit_id IS NOT NULL)
            ORDER BY unit_number
        """, (property_row['id'],)).fetchall()

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            unit_id = request.form.get('unit_id', '').strip()
            if not name:
                flash('Tenant name is required.', 'error')
                return redirect(url_for('add_tenant'))
            if not unit_id:
                flash('Please select a unit.', 'error')
                return redirect(url_for('add_tenant'))

            tenant_id = generate_id('TENANT')
            conn.execute(
                "INSERT INTO tenants (id, property_id, unit_id, name, phone, move_in_date) VALUES (?, ?, ?, ?, ?, date('now'))",
                (tenant_id, property_row['id'], unit_id, name, phone),
            )
            conn.execute(
                "UPDATE units SET status = 'occupied', status_changed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (unit_id,),
            )
            unit_row = conn.execute("SELECT unit_number FROM units WHERE id = ?", (unit_id,)).fetchone()
            unit_number = unit_row['unit_number'] if unit_row else unit_id
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('tenant_created', 'tenant', tenant_id, f'Tenant: {name} | Unit: {unit_number}', 'admin'),
            )
            flash(f'Tenant {name} added successfully!', 'success')
            return redirect(url_for('manage_tenants'))

        return render_template('add_tenant.html', property=property_row, units=units)


@app.route('/tenants/<tenant_id>/generate-token', methods=['POST'])
def generate_tenant_token(tenant_id):
    """Generate a shareable portal access token for the tenant. Log to audit."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        tenant = conn.execute(
            "SELECT t.id, t.name, t.unit_id, u.unit_number FROM tenants t "
            "LEFT JOIN units u ON t.unit_id = u.id WHERE t.id = ? AND t.property_id = ?",
            (tenant_id, property_row['id']),
        ).fetchone()
        if not tenant:
            flash('Tenant not found.', 'error')
            return redirect(url_for('manage_tenants'))
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE tenants SET access_token = ? WHERE id = ?", (token, tenant_id))
        unit_number = tenant['unit_number'] or '-'
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('tenant_token_generated', 'tenant', tenant_id,
             f"Tenant: {tenant['name']} | Unit: {unit_number} | Portal link generated", 'admin'),
        )
    flash('Portal link generated. Copy the link from the tenant row.', 'success')
    return redirect(url_for('manage_tenants'))


@app.route('/tenants/<tenant_id>/revoke-token', methods=['POST'])
def revoke_tenant_token(tenant_id):
    """Revoke tenant portal access token. Log to audit."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        tenant = conn.execute(
            "SELECT t.id, t.name, t.unit_id, u.unit_number FROM tenants t "
            "LEFT JOIN units u ON t.unit_id = u.id WHERE t.id = ? AND t.property_id = ?",
            (tenant_id, property_row['id']),
        ).fetchone()
        if not tenant:
            flash('Tenant not found.', 'error')
            return redirect(url_for('manage_tenants'))
        conn.execute("UPDATE tenants SET access_token = NULL WHERE id = ?", (tenant_id,))
        unit_number = tenant['unit_number'] or '-'
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('tenant_token_revoked', 'tenant', tenant_id,
             f"Tenant: {tenant['name']} | Unit: {unit_number} | Portal link revoked", 'admin'),
        )
    flash('Portal link revoked.', 'success')
    return redirect(url_for('manage_tenants'))


@app.route('/tenants/<tenant_id>/move-out', methods=['POST'])
def move_out_tenant(tenant_id):
    """Mark tenant as moved out; set their unit to vacant."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))
        tenant = conn.execute(
            "SELECT t.id, t.name, t.unit_id, u.unit_number FROM tenants t "
            "LEFT JOIN units u ON t.unit_id = u.id WHERE t.id = ? AND t.property_id = ?",
            (tenant_id, property_row['id']),
        ).fetchone()
        if not tenant:
            flash('Tenant not found.', 'error')
            return redirect(url_for('manage_tenants'))
        conn.execute(
            "UPDATE tenants SET status = 'inactive', move_out_date = date('now'), access_token = NULL WHERE id = ?",
            (tenant_id,),
        )
        if tenant['unit_id']:
            conn.execute(
                "UPDATE units SET status = 'vacant', status_changed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (tenant['unit_id'],),
            )
        unit_number = tenant['unit_number'] or '-'
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('tenant_moved_out', 'tenant', tenant_id,
             f"Tenant: {tenant['name']} | Unit: {unit_number} | Moved out", 'admin'),
        )
    flash(f"{tenant['name']} moved out. Unit {unit_number} is now vacant.", 'success')
    return redirect(url_for('manage_tenants'))


# ============================================================
# OWNER MANAGEMENT
# ============================================================

@app.route('/owners')
def manage_owners():
    """List all property owners, their property count and access status."""
    with get_connection() as conn:
        owners = conn.execute("""
            SELECT o.id, o.name, o.phone, o.email, o.access_token,
                   o.password_hash IS NOT NULL as has_password,
                   o.created_at,
                   COUNT(p.id) as property_count
            FROM owners o
            LEFT JOIN property_owners po ON po.owner_id = o.id
            LEFT JOIN properties p ON p.id = po.property_id AND p.status = 'active'
            GROUP BY o.id
            ORDER BY o.name
        """).fetchall()
        owner_properties = {}
        for o in owners:
            props = conn.execute("""
                SELECT p.id, p.name FROM properties p
                JOIN property_owners po ON po.property_id = p.id
                WHERE po.owner_id = ? AND p.status = 'active'
            """, (o['id'],)).fetchall()
            owner_properties[o['id']] = props
        all_properties = conn.execute(
            "SELECT id, name FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()
    return render_template('owners.html', owners=owners, all_properties=all_properties, owner_properties=owner_properties)


@app.route('/owners/new', methods=['POST'])
def create_owner():
    """Create a new owner record."""
    from werkzeug.security import generate_password_hash
    import secrets as _secrets
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    if not name:
        flash('Name is required.', 'error')
        return redirect(url_for('manage_owners'))
    property_id = request.form.get('property_id', '').strip()
    owner_id = _secrets.token_hex(8)
    password_hash = generate_password_hash(password) if password else None
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO owners (id, name, phone, email, password_hash) VALUES (?, ?, ?, ?, ?)",
            (owner_id, name, phone or None, email or None, password_hash),
        )
        if property_id:
            jid = generate_id('POWN')
            conn.execute(
                "INSERT OR IGNORE INTO property_owners (id, property_id, owner_id) VALUES (?, ?, ?)",
                (jid, property_id, owner_id)
            )
            conn.execute("UPDATE properties SET owner_id = ? WHERE id = ? AND owner_id IS NULL", (owner_id, property_id))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('owner_created', 'owner', owner_id, f"Owner: {name}", 'admin'),
        )
    flash(f"Owner '{name}' created.", 'success')
    return redirect(url_for('manage_owners'))


@app.route('/owners/<owner_id>/generate-token', methods=['POST'])
def generate_owner_token(owner_id):
    """Generate (or regenerate) the owner's shareable portal link token."""
    import secrets as _secrets
    with get_connection() as conn:
        owner = conn.execute("SELECT name FROM owners WHERE id = ?", (owner_id,)).fetchone()
        if not owner:
            flash('Owner not found.', 'error')
            return redirect(url_for('manage_owners'))
        token = _secrets.token_urlsafe(32)
        conn.execute("UPDATE owners SET access_token = ? WHERE id = ?", (token, owner_id))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('owner_token_generated', 'owner', owner_id, f"Owner: {owner['name']} | New portal link", 'admin'),
        )
    flash(f"New access link generated for {owner['name']}.", 'success')
    return redirect(url_for('manage_owners'))


@app.route('/owners/<owner_id>/set-password', methods=['POST'])
def set_owner_password(owner_id):
    """Set or change an owner's portal password."""
    from werkzeug.security import generate_password_hash
    password = request.form.get('password', '').strip()
    if not password:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('manage_owners'))
    with get_connection() as conn:
        owner = conn.execute("SELECT name FROM owners WHERE id = ?", (owner_id,)).fetchone()
        if not owner:
            flash('Owner not found.', 'error')
            return redirect(url_for('manage_owners'))
        conn.execute(
            "UPDATE owners SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), owner_id),
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('owner_password_set', 'owner', owner_id, f"Owner: {owner['name']} | Password updated", 'admin'),
        )
    flash(f"Password updated for {owner['name']}.", 'success')
    return redirect(url_for('manage_owners'))


@app.route('/owners/<owner_id>/assign-property', methods=['POST'])
def assign_property_to_owner(owner_id):
    """Assign a property to an owner."""
    property_id = request.form.get('property_id', '').strip()
    with get_connection() as conn:
        owner = conn.execute("SELECT name FROM owners WHERE id = ?", (owner_id,)).fetchone()
        prop = conn.execute("SELECT name FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not owner or not prop:
            flash('Owner or property not found.', 'error')
            return redirect(url_for('manage_owners'))
        jid = generate_id('POWN')
        conn.execute(
            "INSERT OR IGNORE INTO property_owners (id, property_id, owner_id) VALUES (?, ?, ?)",
            (jid, property_id, owner_id)
        )
        conn.execute("UPDATE properties SET owner_id = ? WHERE id = ? AND owner_id IS NULL", (owner_id, property_id))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('property_assigned', 'owner', owner_id,
             f"Property '{prop['name']}' assigned to {owner['name']}", 'admin'),
        )
    flash(f"'{prop['name']}' assigned to {owner['name']}.", 'success')
    return redirect(url_for('manage_owners'))


@app.route('/owners/<owner_id>/delete', methods=['POST'])
def delete_owner(owner_id):
    """Delete an owner. Unassigns their properties first."""
    with get_connection() as conn:
        owner = conn.execute("SELECT name FROM owners WHERE id = ?", (owner_id,)).fetchone()
        if not owner:
            flash('Owner not found.', 'error')
            return redirect(url_for('manage_owners'))
        conn.execute("DELETE FROM property_owners WHERE owner_id = ?", (owner_id,))
        conn.execute("UPDATE properties SET owner_id = NULL WHERE owner_id = ?", (owner_id,))
        conn.execute("DELETE FROM owners WHERE id = ?", (owner_id,))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('owner_deleted', 'owner', owner_id, f"Owner: {owner['name']} | Deleted", 'admin'),
        )
    flash(f"Owner '{owner['name']}' deleted. Their properties are now unassigned.", 'success')
    return redirect(url_for('manage_owners'))


@app.route('/owners/<owner_id>/remove-property/<property_id>', methods=['POST'])
def remove_property_from_owner(owner_id, property_id):
    """Remove a property from an owner's assignments."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM property_owners WHERE owner_id = ? AND property_id = ?",
            (owner_id, property_id)
        )
        conn.execute(
            "UPDATE properties SET owner_id = NULL WHERE id = ? AND owner_id = ?",
            (property_id, owner_id)
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('property_unassigned', 'owner', owner_id,
             f"Property {property_id} removed from owner {owner_id}", 'admin'),
        )
    flash('Property removed from owner.', 'success')
    return redirect(url_for('manage_owners'))


@app.route('/caretakers')
def manage_caretakers():
    """List all caretakers across properties."""
    with get_connection() as conn:
        caretakers = conn.execute("""
            SELECT c.id, c.name, c.phone, c.property_id, c.password_hash,
                   p.name AS property_name
            FROM caretakers c
            JOIN properties p ON c.property_id = p.id
            ORDER BY p.name, c.name
        """).fetchall()
        all_properties = conn.execute(
            "SELECT id, name FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()
    caretakers_out = [
        {**dict(c), 'has_password': bool(c['password_hash'])}
        for c in caretakers
    ]
    return render_template('caretakers.html', caretakers=caretakers_out, all_properties=all_properties)


@app.route('/caretakers/new', methods=['POST'])
def create_caretaker():
    """Create a new caretaker account."""
    from werkzeug.security import generate_password_hash
    name = (request.form.get('name') or '').strip()
    phone = (request.form.get('phone') or '').strip()
    property_id = (request.form.get('property_id') or '').strip()
    password = (request.form.get('password') or '').strip()

    if not name or not property_id:
        flash('Name and property are required.', 'error')
        return redirect(url_for('manage_caretakers'))

    with get_connection() as conn:
        prop = conn.execute("SELECT name FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            flash('Property not found.', 'error')
            return redirect(url_for('manage_caretakers'))
        caretaker_id = generate_id('CTKR')
        password_hash = generate_password_hash(password) if password else None
        conn.execute(
            "INSERT INTO caretakers (id, property_id, name, phone, password_hash) VALUES (?, ?, ?, ?, ?)",
            (caretaker_id, property_id, name, phone or None, password_hash),
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('caretaker_created', 'caretaker', caretaker_id, f"Caretaker: {name} | Property: {prop['name']}", 'admin'),
        )
    flash(f"Caretaker '{name}' created.", 'success')
    return redirect(url_for('manage_caretakers'))


@app.route('/caretakers/<caretaker_id>/set-password', methods=['POST'])
def set_caretaker_password(caretaker_id):
    """Set or change a caretaker's password."""
    from werkzeug.security import generate_password_hash
    password = (request.form.get('password') or '').strip()
    if not password:
        flash('Password cannot be empty.', 'error')
        return redirect(url_for('manage_caretakers'))
    with get_connection() as conn:
        c = conn.execute("SELECT name FROM caretakers WHERE id = ?", (caretaker_id,)).fetchone()
        if not c:
            flash('Caretaker not found.', 'error')
            return redirect(url_for('manage_caretakers'))
        conn.execute(
            "UPDATE caretakers SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), caretaker_id),
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('caretaker_password_set', 'caretaker', caretaker_id, f"Caretaker: {c['name']} | Password updated", 'admin'),
        )
    flash(f"Password updated for {c['name']}.", 'success')
    return redirect(url_for('manage_caretakers'))


@app.route('/caretakers/<caretaker_id>/edit', methods=['POST'])
def edit_caretaker(caretaker_id):
    """Edit caretaker name, phone, and property assignment."""
    name = (request.form.get('name') or '').strip()
    phone = (request.form.get('phone') or '').strip()
    property_id = (request.form.get('property_id') or '').strip()

    if not name or not property_id:
        flash('Name and property are required.', 'error')
        return redirect(url_for('manage_caretakers'))
    with get_connection() as conn:
        c = conn.execute("SELECT name FROM caretakers WHERE id = ?", (caretaker_id,)).fetchone()
        if not c:
            flash('Caretaker not found.', 'error')
            return redirect(url_for('manage_caretakers'))
        conn.execute(
            "UPDATE caretakers SET name = ?, phone = ?, property_id = ? WHERE id = ?",
            (name, phone or None, property_id, caretaker_id),
        )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('caretaker_updated', 'caretaker', caretaker_id, f"Caretaker: {name} | Updated", 'admin'),
        )
    flash(f"Caretaker '{name}' updated.", 'success')
    return redirect(url_for('manage_caretakers'))


@app.route('/caretakers/<caretaker_id>/delete', methods=['POST'])
def delete_caretaker(caretaker_id):
    """Delete a caretaker account."""
    with get_connection() as conn:
        c = conn.execute("SELECT name FROM caretakers WHERE id = ?", (caretaker_id,)).fetchone()
        if not c:
            flash('Caretaker not found.', 'error')
            return redirect(url_for('manage_caretakers'))
        conn.execute("DELETE FROM caretakers WHERE id = ?", (caretaker_id,))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('caretaker_deleted', 'caretaker', caretaker_id, f"Caretaker: {c['name']} | Deleted", 'admin'),
        )
    flash(f"Caretaker '{c['name']}' deleted.", 'success')
    return redirect(url_for('manage_caretakers'))


@app.route('/properties/set-due-day', methods=['POST'])
def update_rent_due_day():
    """Set per-property rent due day."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
        try:
            day = int(request.form.get('rent_due_day', 5))
            day = max(0, min(31, day))
        except (TypeError, ValueError):
            day = 5
        conn.execute(
            "UPDATE properties SET rent_due_day = ? WHERE id = ?", (day, property_row['id'])
        )
        label = 'last day of month' if day == 0 else f"the {day}{'st' if day == 1 else 'nd' if day == 2 else 'rd' if day == 3 else 'th'}"
        flash(f"Rent due day set to {label}.", 'success')
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('rent_due_day_updated', 'property', property_row['id'],
             f"rent_due_day set to {day}", 'admin'),
        )
    return redirect(url_for('generate_charges'))


@app.route('/report-payment', methods=['GET', 'POST'])
def report_payment():
    """Report a payment (create claim). Parse message with parse_mpesa_message (dict)."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        units = conn.execute("""
            SELECT u.*, t.name AS tenant_name
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE u.property_id = ?
            ORDER BY u.unit_number
        """, (property_row['id'],)).fetchall()

        if request.method == 'POST':
            message = request.form.get('message', '').strip()
            unit_id = request.form.get('unit_id', '').strip()
            if not message:
                flash('Please paste the Mpesa message or reference code.', 'error')
                return redirect(url_for('report_payment'))
            if not unit_id:
                flash('Please select a unit.', 'error')
                return redirect(url_for('report_payment'))

            parsed = parse_mpesa_message(message)
            if not parsed.get('success'):
                flash(parsed.get('error', 'Could not extract Mpesa reference from message.'), 'error')
                return redirect(url_for('report_payment'))

            reference = parsed['reference']
            amount = parsed.get('amount')
            if amount is not None and hasattr(amount, '__float__'):
                amount = float(amount)
            else:
                amount = None

            existing = conn.execute(
                "SELECT id FROM payment_claims WHERE property_id = ? AND mpesa_ref = ?",
                (property_row['id'], reference),
            ).fetchone()
            if existing:
                flash(f'Reference {reference} has already been reported.', 'warning')
                return redirect(url_for('report_payment'))

            claim_id = generate_id('CLM')
            conn.execute("""
                INSERT INTO payment_claims
                (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (claim_id, property_row['id'], reference, unit_id, amount, message, 'web'))
            unit_row = conn.execute("SELECT unit_number FROM units WHERE id = ?", (unit_id,)).fetchone()
            unit_number = unit_row['unit_number'] if unit_row else unit_id
            amt_str = f'{amount:.2f}' if amount is not None else '-'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('claim_created', 'claim', claim_id, f'Ref: {reference} | Unit: {unit_number} | Amount: {amt_str}', 'admin'),
            )
            flash(f'Payment claim recorded! Reference: {reference}', 'success')
            return redirect(url_for('dashboard'))

        return render_template('report_payment.html', property=property_row, units=units)


@app.route('/statements')
def manage_statements():
    """List bank statements."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        statements = conn.execute("""
            SELECT * FROM bank_statements WHERE property_id = ? ORDER BY uploaded_at DESC
        """, (property_row['id'],)).fetchall()

        return render_template('statements.html', property=property_row, statements=statements)


@app.route('/statements/upload', methods=['GET', 'POST'])
def upload_statement():
    """Upload and process bank statement PDF."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('upload_statement'))
        file = request.files['file']
        if not file or file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('upload_statement'))
        if not file.filename.lower().endswith('.pdf'):
            flash('Please upload a PDF file', 'error')
            return redirect(url_for('upload_statement'))

        filename = secure_filename(file.filename)
        statement_id = generate_id('STMT')
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{statement_id}.pdf")
        file.save(file_path)

        try:
            result = parse_bank_statement(file_path)
            validation = result.get('validation') or {}
            if not validation.get('valid'):
                flash('Statement failed validation: ' + (validation.get('error') or 'Unknown error'), 'error')
                if os.path.exists(file_path):
                    os.remove(file_path)
                return redirect(url_for('upload_statement'))

            transactions = result.get('transactions') or []
            period_start = None
            period_end = None
            if transactions:
                dates = []
                for t in transactions:
                    iso = _parse_txn_date_to_iso(t.transaction_date) if getattr(t, 'transaction_date', None) else None
                    if iso:
                        dates.append(iso)
                if dates:
                    period_start = min(dates)
                    period_end = max(dates)

            opening = result.get('opening_balance')
            closing = result.get('closing_balance')
            if isinstance(opening, Decimal):
                opening = float(opening)
            if isinstance(closing, Decimal):
                closing = float(closing)
            summary = result.get('summary') or {}
            paybill_count = summary.get('paybill_credits', 0)

            with get_connection() as conn:
                property_row = get_current_property(conn)
                if not property_row:
                    flash('Please select a property first.', 'error')
                    return redirect(url_for('property_list'))
                if period_start and period_end and property_row:
                    conn.execute("""
                        UPDATE bank_statements SET status = 'superseded'
                        WHERE property_id = ? AND status = 'active'
                        AND period_start <= ? AND period_end >= ?
                    """, (property_row['id'], period_end, period_start))

                conn.execute("""
                    INSERT INTO bank_statements
                    (id, property_id, filename, file_path, period_start, period_end,
                     opening_balance, closing_balance, total_transactions, rent_transactions, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """, (
                    statement_id, property_row['id'], filename, file_path,
                    period_start, period_end, opening, closing,
                    len(transactions), paybill_count,
                ))

                for txn in transactions:
                    txn_id = generate_id('TXN')
                    amount = float(txn.amount) if hasattr(txn.amount, '__float__') else float(txn.amount)
                    txn_date = getattr(txn, 'transaction_date', None)
                    if txn_date:
                        txn_date = _parse_txn_date_to_iso(txn_date) or txn_date
                    conn.execute("""
                        INSERT INTO bank_transactions
                        (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date, raw_text, unit_hint)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        txn_id, statement_id,
                        getattr(txn, 'reference', None),
                        amount,
                        getattr(txn, 'txn_type', 'UNKNOWN'),
                        getattr(txn, 'sender', None),
                        txn_date,
                        getattr(txn, 'raw_text', '') or '',
                        getattr(txn, 'unit_hint', None),
                    ))

                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('statement_uploaded', 'statement', statement_id,
                     f'File: {filename} | {len(transactions)} transactions | {paybill_count} rent', 'admin'),
                )

            flash(f'Statement uploaded! {paybill_count} rent payments found.', 'success')
            return redirect(url_for('verify_payments', statement_id=statement_id))

        except Exception as e:
            flash(f'Error processing statement: {str(e)}', 'error')
            if os.path.exists(file_path):
                os.remove(file_path)
            return redirect(url_for('upload_statement'))

    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
    return render_template('upload_statement.html', property=property_row)


@app.route('/statements/<statement_id>/reparse', methods=['POST'])
def reparse_statement(statement_id):
    """Re-run the PDF parser on an already-uploaded statement, replacing unassigned transactions."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        stmt = conn.execute(
            "SELECT * FROM bank_statements WHERE id = ? AND property_id = ?",
            (statement_id, property_row['id']),
        ).fetchone()
        if not stmt:
            flash('Statement not found.', 'error')
            return redirect(url_for('manage_statements'))

        file_path = stmt['file_path']
        if not os.path.exists(file_path):
            flash('PDF file not found on server — cannot re-parse.', 'error')
            return redirect(url_for('manage_statements'))

        # Delete only transactions that have NO linked payment (safe to replace)
        conn.execute("""
            DELETE FROM bank_transactions
            WHERE statement_id = ?
            AND id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
        """, (statement_id,))

    try:
        result = parse_bank_statement(file_path)
        validation = result.get('validation') or {}
        if not validation.get('valid'):
            flash('Re-parse failed validation: ' + (validation.get('error') or 'Unknown error'), 'error')
            return redirect(url_for('manage_statements'))

        transactions = result.get('transactions') or []
        opening = result.get('opening_balance')
        closing = result.get('closing_balance')
        if isinstance(opening, Decimal):
            opening = float(opening)
        if isinstance(closing, Decimal):
            closing = float(closing)
        summary = result.get('summary') or {}
        paybill_count = summary.get('paybill_credits', 0)

        with get_connection() as conn:
            # Get existing mpesa_refs already in DB for this statement (kept because they had payments)
            existing_refs = {
                row[0] for row in conn.execute(
                    "SELECT mpesa_ref FROM bank_transactions WHERE statement_id = ? AND mpesa_ref IS NOT NULL",
                    (statement_id,),
                ).fetchall()
            }

            inserted = 0
            skipped = 0
            for txn in transactions:
                ref = getattr(txn, 'reference', None)
                if ref and ref in existing_refs:
                    skipped += 1
                    continue
                txn_id = generate_id('TXN')
                amount = float(txn.amount)
                txn_date = getattr(txn, 'transaction_date', None)
                if txn_date:
                    txn_date = _parse_txn_date_to_iso(txn_date) or txn_date
                conn.execute("""
                    INSERT INTO bank_transactions
                    (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date, raw_text, unit_hint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    txn_id, statement_id, ref, amount,
                    getattr(txn, 'txn_type', 'UNKNOWN'),
                    getattr(txn, 'sender', None),
                    txn_date,
                    getattr(txn, 'raw_text', '') or '',
                    getattr(txn, 'unit_hint', None),
                ))
                inserted += 1

            # Update statement metadata with fresh parse results
            conn.execute("""
                UPDATE bank_statements
                SET opening_balance = ?, closing_balance = ?, total_transactions = ?, rent_transactions = ?
                WHERE id = ?
            """, (opening, closing, len(transactions), paybill_count, statement_id))

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('statement_reparsed', 'statement', statement_id,
                 f'Inserted: {inserted} | Skipped (assigned): {skipped} | Paybill: {paybill_count}', 'admin'),
            )

        flash(f'Re-parsed: {inserted} transactions replaced, {skipped} kept (already assigned).', 'success')
        return redirect(url_for('verify_payments', statement_id=statement_id))

    except Exception as e:
        flash(f'Re-parse error: {str(e)}', 'error')
        return redirect(url_for('manage_statements'))


@app.route('/verify/<statement_id>')
def verify_payments(statement_id):
    """Auto-verify pending claims against this statement."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        pending_claims = conn.execute("""
            SELECT * FROM payment_claims WHERE property_id = ? AND status = 'pending'
        """, (property_row['id'],)).fetchall()

        bank_txns = conn.execute("""
            SELECT * FROM bank_transactions
            WHERE statement_id = ? AND txn_type = 'PAYBILL_CREDIT'
        """, (statement_id,)).fetchall()

        bank_by_ref = {row['mpesa_ref']: row for row in bank_txns if row['mpesa_ref']}
        verified_count = 0
        verified_claim_ids = set()

        for claim in pending_claims:
            ref = claim['mpesa_ref']
            if ref not in bank_by_ref:
                continue
            bank_txn = bank_by_ref[ref]
            existing_payment = conn.execute("SELECT id FROM payments WHERE bank_txn_id = ?", (bank_txn['id'],)).fetchone()
            if existing_payment:
                continue

            payment_id = generate_id('PAY')
            conn.execute("""
                INSERT INTO payments
                (id, property_id, unit_id, claim_id, bank_txn_id, statement_id, amount, payment_date, assignment_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto')
            """, (
                payment_id, property_row['id'], claim['unit_id'], claim['id'],
                bank_txn['id'], statement_id, bank_txn['amount'], bank_txn['txn_date'] or '',
            ))
            allocate_payment(conn, payment_id, claim['unit_id'], bank_txn['amount'])
            conn.execute(
                "UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (claim['id'],),
            )
            info = conn.execute(
                "SELECT u.unit_number, t.name AS tenant_name FROM units u "
                "LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active' WHERE u.id = ?",
                (claim['unit_id'],),
            ).fetchone()
            unit_number = info['unit_number'] if info else '-'
            tenant_name = (info['tenant_name'] or '-') if info else '-'
            amount_val = bank_txn['amount']
            details = f'Ref: {ref} | Amount: KES {amount_val:.2f} | Unit: {unit_number} | Tenant: {tenant_name}'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('payment_verified', 'payment', payment_id, details, 'admin'),
            )
            verified_claim_ids.add(claim['id'])
            verified_count += 1

            try:
                _tenant = conn.execute(
                    "SELECT name, phone, access_token FROM tenants WHERE unit_id = ? AND status = 'active'",
                    (claim['unit_id'],),
                ).fetchone()
                if _tenant and _tenant['phone']:
                    _token = _tenant['access_token']
                    if not _token:
                        _token = secrets.token_urlsafe(32)
                        conn.execute(
                            "UPDATE tenants SET access_token = ? WHERE unit_id = ? AND status = 'active'",
                            (_token, claim['unit_id']),
                        )
                    _base = request.host_url.rstrip('/')
                    _link = f"{_base}/tenant/{_token}"
                    _sms = (
                        f"Hi {_tenant['name']}, KES {float(bank_txn['amount']):,.0f} payment confirmed.\n"
                        f"View your account: {_link}"
                    )
                    from src.messaging.delivery import send_sms
                    send_sms([{'phone': _tenant['phone']}], _sms)
            except Exception:
                pass

        # Notify tenants whose claims were not on this statement
        for claim in pending_claims:
            if claim['id'] in verified_claim_ids:
                continue
            if not claim.get('mpesa_ref'):
                continue
            try:
                _rej_tenant = conn.execute(
                    "SELECT name, phone FROM tenants WHERE unit_id = ? AND status = 'active'",
                    (claim['unit_id'],),
                ).fetchone()
                if _rej_tenant and _rej_tenant['phone']:
                    _rej_sms = (
                        f"Hi {_rej_tenant['name']}, reference {claim['mpesa_ref']} "
                        "was not found on this statement. Please contact us or provide proof of payment."
                    )
                    from src.messaging.delivery import send_sms
                    send_sms([{'phone': _rej_tenant['phone']}], _rej_sms)
            except Exception:
                pass

        flash(f'Verification complete! {verified_count} payments verified.', 'success')
        return redirect(url_for('dashboard'))


@app.route('/payments/auto-assign/<txn_id>', methods=['POST'])
def auto_assign_payment(txn_id):
    """One-click auto-assign using unit_hint extracted from narration. Logs full suggestion trail."""
    from markupsafe import Markup
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
        property_id = property_row['id']

        txn = conn.execute("""
            SELECT bt.* FROM bank_transactions bt
            JOIN bank_statements bs ON bt.statement_id = bs.id
            WHERE bt.id = ? AND bs.property_id = ?
        """, (txn_id, property_id)).fetchone()
        if not txn:
            flash('Transaction not found.', 'error')
            return redirect(url_for('review', tab='unreported'))

        unit_hint = (txn['unit_hint'] or '').strip()
        if not unit_hint:
            flash('No unit hint available — assign manually.', 'error')
            return redirect(url_for('assign_payment', txn_id=txn_id))

        unit = conn.execute("""
            SELECT id, unit_number FROM units
            WHERE property_id = ? AND UPPER(TRIM(unit_number)) = ?
        """, (property_id, unit_hint.upper())).fetchone()
        if not unit:
            flash(f'Hint "{unit_hint}" did not match any unit. Assign manually.', 'error')
            return redirect(url_for('assign_payment', txn_id=txn_id))

        if conn.execute("SELECT id FROM payments WHERE bank_txn_id = ?", (txn_id,)).fetchone():
            flash('This transaction has already been assigned.', 'warning')
            return redirect(url_for('review', tab='unreported'))

        payment_id = generate_id('PAY')
        amount = float(txn['amount'])
        unit_id = unit['id']
        unit_number = unit['unit_number']
        auto_reason = f"Auto-assigned via narration hint: MOWIN {unit_hint} → Unit {unit_number}"

        conn.execute("""
            INSERT INTO payments
            (id, property_id, unit_id, bank_txn_id, statement_id, amount, payment_date, assignment_type, assignment_reason, assigned_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'auto_hint', ?, 'admin')
        """, (payment_id, property_id, unit_id, txn_id, txn['statement_id'],
              amount, txn['txn_date'] or '', auto_reason))
        allocate_payment(conn, payment_id, unit_id, amount)
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('payment_auto_hint_assigned', 'payment', payment_id,
             f"Ref: {txn['mpesa_ref'] or '-'} | KES {amount:.2f} | Unit: {unit_number} | Hint: {unit_hint}", 'admin'),
        )

        try:
            _tenant = conn.execute(
                "SELECT name, phone, access_token FROM tenants WHERE unit_id = ? AND status = 'active'",
                (unit_id,),
            ).fetchone()
            if _tenant and _tenant['phone']:
                _token = _tenant['access_token']
                if not _token:
                    _token = secrets.token_urlsafe(32)
                    conn.execute(
                        "UPDATE tenants SET access_token = ? WHERE unit_id = ? AND status = 'active'",
                        (_token, unit_id),
                    )
                _base = request.host_url.rstrip('/')
                _link = f"{_base}/tenant/{_token}"
                _sms = (
                    f"Hi {_tenant['name']}, KES {amount:,.0f} payment confirmed.\n"
                    f"View your account: {_link}"
                )
                from src.messaging.delivery import send_sms
                send_sms([{'phone': _tenant['phone']}], _sms)
        except Exception:
            pass

    undo_url = url_for('delete_payment', payment_id=payment_id)
    flash(Markup(
        f'KES {amount:,.0f} assigned to Unit {unit_number} via narration hint. '
        f'<a href="{undo_url}" class="alert-link fw-semibold">Undo</a>'
    ), 'success')
    return redirect(url_for('review', tab='unreported'))


@app.route('/payments/<payment_id>/delete', methods=['GET', 'POST'])
def delete_payment(payment_id):
    """Delete a payment and cascade its allocations. Resets linked claim to pending. Audits the deletion."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        payment = conn.execute(
            "SELECT * FROM payments WHERE id = ? AND property_id = ?",
            (payment_id, property_row['id']),
        ).fetchone()
        if not payment:
            flash('Payment not found.', 'error')
            return redirect(url_for('review', tab='unreported'))

        unit_info = conn.execute(
            "SELECT unit_number FROM units WHERE id = ?", (payment['unit_id'],)
        ).fetchone()
        unit_number = unit_info['unit_number'] if unit_info else '-'
        amount = float(payment['amount'])

        if payment['claim_id']:
            conn.execute(
                "UPDATE payment_claims SET status = 'pending', verified_at = NULL WHERE id = ?",
                (payment['claim_id'],),
            )
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('payment_deleted', 'payment', payment_id,
             f"Deleted | KES {amount:.2f} | Unit: {unit_number} | Type: {payment['assignment_type'] or '-'}", 'admin'),
        )
        conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))

    flash(f'Payment undone — KES {amount:,.0f} removed from Unit {unit_number}.', 'success')
    next_url = request.args.get('next') or url_for('review', tab='unreported')
    return redirect(next_url)


@app.route('/assign/<txn_id>', methods=['GET', 'POST'])
def assign_payment(txn_id):
    """Manually assign an unassigned bank payment; require reason (min 5 chars)."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        txn = conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not txn:
            flash('Transaction not found', 'error')
            return redirect(url_for('dashboard'))

        existing = conn.execute("SELECT id FROM payments WHERE bank_txn_id = ?", (txn_id,)).fetchone()
        if existing:
            flash('This payment has already been assigned', 'warning')
            return redirect(url_for('dashboard'))

        units = conn.execute("""
            SELECT u.*, t.name AS tenant_name
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE u.property_id = ?
            ORDER BY u.unit_number
        """, (property_row['id'],)).fetchall()

        if request.method == 'POST':
            unit_id = request.form.get('unit_id', '').strip()
            reason = request.form.get('reason', '').strip()
            if not unit_id:
                flash('Please select a unit.', 'error')
                return redirect(url_for('assign_payment', txn_id=txn_id))
            if len(reason) < 5:
                flash('Please provide a reason for manual assignment (min 5 characters)', 'error')
                return redirect(url_for('assign_payment', txn_id=txn_id))

            payment_id = generate_id('PAY')
            conn.execute("""
                INSERT INTO payments
                (id, property_id, unit_id, bank_txn_id, statement_id, amount, payment_date, assignment_type, assignment_reason, assigned_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, 'admin')
            """, (
                payment_id, property_row['id'], unit_id, txn_id, txn['statement_id'],
                txn['amount'], txn['txn_date'] or '', reason,
            ))
            allocate_payment(conn, payment_id, unit_id, txn['amount'])
            unit_row = conn.execute("SELECT unit_number FROM units WHERE id = ?", (unit_id,)).fetchone()
            unit_number = unit_row['unit_number'] if unit_row else unit_id
            mpesa_ref = txn['mpesa_ref'] or '-'
            details = f'Ref: {mpesa_ref} | Amount: KES {txn["amount"]:.2f} | Unit: {unit_number} | Reason: {reason}'
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('payment_manual_assigned', 'payment', payment_id, details, 'admin'),
            )
            try:
                _tenant = conn.execute(
                    "SELECT name, phone, access_token FROM tenants WHERE unit_id = ? AND status = 'active'",
                    (unit_id,),
                ).fetchone()
                if _tenant and _tenant['phone']:
                    _token = _tenant['access_token']
                    if not _token:
                        _token = secrets.token_urlsafe(32)
                        conn.execute(
                            "UPDATE tenants SET access_token = ? WHERE unit_id = ? AND status = 'active'",
                            (_token, unit_id),
                        )
                    _base = request.host_url.rstrip('/')
                    _link = f"{_base}/tenant/{_token}"
                    _sms = (
                        f"Hi {_tenant['name']}, KES {float(txn['amount']):,.0f} payment confirmed.\n"
                        f"View your account: {_link}"
                    )
                    from src.messaging.delivery import send_sms
                    send_sms([{'phone': _tenant['phone']}], _sms)
            except Exception:
                pass
            flash('Payment assigned successfully!', 'success')
            return redirect(url_for('review', tab='unreported'))

        suggested_unit_id = request.args.get('suggested_unit_id', '').strip() or None
        suggestion_source = request.args.get('suggestion_source', '').strip() or None
        suggested_tenant_name = request.args.get('suggested_tenant_name', '').strip() or None

        return render_template(
            'assign_payment.html',
            property=property_row,
            txn=txn,
            units=units,
            suggested_unit_id=suggested_unit_id,
            suggestion_source=suggestion_source,
            suggested_tenant_name=suggested_tenant_name,
        )


@app.route('/assign-group', methods=['POST'])
def assign_group():
    """Assign a group of unreported bank payments from the same sender to a single unit."""
    txn_ids_raw = request.form.get('txn_ids', '').strip()
    unit_id = request.form.get('unit_id', '').strip()
    reason = request.form.get('reason', '').strip()
    sender_name = request.form.get('sender_name', '').strip()

    if not txn_ids_raw:
        flash('No transactions selected for group assignment.', 'error')
        return redirect(url_for('review', tab='unreported'))
    if not unit_id:
        flash('Please select a unit.', 'error')
        return redirect(url_for('review', tab='unreported'))
    if len(reason) < 5:
        flash('Please provide a reason for manual assignment (min 5 characters).', 'error')
        return redirect(url_for('review', tab='unreported'))

    txn_ids = [tid.strip() for tid in txn_ids_raw.split(',') if tid.strip()]
    if not txn_ids:
        flash('No valid transactions selected for group assignment.', 'error')
        return redirect(url_for('review', tab='unreported'))

    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
        property_id = property_row['id']

        unit = conn.execute(
            "SELECT id, unit_number FROM units WHERE id = ? AND property_id = ?",
            (unit_id, property_id),
        ).fetchone()
        if not unit:
            flash('Selected unit not found for this property.', 'error')
            return redirect(url_for('review', tab='unreported'))

        unit_number = unit['unit_number']

        total_amount = 0.0
        assigned_count = 0

        for txn_id in txn_ids:
            txn = conn.execute(
                """
                SELECT bt.*
                FROM bank_transactions bt
                JOIN bank_statements bs ON bt.statement_id = bs.id
                WHERE bt.id = ? AND bs.property_id = ?
                """,
                (txn_id, property_id),
            ).fetchone()
            if not txn:
                continue

            existing = conn.execute(
                "SELECT id FROM payments WHERE bank_txn_id = ?",
                (txn_id,),
            ).fetchone()
            if existing:
                continue

            payment_id = generate_id('PAY')
            conn.execute(
                """
                INSERT INTO payments
                (id, property_id, unit_id, bank_txn_id, statement_id, amount, payment_date, assignment_type, assignment_reason, assigned_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, 'admin')
                """,
                (
                    payment_id,
                    property_id,
                    unit_id,
                    txn['id'],
                    txn['statement_id'],
                    txn['amount'],
                    txn['txn_date'] or '',
                    reason,
                ),
            )
            allocate_payment(conn, payment_id, unit_id, txn['amount'])
            mpesa_ref = txn['mpesa_ref'] or '-'
            details = f"Ref: {mpesa_ref} | Amount: KES {txn['amount']:.2f} | Unit: {unit_number} | Reason: {reason}"
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('payment_manual_assigned', 'payment', payment_id, details, 'admin'),
            )

            try:
                _tenant = conn.execute(
                    "SELECT name, phone, access_token FROM tenants WHERE unit_id = ? AND status = 'active'",
                    (unit_id,),
                ).fetchone()
                if _tenant and _tenant['phone']:
                    _token = _tenant['access_token']
                    if not _token:
                        _token = secrets.token_urlsafe(32)
                        conn.execute(
                            "UPDATE tenants SET access_token = ? WHERE unit_id = ? AND status = 'active'",
                            (_token, unit_id),
                        )
                    _base = request.host_url.rstrip('/')
                    _link = f"{_base}/tenant/{_token}"
                    _sms = (
                        f"Hi {_tenant['name']}, KES {float(txn['amount']):,.0f} payment confirmed.\n"
                        f"View your account: {_link}"
                    )
                    from src.messaging.delivery import send_sms
                    send_sms([{'phone': _tenant['phone']}], _sms)
            except Exception:
                pass

            total_amount += float(txn['amount'])
            assigned_count += 1

        if assigned_count > 0:
            summary_details = (
                f"Sender: {sender_name or '-'} | "
                f"Payments: {assigned_count} | "
                f"Total: KES {total_amount:.2f} | "
                f"Unit: {unit_number}"
            )
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('group_assign', 'payment_group', unit_id, summary_details, 'admin'),
            )
            flash(f'Assigned {assigned_count} payments (KES {total_amount:.2f}) to unit {unit_number}.', 'success')
        else:
            flash('No payments were assigned (they may have already been assigned).', 'warning')

    return redirect(url_for('review', tab='unreported'))


@app.route('/charges/generate', methods=['GET', 'POST'])
def generate_charges():
    """Generate monthly rent charges for occupied units."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        if request.method == 'POST':
            period = request.form.get('period', '').strip()
            if not period:
                flash('Please enter a period (e.g. 2026-01).', 'error')
                return redirect(url_for('generate_charges'))

            import calendar as _cal
            try:
                y, m = period.split('-')
                y, m = int(y), int(m)
                due_month = m + 1 if m < 12 else 1
                due_year = y if m < 12 else y + 1
                due_day = int(property_row.get('rent_due_day') or 5)
                if due_day == 0:
                    last_day = _cal.monthrange(due_year, due_month)[1]
                    due_date = f"{due_year:04d}-{due_month:02d}-{last_day:02d}"
                else:
                    safe_day = min(due_day, 28)
                    due_date = f"{due_year:04d}-{due_month:02d}-{safe_day:02d}"
            except (ValueError, TypeError):
                due_date = None

            units = conn.execute("""
                SELECT * FROM units WHERE property_id = ? AND status = 'occupied'
            """, (property_row['id'],)).fetchall()
            rent_created = 0
            svc_created = 0
            for unit in units:
                # Rent charge
                existing_rent = conn.execute(
                    "SELECT id FROM rent_charges WHERE unit_id = ? AND period = ? AND charge_type = 'rent'",
                    (unit['id'], period),
                ).fetchone()
                if not existing_rent:
                    charge_id = generate_id('CHG')
                    conn.execute("""
                        INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount, due_date)
                        VALUES (?, ?, ?, ?, 'rent', ?, ?)
                    """, (charge_id, property_row['id'], unit['id'], period, unit['monthly_rent'], due_date))
                    rent_created += 1

                # Service charge (only if > 0)
                if unit['service_charge'] and float(unit['service_charge']) > 0:
                    existing_svc = conn.execute(
                        "SELECT id FROM rent_charges WHERE unit_id = ? AND period = ? AND charge_type = 'service'",
                        (unit['id'], period),
                    ).fetchone()
                    if not existing_svc:
                        charge_id = generate_id('CHG')
                        conn.execute("""
                            INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount, due_date)
                            VALUES (?, ?, ?, ?, 'service', ?, ?)
                        """, (charge_id, property_row['id'], unit['id'], period, unit['service_charge'], due_date))
                        svc_created += 1

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('charges_generated', 'charge', period,
                 f'Period: {period} | {rent_created} rent + {svc_created} service charges created', 'admin'),
            )
            try:
                from src.messaging.owner_notify import notify_property_owners
                _base = request.host_url.rstrip('/')
                _owner_msg = (
                    f"Charges generated: {property_row['name']} — {period}.\n"
                    f"{rent_created} rent + {svc_created} service charges.\n"
                    f"View: {_base}/view/{property_row['id']}"
                )
                notify_property_owners(conn, property_row['id'], _owner_msg, sent_by='Admin')
            except Exception:
                pass
            flash(f'Generated {rent_created} rent and {svc_created} service charges for {period}. Water charges should be uploaded separately.', 'success')
            return redirect(url_for('dashboard'))

        return render_template('generate_charges.html', property=property_row)


@app.route('/charges/water', methods=['GET', 'POST'])
def upload_water_charges():
    """Upload water readings Excel and create water charge records."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))

        if request.method == 'POST':
            period = request.form.get('period', '').strip()
            if not period:
                flash('Please enter a period (e.g. 2026-03).', 'error')
                return redirect(url_for('upload_water_charges'))

            file = request.files.get('file')
            if not file or file.filename == '':
                flash('Please select an Excel file.', 'error')
                return redirect(url_for('upload_water_charges'))
            if not file.filename.lower().endswith(('.xlsx', '.xls')):
                flash('Please upload an Excel file (.xlsx or .xls)', 'error')
                return redirect(url_for('upload_water_charges'))

            filename = secure_filename(file.filename)
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_water_{filename}")
            file.save(temp_path)

            try:
                result = parse_water_excel(temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if not result['success']:
                for error in result['errors']:
                    flash(error, 'error')
                return redirect(url_for('upload_water_charges'))

            if not result['rows']:
                flash('No valid water charge rows found.', 'error')
                return redirect(url_for('upload_water_charges'))

            # Match unit_numbers to DB
            units = conn.execute(
                "SELECT id, unit_number FROM units WHERE property_id = ?",
                (property_row['id'],)
            ).fetchall()
            unit_map = {u['unit_number'].strip().upper(): u['id'] for u in units}

            created = 0
            skipped = 0
            not_found = []

            for row in result['rows']:
                unit_key = row['unit_number'].strip().upper()
                unit_id = unit_map.get(unit_key)
                if not unit_id:
                    not_found.append(row['unit_number'])
                    continue

                existing = conn.execute(
                    "SELECT id FROM rent_charges WHERE unit_id = ? AND period = ? AND charge_type = 'water'",
                    (unit_id, period)
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                charge_id = generate_id('CHG')
                conn.execute(
                    "INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) VALUES (?, ?, ?, ?, 'water', ?)",
                    (charge_id, property_row['id'], unit_id, period, row['water_charge'])
                )
                created += 1

            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('water_charges_uploaded', 'charge', period,
                 f'Period: {period} | {created} water charges created | {skipped} skipped (existing) | Total: KES {result["total_water_charges"]:,.0f}', 'admin')
            )

            msg = f'Uploaded {created} water charges for {period}.'
            if skipped:
                msg += f' {skipped} skipped (already exist).'
            if not_found:
                msg += f' Units not found: {", ".join(not_found[:5])}.'
            flash(msg, 'success')
            return redirect(url_for('dashboard'))

        return render_template('upload_water_charges.html', property=property_row)


@app.route('/search')
def search():
    """Search units and tenants by query string."""
    q = request.args.get('q', '').strip()
    units = []
    tenants = []
    if q:
        like = f'%{q}%'
        with get_connection() as conn:
            property_row = get_current_property(conn)
            if property_row:
                pid = property_row['id']
                units = conn.execute("""
                    SELECT u.*, t.name AS tenant_name, ub.balance
                    FROM units u
                    LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
                    LEFT JOIN unit_balances ub ON ub.unit_id = u.id
                    WHERE u.property_id = ? AND (u.unit_number LIKE ? OR t.name LIKE ?)
                    ORDER BY u.unit_number
                """, (pid, like, like)).fetchall()
                tenants = conn.execute("""
                    SELECT t.*, u.unit_number
                    FROM tenants t
                    LEFT JOIN units u ON t.unit_id = u.id
                    WHERE t.property_id = ? AND t.status = 'active'
                      AND (t.name LIKE ? OR t.phone LIKE ?)
                    ORDER BY t.name
                """, (pid, like, like)).fetchall()
    return render_template('search_results.html', q=q, units=units, tenants=tenants)


@app.route('/activity')
def activity():
    """Full audit log; optional filter by action type (?action=charges_generated)."""
    filter_action = request.args.get('action', '').strip()
    with get_connection() as conn:
        if filter_action:
            activity_list = conn.execute("""
                SELECT * FROM audit_log WHERE action = ? ORDER BY timestamp DESC
            """, (filter_action,)).fetchall()
        else:
            activity_list = conn.execute("""
                SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 200
            """).fetchall()
    return render_template(
        'activity.html',
        activity_list=activity_list,
        filter_action=filter_action or None,
    )


@app.route('/export/current-state')
def export_current_state():
    """Export current state of all units as Excel."""
    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))

        property_id = property_row['id']

        units = conn.execute("""
            SELECT u.unit_number, u.apartment_size, u.monthly_rent, u.service_charge, u.status,
                   t.name as tenant_name, t.phone as tenant_phone
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE u.property_id = ?
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

        wb = Workbook()
        ws = wb.active
        ws.title = "Current State"

        # Header
        ws.append([f"Property: {property_row['name']}"])
        ws.append([f"Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
        ws.append([])

        # Column headers
        headers = ['Unit', 'Tenant', 'Apt Size', 'Monthly Rent', 'Service Charge',
                   'Rent Due', 'Svc Charge Due', 'Water Due', 'Total Pending', 'Status']
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

        totals = {'rent_due': 0, 'svc_due': 0, 'water_due': 0, 'total': 0}

        for unit in units:
            # Get unit_id for charge queries
            unit_row = conn.execute("SELECT id FROM units WHERE property_id = ? AND unit_number = ?", (property_id, unit['unit_number'])).fetchone()
            if not unit_row:
                continue
            unit_id = unit_row['id']

            # Get outstanding by charge type
            charges = conn.execute("""
                SELECT rc.charge_type,
                       SUM(rc.amount) as charged,
                       COALESCE((SELECT SUM(pa.amount) FROM payment_allocations pa WHERE pa.charge_id = rc.id), 0) as paid
                FROM rent_charges rc
                WHERE rc.unit_id = ?
                GROUP BY rc.charge_type
            """, (unit_id,)).fetchall()

            dues = {'rent': 0, 'service': 0, 'water': 0}
            for c in charges:
                due = float(c['charged']) - float(c['paid'])
                if due > 0 and c['charge_type'] in dues:
                    dues[c['charge_type']] = due

            total_pending = dues['rent'] + dues['service'] + dues['water']
            totals['rent_due'] += dues['rent']
            totals['svc_due'] += dues['service']
            totals['water_due'] += dues['water']
            totals['total'] += total_pending

            ws.append([
                unit['unit_number'],
                unit['tenant_name'] or '',
                unit['apartment_size'] or '',
                float(unit['monthly_rent']),
                float(unit['service_charge']) if unit['service_charge'] else 0,
                dues['rent'],
                dues['service'],
                dues['water'],
                total_pending,
                unit['status'],
            ])

        # Totals row
        ws.append([])
        totals_row = ['', '', '', '', 'TOTALS:', totals['rent_due'], totals['svc_due'], totals['water_due'], totals['total'], '']
        ws.append(totals_row)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

        # Format currency columns
        for row in ws.iter_rows(min_row=5, min_col=4, max_col=9):
            for cell in row:
                cell.number_format = '#,##0'

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('export_generated', 'export', 'current-state',
             f'Export: Current State | Property: {property_row["name"]}', 'admin')
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"current_state_{property_row['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/activity')
def export_activity():
    """Export activity log as Excel."""
    date_from = request.args.get('from', '2000-01-01')
    date_to = request.args.get('to', '2099-12-31')

    with get_connection() as conn:
        logs = conn.execute("""
            SELECT timestamp, action, entity_type, entity_id, details, user_id
            FROM audit_log
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp DESC
        """, (date_from, date_to)).fetchall()

        wb = Workbook()
        ws = wb.active
        ws.title = "Activity Log"

        ws.append([f"Activity Log: {date_from} to {date_to}"])
        ws.append([f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
        ws.append([])

        headers = ['Date/Time', 'Action', 'Entity Type', 'Entity ID', 'Details', 'User']
        ws.append(headers)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

        action_labels = {
            'payment_verified': 'Payment Verified',
            'payment_manual_assigned': 'Payment Manually Assigned',
            'statement_uploaded': 'Bank Statement Uploaded',
            'claim_created': 'Payment Claim Created',
            'charges_generated': 'Charges Generated',
            'water_charges_uploaded': 'Water Charges Uploaded',
            'property_onboarded': 'Property Onboarded',
            'property_created': 'Property Created',
            'tenant_created': 'Tenant Created',
            'tenant_token_generated': 'Tenant Portal Link Generated',
            'tenant_token_revoked': 'Tenant Portal Link Revoked',
            'unit_created': 'Unit Created',
            'export_generated': 'Export Generated',
            'broadcast_sent': 'Broadcast Sent',
            'group_assign': 'Group Assignment',
        }

        for log in logs:
            ws.append([
                log['timestamp'],
                action_labels.get(log['action'], log['action'].replace('_', ' ').title()),
                log['entity_type'] or '',
                log['entity_id'] or '',
                log['details'] or '',
                log['user_id'] or 'System',
            ])

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('export_generated', 'export', 'activity',
             f'Export: Activity Log | Period: {date_from} to {date_to} | {len(logs)} entries', 'admin')
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"activity_log_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/payments')
def export_payments():
    """Export payment verification report as Excel (legal audit trail)."""
    date_from = request.args.get('from', '2000-01-01')
    date_to = request.args.get('to', '2099-12-31')

    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            flash('Please select a property first.', 'error')
            return redirect(url_for('property_list'))

        property_id = property_row['id']

        payments = conn.execute("""
            SELECT p.id, p.amount, p.payment_date, p.assignment_type, p.assignment_reason,
                   u.unit_number, t.name as tenant_name,
                   bt.mpesa_ref, bt.sender_name, bt.txn_date,
                   bs.filename as statement_file
            FROM payments p
            JOIN units u ON p.unit_id = u.id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            JOIN bank_transactions bt ON p.bank_txn_id = bt.id
            JOIN bank_statements bs ON p.statement_id = bs.id
            WHERE p.property_id = ? AND p.payment_date BETWEEN ? AND ?
            ORDER BY p.payment_date ASC
        """, (property_id, date_from, date_to)).fetchall()

        wb = Workbook()

        # Sheet 1: Payment Summary
        ws1 = wb.active
        ws1.title = "Payment Summary"
        ws1.append([f"Payment Verification Report - {property_row['name']}"])
        ws1.append([f"Period: {date_from} to {date_to}"])
        ws1.append([f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
        ws1.append(["This document serves as an audit record of payment allocation."])
        ws1.append([])

        headers1 = ['#', 'Date', 'M-Pesa Ref', 'Sender', 'Unit', 'Tenant', 'Amount (KES)', 'Assignment', 'Reason']
        ws1.append(headers1)
        for cell in ws1[ws1.max_row]:
            cell.font = Font(bold=True)

        for i, p in enumerate(payments, 1):
            ws1.append([
                i,
                p['payment_date'],
                p['mpesa_ref'] or '',
                p['sender_name'] or '',
                p['unit_number'],
                p['tenant_name'] or '',
                float(p['amount']),
                p['assignment_type'],
                p['assignment_reason'] or '',
            ])

        # Sheet 2: Allocation Details
        ws2 = wb.create_sheet("Allocation Details")
        ws2.append([f"Payment Allocation Details - {property_row['name']}"])
        ws2.append([f"Period: {date_from} to {date_to}"])
        ws2.append([])

        headers2 = ['M-Pesa Ref', 'Unit', 'Alloc #', 'Charge Type', 'Charge Period',
                     'Allocated (KES)', 'Charge Total (KES)', 'Remaining After (KES)', 'Status']
        ws2.append(headers2)
        for cell in ws2[ws2.max_row]:
            cell.font = Font(bold=True)

        for p in payments:
            allocations = conn.execute("""
                SELECT pa.amount as alloc_amount, rc.charge_type, rc.period,
                       rc.amount as charge_total,
                       (SELECT COALESCE(SUM(pa2.amount), 0)
                        FROM payment_allocations pa2 WHERE pa2.charge_id = rc.id) as total_paid_on_charge
                FROM payment_allocations pa
                JOIN rent_charges rc ON pa.charge_id = rc.id
                WHERE pa.payment_id = ?
                ORDER BY rc.created_at ASC
            """, (p['id'],)).fetchall()

            alloc_total = sum(float(a['alloc_amount']) for a in allocations)
            unallocated = float(p['amount']) - alloc_total

            for j, a in enumerate(allocations, 1):
                remaining = float(a['charge_total']) - float(a['total_paid_on_charge'])
                status = 'Fully Settled' if remaining <= 0 else 'Partially Settled'
                ws2.append([
                    p['mpesa_ref'] or p['id'],
                    p['unit_number'],
                    j,
                    a['charge_type'].title(),
                    a['period'],
                    float(a['alloc_amount']),
                    float(a['charge_total']),
                    max(remaining, 0),
                    status,
                ])

            if unallocated > 0.01:
                ws2.append([
                    p['mpesa_ref'] or p['id'],
                    p['unit_number'],
                    len(allocations) + 1,
                    'Overpayment / Credit',
                    '-',
                    unallocated,
                    '-',
                    '-',
                    'Unallocated',
                ])

        # Sheet 3: Unit Balance Summary
        ws3 = wb.create_sheet("Unit Balances")
        ws3.append([f"Unit Balance Summary - {property_row['name']}"])
        ws3.append([])

        headers3 = ['Unit', 'Tenant', 'Total Charged (KES)', 'Total Paid (KES)', 'Balance (KES)', '# Payments']
        ws3.append(headers3)
        for cell in ws3[ws3.max_row]:
            cell.font = Font(bold=True)

        balances = conn.execute("""
            SELECT ub.unit_number, ub.tenant_name, ub.total_charged, ub.total_paid, ub.balance,
                   (SELECT COUNT(*) FROM payments WHERE unit_id = ub.unit_id) as payment_count
            FROM unit_balances ub
            WHERE ub.property_id = ?
            ORDER BY ub.unit_number
        """, (property_id,)).fetchall()

        for b in balances:
            ws3.append([
                b['unit_number'],
                b['tenant_name'] or '',
                float(b['total_charged']),
                float(b['total_paid']),
                float(b['balance']),
                b['payment_count'],
            ])

        # Format currency columns on all sheets
        for ws in [ws1, ws2, ws3]:
            for row in ws.iter_rows(min_row=1):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = '#,##0.00'

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('export_generated', 'export', 'payments',
             f'Export: Payment Verification | Period: {date_from} to {date_to} | {len(payments)} payments', 'admin')
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"payment_verification_{property_row['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export')
def export_page():
    """Export landing page with date pickers."""
    return render_template('export_page.html')


@app.route('/review')
def review():
    """Phase 4 Human Review: tabs for Confirmed, Unreported, Unconfirmed, Reversals, Parse errors."""
    tab = request.args.get('tab', 'confirmed').strip().lower()
    if tab not in ('confirmed', 'unreported', 'unconfirmed', 'reversals', 'parse_errors'):
        tab = 'confirmed'

    with get_connection() as conn:
        property_row = get_current_property(conn)
        if not property_row:
            return redirect(url_for('property_list'))
        property_id = property_row['id']

        confirmed = []
        unreported = []
        unreported_single = []
        unreported_groups = []
        unconfirmed = []
        reversals = []

        if tab == 'confirmed':
            confirmed = conn.execute("""
                SELECT p.id, p.amount, p.payment_date, p.assignment_type, p.assignment_reason,
                       p.source, u.unit_number, t.name AS tenant_name,
                       COALESCE(bt.mpesa_ref, p.assignment_reason) as mpesa_ref, bt.txn_date
                FROM payments p
                JOIN units u ON p.unit_id = u.id
                LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
                LEFT JOIN bank_transactions bt ON p.bank_txn_id = bt.id
                WHERE p.property_id = ?
                ORDER BY p.payment_date DESC, p.id
            """, (property_id,)).fetchall()

        if tab == 'unreported':
            raw_unreported = conn.execute("""
                SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name, bt.unit_hint
                FROM bank_transactions bt
                JOIN bank_statements bs ON bt.statement_id = bs.id
                WHERE bs.property_id = ?
                AND bt.txn_type = 'PAYBILL_CREDIT'
                AND bt.id NOT IN (SELECT bank_txn_id FROM payments)
                ORDER BY bt.txn_date DESC
            """, (property_id,)).fetchall()

            # Preload active tenants with unit info for name matching
            tenant_rows = conn.execute("""
                SELECT t.id, t.name, t.unit_id, u.unit_number
                FROM tenants t
                JOIN units u ON t.unit_id = u.id
                WHERE t.property_id = ? AND t.status = 'active'
            """, (property_id,)).fetchall()

            def _name_tokens(s):
                if not s:
                    return []
                # Normalize: lowercase, replace non-alphanumeric with space, split on whitespace
                cleaned = re.sub(r'[^A-Za-z0-9]+', ' ', s).strip().lower()
                return [tok for tok in cleaned.split() if tok]

            tenant_index = []
            for t in tenant_rows:
                tokens = _name_tokens(t['name'])
                if len(tokens) < 2:
                    continue
                tenant_index.append(
                    {
                        'tenant_id': t['id'],
                        'unit_id': t['unit_id'],
                        'unit_number': t['unit_number'],
                        'name': t['name'],
                        'tokens': set(tokens),
                    }
                )

            unreported = []
            for row in raw_unreported:
                r = dict(row)

                # Tier 1: unit_hint-based suggestion
                unit_hint = (r.get('unit_hint') or '').strip()
                if unit_hint:
                    hint_key = unit_hint.upper()
                    matches = conn.execute("""
                        SELECT id, unit_number
                        FROM units
                        WHERE property_id = ?
                        AND UPPER(TRIM(unit_number)) = ?
                    """, (property_id, hint_key)).fetchall()
                    if len(matches) == 1:
                        match = matches[0]
                        r['suggested_unit_id'] = match['id']
                        r['suggested_unit_number'] = match['unit_number']
                        r['suggestion_source'] = 'unit_hint'

                # Tier 2: tenant name matching (only if no unit_hint suggestion)
                if not r.get('suggested_unit_id'):
                    sender_name = r.get('sender_name') or ''
                    sender_tokens = _name_tokens(sender_name)
                    if len(sender_tokens) >= 2 and tenant_index:
                        sender_token_set = set(sender_tokens)
                        for tenant in tenant_index:
                            common = sender_token_set & tenant['tokens']
                            if len(common) >= 2:
                                r['suggested_unit_id'] = tenant['unit_id']
                                r['suggested_unit_number'] = tenant['unit_number']
                                r['suggested_tenant_name'] = tenant['name']
                                r['suggestion_source'] = 'name_match'
                                break

                unreported.append(r)

            # Tier 3: group by sender_name (case-insensitive, trimmed)
            groups_by_sender = {}
            for r in unreported:
                sender_key = (r.get('sender_name') or '').strip().lower()
                if not sender_key:
                    # Treat rows without sender_name as their own singletons
                    sender_key = f"__no_sender__:{r['id']}"
                groups_by_sender.setdefault(sender_key, []).append(r)

            for sender_key, items in groups_by_sender.items():
                if len(items) == 1:
                    unreported_single.append(items[0])
                    continue
                dates = [it.get('txn_date') for it in items if it.get('txn_date')]
                date_min = min(dates) if dates else None
                date_max = max(dates) if dates else None
                total_amount = sum(float(it['amount']) for it in items if it.get('amount') is not None)
                group = {
                    'sender_name': items[0].get('sender_name') or '-',
                    'count': len(items),
                    'total_amount': total_amount,
                    'date_min': date_min,
                    'date_max': date_max,
                    'transactions': items,
                }
                unreported_groups.append(group)

            # Units list for group-assign dropdown
            group_units = conn.execute("""
                SELECT u.*, t.name AS tenant_name
                FROM units u
                LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
                WHERE u.property_id = ?
                ORDER BY u.unit_number
            """, (property_id,)).fetchall()
        else:
            group_units = []

        if tab == 'unconfirmed':
            unconfirmed = conn.execute("""
                SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.raw_message, pc.created_at, u.unit_number
                FROM payment_claims pc
                JOIN units u ON pc.unit_id = u.id
                WHERE pc.property_id = ? AND pc.status = 'pending'
                ORDER BY pc.created_at DESC
            """, (property_id,)).fetchall()

        if tab == 'reversals':
            reversals = conn.execute("""
                SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name, bt.raw_text
                FROM bank_transactions bt
                JOIN bank_statements bs ON bt.statement_id = bs.id
                WHERE bs.property_id = ? AND bt.txn_type = 'REVERSAL'
                ORDER BY bt.txn_date DESC
            """, (property_id,)).fetchall()

    return render_template(
        'review.html',
        property=property_row,
        tab=tab,
        confirmed=confirmed,
        unreported=unreported,
        unreported_single=unreported_single,
        unreported_groups=unreported_groups,
        group_units=group_units,
        unconfirmed=unconfirmed,
        reversals=reversals,
    )


@app.route('/onboard', methods=['GET', 'POST'])
def onboard():
    """Property onboarding - upload Excel with units/tenants."""
    if request.method == 'POST':
        property_name = request.form.get('property_name', '').strip()
        property_address = request.form.get('property_address', '').strip()

        if not property_name:
            flash('Property name is required.', 'error')
            return redirect(url_for('onboard'))

        file = request.files.get('file')
        if not file or file.filename == '':
            flash('Please select an Excel file.', 'error')
            return redirect(url_for('onboard'))

        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            flash('Please upload an Excel file (.xlsx or .xls)', 'error')
            return redirect(url_for('onboard'))

        # Save file temporarily
        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        file.save(temp_path)

        # Parse Excel
        result = parse_tenant_excel(temp_path)

        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)

        if not result['success']:
            for error in result['errors']:
                flash(error, 'error')
            return redirect(url_for('onboard'))

        if not result['rows']:
            flash('No valid rows found in Excel file.', 'error')
            return redirect(url_for('onboard'))

        # Store in session for preview
        session['onboard_data'] = {
            'property_name': property_name,
            'property_address': property_address,
            'rows': result['rows'],
            'total_arrears': result['total_arrears'],
            'warnings': result['warnings']
        }

        return redirect(url_for('onboard_preview'))

    return render_template('onboard.html')


@app.route('/onboard/preview', methods=['GET', 'POST'])
def onboard_preview():
    """Preview parsed data and confirm import."""
    data = session.get('onboard_data')
    if not data:
        flash('No onboarding data found. Please upload an Excel file.', 'error')
        return redirect(url_for('onboard'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'cancel':
            session.pop('onboard_data', None)
            flash('Onboarding cancelled.', 'info')
            return redirect(url_for('onboard'))

        if action == 'confirm':
            with get_connection() as conn:
                # Create property
                property_id = generate_id('PROP')
                conn.execute(
                    "INSERT INTO properties (id, name, address) VALUES (?, ?, ?)",
                    (property_id, data['property_name'], data['property_address'])
                )

                units_created = 0
                tenants_created = 0
                charges_created = 0

                for row in data['rows']:
                    # Create unit
                    unit_id = f"{property_id}-{row['unit_number']}"
                    if row.get('status') == 'office':
                        unit_status = 'office'
                    elif row['tenant_name']:
                        unit_status = 'occupied'
                    else:
                        unit_status = 'vacant'
                    conn.execute(
                        "INSERT INTO units (id, property_id, unit_number, monthly_rent, service_charge, apartment_size, status, status_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                        (unit_id, property_id, row['unit_number'], row['monthly_rent'], row.get('service_charge', 0), row.get('apartment_size', ''), unit_status)
                    )
                    units_created += 1

                    # Create tenant if name provided
                    if row['tenant_name']:
                        tenant_id = generate_id('TENANT')
                        conn.execute(
                            "INSERT INTO tenants (id, property_id, unit_id, name, phone, status) VALUES (?, ?, ?, ?, ?, ?)",
                            (tenant_id, property_id, unit_id, row['tenant_name'], row['contact'], 'active')
                        )
                        tenants_created += 1

                    # Create arrears charge if pending rent > 0
                    if row['pending_rent'] > 0:
                        charge_id = generate_id('CHG')
                        conn.execute(
                            "INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) VALUES (?, ?, ?, ?, 'rent', ?)",
                            (charge_id, property_id, unit_id, 'ARREARS', row['pending_rent'])
                        )
                        charges_created += 1

                # Audit log
                conn.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                    ('property_onboarded', 'property', property_id,
                     f"Onboarded {data['property_name']}: {units_created} units, {tenants_created} tenants, {charges_created} arrears charges, total arrears: {data['total_arrears']}", 'admin')
                )

            session['property_id'] = property_id
            session.pop('onboard_data', None)
            flash(f"Successfully onboarded {data['property_name']}: {units_created} units, {tenants_created} tenants.", 'success')
            return redirect(url_for('dashboard'))

    return render_template('onboard_preview.html', data=data)


@app.route('/tools')
def tools_index():
    """Tools index - parser testing (no DB persistence)."""
    return render_template('tools_index.html')


@app.route('/tools/test-pdf', methods=['GET', 'POST'])
def tools_test_pdf():
    """Test PDF parser - no DB persistence."""
    result = None
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('Please select a PDF file.', 'error')
            return redirect(url_for('tools_test_pdf'))
        if not file.filename.lower().endswith('.pdf'):
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('tools_test_pdf'))

        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        file.save(temp_path)

        try:
            result = parse_bank_statement(temp_path)
            # Convert Decimal for template
            if result.get('opening_balance') is not None:
                result['opening_balance'] = float(result['opening_balance'])
            if result.get('closing_balance') is not None:
                result['closing_balance'] = float(result['closing_balance'])
            if result.get('summary') and result['summary'].get('total_rent_amount') is not None:
                result['summary']['total_rent_amount'] = float(result['summary']['total_rent_amount'])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return render_template('tools_test_pdf.html', result=result)


@app.route('/tools/test-sms', methods=['GET', 'POST'])
def tools_test_sms():
    """Test SMS parser - no DB persistence."""
    result = None
    message = ''
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        if not message:
            flash('Please paste an M-Pesa message or reference code.', 'error')
            return redirect(url_for('tools_test_sms'))

        result = parse_mpesa_message(message)
        # Convert amount Decimal for template if present
        if result.get('amount') is not None:
            result = dict(result)
            result['amount'] = float(result['amount'])

    return render_template('tools_test_sms.html', result=result, message=message)


@app.route('/tools/test-excel', methods=['GET', 'POST'])
def tools_test_excel():
    """Test Excel parser - no DB persistence."""
    result = None
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('Please select an Excel file.', 'error')
            return redirect(url_for('tools_test_excel'))
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            flash('Please upload an Excel file (.xlsx or .xls).', 'error')
            return redirect(url_for('tools_test_excel'))

        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{filename}")
        file.save(temp_path)

        try:
            result = parse_tenant_excel(temp_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return render_template('tools_test_excel.html', result=result)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=True, port=port)

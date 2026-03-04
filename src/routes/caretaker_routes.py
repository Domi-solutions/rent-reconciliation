"""
Caretaker portal routes — live operational view.
Auth: CARETAKER_PASSWORD env var; session key 'caretaker_authenticated'.
"""
import os
from math import ceil

from flask import Blueprint, render_template, request, redirect, url_for, session, abort

from src.database.db import get_connection

caretaker_bp = Blueprint('caretaker', __name__, url_prefix='/caretaker')


@caretaker_bp.before_request
def require_caretaker_auth():
    exempt = ('caretaker.login', 'caretaker.logout')
    if request.endpoint in exempt:
        return None
    if not os.environ.get('CARETAKER_PASSWORD'):
        return None  # dev mode — open access
    if session.get('caretaker_authenticated'):
        return None
    return redirect(url_for('caretaker.login', next=request.url))


@caretaker_bp.route('/login', methods=['GET', 'POST'])
def login():
    password = os.environ.get('CARETAKER_PASSWORD')
    if not password:
        return redirect(url_for('caretaker.index'))
    error = None
    if request.method == 'POST':
        if request.form.get('password') == password:
            session['caretaker_authenticated'] = True
            next_url = request.args.get('next') or url_for('caretaker.index')
            return redirect(next_url)
        error = 'Incorrect password.'
    return render_template('caretaker/login.html', error=error)


@caretaker_bp.route('/logout')
def logout():
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

    return render_template('caretaker/dashboard.html',
                           property=prop,
                           occ=occ,
                           arrears=arrears[:5],        # top 5 on dashboard
                           arrears_total=len(arrears),
                           vacant_units=vacant_units,
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

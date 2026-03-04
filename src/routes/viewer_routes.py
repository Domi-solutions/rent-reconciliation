"""
View-only routes for property owners/stakeholders.
No write operations - read only access to property data.

Auth model:
  - Owner receives a shareable link:  /view/o/<token>
  - They enter their password on that page
  - Session stores owner_id; all routes filter by owner_id
  - /view/login shows a "use your private link" message for anyone
    who lands on /view without a valid session
"""
import json
from math import ceil

from flask import Blueprint, render_template, abort, request, redirect, session, url_for
from src.database.db import get_connection
from src.reports.landlord_report import enrich_report_data

viewer_bp = Blueprint('viewer', __name__, url_prefix='/view')


@viewer_bp.before_request
def require_viewer_auth():
    """Gate all /view/* routes. Exempt: login page, owner_login, logout."""
    exempt = ('viewer.login', 'viewer.owner_login', 'viewer.logout')
    if request.endpoint in exempt:
        return None
    if session.get('owner_id'):
        return None
    return redirect(url_for('viewer.login'))


@viewer_bp.route('/login')
def login():
    """Landing page when someone arrives at /view without a session.
    Tells them to use their private link."""
    session.pop('owner_id', None)
    session.pop('owner_token', None)
    return render_template('viewer/login.html')


@viewer_bp.route('/o/<token>', methods=['GET', 'POST'])
def owner_login(token):
    """Owner portal login: token in URL + password entered in form."""
    from werkzeug.security import check_password_hash
    with get_connection() as conn:
        owner = conn.execute(
            "SELECT id, name, password_hash FROM owners WHERE access_token = ?", (token,)
        ).fetchone()
    if not owner:
        return render_template('viewer/owner_login.html',
                               error='This link is invalid or has been revoked.',
                               token=token, owner_name=None)
    if request.method == 'POST':
        password = request.form.get('password', '')
        if not owner['password_hash']:
            return render_template('viewer/owner_login.html',
                                   error='Access not yet configured. Please contact your property manager.',
                                   token=token, owner_name=owner['name'])
        if check_password_hash(owner['password_hash'], password):
            session['owner_id'] = owner['id']
            session['owner_token'] = token
            return redirect(url_for('viewer.property_list'))
        return render_template('viewer/owner_login.html',
                               error='Incorrect password. Please try again.',
                               token=token, owner_name=owner['name'])
    return render_template('viewer/owner_login.html',
                           error=None, token=token, owner_name=owner['name'])


@viewer_bp.route('/logout')
def logout():
    """Clear owner session."""
    token = session.get('owner_token')
    session.pop('owner_id', None)
    session.pop('owner_token', None)
    if token:
        return redirect(url_for('viewer.owner_login', token=token))
    return redirect(url_for('viewer.login'))


@viewer_bp.route('/')
def property_list():
    """List all properties for owner selection. Auto-selects if only one exists."""
    with get_connection() as conn:
        properties = conn.execute(
            "SELECT id, name, address FROM properties WHERE status = 'active' ORDER BY name"
        ).fetchall()
    if len(properties) == 1:
        return redirect(url_for('viewer.property_dashboard', property_id=properties[0]['id']))
    return render_template('viewer/property_list.html', properties=properties)


@viewer_bp.route('/<property_id>')
def property_dashboard(property_id):
    """View-only property dashboard for owners."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()
        if not prop:
            abort(404)

        total_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ?", (property_id,)
        ).fetchone()[0]
        occupied_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'occupied'", (property_id,)
        ).fetchone()[0]
        vacant_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'vacant'", (property_id,)
        ).fetchone()[0]
        office_units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ? AND status = 'office'", (property_id,)
        ).fetchone()[0]

        rentable_units = total_units - office_units
        occupancy_rate = round(occupied_units / rentable_units * 100, 1) if rentable_units > 0 else 0

        income_row = conn.execute(
            "SELECT COALESCE(SUM(monthly_rent), 0) as rent, COALESCE(SUM(service_charge), 0) as svc FROM units WHERE property_id = ? AND status = 'occupied'",
            (property_id,)
        ).fetchone()
        total_monthly_rent = float(income_row['rent'])
        total_service_charge = float(income_row['svc'])
        total_expected = total_monthly_rent + total_service_charge

        arrears_row = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(balance), 0) as total FROM unit_balances WHERE property_id = ? AND balance > 0",
            (property_id,)
        ).fetchone()
        units_in_arrears = arrears_row['cnt']
        total_arrears = float(arrears_row['total'])

        total_collected = float(conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ?", (property_id,)
        ).fetchone()[0])
        total_charged = float(conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM rent_charges WHERE property_id = ?", (property_id,)
        ).fetchone()[0])

        # Pending claims for projected arrears
        pending_total = float(conn.execute(
            "SELECT COALESCE(SUM(claimed_amount), 0) FROM payment_claims WHERE property_id = ? AND status = 'pending'",
            (property_id,)
        ).fetchone()[0])

        # Arrears breakdown — top 4 units with proportional bars
        # Include pending payments per unit
        arrears_list = conn.execute("""
            SELECT 
                ub.unit_number, 
                ub.balance,
                COALESCE(pending.pending_amount, 0) as pending_amount
            FROM unit_balances ub
            LEFT JOIN (
                SELECT unit_id, SUM(claimed_amount) as pending_amount
                FROM payment_claims
                WHERE property_id = ? AND status = 'pending'
                GROUP BY unit_id
            ) pending ON pending.unit_id = ub.unit_id
            WHERE ub.property_id = ? AND ub.balance > 0
            ORDER BY ub.balance DESC
        """, (property_id, property_id)).fetchall()

        arrears_breakdown = []
        arrears_others_count = 0
        arrears_others_amount = 0
        arrears_others_pending = 0

        for i, a in enumerate(arrears_list):
            balance = float(a['balance'])
            pending_amount = float(a['pending_amount'] or 0)
            projected_balance = max(balance - pending_amount, 0)
            
            if i < 4:
                arrears_breakdown.append({
                    'unit_number': a['unit_number'],
                    'balance': balance,
                    'pending_amount': pending_amount,
                    'projected_balance': projected_balance,
                    'pct': round(balance / total_arrears * 100) if total_arrears > 0 else 0,
                })
            else:
                arrears_others_count += 1
                arrears_others_amount += balance
                arrears_others_pending += pending_amount

        arrears_others_pct = round(arrears_others_amount / total_arrears * 100) if total_arrears > 0 else 0
        arrears_others_projected = max(arrears_others_amount - arrears_others_pending, 0)

        stats = {
            'total_units': total_units,
            'occupied_units': occupied_units,
            'vacant_units': vacant_units,
            'office_units': office_units,
            'rentable_units': rentable_units,
            'occupancy_rate': occupancy_rate,
            'total_monthly_rent': total_monthly_rent,
            'total_service_charge': total_service_charge,
            'total_expected': total_expected,
            'total_arrears': total_arrears,
            'units_in_arrears': units_in_arrears,
            'total_collected': total_collected,
            'total_charged': total_charged,
            'pending_total': pending_total,
            'projected_arrears': max(total_arrears - pending_total, 0),
        }

        units = conn.execute("""
            SELECT u.unit_number, u.monthly_rent, u.service_charge, u.status,
                   t.name as tenant_name, t.phone as tenant_phone,
                   COALESCE(charges.total, 0) - COALESCE(payments_sum.total, 0) as balance
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) charges ON charges.unit_id = u.id
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) payments_sum ON payments_sum.unit_id = u.id
            WHERE u.property_id = ?
            ORDER BY u.unit_number
        """, (property_id,)).fetchall()

    return render_template('viewer/dashboard.html',
                          property=prop,
                          stats=stats,
                          units=units,
                          arrears_breakdown=arrears_breakdown,
                          arrears_others_count=arrears_others_count,
                          arrears_others_amount=arrears_others_amount,
                          arrears_others_pending=arrears_others_pending,
                          arrears_others_projected=arrears_others_projected,
                          arrears_others_pct=arrears_others_pct,
                          active_tab='overview')


@viewer_bp.route('/<property_id>/payments')
def property_payments(property_id):
    """View recent payments and pending claims for property."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)

        # Verified payments
        verified = conn.execute("""
            SELECT p.amount, p.payment_date as date, u.unit_number,
                   t.name as tenant_name, bt.mpesa_ref, 'verified' as status
            FROM payments p
            JOIN units u ON u.id = p.unit_id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN bank_transactions bt ON bt.id = p.bank_txn_id
            WHERE p.property_id = ?
            ORDER BY p.payment_date DESC
            LIMIT 50
        """, (property_id,)).fetchall()

        # Pending claims
        pending = conn.execute("""
            SELECT pc.claimed_amount as amount, pc.created_at as date,
                   u.unit_number, t.name as tenant_name, pc.mpesa_ref,
                   'pending' as status
            FROM payment_claims pc
            JOIN units u ON u.id = pc.unit_id
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            WHERE pc.property_id = ? AND pc.status = 'pending'
            ORDER BY pc.created_at DESC
        """, (property_id,)).fetchall()

        # Combine into dicts and sort by date descending
        all_payments = []
        for row in pending:
            all_payments.append(dict(row))
        for row in verified:
            all_payments.append(dict(row))
        all_payments.sort(key=lambda x: x['date'] or '', reverse=True)

        # Summary totals
        verified_total = sum(float(row['amount'] or 0) for row in verified)
        pending_total = sum(float(row['amount'] or 0) for row in pending)

    return render_template('viewer/payments.html',
                          property=prop,
                          payments=all_payments,
                          verified_total=verified_total,
                          pending_total=pending_total,
                          active_tab='payments')


@viewer_bp.route('/<property_id>/arrears')
def property_arrears(property_id):
    """View units with outstanding balances."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)

        arrears_rows = conn.execute("""
            SELECT
                u.id as unit_id,
                u.unit_number,
                u.monthly_rent,
                t.name as tenant_name,
                t.phone as tenant_phone,
                COALESCE(charges.total, 0) as total_charged,
                COALESCE(payments.total, 0) as total_paid,
                COALESCE(charges.total, 0) - COALESCE(payments.total, 0) as balance
            FROM units u
            LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM rent_charges GROUP BY unit_id) charges ON charges.unit_id = u.id
            LEFT JOIN (SELECT unit_id, SUM(amount) as total FROM payments GROUP BY unit_id) payments ON payments.unit_id = u.id
            WHERE u.property_id = ?
              AND COALESCE(charges.total, 0) - COALESCE(payments.total, 0) > 0
            ORDER BY balance DESC
        """, (property_id,)).fetchall()

        # Query pending claims per unit
        pending_by_unit = {}
        pending_rows = conn.execute("""
            SELECT unit_id, SUM(claimed_amount) as pending_amount, COUNT(*) as pending_count
            FROM payment_claims
            WHERE property_id = ? AND status = 'pending'
            GROUP BY unit_id
        """, (property_id,)).fetchall()
        for pr in pending_rows:
            pending_by_unit[pr['unit_id']] = {
                'amount': float(pr['pending_amount'] or 0),
                'count': pr['pending_count']
            }

        arrears = []
        for row in arrears_rows:
            d = dict(row)
            d['months_behind'] = ceil(d['balance'] / d['monthly_rent']) if d.get('monthly_rent') and d['monthly_rent'] > 0 else 0
            pending = pending_by_unit.get(d['unit_id'])
            d['pending_amount'] = pending['amount'] if pending else 0
            d['pending_count'] = pending['count'] if pending else 0
            d['projected_balance'] = max(d['balance'] - d['pending_amount'], 0)
            arrears.append(d)

        total_arrears = sum(a['balance'] for a in arrears)
        arrears_count = len(arrears)

        # Pending claims total for projected arrears
        pending_total = float(conn.execute(
            "SELECT COALESCE(SUM(claimed_amount), 0) FROM payment_claims WHERE property_id = ? AND status = 'pending'",
            (property_id,)
        ).fetchone()[0])
        projected_arrears = max(total_arrears - pending_total, 0)

    return render_template('viewer/arrears.html',
                          property=prop,
                          arrears=arrears,
                          total_arrears=total_arrears,
                          arrears_count=arrears_count,
                          pending_total=pending_total,
                          projected_arrears=projected_arrears,
                          active_tab='arrears')


@viewer_bp.route('/<property_id>/reports')
def property_reports(property_id):
    """List all stored reports for property."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)

        reports = conn.execute("""
            SELECT id, period_start, period_end, created_at, report_type
            FROM landlord_reports
            WHERE property_id = ?
            ORDER BY created_at DESC
        """, (property_id,)).fetchall()

        # Load report data for each to get headline numbers
        reports_with_summary = []
        for r in reports:
            report_data = json.loads(
                conn.execute(
                    "SELECT report_data FROM landlord_reports WHERE id = ?", (r['id'],)
                ).fetchone()[0]
            )
            reports_with_summary.append({
                'id': r['id'],
                'period_start': r['period_start'],
                'period_end': r['period_end'],
                'created_at': r['created_at'],
                'report_type': r['report_type'],
                'collection_rate': report_data.get('collection_rate', 0),
                'total_collected': report_data.get('collections', {}).get('total_verified', 0),
                'total_arrears': report_data.get('arrears', {}).get('total_arrears', 0),
            })

    return render_template('viewer/reports.html',
                          property=prop,
                          reports=reports_with_summary,
                          active_tab='reports')


@viewer_bp.route('/<property_id>/reports/<report_id>')
def viewer_report_detail(property_id, report_id):
    """View full report detail."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)

        report_row = conn.execute(
            "SELECT * FROM landlord_reports WHERE id = ? AND property_id = ?", 
            (report_id, property_id)
        ).fetchone()

        if not report_row:
            abort(404)

        report_data = json.loads(report_row['report_data'])
        enrich_report_data(
            report_data, conn,
            property_id,
            report_row['period_start'],
            report_row['period_end'],
        )

    return render_template('viewer/report_detail.html',
                          property=prop,
                          report=report_data,
                          period_start=report_row['period_start'],
                          period_end=report_row['period_end'],
                          created_at=report_row['created_at'],
                          active_tab='reports')

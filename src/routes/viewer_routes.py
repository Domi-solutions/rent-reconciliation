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
from datetime import datetime
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


@viewer_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Shared-password login for property viewer. Also serves as fallback landing page."""
    import os
    if request.method == 'POST':
        password = request.form.get('password', '')
        viewer_pw = os.environ.get('VIEWER_PASSWORD', '')
        if not viewer_pw or password == viewer_pw:
            # Set owner_id to first active owner so ownership checks pass
            with get_connection() as conn:
                owner = conn.execute(
                    "SELECT id FROM owners ORDER BY created_at LIMIT 1"
                ).fetchone()
                if owner:
                    session['owner_id'] = owner['id']
            next_url = request.form.get('next') or url_for('viewer.property_list')
            return redirect(next_url)
        return render_template('viewer/login.html', error='Incorrect password.')
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
    """List properties assigned to the logged-in owner. Auto-selects if only one exists."""
    owner_id = session.get('owner_id')
    with get_connection() as conn:
        properties = conn.execute("""
            SELECT p.id, p.name, p.address
            FROM properties p
            JOIN property_owners po ON po.property_id = p.id
            WHERE po.owner_id = ? AND p.status = 'active'
            ORDER BY p.name
        """, (owner_id,)).fetchall()
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
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

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

        _today = datetime.today()
        _month_start = _today.replace(day=1).strftime('%Y-%m-%d')
        _month_label = _today.strftime('%B %Y')                      # e.g. March 2026

        total_collected = float(conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ? AND payment_date >= ?",
            (property_id, _month_start)
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

        collection_rate = round(total_collected / total_expected * 100, 1) if total_expected > 0 else 0

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
            'collection_rate': collection_rate,
            'month_label': _month_label,
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

        # Recent activity preview: audit log + maintenance events merged
        _audit_recent = conn.execute("""
            SELECT timestamp, action, details FROM audit_log
            ORDER BY timestamp DESC LIMIT 20
        """).fetchall()
        _maint_recent = conn.execute("""
            SELECT m.created_at AS timestamp, 'maintenance_issue_raised' AS action,
                   COALESCE(u.unit_number, 'Common area') || ' — ' || m.title AS details
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            WHERE m.property_id = ?
            UNION ALL
            SELECT m.resolved_at AS timestamp, 'maintenance_issue_resolved' AS action,
                   COALESCE(u.unit_number, 'Common area') || ' — ' || m.title AS details
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            WHERE m.property_id = ? AND m.status = 'resolved' AND m.resolved_at IS NOT NULL
        """, (property_id, property_id)).fetchall()
        _combined = [dict(r) for r in _audit_recent] + [dict(r) for r in _maint_recent]
        _combined.sort(key=lambda x: x['timestamp'] or '', reverse=True)
        recent_activity = _combined[:5]

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
                          recent_activity=recent_activity,
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
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        # Verified payments (bank statement + Daraja/Pesapal)
        verified = conn.execute("""
            SELECT p.amount, p.payment_date as date, u.unit_number,
                   t.name as tenant_name,
                   COALESCE(bt.mpesa_ref, p.assignment_reason) as mpesa_ref,
                   p.source,
                   'verified' as status
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
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

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


@viewer_bp.route('/<property_id>/maintenance')
def property_maintenance(property_id):
    """Maintenance tab: open issues and recent resolved history for a property."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        open_issues = conn.execute(
            """
            SELECT m.*, u.unit_number, t.name AS tenant_name
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            LEFT JOIN tenants t ON m.raised_by_tenant_id = t.id
            WHERE m.property_id = ? AND m.status = 'open'
            ORDER BY m.created_at DESC
            """,
            (property_id,),
        ).fetchall()

        resolved_issues = conn.execute(
            """
            SELECT m.*, u.unit_number, t.name AS tenant_name
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            LEFT JOIN tenants t ON m.raised_by_tenant_id = t.id
            WHERE m.property_id = ?
              AND m.status = 'resolved'
              AND m.resolved_at >= datetime('now', '-30 days')
            ORDER BY m.resolved_at DESC, m.created_at DESC
            """,
            (property_id,),
        ).fetchall()

    return render_template(
        'viewer/maintenance.html',
        property=prop,
        open_issues=open_issues,
        resolved_issues=resolved_issues,
        active_tab='maintenance',
    )


def _structure_activity_entry(row, property_id):
    """Transform a raw audit/maintenance row into a structured feed entry dict."""
    entry = {
        'timestamp': row.get('timestamp', ''),
        'action': row.get('action', ''),
        'raw_details': row.get('details', '') or '',
        'user_id': row.get('user_id', '') or '',
        'entity_id': row.get('entity_id', '') or '',
        'link': None,
        'type_class': 'system',
        'label': '',
        'meta': '',
        'amount': None,
    }
    action = entry['action']
    details = entry['raw_details']
    entity_id = entry['entity_id']

    # Parse pipe-delimited details into a dict
    parts = {}
    for part in details.split(' | '):
        if ':' in part:
            k, _, v = part.partition(':')
            parts[k.strip()] = v.strip()

    if action == 'broadcast_sent':
        entry['type_class'] = 'message'
        count = parts.get('Recipients', '')
        ref = parts.get('Ref', '')
        channel = parts.get('Channel', 'portal')
        entry['label'] = f"Message sent to {count} tenant{'s' if count != '1' else ''}" if count else 'Message sent to tenants'
        entry['meta'] = f'"{ref}"' if ref else ''
        if entity_id:
            entry['link'] = f'/view/{property_id}/messages/{entity_id}'

    elif action == 'reminder_sent':
        entry['type_class'] = 'message'
        count_part = parts.get('tenants notified', '')
        # details like: "10-day reminder (10d before due) — 5 tenants notified | arrears"
        entry['label'] = details.split(' — ')[0] if ' — ' in details else 'Reminder sent'
        entry['meta'] = details.split(' — ')[1] if ' — ' in details else ''
        if entity_id:
            entry['link'] = f'/view/{property_id}/messages/{entity_id}'

    elif action in ('payment_manual_assigned', 'payment_auto_hint_assigned'):
        entry['type_class'] = 'payment'
        amount = parts.get('Amount', parts.get('KES', ''))
        unit = parts.get('Unit', '')
        ref = parts.get('Ref', '')
        if amount and unit:
            entry['label'] = f'{amount} → Unit {unit}'
        else:
            entry['label'] = 'Payment assigned'
        entry['meta'] = f'Ref: {ref}' if ref else ''
        entry['link'] = f'/view/{property_id}/payments'

    elif action == 'payments_verified':
        entry['type_class'] = 'payment'
        entry['label'] = details or 'Payments auto-verified'
        entry['link'] = f'/view/{property_id}/payments'

    elif action == 'payment_claim_submitted':
        entry['type_class'] = 'payment'
        entry['label'] = 'Payment claim submitted'
        entry['meta'] = details
        entry['link'] = f'/view/{property_id}/payments'

    elif action == 'charges_generated':
        entry['type_class'] = 'charge'
        period = parts.get('Period', '')
        entry['label'] = f'Charges generated{" — " + period if period else ""}'
        entry['meta'] = details

    elif action == 'water_charges_uploaded':
        entry['type_class'] = 'charge'
        entry['label'] = 'Water charges uploaded'
        entry['meta'] = details

    elif action == 'report_generated':
        entry['type_class'] = 'report'
        entry['label'] = 'Report generated'
        entry['meta'] = details
        if entity_id:
            entry['link'] = f'/view/{property_id}/reports/{entity_id}'

    elif action == 'maintenance_issue_raised':
        entry['type_class'] = 'maintenance'
        entry['label'] = 'Maintenance issue raised'
        entry['meta'] = details
        entry['link'] = f'/view/{property_id}/maintenance'

    elif action == 'maintenance_issue_resolved':
        entry['type_class'] = 'maintenance'
        entry['label'] = 'Maintenance issue resolved'
        entry['meta'] = details
        entry['link'] = f'/view/{property_id}/maintenance'

    else:
        fallback = {
            'export_generated': 'Export downloaded',
            'tenant_token_generated': 'Tenant portal link generated',
            'tenant_token_revoked': 'Tenant portal link revoked',
            'owner_token_generated': 'Owner portal link generated',
            'rent_due_day_updated': 'Rent due day updated',
            'property_created': 'Property created',
            'property_unassigned': 'Property unassigned from owner',
        }
        entry['label'] = fallback.get(action, action.replace('_', ' ').title())
        entry['meta'] = details

    return entry


@viewer_bp.route('/<property_id>/notifications')
def property_notifications(property_id):
    """Owner notification inbox — all messages stored for this owner/property."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        notifications = conn.execute("""
            SELECT id, subject, body, template_body, message_type,
                   channel, recipient_count, sent_by, read_at, created_at
            FROM owner_messages
            WHERE property_id = ? AND owner_id = ?
            ORDER BY created_at DESC
        """, (property_id, owner_id)).fetchall()

        unread_count = sum(1 for n in notifications if not n['read_at'])

        # Mark all as read
        conn.execute(
            "UPDATE owner_messages SET read_at = CURRENT_TIMESTAMP WHERE property_id = ? AND owner_id = ? AND read_at IS NULL",
            (property_id, owner_id)
        )

    return render_template(
        'viewer/notifications.html',
        property=prop,
        notifications=notifications,
        unread_count=unread_count,
        active_tab='notifications',
    )


@viewer_bp.route('/<property_id>/activity')
def property_activity(property_id):
    """Full unified activity feed — audit events + maintenance timeline."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        audit_entries = conn.execute("""
            SELECT timestamp, action, details, user_id, entity_id
            FROM audit_log
            ORDER BY timestamp DESC
            LIMIT 200
        """).fetchall()

        maint_entries = conn.execute("""
            SELECT
                m.created_at AS timestamp,
                'maintenance_issue_raised' AS action,
                COALESCE(u.unit_number, 'Common area') || ' — ' || m.title AS details,
                CASE WHEN m.source = 'tenant' THEN t.name ELSE 'Caretaker' END AS user_id,
                m.id AS entity_id
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            LEFT JOIN tenants t ON m.raised_by_tenant_id = t.id
            WHERE m.property_id = ?
            UNION ALL
            SELECT
                m.resolved_at AS timestamp,
                'maintenance_issue_resolved' AS action,
                COALESCE(u.unit_number, 'Common area') || ' — ' || m.title AS details,
                'Caretaker' AS user_id,
                m.id AS entity_id
            FROM maintenance_issues m
            LEFT JOIN units u ON m.unit_id = u.id
            WHERE m.property_id = ? AND m.status = 'resolved' AND m.resolved_at IS NOT NULL
        """, (property_id, property_id)).fetchall()

        raw = [dict(r) for r in audit_entries] + [dict(r) for r in maint_entries]
        raw.sort(key=lambda x: x['timestamp'] or '', reverse=True)
        entries = [_structure_activity_entry(r, property_id) for r in raw[:200]]

    return render_template(
        'viewer/activity.html',
        property=prop,
        entries=entries,
        active_tab='activity',
    )


@viewer_bp.route('/<property_id>/messages/<batch_id>')
def property_message_detail(property_id, batch_id):
    """Detail view for a broadcast or reminder message batch."""
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        first_msg = conn.execute("""
            SELECT subject, body, template_body, delivery_channel, message_type, created_at
            FROM messages WHERE batch_id = ? LIMIT 1
        """, (batch_id,)).fetchone()
        if not first_msg:
            abort(404)

        # For broadcasts (>1 recipient), get the original reference label from audit log
        # so we don't show a personalized subject with unfilled placeholders
        audit_row = conn.execute("""
            SELECT details FROM audit_log
            WHERE entity_id = ? AND action IN ('broadcast_sent', 'reminder_sent')
            LIMIT 1
        """, (batch_id,)).fetchone()
        broadcast_label = None
        if audit_row:
            for part in audit_row['details'].split(' | '):
                if part.strip().startswith('Ref:') or part.strip().startswith('Subject:'):
                    broadcast_label = part.split(':', 1)[1].strip()
                    break

        recipients = conn.execute("""
            SELECT t.name AS tenant_name, u.unit_number,
                   m.delivery_status, m.read_at, m.delivered_at
            FROM messages m
            JOIN tenants t ON m.tenant_id = t.id
            LEFT JOIN units u ON t.unit_id = u.id
            WHERE m.batch_id = ?
            ORDER BY u.unit_number
        """, (batch_id,)).fetchall()

    return render_template(
        'viewer/message_detail.html',
        property=prop,
        message=first_msg,
        broadcast_label=broadcast_label,
        recipients=recipients,
        recipient_count=len(recipients),
        active_tab='activity',
    )


@viewer_bp.route('/<property_id>/reports')
def property_reports(property_id):
    """List all stored reports for property."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()

        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

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

        disbursements = conn.execute("""
            SELECT period, total_collected, fee_rate, fee_amount, net_amount,
                   status, disbursed_at, created_at
            FROM disbursements
            WHERE property_id = ?
            ORDER BY period DESC
        """, (property_id,)).fetchall()
        disbursements = [dict(d) for d in disbursements]

    return render_template('viewer/reports.html',
                          property=prop,
                          reports=reports_with_summary,
                          disbursements=disbursements,
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
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

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


@viewer_bp.route('/<property_id>/wallet')
def property_wallet(property_id):
    """Owner wallet — balance, management fee, disbursement history."""
    with get_connection() as conn:
        prop = conn.execute(
            "SELECT * FROM properties WHERE id = ?", (property_id,)
        ).fetchone()
        if not prop:
            abort(404)
        owner_id = session.get('owner_id')
        access = conn.execute(
            "SELECT 1 FROM property_owners WHERE property_id = ? AND owner_id = ?",
            (property_id, owner_id)
        ).fetchone()
        if not access:
            abort(403)

        fee_rate = float(prop['management_fee_rate'] or 0.08)

        total_collected = float(conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ?",
            (property_id,)
        ).fetchone()[0])

        total_disbursed = float(conn.execute(
            "SELECT COALESCE(SUM(net_amount), 0) FROM disbursements WHERE property_id = ? AND status = 'completed'",
            (property_id,)
        ).fetchone()[0])

        fee_amount = round(total_collected * fee_rate, 2)
        balance = round(total_collected - fee_amount - total_disbursed, 2)

        disbursements = conn.execute(
            """SELECT * FROM disbursements WHERE property_id = ?
               ORDER BY created_at DESC LIMIT 50""",
            (property_id,)
        ).fetchall()

        this_month = __import__('datetime').date.today().strftime('%Y-%m')
        month_collected = float(conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE property_id = ? AND strftime('%Y-%m', payment_date) = ?",
            (property_id, this_month)
        ).fetchone()[0])

    return render_template(
        'viewer/wallet.html',
        property=prop,
        active_tab='wallet',
        total_collected=total_collected,
        fee_rate=fee_rate,
        fee_amount=fee_amount,
        balance=balance,
        total_disbursed=total_disbursed,
        disbursements=disbursements,
        month_collected=month_collected,
    )

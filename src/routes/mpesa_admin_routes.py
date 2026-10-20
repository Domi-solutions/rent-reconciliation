"""
M-Pesa payments received into the property's own paybill — admin view.

Lists what Safaricom sent and what became of it (booked, waiting for Safaricom,
failed, duplicate). A paybill payment whose account number named nobody is held
as 'unmatched' until an admin names the tenant here; it is then booked like any
other. Exempt from nothing: admin auth applies.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from src.database.db import get_connection
from src.platform.guardian import notify_owner_change, platform_log, raise_alert
from src.payments import daraja, mpesa
from src.utils import staff

mpesa_admin_bp = Blueprint('mpesa_admin', __name__, url_prefix='/mpesa')


def unmatched_count(conn, org_id):
    """Paybill payments waiting for a tenant name — for the sidebar badge."""
    return conn.execute("""
        SELECT COUNT(*) FROM payment_transactions
        WHERE processing_status = 'unmatched' AND org_id = ?
    """, (org_id,)).fetchone()[0]


@mpesa_admin_bp.route('')
def payments():
    org_id = session.get('org_id')
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT pt.*, t.name AS tenant_name, u.unit_number
            FROM payment_transactions pt
            LEFT JOIN tenants t ON t.id = pt.tenant_id
            LEFT JOIN units u ON u.id = pt.unit_id
            WHERE pt.source IN ('daraja', 'c2b')
              AND pt.org_id = ?
            ORDER BY pt.processing_status = 'unmatched' DESC, pt.received_at DESC
            LIMIT 200
        """, (org_id,)).fetchall()
        tenants = conn.execute("""
            SELECT t.id, t.name, t.status, u.unit_number, p.name AS property_name
            FROM tenants t JOIN units u ON u.id = t.unit_id JOIN properties p ON p.id = u.property_id
            WHERE t.status IN ('active', 'departed') AND p.organization_id IS ?
            ORDER BY p.name, u.unit_number, t.status
        """, (org_id,)).fetchall()
        prop = conn.execute("SELECT * FROM properties WHERE id = ? AND organization_id IS ?",
                            (session.get('property_id'), org_id)).fetchone()
    return render_template('mpesa_payments.html', rows=rows, tenants=tenants, property=prop,
                           live=daraja.configured(), active_nav='payments')


@mpesa_admin_bp.route('/instructions', methods=['POST'])
def save_instructions():
    """Where tenants are told to pay. This redirects every tenant's rent, so a
    change is logged where the agency cannot edit it, raised to the platform and
    SMSed to the owners — the same treatment as an owner's payout number."""
    paybill = ''.join(ch for ch in request.form.get('pay_paybill', '') if ch.isdigit())
    account = ' '.join(request.form.get('pay_account', '').split())
    name = ' '.join(request.form.get('pay_account_name', '').split())
    with get_connection() as conn:
        prop = conn.execute("SELECT * FROM properties WHERE id = ? AND organization_id IS ?",
                            (session.get('property_id'), session.get('org_id'))).fetchone()
        if not prop:
            flash('Select a property first.', 'error')
            return redirect(url_for('mpesa_admin.payments'))
        if not (5 <= len(paybill) <= 7) or not account:
            flash('Enter the paybill number (5–7 digits) and the account number.', 'error')
            return redirect(url_for('mpesa_admin.payments'))
        old = (prop['pay_paybill'], prop['pay_account'], prop['pay_account_name'])
        if old == (paybill, account, name):
            return redirect(url_for('mpesa_admin.payments'))
        conn.execute("UPDATE properties SET pay_paybill = ?, pay_account = ?, pay_account_name = ? WHERE id = ?",
                     (paybill, account, name, prop['id']))
        details = (f"Paybill {old[0] or '-'} / {old[1] or '-'} → {paybill} / {account}"
                   + (f" ({name})" if name else ''))
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('payment_instructions_changed', 'property', prop['id'], details, staff.actor()))
        platform_log(conn, 'payment_instructions_changed', 'property', prop['id'], details,
                     org_id=session.get('org_id'), property_id=prop['id'], actor=staff.actor())
        if old[0]:      # first-time setup is not an alert; a change of destination is
            raise_alert(conn, 'payment_instructions_changed', details, org_id=session.get('org_id'),
                        property_id=prop['id'], severity='critical')
            notify_owner_change(conn, prop['id'], 'Rent payment details changed',
                                f"{prop['name'].title()}: tenants are now told to pay to Paybill {paybill}, "
                                f"account {account}. If you did not expect this, contact Domi.")
    flash('Payment details saved. Every tenant now sees these on their Pay page.', 'success')
    return redirect(url_for('mpesa_admin.payments'))


@mpesa_admin_bp.route('/<txn_id>/assign', methods=['POST'])
def assign(txn_id):
    tenant_id = request.form.get('tenant_id')
    with get_connection() as conn:
        t = conn.execute("""
            SELECT t.name FROM tenants t JOIN properties p ON p.id = t.property_id
            WHERE t.id = ? AND p.organization_id IS ?
        """, (tenant_id, session.get('org_id'))).fetchone()
        if t and mpesa.assign_unmatched(conn, txn_id, tenant_id, session.get('org_id')):
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('mpesa_payment_assigned', 'payment_transaction', txn_id, f"Named tenant: {t['name']}", staff.actor()))
            flash(f"Assigned to {t['name']}. It will be booked within a minute and they get an SMS.", 'success')
        else:
            flash('Could not assign that payment.', 'error')
    return redirect(url_for('mpesa_admin.payments'))

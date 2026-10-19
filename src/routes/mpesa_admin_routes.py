"""
M-Pesa payments received into the property's own paybill — admin view.

Lists what Safaricom sent and what became of it (booked, waiting for Safaricom,
failed, duplicate). A paybill payment whose account number named nobody is held
as 'unmatched' until an admin names the tenant here; it is then booked like any
other. Exempt from nothing: admin auth applies.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from src.database.db import get_connection
from src.payments import daraja, mpesa
from src.utils import staff

mpesa_admin_bp = Blueprint('mpesa_admin', __name__, url_prefix='/mpesa')


def unmatched_count(conn, org_id):
    """Paybill payments waiting for a tenant name — for the sidebar badge."""
    return conn.execute("""
        SELECT COUNT(*) FROM payment_transactions
        WHERE processing_status = 'unmatched'
          AND (property_id IS NULL OR property_id IN (SELECT id FROM properties WHERE organization_id IS ?))
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
              AND (pt.property_id IS NULL OR pt.property_id IN (SELECT id FROM properties WHERE organization_id IS ?))
            ORDER BY pt.processing_status = 'unmatched' DESC, pt.received_at DESC
            LIMIT 200
        """, (org_id,)).fetchall()
        tenants = conn.execute("""
            SELECT t.id, t.name, t.status, u.unit_number, p.name AS property_name
            FROM tenants t JOIN units u ON u.id = t.unit_id JOIN properties p ON p.id = u.property_id
            WHERE t.status IN ('active', 'departed') AND p.organization_id IS ?
            ORDER BY p.name, u.unit_number, t.status
        """, (org_id,)).fetchall()
    return render_template('mpesa_payments.html', rows=rows, tenants=tenants,
                           live=daraja.configured(), active_nav='payments')


@mpesa_admin_bp.route('/<txn_id>/assign', methods=['POST'])
def assign(txn_id):
    tenant_id = request.form.get('tenant_id')
    with get_connection() as conn:
        t = conn.execute("""
            SELECT t.name FROM tenants t JOIN properties p ON p.id = t.property_id
            WHERE t.id = ? AND p.organization_id IS ?
        """, (tenant_id, session.get('org_id'))).fetchone()
        if t and mpesa.assign_unmatched(conn, txn_id, tenant_id):
            conn.execute(
                "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                ('mpesa_payment_assigned', 'payment_transaction', txn_id, f"Named tenant: {t['name']}", staff.actor()))
            flash(f"Assigned to {t['name']}. It will be booked within a minute and they get an SMS.", 'success')
        else:
            flash('Could not assign that payment.', 'error')
    return redirect(url_for('mpesa_admin.payments'))

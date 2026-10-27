"""Lease intake — /leases (admin). Logic lives in src/leases/intake.py."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_from_directory, session, url_for

from src.database.db import get_connection
from src.leases import intake
from src.utils import staff

lease_bp = Blueprint('leases', __name__, url_prefix='/leases')


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
            prop = conn.execute("SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


@lease_bp.route('/')
def board():
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        rows, counts = intake.unit_board(conn, prop['id'])
    return render_template('leases/board.html', property=prop, rows=rows, counts=counts,
                           phase=request.args.get('phase', ''))


@lease_bp.route('/new', methods=['GET', 'POST'])
def new():
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        unit_id = request.values.get('unit_id', '')
        unit = conn.execute("SELECT * FROM units WHERE id = ? AND property_id = ?",
                            (unit_id, prop['id'])).fetchone()
        if not unit:
            flash('Choose a unit from the list.', 'error')
            return redirect(url_for('leases.board'))
        current = conn.execute("SELECT * FROM tenants WHERE unit_id = ? AND status = 'active'",
                               (unit['id'],)).fetchone()
        prior = intake.prior_account(conn, current['id']) if current else None

        if request.method == 'POST':
            form = request.form.to_dict()
            try:
                lease_id, _ = intake.record_lease(conn, prop, form, staff.actor())
            except intake.LeaseError as e:
                flash(str(e), 'error')
                return render_template('leases/form.html', property=prop, unit=unit, current=current,
                                       prior=prior, form=form, kinds=intake.DOCUMENT_KINDS)
            saved = rejected = 0
            for kind, _label in intake.DOCUMENT_KINDS:
                s, r = intake.add_documents(conn, lease_id, request.files.getlist(f'doc_{kind}'),
                                            kind, staff.actor())
                saved, rejected = saved + s, rejected + r
            msg = f"Lease recorded for Unit {unit['unit_number']}."
            if saved:
                msg += f' {saved} document(s) attached.'
            flash(msg, 'success')
            if rejected:
                flash(f'{rejected} file(s) were not a PDF or photo and were not attached.', 'warning')
            return redirect(url_for('leases.detail', lease_id=lease_id))

        form = {
            'who': 'same' if current else '',
            'name': current['name'] if current else '',
            'phone': current['phone'] if current else '',
            'monthly_rent': f"{unit['monthly_rent'] or 0:.0f}",
            'service_charge': f"{unit['service_charge'] or 0:.0f}",
            'deposit_required': f"{unit['monthly_rent'] or 0:.0f}",
            'deposit_held': f"{(current['deposit_paid'] if current else 0) or 0:.0f}",
            'start_date': _next_month_start().isoformat(),
            'brought_forward': '0',
        }
    return render_template('leases/form.html', property=prop, unit=unit, current=current, prior=prior,
                           form=form, kinds=intake.DOCUMENT_KINDS)


def _next_month_start():
    today = date.today()
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def _lease_or_404(conn, lease_id):
    prop = get_current_property(conn)
    if not prop:
        abort(404)
    lease = intake.get_lease(conn, lease_id, prop['id'])
    if not lease:
        abort(404)
    return prop, lease


@lease_bp.route('/<lease_id>')
def detail(lease_id):
    with get_connection() as conn:
        prop, lease = _lease_or_404(conn, lease_id)
        documents = conn.execute("SELECT * FROM lease_documents WHERE lease_id = ? ORDER BY created_at",
                                 (lease_id,)).fetchall()
        prior = intake.prior_account(conn, lease['prior_tenant_id']) if lease['prior_tenant_id'] else None
        balance = conn.execute("SELECT balance FROM tenant_balances WHERE tenant_id = ?",
                               (lease['tenant_id'],)).fetchone()
    return render_template('leases/detail.html', property=prop, lease=lease, documents=documents,
                           prior=prior, balance=balance['balance'] if balance else 0,
                           kinds=intake.DOCUMENT_KINDS, today=date.today().isoformat())


@lease_bp.route('/<lease_id>/signed', methods=['POST'])
def signed(lease_id):
    with get_connection() as conn:
        _, lease = _lease_or_404(conn, lease_id)
        try:
            intake.mark_signed(conn, lease, request.form.get('signed_on'), request.form.get('national_id'),
                               staff.actor())
        except intake.LeaseError as e:
            flash(str(e), 'error')
            return redirect(url_for('leases.detail', lease_id=lease_id))
        intake.add_documents(conn, lease_id, request.files.getlist('doc_lease'), 'lease', staff.actor())
    flash('Marked as signed.', 'success')
    return redirect(url_for('leases.detail', lease_id=lease_id))


@lease_bp.route('/<lease_id>/documents', methods=['POST'])
def documents(lease_id):
    with get_connection() as conn:
        _, lease = _lease_or_404(conn, lease_id)
        saved, rejected = intake.add_documents(conn, lease_id, request.files.getlist('files'),
                                               request.form.get('kind', 'other'), staff.actor())
        if saved:
            conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) "
                         "VALUES (?, ?, ?, ?, ?)",
                         ('lease_document_added', 'lease', lease_id,
                          f"Unit {lease['unit_number']} | {saved} {request.form.get('kind', 'other')} file(s)",
                          staff.actor()))
    if saved:
        flash(f'{saved} document(s) attached.', 'success')
    if rejected:
        flash(f'{rejected} file(s) were not a PDF or photo and were not attached.', 'warning')
    return redirect(url_for('leases.detail', lease_id=lease_id))


@lease_bp.route('/<lease_id>/brought-forward', methods=['POST'])
def brought_forward(lease_id):
    with get_connection() as conn:
        prop, lease = _lease_or_404(conn, lease_id)
        try:
            intake.set_brought_forward(conn, lease, prop, request.form.get('amount'),
                                       request.form.get('evidence'), staff.actor())
        except intake.LeaseError as e:
            flash(str(e), 'error')
            return redirect(url_for('leases.detail', lease_id=lease_id))
    flash('Brought-forward amount updated.', 'success')
    return redirect(url_for('leases.detail', lease_id=lease_id))


@lease_bp.route('/<lease_id>/documents/<doc_id>')
def document_file(lease_id, doc_id):
    """Admin-only: ID copies and signed leases are never served from static/."""
    with get_connection() as conn:
        _lease_or_404(conn, lease_id)
        doc = conn.execute("SELECT * FROM lease_documents WHERE id = ? AND lease_id = ?",
                           (doc_id, lease_id)).fetchone()
    if not doc:
        abort(404)
    resp = send_from_directory(intake.documents_dir(), doc['file_name'], mimetype=doc['content_type'])
    resp.headers['Cache-Control'] = 'private, no-store'
    return resp

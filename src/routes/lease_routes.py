"""Lease intake — /leases (admin). Logic lives in src/leases/intake.py."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_from_directory, session, url_for

from src.database.db import get_connection
from src.leases import intake, move_out, terms as lease_terms
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
        moving_out = move_out.open_count(conn, prop['id'])
        renewals_due = len(lease_terms.ending_soon(conn, prop['id'])) + len(lease_terms.reviews_due(conn, prop['id']))
    return render_template('leases/board.html', property=prop, rows=rows, counts=counts,
                           phase=request.args.get('phase', ''), moving_out=moving_out, renewals_due=renewals_due)


@lease_bp.route('/moving-out', methods=['GET', 'POST'])
def moving_out():
    """Notices to move out that tenants gave in their portal (src/leases/move_out.py)."""
    from src.utils.phone import normalize_to_e164
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        if request.method == 'POST':
            notice_id = request.form.get('notice_id', '')
            move_out.acknowledge(conn, notice_id, prop['id'], staff.actor())
            conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                         ('move_out_notice_seen', 'move_out_notice', notice_id, 'Marked as seen', staff.actor()))
            flash('Marked as seen — the tenant sees this on their Moving out page.', 'success')
            return redirect(url_for('leases.moving_out'))
        rows = move_out.for_property(conn, prop['id'])
        for r in rows:
            r['refund_other'] = bool(r['refund_phone'] and r['refund_phone'] != normalize_to_e164(r['tenant_phone'] or ''))
            r['estimate'] = move_out.estimate(r['deposit_paid'], r['balance'])
    return render_template('leases/moving_out.html', property=prop, rows=rows)


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
        rent_changes = conn.execute("SELECT * FROM lease_rent_changes WHERE lease_id = ? ORDER BY created_at",
                                    (lease_id,)).fetchall()
    return render_template('leases/detail.html', property=prop, lease=lease, documents=documents,
                           prior=prior, balance=balance['balance'] if balance else 0,
                           kinds=intake.DOCUMENT_KINDS, today=date.today().isoformat(),
                           term_types=lease_terms.TERM_TYPES, decisions=lease_terms.DECISIONS,
                           next_review=lease_terms.next_review(lease), rent_changes=rent_changes)


@lease_bp.route('/<lease_id>/terms', methods=['POST'])
def terms(lease_id):
    with get_connection() as conn:
        prop, lease = _lease_or_404(conn, lease_id)
        try:
            start = date.fromisoformat(lease['start_date'][:10]) if lease['start_date'] else None
            lease_terms.save(conn, lease, lease_terms.parse(request.form, start), staff.actor(), prop)
        except lease_terms.TermsError as e:
            flash(str(e), 'error')
            return redirect(url_for('leases.detail', lease_id=lease_id))
    flash('Lease terms saved.', 'success')
    return redirect(url_for('leases.detail', lease_id=lease_id))


@lease_bp.route('/<lease_id>/documents/<doc_id>/visibility', methods=['POST'])
def document_visibility(lease_id, doc_id):
    """Let the tenant see a lease document (never an ID copy), or hide it again."""
    visible = 1 if request.form.get('visible') == '1' else 0
    with get_connection() as conn:
        _, lease = _lease_or_404(conn, lease_id)
        doc = conn.execute("SELECT kind FROM lease_documents WHERE id = ? AND lease_id = ?", (doc_id, lease_id)).fetchone()
        if doc and doc['kind'] != 'id_copy':
            conn.execute("UPDATE lease_documents SET tenant_visible = ? WHERE id = ?", (visible, doc_id))
            conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                         ('lease_document_shared' if visible else 'lease_document_hidden', 'lease', lease_id,
                          f"Unit {lease['unit_number']} | document {doc_id}", staff.actor()))
    return redirect(url_for('leases.detail', lease_id=lease_id))


@lease_bp.route('/renewals', methods=['GET', 'POST'])
def renewals():
    """Fixed-term leases ending soon, and rent reviews due (src/leases/terms.py)."""
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        if request.method == 'POST':
            lease = intake.get_lease(conn, request.form.get('lease_id', ''), prop['id'])
            try:
                if not lease:
                    raise lease_terms.TermsError('Lease not found.')
                if request.form.get('action') == 'decide':
                    lease_terms.decide_ending(conn, lease, request.form.get('decision'), staff.actor(),
                                              request.form.get('new_end'))
                    flash('Saved. The tenant sees a notice about their lease.', 'success')
                elif request.form.get('action') == 'rent':
                    lease['unit_rent'] = conn.execute("SELECT monthly_rent FROM units WHERE id = ?",
                                                      (lease['unit_id'],)).fetchone()[0]
                    lease_terms.schedule_rent_change(conn, lease, prop, request.form.get('new_rent'),
                                                     request.form.get('effective_period'), staff.actor(),
                                                     request.form.get('reason'))
                    flash('New rent confirmed. The tenant sees a notice now; it is billed from that month.', 'success')
            except lease_terms.TermsError as e:
                flash(str(e), 'error')
            return redirect(url_for('leases.renewals'))
        ending = lease_terms.ending_soon(conn, prop['id'])
        reviews = lease_terms.reviews_due(conn, prop['id'])
    return render_template('leases/renewals.html', property=prop, ending=ending, reviews=reviews,
                           decisions=lease_terms.DECISIONS)


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

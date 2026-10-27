"""Money out (admin) — /money: expenses, approvals, payments to the owner, cash book.
Logic in src/money/expenses.py."""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_from_directory, session, url_for

from src.database.db import get_connection
from src.money import expenses as ex
from src.utils import staff

money_bp = Blueprint('money', __name__, url_prefix='/money')


def get_current_property(conn):
    """Current property from session, scoped to session org if set."""
    pid = session.get('property_id')
    if pid:
        org_id = session.get('org_id')
        if org_id:
            prop = conn.execute("SELECT * FROM properties WHERE id = ? AND status = 'active' AND organization_id = ?",
                                (pid, org_id)).fetchone()
        else:
            prop = conn.execute("SELECT * FROM properties WHERE id = ? AND status = 'active'", (pid,)).fetchone()
        if prop:
            return prop
    session.pop('property_id', None)
    return None


@money_bp.route('/expenses')
def expenses():
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        status = request.args.get('status') or None
        rows = ex.listing(conn, prop['id'], status=status)
        waiting = ex.waiting_count(conn, prop['id'])
    return render_template('money/expenses.html', property=prop, rows=rows, status=status, waiting=waiting,
                           statuses=ex.STATUS_LABELS)


@money_bp.route('/expenses/new', methods=['GET', 'POST'])
def new_expense():
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        form = request.form.to_dict() if request.method == 'POST' else {
            'expense_date': date.today().isoformat(), 'already_paid': '1',
            'category': request.args.get('category', '')}
        if request.method == 'POST':
            try:
                eid, saved, rejected = ex.record(conn, prop, form, staff.actor(), by='admin',
                                                 uploads=request.files.getlist('receipts'))
            except ex.ExpenseError as e:
                flash(str(e), 'error')
                return render_template('money/expense_new.html', property=prop, form=form,
                                       categories=ex.CATEGORIES, methods=ex.METHODS)
            if rejected:
                flash(f'{rejected} file(s) were not a PDF or photo and were not attached.', 'warning')
            flash('Expense recorded.', 'success')
            return redirect(url_for('money.expense', expense_id=eid))
    return render_template('money/expense_new.html', property=prop, form=form, categories=ex.CATEGORIES,
                           methods=ex.METHODS)


def _expense_or_404(conn, expense_id):
    prop = get_current_property(conn)
    e = ex.get(conn, expense_id, prop['id']) if prop else None
    if not e:
        abort(404)
    return prop, e


@money_bp.route('/expenses/<expense_id>')
def expense(expense_id):
    with get_connection() as conn:
        prop, e = _expense_or_404(conn, expense_id)
    return render_template('money/expense_detail.html', property=prop, e=e, methods=ex.METHODS)


@money_bp.route('/expenses/<expense_id>/<action>', methods=['POST'])
def expense_action(expense_id, action):
    with get_connection() as conn:
        prop, e = _expense_or_404(conn, expense_id)
        try:
            if action == 'approve':
                ex.decide(conn, e, True, request.form.get('note'), staff.actor())
                msg = 'Approved.' + (' Mark it paid once the caretaker has been paid back.' if e['reimburse_caretaker'] else '')
            elif action == 'reject':
                ex.decide(conn, e, False, request.form.get('note'), staff.actor())
                msg = 'Rejected.'
            elif action == 'pay':
                ex.mark_paid(conn, e, request.form.get('method'), request.form.get('reference'), staff.actor())
                msg = 'Marked paid.'
            elif action == 'void':
                ex.void(conn, e, request.form.get('reason'), staff.actor())
                msg = 'Voided. It stays on record but no longer counts.'
            else:
                abort(404)
        except ex.ExpenseError as err:
            flash(str(err), 'error')
            return redirect(url_for('money.expense', expense_id=expense_id))
    flash(msg, 'success')
    return redirect(url_for('money.expense', expense_id=expense_id))


@money_bp.route('/expenses/<expense_id>/receipt/<receipt_id>')
def receipt(expense_id, receipt_id):
    with get_connection() as conn:
        _, e = _expense_or_404(conn, expense_id)
    r = next((r for r in e['receipts'] if r['id'] == receipt_id), None)
    if not r:
        abort(404)
    resp = send_from_directory(ex.receipts_dir(), r['file_name'], mimetype=r['content_type'])
    resp.headers['Cache-Control'] = 'private, no-store'
    return resp


@money_bp.route('/cashbook')
def cashbook():
    with get_connection() as conn:
        prop = get_current_property(conn)
        if not prop:
            return redirect(url_for('property_list'))
        months, totals = ex.cash_book(conn, prop['id'])
    return render_template('money/cashbook.html', property=prop, months=months, totals=totals)

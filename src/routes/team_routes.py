"""
Team — the organisation's administrator accounts.

A lead admin adds people, switches accounts off and on, sets a temporary
password when someone forgets theirs, and changes roles. Accounts are never
deleted: a switched-off account keeps its name on everything it did. Every
change is written to audit_log and the platform shadow log.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from src.database.db import get_connection
from src.platform.guardian import platform_log
from src.utils import staff

team_bp = Blueprint('team', __name__, url_prefix='/team')


def _can_manage():
    return (session.get('admin_role') == 'lead' or session.get('admin_master')
            or session.get('admin_via_platform'))


def _record(conn, action, account_id, details):
    conn.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
        (action, 'admin_user', account_id, details, staff.actor()))
    platform_log(conn, action, 'admin_user', account_id, details, org_id=session.get('org_id'), actor=staff.actor())


def _target(conn, account_id):
    """An account in the signed-in organisation, for a lead to change — else 403/404."""
    if not _can_manage():
        abort(403)
    row = conn.execute("SELECT * FROM admin_users WHERE id = ? AND org_id = ?",
                       (account_id, session.get('org_id'))).fetchone()
    if not row:
        abort(404)
    return row


@team_bp.route('')
def team():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT a.*, (SELECT COUNT(*) FROM audit_log l WHERE l.user_id = 'admin:' || a.name) AS actions
            FROM admin_users a WHERE a.org_id = ?
            ORDER BY a.is_active DESC, a.role = 'lead' DESC, a.name
        """, (session.get('org_id'),)).fetchall()
    return render_template('team.html', rows=rows, roles=staff.ROLES, can_manage=_can_manage(),
                           me=session.get('admin_user_id'), min_password=staff.MIN_PASSWORD)


@team_bp.route('/add', methods=['POST'])
def add():
    if not _can_manage():
        abort(403)
    org_id = session.get('org_id')
    name = ' '.join(request.form.get('name', '').split())
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    role = request.form.get('role', 'admin')
    with get_connection() as conn:
        if not org_id or len(name) < 2 or '@' not in email:
            flash('Enter a name and a valid email.', 'error')
        elif len(password) < staff.MIN_PASSWORD:
            flash(f'Temporary password must be at least {staff.MIN_PASSWORD} characters.', 'error')
        elif staff.email_taken(conn, email):
            flash('That email already has an account.', 'error')
        elif staff.name_taken(conn, org_id, name):
            flash('Someone in the team already has that name — add a surname or initial.', 'error')
        else:
            uid = staff.create_account(conn, org_id, name, email, password, role, staff.actor())
            _record(conn, 'admin_added', uid, f"{name} <{email}> | role: {role}")
            flash(f'{name} added. Give them the temporary password in person; they can change it under Account Settings.',
                  'success')
    return redirect(url_for('team.team'))


@team_bp.route('/<account_id>/deactivate', methods=['POST'])
def deactivate(account_id):
    with get_connection() as conn:
        row = _target(conn, account_id)
        if row['id'] == session.get('admin_user_id'):
            flash("You can't switch off your own account.", 'error')
        elif row['role'] == 'lead' and staff.active_leads(conn, row['org_id']) <= 1:
            flash('Make someone else a lead admin first — the team needs one.', 'error')
        elif row['is_active']:
            conn.execute("""
                UPDATE admin_users SET is_active = 0, deactivated_at = CURRENT_TIMESTAMP, deactivated_by = ?
                WHERE id = ?
            """, (staff.actor(), row['id']))
            _record(conn, 'admin_deactivated', row['id'], f"{row['name'] or row['email']} switched off")
            flash(f"{row['name'] or row['email']} can no longer sign in. Their name stays on what they did.", 'success')
    return redirect(url_for('team.team'))


@team_bp.route('/<account_id>/reactivate', methods=['POST'])
def reactivate(account_id):
    with get_connection() as conn:
        row = _target(conn, account_id)
        if not row['is_active']:
            conn.execute("UPDATE admin_users SET is_active = 1, deactivated_at = NULL, deactivated_by = NULL WHERE id = ?",
                         (row['id'],))
            _record(conn, 'admin_reactivated', row['id'], f"{row['name'] or row['email']} switched back on")
            flash(f"{row['name'] or row['email']} can sign in again.", 'success')
    return redirect(url_for('team.team'))


@team_bp.route('/<account_id>/password', methods=['POST'])
def set_password(account_id):
    password = request.form.get('password', '')
    with get_connection() as conn:
        row = _target(conn, account_id)
        if len(password) < staff.MIN_PASSWORD:
            flash(f'Temporary password must be at least {staff.MIN_PASSWORD} characters.', 'error')
        else:
            conn.execute("UPDATE admin_users SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(password), row['id']))
            _record(conn, 'admin_password_reset', row['id'], f"Temporary password set for {row['name'] or row['email']}")
            flash(f"Temporary password set for {row['name'] or row['email']}.", 'success')
    return redirect(url_for('team.team'))


@team_bp.route('/<account_id>/role', methods=['POST'])
def set_role(account_id):
    role = request.form.get('role')
    with get_connection() as conn:
        row = _target(conn, account_id)
        if role not in staff.ROLES or role == row['role']:
            pass
        elif row['role'] == 'lead' and row['is_active'] and staff.active_leads(conn, row['org_id']) <= 1:
            flash('Make someone else a lead admin first — the team needs one.', 'error')
        else:
            conn.execute("UPDATE admin_users SET role = ? WHERE id = ?", (role, row['id']))
            _record(conn, 'admin_role_changed', row['id'], f"{row['name'] or row['email']}: {row['role']} → {role}")
            flash(f"{row['name'] or row['email']} is now: {staff.ROLES[role].split(' —')[0]}.", 'success')
    return redirect(url_for('team.team'))

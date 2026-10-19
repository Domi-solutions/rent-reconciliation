"""
Who is acting on the admin side.

Each administrator has their own account (admin_users) under the organisation.
Tenants see management replies signed 'Management'; the record — audit_log,
conversation messages — keeps the person, so when administrators change you
can tell who did what. Accounts are switched off, never deleted.

Sign-in, the per-request check and the actor label all live here — never inline
them in routes.
"""

from flask import has_request_context, session
from werkzeug.security import check_password_hash, generate_password_hash

from src.database.db import generate_id

ROLES = {'lead': 'Lead admin — also manages the team', 'admin': 'Admin'}
MIN_PASSWORD = 8
_SESSION_KEYS = ('admin_authenticated', 'admin_user_id', 'admin_name', 'admin_role', 'admin_master',
                 'admin_via_platform', 'org_id', 'org_selection_done', 'property_id')


def actor():
    """Label for audit_log.user_id and similar 'who did this' columns."""
    if not has_request_context():
        return 'admin'
    if session.get('admin_user_id'):
        return f"admin:{session.get('admin_name') or session['admin_user_id']}"
    if session.get('admin_via_platform'):
        return 'platform (as organisation)'
    if session.get('admin_master'):
        return 'admin:master key'
    return 'admin'


def find_login(conn, email, password):
    """The active admin account these credentials open, or None.

    An organisation that has no accounts yet (created after the migration) signs
    in once with its organisation login; that login becomes its lead account.
    Once an organisation has accounts, the organisation password no longer opens
    anything — otherwise a changed personal password could be bypassed with it.
    """
    email = (email or '').strip().lower()
    if not email or not password:
        return None
    user = conn.execute("""
        SELECT a.* FROM admin_users a JOIN organizations o ON o.id = a.org_id
        WHERE LOWER(a.email) = ? AND a.is_active = 1 AND o.is_active = 1
    """, (email,)).fetchone()
    if user:
        return user if check_password_hash(user['password_hash'], password) else None
    org = conn.execute("""
        SELECT id, admin_password_hash FROM organizations
        WHERE LOWER(contact_email) = ? AND is_active = 1
          AND NOT EXISTS (SELECT 1 FROM admin_users a WHERE a.org_id = organizations.id)
    """, (email,)).fetchone()
    if not org or not org['admin_password_hash'] or not check_password_hash(org['admin_password_hash'], password):
        return None
    if conn.execute("SELECT 1 FROM admin_users WHERE LOWER(email) = ?", (email,)).fetchone():
        return None                       # a switched-off account holds this email
    uid = generate_id('ADM')
    conn.execute("""
        INSERT INTO admin_users (id, org_id, email, password_hash, role, created_by)
        VALUES (?, ?, ?, ?, 'lead', 'organisation login')
    """, (uid, org['id'], email, org['admin_password_hash']))
    return conn.execute("SELECT * FROM admin_users WHERE id = ?", (uid,)).fetchone()


def sign_in(conn, user):
    for k in _SESSION_KEYS:
        session.pop(k, None)
    session['admin_authenticated'] = True
    session['admin_user_id'] = user['id']
    session['admin_name'] = user['name']
    session['admin_role'] = user['role']
    session['org_id'] = user['org_id']
    session['org_selection_done'] = True
    conn.execute("UPDATE admin_users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user['id'],))


def sign_out():
    for k in _SESSION_KEYS:
        session.pop(k, None)


def current_account(conn):
    """The signed-in admin's account if it is still active, else None. Checked on
    every request, so switching an account off ends its sessions at once."""
    uid = session.get('admin_user_id')
    if not uid:
        return None
    return conn.execute("SELECT * FROM admin_users WHERE id = ? AND is_active = 1", (uid,)).fetchone()


def email_taken(conn, email, except_id=None):
    return conn.execute("SELECT 1 FROM admin_users WHERE LOWER(email) = ? AND id IS NOT ?",
                        (email.strip().lower(), except_id)).fetchone() is not None


def name_taken(conn, org_id, name, except_id=None):
    """Names label the record, so two people in one organisation can't share one."""
    return conn.execute("""
        SELECT 1 FROM admin_users WHERE org_id = ? AND LOWER(TRIM(name)) = ? AND id IS NOT ?
    """, (org_id, name.strip().lower(), except_id)).fetchone() is not None


def create_account(conn, org_id, name, email, password, role, created_by):
    uid = generate_id('ADM')
    conn.execute("""
        INSERT INTO admin_users (id, org_id, name, email, password_hash, role, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (uid, org_id, name.strip(), email.strip().lower(), generate_password_hash(password),
          role if role in ROLES else 'admin', created_by))
    return uid


def active_leads(conn, org_id):
    return conn.execute("SELECT COUNT(*) FROM admin_users WHERE org_id = ? AND role = 'lead' AND is_active = 1",
                        (org_id,)).fetchone()[0]

"""
Tenant Settings: extra contacts, changing the phone number, signing out everywhere.

Contacts (tenant_contacts): numbers that pay for this tenant (a spouse, an employer —
claim_owner treats a payment from them as the tenant's), an emergency contact, and a
second phone. Removing one keeps the row (removed_at); every change is in audit_log.

The phone number is how a tenant signs in and where receipts go, so changing it is never
silent and never the tenant's alone (LINCKS, 2026-10-28): the tenant proves the new number
with an SMS code, management approves, the old number is told, and every old number stays
on record (phone_change_requests). A number on another tenancy is refused.
"""

import secrets
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from src.database.db import generate_id
from src.messaging import threads
from src.platform.guardian import platform_log
from src.utils.phone import normalize_to_e164
from src.utils.tenant_auth import mask_phone

KINDS = {'payer': 'Numbers you pay from', 'emergency': 'Emergency contact', 'second_phone': 'Second phone number'}
LIMITS = {'payer': 3, 'emergency': 2, 'second_phone': 1}
CODE_MINUTES = 10
CODE_TRIES = 5


# ── contacts ────────────────────────────────────────────────────────────────

def contacts(conn, tenant_id):
    """{kind: [rows]} for the tenant's current contacts."""
    out = {k: [] for k in KINDS}
    for r in conn.execute("""SELECT * FROM tenant_contacts WHERE tenant_id = ? AND removed_at IS NULL
                             ORDER BY created_at""", (tenant_id,)):
        out.setdefault(r['kind'], []).append(dict(r))
    return out


def add_contact(conn, tenant, kind, phone, name='', relationship=''):
    """Returns the new id, or raises ValueError with a message for the tenant."""
    if kind not in KINDS:
        raise ValueError('Choose what kind of contact this is.')
    e164 = normalize_to_e164(phone or '')
    if not e164:
        raise ValueError('That is not a full phone number — use the 10 digits, e.g. 0712 345 678.')
    if e164 == normalize_to_e164(tenant.get('phone') or ''):
        raise ValueError('That is already your main phone number.')
    if kind == 'emergency' and not (name or '').strip():
        raise ValueError('Add the name of your emergency contact.')
    current = contacts(conn, tenant['id'])[kind]
    if any(c['phone'] == e164 for c in current):
        raise ValueError('That number is already on your list.')
    if len(current) >= LIMITS[kind]:
        raise ValueError(f'You can keep up to {LIMITS[kind]} here. Remove one first.')
    contact_id = generate_id('TCON')
    conn.execute("""INSERT INTO tenant_contacts (id, tenant_id, kind, name, relationship, phone)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (contact_id, tenant['id'], kind, (name or '').strip()[:80] or None,
                  (relationship or '').strip()[:40] or None, e164))
    _record(conn, tenant, 'tenant_contact_added', f"{KINDS[kind]}: {mask_phone(e164)}")
    return contact_id


def remove_contact(conn, tenant, contact_id):
    row = conn.execute("""SELECT * FROM tenant_contacts WHERE id = ? AND tenant_id = ? AND removed_at IS NULL""",
                       (contact_id, tenant['id'])).fetchone()
    if row:
        conn.execute("UPDATE tenant_contacts SET removed_at = CURRENT_TIMESTAMP WHERE id = ?", (contact_id,))
        _record(conn, tenant, 'tenant_contact_removed', f"{KINDS.get(row['kind'], row['kind'])}: {mask_phone(row['phone'])}")


def payer_phones(conn, tenant_id):
    """E.164 numbers the tenant says pay for them (used by claim_owner)."""
    return [r['phone'] for r in conn.execute("""SELECT phone FROM tenant_contacts WHERE tenant_id = ?
                                                AND kind = 'payer' AND removed_at IS NULL""", (tenant_id,))]


# ── phone number change ─────────────────────────────────────────────────────

def open_request(conn, tenant_id):
    row = conn.execute("""SELECT * FROM phone_change_requests WHERE tenant_id = ?
                          AND status IN ('awaiting_code', 'pending') ORDER BY rowid DESC LIMIT 1""",
                       (tenant_id,)).fetchone()
    return dict(row) if row else None


def request_change(conn, tenant, person_id, prop, new_phone, now=None):
    """Start a change: returns (request_id, code) — the caller texts the code to the new number
    after commit. Raises ValueError with a message for the tenant."""
    now = now or datetime.utcnow()
    e164 = normalize_to_e164(new_phone or '')
    if not e164:
        raise ValueError('That is not a full phone number — use the 10 digits, e.g. 0712 345 678.')
    if e164 == normalize_to_e164(tenant.get('phone') or ''):
        raise ValueError('That is already your phone number.')
    taken = conn.execute("""SELECT 1 FROM tenants WHERE status IN ('active', 'departed') AND person_id IS NOT ?
                            AND REPLACE(phone, ' ', '') LIKE '%' || ?""", (person_id, e164[-9:])).fetchone()
    if taken:
        raise ValueError('That number is on another tenancy. Ask management.')
    existing = open_request(conn, tenant['id'])
    if existing and existing['status'] == 'pending':
        raise ValueError('You already asked to change your number. Management will reply.')
    if existing:
        conn.execute("UPDATE phone_change_requests SET status = 'cancelled' WHERE id = ?", (existing['id'],))
    code = f'{secrets.randbelow(1_000_000):06d}'
    request_id = generate_id('PHCH')
    conn.execute("""INSERT INTO phone_change_requests (id, tenant_id, person_id, property_id, old_phone, new_phone,
                    code_hash, code_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                 (request_id, tenant['id'], person_id, prop['id'], normalize_to_e164(tenant.get('phone') or ''),
                  e164, generate_password_hash(code), (now + timedelta(minutes=CODE_MINUTES)).isoformat()))
    return request_id, code


def confirm_code(conn, tenant, unit, prop, code, now=None):
    """The tenant typed the code sent to the new number. On success the request goes to
    management as a conversation; returns its id. Raises ValueError otherwise."""
    now = now or datetime.utcnow()
    req = open_request(conn, tenant['id'])
    if not req or req['status'] != 'awaiting_code':
        raise ValueError('Ask for a new code.')
    if (req['code_attempts'] or 0) >= CODE_TRIES:
        raise ValueError('Too many wrong codes. Ask for a new code.')
    if now > datetime.fromisoformat(req['code_expires_at']):
        raise ValueError('That code has expired. Ask for a new code.')
    if not check_password_hash(req['code_hash'], (code or '').strip()):
        conn.execute("UPDATE phone_change_requests SET code_attempts = code_attempts + 1 WHERE id = ?", (req['id'],))
        raise ValueError('That code is not correct.')
    body = (f"Please change the phone number on my tenancy from {mask_phone(req['old_phone'])} to "
            f"{req['new_phone']}. The new number is confirmed by an SMS code.")
    conv_id, _, _ = threads.start_conversation(conn, tenant, unit, prop, 'other', 'Change of phone number', body)
    conn.execute("""UPDATE phone_change_requests SET status = 'pending', verified_at = CURRENT_TIMESTAMP,
                    code_hash = NULL, conversation_id = ? WHERE id = ?""", (conv_id, req['id']))
    _record(conn, tenant, 'phone_change_requested', f"{mask_phone(req['old_phone'])} → {mask_phone(req['new_phone'])}")
    return conv_id


def cancel(conn, tenant):
    req = open_request(conn, tenant['id'])
    if req:
        conn.execute("UPDATE phone_change_requests SET status = 'cancelled' WHERE id = ?", (req['id'],))
        if req['conversation_id']:
            threads.add_message(conn, req['conversation_id'], 'tenant', tenant['name'],
                                'I no longer want to change my phone number.')


def pending_for_conversation(conn, conversation_id):
    row = conn.execute("""SELECT * FROM phone_change_requests WHERE conversation_id = ? AND status = 'pending'""",
                       (conversation_id,)).fetchone()
    return dict(row) if row else None


def decide(conn, request_id, approve, actor, org_id=None):
    """Management approves or declines. Approving moves every tenancy of this person from the
    old number to the new one. Returns the texts to send after commit: [(phone, text), ...]."""
    req = conn.execute("SELECT * FROM phone_change_requests WHERE id = ? AND status = 'pending'",
                       (request_id,)).fetchone()
    if not req:
        return []
    prop = conn.execute("SELECT name FROM properties WHERE id = ?", (req['property_id'],)).fetchone()
    place = (prop['name'] if prop else '').title()
    status = 'approved' if approve else 'declined'
    conn.execute("""UPDATE phone_change_requests SET status = ?, decided_at = CURRENT_TIMESTAMP, decided_by = ?
                    WHERE id = ?""", (status, actor, request_id))
    details = f"{mask_phone(req['old_phone'])} → {mask_phone(req['new_phone'])} {status}"
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('tenant_phone_change_' + status, 'tenant', req['tenant_id'], details, actor))
    platform_log(conn, 'tenant_phone_change_' + status, 'tenant', req['tenant_id'], details,
                 org_id=org_id, property_id=req['property_id'], actor=actor)
    if not approve:
        threads.add_message(conn, req['conversation_id'], 'system', 'Domi',
                            'Management did not change the phone number. Your number stays as it was.')
        return []
    old = req['old_phone']
    rows = conn.execute("""SELECT id, phone FROM tenants WHERE (id = ? OR (person_id = ? AND person_id IS NOT NULL))
                           AND status IN ('active', 'departed')""", (req['tenant_id'], req['person_id'])).fetchall()
    for t in rows:
        if t['id'] == req['tenant_id'] or normalize_to_e164(t['phone'] or '') == old:
            conn.execute("UPDATE tenants SET phone = ? WHERE id = ?", (req['new_phone'], t['id']))
    if req['person_id']:
        conn.execute("UPDATE persons SET phone = ? WHERE id = ?", (req['new_phone'], req['person_id']))
    threads.add_message(conn, req['conversation_id'], 'system', 'Domi',
                        f"Phone number changed to {req['new_phone']}. Sign in with the new number from now on.")
    texts = [(req['new_phone'], f"{place}: this is now the phone number on your tenancy. Sign in with it from now on.")]
    if old:
        texts.append((old, f"{place}: the phone number on your tenancy was changed to {mask_phone(req['new_phone'])}. "
                           f"If you did not ask for this, call management now."))
    return texts


# ── sign out everywhere ─────────────────────────────────────────────────────

def sign_out_everywhere(conn, person_id):
    conn.execute("UPDATE persons SET session_version = COALESCE(session_version, 0) + 1 WHERE id = ?", (person_id,))


def session_version(conn, person_id):
    row = conn.execute("SELECT COALESCE(session_version, 0) FROM persons WHERE id = ?", (person_id,)).fetchone()
    return row[0] if row else 0


def _record(conn, tenant, action, details):
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 (action, 'tenant', tenant['id'], details, 'tenant'))

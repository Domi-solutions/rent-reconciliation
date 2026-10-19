"""
Tenant sign-in: phone + 4-digit PIN, with an SMS code to set or reset the PIN.

A tenant proves they hold the phone on record by entering a 6-digit code sent to
it, then chooses a PIN. Afterwards they sign in with phone + PIN. A forwarded
portal link is therefore not enough to get in — the code goes to the phone on
record, not to whoever holds the link.

The PIN lives on persons.pin_hash, separate from persons.password_hash (owner
passwords), because one person can be both an owner and a tenant.
"""

import random
import re
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from src.database.db import generate_id
from src.utils.phone import normalize_to_e164

OTP_TTL = timedelta(minutes=10)
OTP_RESEND_GAP = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
PIN_MAX_ATTEMPTS = 5
PIN_LOCK = timedelta(minutes=15)


def _now():
    return datetime.utcnow()


def _ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def _parse(ts):
    try:
        return datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S') if ts else None
    except ValueError:
        return None


def mask_phone(phone):
    """'0720497781' → '0720•••781' — enough for the tenant to recognise their own number."""
    d = re.sub(r'\D', '', phone or '')
    if d.startswith('254'):
        d = '0' + d[3:]
    return f'{d[:4]}•••{d[-3:]}' if len(d) >= 7 else '•••'


def valid_pin(pin):
    return bool(pin) and pin.isdigit() and len(pin) == 4


def tenancies_for_phone(conn, phone):
    """Current and departed tenancies whose phone on record is this number."""
    e164 = normalize_to_e164(phone or '')
    if not e164:
        return []
    last9 = e164[-9:]
    rows = conn.execute("""
        SELECT t.id, t.name, t.phone, t.person_id, t.access_token, t.status,
               u.unit_number, p.name AS property_name
        FROM tenants t
        JOIN units u ON u.id = t.unit_id
        JOIN properties p ON p.id = t.property_id
        WHERE t.status IN ('active', 'departed') AND t.access_token IS NOT NULL
          AND REPLACE(REPLACE(t.phone, ' ', ''), '-', '') LIKE '%' || ?
    """, (last9,)).fetchall()
    return [dict(r) for r in rows if normalize_to_e164(r['phone'] or '') == e164]


def person_for_phone(conn, phone, tenancies):
    """The person record behind these tenancies, created and linked if missing."""
    e164 = normalize_to_e164(phone)
    linked = {t['person_id'] for t in tenancies if t['person_id']}
    if linked:
        person_id = sorted(linked)[0]
    else:
        row = conn.execute("SELECT id FROM persons WHERE phone = ?", (e164,)).fetchone()
        if row:
            person_id = row['id']
        else:
            person_id = generate_id('PER')
            conn.execute("INSERT INTO persons (id, name, phone) VALUES (?, ?, ?)",
                         (person_id, tenancies[0]['name'] or 'Tenant', e164))
    for t in tenancies:
        if t['person_id'] != person_id:
            conn.execute("UPDATE tenants SET person_id = ? WHERE id = ?", (person_id, t['id']))
    return person_id


def issue_otp(conn, person_id):
    """Store a fresh 6-digit code (hashed) and return it, or None if one was sent under a minute ago."""
    row = conn.execute("SELECT otp_last_sent_at FROM persons WHERE id = ?", (person_id,)).fetchone()
    last = _parse(row['otp_last_sent_at']) if row else None
    if last and _now() - last < OTP_RESEND_GAP:
        return None
    code = f'{random.SystemRandom().randint(0, 999999):06d}'
    conn.execute("""
        UPDATE persons SET otp_code = ?, otp_expires_at = ?, otp_attempts = 0,
               otp_last_sent_at = ?, otp_purpose = 'tenant_pin'
        WHERE id = ?
    """, (generate_password_hash(code), _ts(_now() + OTP_TTL), _ts(_now()), person_id))
    return code


def check_otp(conn, person_id, code):
    """'ok' | 'wrong' | 'expired' | 'too_many'. A correct code is consumed."""
    row = conn.execute("""
        SELECT otp_code, otp_expires_at, otp_attempts, otp_purpose FROM persons WHERE id = ?
    """, (person_id,)).fetchone()
    if not row or not row['otp_code'] or row['otp_purpose'] != 'tenant_pin':
        return 'expired'
    if (row['otp_attempts'] or 0) >= OTP_MAX_ATTEMPTS:
        return 'too_many'
    expires = _parse(row['otp_expires_at'])
    if not expires or _now() > expires:
        return 'expired'
    if not check_password_hash(row['otp_code'], (code or '').strip()):
        conn.execute("UPDATE persons SET otp_attempts = COALESCE(otp_attempts, 0) + 1 WHERE id = ?", (person_id,))
        return 'wrong'
    conn.execute("""
        UPDATE persons SET otp_code = NULL, otp_expires_at = NULL, otp_attempts = 0,
               otp_purpose = NULL, phone_verified = 1
        WHERE id = ?
    """, (person_id,))
    return 'ok'


def set_pin(conn, person_id, pin):
    conn.execute("""
        UPDATE persons SET pin_hash = ?, pin_failed_attempts = 0, pin_locked_until = NULL WHERE id = ?
    """, (generate_password_hash(pin), person_id))


def check_pin(conn, person_id, pin):
    """('ok'|'wrong'|'locked'|'no_pin', minutes_until_unlock)."""
    row = conn.execute("""
        SELECT pin_hash, pin_failed_attempts, pin_locked_until FROM persons WHERE id = ?
    """, (person_id,)).fetchone()
    if not row or not row['pin_hash']:
        return 'no_pin', 0
    locked = _parse(row['pin_locked_until'])
    if locked and _now() < locked:
        return 'locked', max(1, int((locked - _now()).total_seconds() // 60) + 1)
    if check_password_hash(row['pin_hash'], pin or ''):
        conn.execute("UPDATE persons SET pin_failed_attempts = 0, pin_locked_until = NULL WHERE id = ?", (person_id,))
        return 'ok', 0
    fails = (row['pin_failed_attempts'] or 0) + 1
    if fails >= PIN_MAX_ATTEMPTS:
        conn.execute("UPDATE persons SET pin_failed_attempts = 0, pin_locked_until = ? WHERE id = ?",
                     (_ts(_now() + PIN_LOCK), person_id))
        return 'locked', int(PIN_LOCK.total_seconds() // 60)
    conn.execute("UPDATE persons SET pin_failed_attempts = ? WHERE id = ?", (fails, person_id))
    return 'wrong', 0

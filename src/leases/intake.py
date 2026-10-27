"""Lease intake: turn a signed (or awaited) tenancy agreement into a tenant profile.

Built for onboarding a property whose records are unreliable, and for every
property after it:

* A lease opens a tenancy. When the person signing is already recorded on the
  unit, their old record is frozen ("before the lease") and a fresh one opens
  on the lease's terms. Nothing is deleted; the old record keeps its history
  and stays visible to the admin, but the tenant's account starts clean.
* Money owed from before the lease is carried only if someone enters it, with
  the evidence for it, as a "brought forward" charge on the new account. It
  can be corrected later (e.g. once old bank statements are reconciled); every
  change is audited and logged to the platform.
* A profile can be opened before the signed copy comes back (status pending)
  and completed when it does.

The account cut-over is by month: charges for the lease's start month and
later belong to the new account. Payments dated before that month stay with
the old record.

Routes call these helpers; no lease logic lives in a route.
"""

import io
import os
import secrets
import string
import uuid
from datetime import date, datetime, timedelta

from PIL import Image, ImageOps, UnidentifiedImageError

from src.database.db import apply_unallocated_credit, generate_id, get_db_path
from src.utils.phone import normalize_to_e164

try:                                     # iPhone photos of the paper lease are HEIC
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:                      # pragma: no cover — optional in dev
    pass

DOCUMENT_KINDS = (('lease', 'Signed lease'), ('id_copy', 'ID copy'), ('other', 'Other'))
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
STORED_MAX_SIDE = 2400                   # big enough to read a printed page
MIN_EVIDENCE_CHARS = 10
NOT_RENTABLE = ('owner_use', 'short_term')


class LeaseError(ValueError):
    """Shown to the admin as-is."""


# ── Documents ────────────────────────────────────────────────────────────────

def documents_dir():
    """Beside the database (the Fly volume), never under static/."""
    d = os.path.join(os.path.dirname(os.path.abspath(get_db_path())), 'lease_documents')
    os.makedirs(d, exist_ok=True)
    return d


def store_document(upload):
    """Save an uploaded PDF as-is, or an image re-encoded as JPEG (EXIF/GPS
    dropped). Returns (file_name, content_type, size) or None if neither."""
    data = upload.read(MAX_DOCUMENT_BYTES + 1)
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        return None
    if data[:5] == b'%PDF-':
        name = f'{uuid.uuid4().hex}.pdf'
        with open(os.path.join(documents_dir(), name), 'wb') as f:
            f.write(data)
        return name, 'application/pdf', len(data)
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    img.thumbnail((STORED_MAX_SIDE, STORED_MAX_SIDE))
    name = f'{uuid.uuid4().hex}.jpg'
    path = os.path.join(documents_dir(), name)
    img.save(path, 'JPEG', quality=85, optimize=True)
    return name, 'image/jpeg', os.path.getsize(path)


def add_documents(conn, lease_id, uploads, kind, actor):
    """Store each upload against the lease. Returns (saved, rejected)."""
    kind = kind if kind in dict(DOCUMENT_KINDS) else 'other'
    saved = rejected = 0
    for upload in uploads:
        if not upload or not upload.filename:
            continue
        stored = store_document(upload)
        if not stored:
            rejected += 1
            continue
        file_name, content_type, size = stored
        conn.execute("""
            INSERT INTO lease_documents (id, lease_id, kind, file_name, original_name,
                                         content_type, size_bytes, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (generate_id('LDOC'), lease_id, kind, file_name, upload.filename[:200],
              content_type, size, actor))
        saved += 1
    return saved, rejected


# ── Reading ──────────────────────────────────────────────────────────────────

def prior_account(conn, tenant_id):
    """What the records say about a tenancy before a new lease replaces it."""
    row = conn.execute("""
        SELECT tb.total_charged, tb.total_paid, tb.balance,
               (SELECT MAX(period) FROM rent_charges
                 WHERE tenant_id = tb.tenant_id AND period != 'ARREARS') AS last_charge_period,
               (SELECT MAX(payment_date) FROM payments WHERE tenant_id = tb.tenant_id) AS last_payment_date,
               (SELECT COALESCE(SUM(amount), 0) FROM rent_charges
                 WHERE tenant_id = tb.tenant_id AND period = 'ARREARS') AS opening_arrears
        FROM tenant_balances tb WHERE tb.tenant_id = ?
    """, (tenant_id,)).fetchone()
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


def open_lease_for_tenant(conn, tenant_id):
    return conn.execute("""
        SELECT * FROM leases WHERE tenant_id = ? AND status IN ('pending', 'signed')
        ORDER BY created_at DESC LIMIT 1
    """, (tenant_id,)).fetchone()


def unit_board(conn, property_id):
    """Every unit with where its lease stands. Returns (rows, counts).

    phase: signed | pending (profile open, signed copy not back) |
           recorded (someone on the old records, no lease yet) |
           empty (nobody recorded) | not_rentable (owner use / short-term)
    """
    units = conn.execute("""
        SELECT u.id, u.unit_number, u.status, u.monthly_rent, u.service_charge,
               t.id AS tenant_id, t.name AS tenant_name, t.phone AS tenant_phone,
               tb.balance
        FROM units u
        LEFT JOIN tenants t ON t.unit_id = u.id AND t.status = 'active'
        LEFT JOIN tenant_balances tb ON tb.tenant_id = t.id
        WHERE u.property_id = ?
        ORDER BY u.unit_number
    """, (property_id,)).fetchall()
    rows = []
    counts = {'signed': 0, 'pending': 0, 'recorded': 0, 'empty': 0, 'not_rentable': 0}
    for u in units:
        r = dict(u)
        lease = open_lease_for_tenant(conn, u['tenant_id']) if u['tenant_id'] else None
        r['lease'] = dict(lease) if lease else None
        if u['status'] in NOT_RENTABLE:
            r['phase'] = 'not_rentable'
        elif lease:
            r['phase'] = lease['status']
            r['documents'] = conn.execute("SELECT COUNT(*) FROM lease_documents WHERE lease_id = ?",
                                          (lease['id'],)).fetchone()[0]
        elif u['tenant_id']:
            r['phase'] = 'recorded'
        else:
            r['phase'] = 'empty'
        counts[r['phase']] += 1
        rows.append(r)
    counts['rentable'] = len(rows) - counts['not_rentable']
    return rows, counts


# ── Recording ────────────────────────────────────────────────────────────────

def _money(value, label, required=False):
    try:
        amount = round(float(str(value or '0').replace(',', '').strip()), 2)
    except ValueError:
        raise LeaseError(f'{label}: enter a number.')
    if amount < 0:
        raise LeaseError(f'{label} cannot be negative.')
    if required and amount <= 0:
        raise LeaseError(f'{label} is required.')
    return amount


def _day(value, label):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        raise LeaseError(f'{label}: choose a date.')


def _new_short_code(conn):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(6))
        if not conn.execute("SELECT 1 FROM tenants WHERE short_code = ?", (code,)).fetchone():
            return code


def _rent_change_guard(conn, property_row, unit, field, new_value):
    """Same rule as the unit edit: a >10% change alerts the platform and owners."""
    old_value = float(unit[field] or 0)
    if old_value == new_value:
        return
    from src.platform.guardian import platform_log, raise_alert, notify_owner_change
    org_id = property_row['organization_id']
    platform_log(conn, 'unit_field_edited', 'unit', unit['id'],
                 f"Unit {unit['unit_number']} | {field}: {old_value:,.0f} → {new_value:,.0f} (from lease)",
                 org_id=org_id, property_id=property_row['id'])
    if old_value > 0:
        pct = abs(new_value - old_value) / old_value * 100
        if pct > 10:
            raise_alert(conn, 'rent_changed',
                        f"Unit {unit['unit_number']}: {field} {old_value:,.0f} → {new_value:,.0f} "
                        f"({pct:.0f}%) from lease intake",
                        org_id=org_id, property_id=property_row['id'],
                        severity='critical' if pct > 30 else 'warning')
            notify_owner_change(conn, property_row['id'], f"Rent change: Unit {unit['unit_number']}",
                                f"The {field.replace('_', ' ')} for Unit {unit['unit_number']} is "
                                f"KES {new_value:,.0f} under the new lease (was KES {old_value:,.0f}).")


def record_lease(conn, property_row, form, actor):
    """Open a tenancy from a lease. form is a dict of the intake fields.
    Returns (lease_id, tenant_id). Raises LeaseError with a message for the admin."""
    pid = property_row['id']
    unit = conn.execute("SELECT * FROM units WHERE id = ? AND property_id = ?",
                        (form.get('unit_id'), pid)).fetchone()
    if not unit:
        raise LeaseError('Choose a unit in this property.')
    if unit['status'] in NOT_RENTABLE:
        raise LeaseError(f"Unit {unit['unit_number']} is marked owner use / short-term. Change its status first.")

    name = (form.get('name') or '').strip()
    if not name:
        raise LeaseError("Tenant's name is required.")
    phone_raw = (form.get('phone') or '').strip()
    phone = normalize_to_e164(phone_raw) if phone_raw else None
    if phone_raw and not phone:
        raise LeaseError(f"'{phone_raw}' is not a Kenyan mobile number.")
    national_id = (form.get('national_id') or '').strip() or None
    start = _day(form.get('start_date'), 'Lease start ("with effect from")')
    rent = _money(form.get('monthly_rent'), 'Monthly rent', required=True)
    service = _money(form.get('service_charge'), 'Service charge')
    deposit_required = _money(form.get('deposit_required'), 'Deposit required')
    deposit_held = _money(form.get('deposit_held'), 'Deposit held')
    signed = form.get('signed') == '1'
    signed_on = _day(form.get('signed_on'), 'Date signed') if signed and form.get('signed_on') else None
    brought_forward = _money(form.get('brought_forward'), 'Brought forward')
    evidence = (form.get('brought_forward_evidence') or '').strip()
    if brought_forward > 0 and len(evidence) < MIN_EVIDENCE_CHARS:
        raise LeaseError('Say what the brought-forward amount is based on (e.g. "acknowledged by tenant '
                         'in writing at signing, copy attached").')
    notes = (form.get('notes') or '').strip() or None

    current = conn.execute("SELECT * FROM tenants WHERE unit_id = ? AND status = 'active'",
                           (unit['id'],)).fetchone()
    if current and open_lease_for_tenant(conn, current['id']):
        raise LeaseError(f"Unit {unit['unit_number']} already has a lease on record. Open it to update it.")
    if current and form.get('who') != 'same':
        raise LeaseError(f"{current['name']} is still recorded in Unit {unit['unit_number']}. "
                         "Record their move-out first, then record the new lease.")

    # Account cut-over: a re-lease starts the new account at the start month.
    cutover = start.replace(day=1)
    prior = None
    if current:
        later = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM rent_charges WHERE tenant_id = :t AND period != 'ARREARS'
                                                    AND period >= :m) AS charges,
              (SELECT COUNT(*) FROM payments WHERE tenant_id = :t AND payment_date >= :d) AS payments
        """, {'t': current['id'], 'm': cutover.strftime('%Y-%m'), 'd': cutover.isoformat()}).fetchone()
        if later['charges'] or later['payments']:
            raise LeaseError(f"{current['name']}'s existing record already has charges or payments from "
                             f"{cutover:%B %Y} on. Choose a later start month, or review those first.")
        prior = prior_account(conn, current['id'])

    if brought_forward > 0 and conn.execute(
            "SELECT 1 FROM rent_charges WHERE unit_id = ? AND period = 'ARREARS' AND charge_type = 'brought_forward'",
            (unit['id'],)).fetchone():
        raise LeaseError(f"Unit {unit['unit_number']} already has a brought-forward balance from an earlier "
                         "lease. Correct that one instead.")

    # Who the person is: the frozen record's person, else whoever signs in with this phone.
    person_id = current['person_id'] if current else None
    if not person_id and phone:
        p = conn.execute("SELECT id FROM persons WHERE phone = ?", (phone,)).fetchone()
        person_id = p['id'] if p else None

    access_token = short_code = None
    if current:
        # Freeze the old record. Hand its portal link to the new account so links
        # already sent keep working; the frozen record cannot be signed into.
        access_token, short_code = current['access_token'], current['short_code']
        conn.execute("""
            UPDATE tenants SET status = 'inactive', move_out_date = ?, access_token = NULL, short_code = NULL
            WHERE id = ?
        """, ((cutover - timedelta(days=1)).isoformat(), current['id']))
    access_token = access_token or secrets.token_urlsafe(32)
    short_code = short_code or _new_short_code(conn)

    tenant_id = generate_id('TENANT')
    conn.execute("""
        INSERT INTO tenants (id, property_id, unit_id, name, phone, email, move_in_date, status,
                             deposit_paid, move_in_notes, access_token, short_code, person_id,
                             language_preference)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
    """, (tenant_id, pid, unit['id'], name, phone or (phone_raw or None),
          current['email'] if current else None,
          (cutover if current else start).isoformat(), deposit_held, notes, access_token, short_code,
          person_id, current['language_preference'] if current else None))
    if current:
        # Conversations and notices follow the person; money stays where it was recorded.
        conn.execute("UPDATE conversations SET tenant_id = ? WHERE tenant_id = ?", (tenant_id, current['id']))
        conn.execute("UPDATE messages SET tenant_id = ? WHERE tenant_id = ?", (tenant_id, current['id']))

    _rent_change_guard(conn, property_row, unit, 'monthly_rent', rent)
    _rent_change_guard(conn, property_row, unit, 'service_charge', service)
    conn.execute("""
        UPDATE units SET status = 'occupied', monthly_rent = ?, service_charge = ?,
               status_changed_at = CASE WHEN status = 'occupied' THEN status_changed_at ELSE CURRENT_TIMESTAMP END
        WHERE id = ?
    """, (rent, service, unit['id']))

    lease_id = generate_id('LEASE')
    charge_id = None
    if brought_forward > 0:
        charge_id = generate_id('CHG')
        conn.execute("""
            INSERT INTO rent_charges (id, property_id, unit_id, tenant_id, period, charge_type, amount, due_date)
            VALUES (?, ?, ?, ?, 'ARREARS', 'brought_forward', ?, ?)
        """, (charge_id, pid, unit['id'], tenant_id, brought_forward, start.isoformat()))
        apply_unallocated_credit(conn, pid)

    conn.execute("""
        INSERT INTO leases (id, org_id, property_id, unit_id, tenant_id, status, start_date,
                            monthly_rent, service_charge, deposit_required, deposit_held, national_id,
                            signed_on, prior_tenant_id, prior_balance, brought_forward,
                            brought_forward_evidence, brought_forward_charge_id, notes, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (lease_id, property_row['organization_id'], pid, unit['id'], tenant_id,
          'signed' if signed else 'pending', start.isoformat(), rent, service, deposit_required,
          deposit_held, national_id, signed_on.isoformat() if signed_on else None,
          current['id'] if current else None, prior['balance'] if prior else None,
          brought_forward, evidence or None, charge_id, notes, actor))

    details = (f"Unit {unit['unit_number']} | {name} | from {start.isoformat()} | rent {rent:,.0f}"
               f" + service {service:,.0f} | {'signed' if signed else 'signed copy pending'}"
               + (f" | replaces record {current['id']} (balance {prior['balance']:,.0f})" if prior else '')
               + f" | brought forward {brought_forward:,.0f}" + (f" — {evidence}" if evidence else ''))
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('lease_recorded', 'lease', lease_id, details, actor))
    from src.platform.guardian import platform_log
    platform_log(conn, 'lease_recorded', 'lease', lease_id, details,
                 org_id=property_row['organization_id'], property_id=pid)
    return lease_id, tenant_id


def get_lease(conn, lease_id, property_id):
    row = conn.execute("""
        SELECT l.*, u.unit_number, t.name AS tenant_name, t.phone AS tenant_phone,
               t.short_code, pt.name AS prior_name
        FROM leases l
        JOIN units u ON u.id = l.unit_id
        JOIN tenants t ON t.id = l.tenant_id
        LEFT JOIN tenants pt ON pt.id = l.prior_tenant_id
        WHERE l.id = ? AND l.property_id = ?
    """, (lease_id, property_id)).fetchone()
    return dict(row) if row else None


def mark_signed(conn, lease, signed_on, national_id, actor):
    day = _day(signed_on, 'Date signed') if signed_on else date.today()
    conn.execute("""
        UPDATE leases SET status = 'signed', signed_on = ?, national_id = COALESCE(?, national_id),
               updated_at = CURRENT_TIMESTAMP WHERE id = ?
    """, (day.isoformat(), (national_id or '').strip() or None, lease['id']))
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('lease_signed', 'lease', lease['id'],
                  f"Unit {lease['unit_number']} | {lease['tenant_name']} | signed copy received ({day})", actor))


def set_brought_forward(conn, lease, property_row, amount_raw, evidence, actor):
    """Correct the brought-forward amount, e.g. after old statements are reconciled."""
    amount = _money(amount_raw, 'Brought forward')
    evidence = (evidence or '').strip()
    if len(evidence) < MIN_EVIDENCE_CHARS:
        raise LeaseError('Say why the amount is changing and what it is based on.')
    old = float(lease['brought_forward'] or 0)
    if amount == old:
        raise LeaseError('That is already the brought-forward amount.')
    charge_id = lease['brought_forward_charge_id']
    if charge_id:
        allocated = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_allocations WHERE charge_id = ?",
                                 (charge_id,)).fetchone()[0]
        if amount < allocated:
            raise LeaseError(f"KES {allocated:,.0f} of payments has already been applied to it, so it cannot go "
                             f"below that. Reverse those payments first if they were misapplied.")
        if amount == 0:
            # The lease points at the charge (foreign key): let go of it before deleting.
            conn.execute("UPDATE leases SET brought_forward_charge_id = NULL WHERE id = ?", (lease['id'],))
            conn.execute("DELETE FROM rent_charges WHERE id = ?", (charge_id,))
            charge_id = None
        else:
            conn.execute("UPDATE rent_charges SET amount = ? WHERE id = ?", (amount, charge_id))
    elif amount > 0:
        charge_id = generate_id('CHG')
        try:
            conn.execute("""
                INSERT INTO rent_charges (id, property_id, unit_id, tenant_id, period, charge_type, amount, due_date)
                VALUES (?, ?, ?, ?, 'ARREARS', 'brought_forward', ?, ?)
            """, (charge_id, lease['property_id'], lease['unit_id'], lease['tenant_id'], amount, lease['start_date']))
        except Exception:
            raise LeaseError(f"Unit {lease['unit_number']} already has a brought-forward balance from an "
                             "earlier lease.")
    conn.execute("""
        UPDATE leases SET brought_forward = ?, brought_forward_evidence = ?, brought_forward_charge_id = ?,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?
    """, (amount, evidence, charge_id, lease['id']))
    if amount > old:
        apply_unallocated_credit(conn, lease['property_id'])
    details = (f"Unit {lease['unit_number']} | {lease['tenant_name']} | brought forward "
               f"{old:,.0f} → {amount:,.0f} — {evidence}")
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('lease_brought_forward_changed', 'lease', lease['id'], details, actor))
    from src.platform.guardian import platform_log
    platform_log(conn, 'lease_brought_forward_changed', 'lease', lease['id'], details,
                 org_id=property_row['organization_id'], property_id=lease['property_id'])

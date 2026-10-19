"""
Tenant ↔ management conversations, with photos.

One conversation per question or problem. Topics:
  money  — a balance or a payment. Management only; never shown to the caretaker.
  repair — also shown to the caretaker, and creates a linked maintenance_issues
           row so it appears on the caretaker's Issues page, in briefings and to
           the owner, building up a record of what breaks in which unit.
  other  — management only.

Messages and photos are append-only (a DB trigger rejects UPDATE). Who wrote
each message and when is stored on the message itself.
"""

import io
import os
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from src.database.db import generate_id, get_db_path

TOPICS = {
    'money': 'My balance or a payment',
    'repair': 'A repair',
    'other': 'Something else',
}
REPAIR_CATEGORIES = [
    ('plumbing', 'Plumbing / water'), ('electrical', 'Electrical'), ('structural', 'Walls, doors, windows'),
    ('pest', 'Pests'), ('appliance', 'Appliance'), ('common_area', 'Common area'), ('general', 'Other'),
]
MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 12 * 1024 * 1024     # what a phone camera produces; stored copies are much smaller
STORED_MAX_SIDE = 1600


def photos_dir():
    """Beside the database — on Fly that is the mounted volume, so photos survive deploys."""
    d = os.path.join(os.path.dirname(os.path.abspath(get_db_path())), 'message_photos')
    os.makedirs(d, exist_ok=True)
    return d


def _store_photo(upload):
    """Decode, shrink and re-encode as JPEG. Re-encoding drops EXIF, including
    the GPS location phone cameras embed. Returns (file_name, w, h, bytes) or None
    if the upload is not an image."""
    data = upload.read(MAX_PHOTO_BYTES + 1)
    if not data or len(data) > MAX_PHOTO_BYTES:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)          # keep the photo the right way up
        img = img.convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    img.thumbnail((STORED_MAX_SIDE, STORED_MAX_SIDE))
    name = f'{uuid.uuid4().hex}.jpg'
    path = os.path.join(photos_dir(), name)
    img.save(path, 'JPEG', quality=80, optimize=True)
    return name, img.width, img.height, os.path.getsize(path)


def add_message(conn, conversation_id, sender_type, sender_name, body, photos=()):
    """Append a message (and up to MAX_PHOTOS photos). Returns (message_id, photos_saved, photos_rejected)."""
    msg_id = generate_id('CMSG')
    conn.execute("""
        INSERT INTO conversation_messages (id, conversation_id, sender_type, sender_name, body)
        VALUES (?, ?, ?, ?, ?)
    """, (msg_id, conversation_id, sender_type, sender_name, body.strip()))
    saved = rejected = 0
    for upload in [p for p in photos if p and p.filename][:MAX_PHOTOS]:
        stored = _store_photo(upload)
        if not stored:
            rejected += 1
            continue
        name, w, h, size = stored
        conn.execute("""
            INSERT INTO conversation_photos (id, conversation_id, message_id, file_name, width, height, bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (generate_id('CPH'), conversation_id, msg_id, name, w, h, size))
        saved += 1
    # A tenant writing means management owes a reply; staff writing means it has one.
    status = 'awaiting_reply' if sender_type == 'tenant' else 'replied'
    read_col = 'tenant_read_at' if sender_type == 'tenant' else 'staff_read_at'
    conn.execute(f"""
        UPDATE conversations
        SET last_from = ?, last_message_at = CURRENT_TIMESTAMP, {read_col} = CURRENT_TIMESTAMP,
            status = CASE WHEN status = 'closed' AND ? = 'system' THEN 'closed' ELSE ? END,
            closed_at = CASE WHEN ? = 'tenant' THEN NULL ELSE closed_at END
        WHERE id = ?
    """, (sender_type, sender_type, status, sender_type, conversation_id))
    if sender_type == 'tenant':
        # "It started again" on a repair marked fixed puts it back on the open list.
        conn.execute("""
            UPDATE maintenance_issues SET status = 'open', resolved_at = NULL
            WHERE status = 'resolved' AND id = (SELECT issue_id FROM conversations WHERE id = ?)
        """, (conversation_id,))
    return msg_id, saved, rejected


def start_conversation(conn, tenant, unit, prop, topic, subject, body, photos=(),
                       ref_type=None, ref_id=None, category='general'):
    """Open a conversation with the tenant's first message. Repairs also open a
    maintenance issue linked to it. Returns (conversation_id, photos_saved, photos_rejected)."""
    if topic not in TOPICS:
        topic = 'other'
    conv_id = generate_id('CONV')
    issue_id = None
    if topic == 'repair':
        issue_id = generate_id('MAINT')
        conn.execute("""
            INSERT INTO maintenance_issues
                (id, property_id, unit_id, source, raised_by_tenant_id, category, title, description)
            VALUES (?, ?, ?, 'tenant', ?, ?, ?, ?)
        """, (issue_id, prop['id'], unit['id'], tenant['id'], category, subject, body))
    conn.execute("""
        INSERT INTO conversations (id, property_id, tenant_id, unit_id, topic, subject, ref_type, ref_id, issue_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (conv_id, prop['id'], tenant['id'], unit['id'], topic, subject, ref_type, ref_id, issue_id))
    _, saved, rejected = add_message(conn, conv_id, 'tenant', tenant['name'], body, photos)
    return conv_id, saved, rejected


def thread(conn, conversation_id):
    """The conversation, its messages oldest first, and each message's photos."""
    conv = conn.execute("""
        SELECT c.*, t.name AS tenant_name, t.phone AS tenant_phone, t.short_code, u.unit_number,
               mi.status AS issue_status, mi.category AS issue_category, p.name AS property_name
        FROM conversations c
        JOIN tenants t ON t.id = c.tenant_id
        JOIN properties p ON p.id = c.property_id
        LEFT JOIN units u ON u.id = c.unit_id
        LEFT JOIN maintenance_issues mi ON mi.id = c.issue_id
        WHERE c.id = ?
    """, (conversation_id,)).fetchone()
    if not conv:
        return None, []
    msgs = [dict(m) for m in conn.execute("""
        SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at, rowid
    """, (conversation_id,)).fetchall()]
    photos = {}
    for p in conn.execute("SELECT * FROM conversation_photos WHERE conversation_id = ? ORDER BY rowid",
                          (conversation_id,)).fetchall():
        photos.setdefault(p['message_id'], []).append(dict(p))
    for m in msgs:
        m['photos'] = photos.get(m['id'], [])
    return dict(conv), msgs


def list_for_staff(conn, property_id, caretaker=False, status=None):
    """Conversations for a property, most urgent first: awaiting a reply (oldest
    first — they have waited longest), then the rest by latest activity. The
    caretaker sees repairs only."""
    where = ["c.property_id = ?"]
    args = [property_id]
    if caretaker:
        where.append("c.topic = 'repair'")
    if status:
        where.append("c.status = ?")
        args.append(status)
    return [dict(r) for r in conn.execute(f"""
        SELECT c.*, t.name AS tenant_name, u.unit_number,
               (SELECT COUNT(*) FROM conversation_photos p WHERE p.conversation_id = c.id) AS photo_count,
               (SELECT body FROM conversation_messages m WHERE m.conversation_id = c.id
                ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) AS last_body,
               CAST((julianday('now') - julianday(c.last_message_at)) * 24 AS INTEGER) AS hours_waiting
        FROM conversations c
        JOIN tenants t ON t.id = c.tenant_id
        LEFT JOIN units u ON u.id = c.unit_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE c.status WHEN 'awaiting_reply' THEN 0 WHEN 'replied' THEN 1 ELSE 2 END,
                 CASE WHEN c.status = 'awaiting_reply' THEN c.last_message_at END ASC,
                 c.last_message_at DESC
    """, args).fetchall()]


def list_for_tenant(conn, tenant_id):
    return [dict(r) for r in conn.execute("""
        SELECT c.*,
               (c.last_from IN ('admin', 'caretaker', 'system')
                AND (c.tenant_read_at IS NULL OR c.tenant_read_at < c.last_message_at)) AS unread
        FROM conversations c WHERE c.tenant_id = ?
        ORDER BY c.last_message_at DESC
    """, (tenant_id,)).fetchall()]


def awaiting_count(conn, property_id, caretaker=False):
    q = "SELECT COUNT(*) FROM conversations WHERE property_id = ? AND status = 'awaiting_reply'"
    if caretaker:
        q += " AND topic = 'repair'"
    return conn.execute(q, (property_id,)).fetchone()[0] or 0


def mark_read(conn, conversation_id, side):
    col = 'tenant_read_at' if side == 'tenant' else 'staff_read_at'
    conn.execute(f"UPDATE conversations SET {col} = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))


def close(conn, conversation_id, closed_by):
    conn.execute("""
        UPDATE conversations SET status = 'closed', closed_at = CURRENT_TIMESTAMP, closed_by = ? WHERE id = ?
    """, (closed_by, conversation_id))


def ref_summary(conn, tenant_id, ref_type, ref_id):
    """A one-line description of the ledger line a question is about — or None
    if it is not this tenant's."""
    if ref_type == 'payment':
        r = conn.execute("""
            SELECT p.amount, p.payment_date,
                   COALESCE(bt.mpesa_ref, pc.mpesa_ref,
                            CASE WHEN p.source IN ('daraja', 'pesapal') THEN p.assignment_reason END) AS ref
            FROM payments p
            LEFT JOIN bank_transactions bt ON bt.id = p.bank_txn_id
            LEFT JOIN payment_claims pc ON pc.id = p.claim_id
            WHERE p.id = ? AND p.tenant_id = ?
        """, (ref_id, tenant_id)).fetchone()
        if r:
            return f"Payment of KES {r['amount']:,.0f} on {(r['payment_date'] or '')[:10]}" + (f" ({r['ref']})" if r['ref'] else '')
    elif ref_type == 'charge':
        r = conn.execute("SELECT amount, period, charge_type FROM rent_charges WHERE id = ? AND tenant_id = ?",
                         (ref_id, tenant_id)).fetchone()
        if r:
            what = {'rent': 'Rent', 'service': 'Service charge', 'water': 'Water'}.get(r['charge_type'], r['charge_type'])
            period = 'brought forward' if r['period'] == 'ARREARS' else r['period']
            return f"{what} {period}: KES {r['amount']:,.0f}"
    return None


def notify_tenant_of_reply(conv, base_url, what='a reply to'):
    """SMS the tenant that a reply (or update) is waiting. The link opens their
    own account, which still asks for their PIN — the SMS carries no content."""
    from src.messaging.delivery import send_sms_async
    if not conv.get('tenant_phone') or not conv.get('short_code'):
        return
    link = f"{base_url.rstrip('/')}/t/{conv['short_code']}"
    send_sms_async([conv['tenant_phone']],
                   f"{(conv.get('property_name') or '').title()}: there is {what} your message "
                   f"\"{conv['subject'][:40]}\". Open: {link}")

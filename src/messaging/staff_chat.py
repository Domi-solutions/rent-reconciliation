"""Management ↔ caretaker chat — one ongoing conversation per caretaker.

The caretaker sends reports and questions up; management sends instructions
down. Append-only (correct something by sending another message); photos go
through the same re-encode as tenant conversations (EXIF/GPS dropped) and are
served only through ownership-checked routes. All writes go through here.
"""

from src.database.db import generate_id
from src.messaging import threads

REPORT_OUTLINE = ("Report:\n"
                  "- Water:\n"
                  "- Repairs / maintenance:\n"
                  "- Visitors / security:\n"
                  "- Tenants to follow up:\n"
                  "- Other:")


def post(conn, property_id, caretaker_id, sender_type, sender_name, body, kind='message',
         photos=(), account_id=None):
    """Add a message (and up to threads.MAX_PHOTOS photos). Returns (message_id, saved, rejected)."""
    body = (body or '').strip()
    kind = 'report' if kind == 'report' else 'message'
    msg_id = generate_id('SMSG')
    conn.execute("""
        INSERT INTO staff_messages (id, property_id, caretaker_id, sender_type, sender_name,
                                    sender_account_id, kind, body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (msg_id, property_id, caretaker_id, sender_type, sender_name, account_id, kind, body))
    saved = rejected = 0
    for upload in [p for p in photos if p and p.filename][:threads.MAX_PHOTOS]:
        stored = threads._store_photo(upload)
        if not stored:
            rejected += 1
            continue
        name, w, h, size = stored
        conn.execute("""INSERT INTO staff_message_photos (id, message_id, file_name, width, height, size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?)""", (generate_id('SPHO'), msg_id, name, w, h, size))
        saved += 1
    mark_read(conn, caretaker_id, 'caretaker' if sender_type == 'caretaker' else 'admin')
    return msg_id, saved, rejected


def thread(conn, caretaker_id):
    msgs = [dict(m) for m in conn.execute("""
        SELECT m.*, a.name AS account_name FROM staff_messages m
        LEFT JOIN admin_users a ON a.id = m.sender_account_id
        WHERE m.caretaker_id = ? ORDER BY m.created_at, m.rowid   -- rowid = order written (ids are random)
    """, (caretaker_id,))]
    photos = {}
    if msgs:
        marks = ','.join('?' * len(msgs))
        for p in conn.execute(f"SELECT * FROM staff_message_photos WHERE message_id IN ({marks})",
                              [m['id'] for m in msgs]):
            photos.setdefault(p['message_id'], []).append(dict(p))
    reads = {r['side']: r['read_at'] for r in conn.execute(
        "SELECT side, read_at FROM staff_chat_reads WHERE caretaker_id = ?", (caretaker_id,))}
    for m in msgs:
        m['photos'] = photos.get(m['id'], [])
        # Ticks: seen once the other side has opened the chat after it was sent
        other_read = reads.get('caretaker' if m['sender_type'] == 'admin' else 'admin')
        m['seen'] = bool(other_read and other_read > m['created_at'])
    return msgs


def _unread_sql(side):
    other = 'caretaker' if side == 'admin' else 'admin'
    return f"""
        SELECT COUNT(*) FROM staff_messages m
        LEFT JOIN staff_chat_reads r ON r.caretaker_id = m.caretaker_id AND r.side = '{side}'
        WHERE m.sender_type = '{other}' AND (r.read_at IS NULL OR m.created_at > r.read_at)
    """


def unread_for_admin(conn, property_id):
    return conn.execute(_unread_sql('admin') + " AND m.property_id = ?", (property_id,)).fetchone()[0]


def unread_for_caretaker(conn, caretaker_id):
    return conn.execute(_unread_sql('caretaker') + " AND m.caretaker_id = ?", (caretaker_id,)).fetchone()[0]


def mark_read(conn, caretaker_id, side):
    conn.execute("""
        INSERT INTO staff_chat_reads (caretaker_id, side, read_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (caretaker_id, side) DO UPDATE SET read_at = CURRENT_TIMESTAMP
    """, (caretaker_id, side))


def chats_for_admin(conn, property_id):
    """Every caretaker of the property, plus former ones with history; unread first."""
    rows = conn.execute("""
        SELECT c.caretaker_id,
               COALESCE(ct.name, (SELECT m2.sender_name FROM staff_messages m2
                                  WHERE m2.caretaker_id = c.caretaker_id AND m2.sender_type = 'caretaker'
                                  ORDER BY m2.created_at DESC, m2.rowid DESC LIMIT 1), 'Former caretaker') AS name,
               ct.id IS NOT NULL AS current,
               ct.phone,
               (SELECT body FROM staff_messages WHERE caretaker_id = c.caretaker_id
                ORDER BY created_at DESC, rowid DESC LIMIT 1) AS last_body,
               (SELECT created_at FROM staff_messages WHERE caretaker_id = c.caretaker_id
                ORDER BY created_at DESC LIMIT 1) AS last_at,
               (SELECT COUNT(*) FROM staff_messages m
                LEFT JOIN staff_chat_reads r ON r.caretaker_id = m.caretaker_id AND r.side = 'admin'
                WHERE m.caretaker_id = c.caretaker_id AND m.sender_type = 'caretaker'
                  AND (r.read_at IS NULL OR m.created_at > r.read_at)) AS unread
        FROM (SELECT id AS caretaker_id FROM caretakers WHERE property_id = :pid
              UNION SELECT DISTINCT caretaker_id FROM staff_messages WHERE property_id = :pid) c
        LEFT JOIN caretakers ct ON ct.id = c.caretaker_id
    """, {'pid': property_id}).fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: r['last_at'] or '', reverse=True)            # most recent first …
    out.sort(key=lambda r: (-(r['unread'] or 0), not r['current']))   # … within unread-first, current-first
    return out


def photo(conn, photo_id):
    """(photo row, message row) or (None, None)."""
    p = conn.execute("SELECT * FROM staff_message_photos WHERE id = ?", (photo_id,)).fetchone()
    if not p:
        return None, None
    m = conn.execute("SELECT * FROM staff_messages WHERE id = ?", (p['message_id'],)).fetchone()
    return p, m


def notify_caretaker(conn, caretaker_id, property_id, base_url):
    """One SMS that a message from management is waiting — never the content."""
    ct = conn.execute("SELECT phone FROM caretakers WHERE id = ?", (caretaker_id,)).fetchone()
    prop = conn.execute("SELECT name FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not ct or not ct['phone'] or not base_url:
        return
    from src.messaging.delivery import send_sms_async
    send_sms_async([{'phone': ct['phone']}],
                   f"{(prop['name'] if prop else '').title()}: management sent you a message. "
                   f"Open: {base_url.rstrip('/')}/caretaker/{property_id}/management")

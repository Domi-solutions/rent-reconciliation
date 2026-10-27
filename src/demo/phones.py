"""Demo site: real SMS, but only to phones someone typed into the demo.

The made-up tenants carry made-up numbers (0700 000 1xx) that may belong to real
strangers, so they never receive anything. When a person signs in as a new
tenant (or pays) with their own number, that number becomes the demo tenant's
and is "claimed": from then on the codes, receipts, replies and notices that
tenant would get arrive on that phone for real. Caps keep a public demo from
running up a bill. Reset forgets every claimed number (fresh database).
"""

from datetime import date

from src.utils.phone import normalize_to_e164

DAILY_CAP = 40          # all demo SMS in a day
PER_PHONE_CAP = 8       # per claimed number in a day
FAKE_PREFIX = '+2547000001'


def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS demo_phones (
                        phone TEXT PRIMARY KEY, tenant_id TEXT, claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        sms_day TEXT, sms_count INTEGER DEFAULT 0)""")
    conn.execute("CREATE TABLE IF NOT EXISTS demo_sms_days (day TEXT PRIMARY KEY, count INTEGER DEFAULT 0)")


def _claim(conn, tenant_id, phone):
    conn.execute("UPDATE tenants SET phone = ? WHERE id = ?", (phone, tenant_id))
    conn.execute("""INSERT INTO demo_phones (phone, tenant_id) VALUES (?, ?)
                    ON CONFLICT(phone) DO UPDATE SET tenant_id = excluded.tenant_id""", (phone, tenant_id))


def claim_first_time(conn, raw_phone):
    """A new tenant signing in with their own number: give it to the first-time demo tenant
    (or the next made-up tenant who has not signed in yet). No-op for numbers already on record."""
    _ensure(conn)
    phone = normalize_to_e164(raw_phone)
    if not phone or phone.startswith(FAKE_PREFIX):
        return None
    if conn.execute("SELECT 1 FROM tenants WHERE phone = ? AND status = 'active'", (phone,)).fetchone():
        return None
    from src.demo.seed import LOGINS
    first = LOGINS['tenant_new']['unit']
    row = conn.execute("""
        SELECT t.id FROM tenants t JOIN units u ON u.id = t.unit_id
        LEFT JOIN persons p ON p.id = t.person_id
        WHERE t.status = 'active' AND t.phone LIKE ? AND p.pin_hash IS NULL
        ORDER BY CASE WHEN u.unit_number = ? THEN 0 ELSE 1 END, u.unit_number LIMIT 1
    """, (FAKE_PREFIX + '%', first)).fetchone()
    if not row:
        return None
    _claim(conn, row['id'], phone)
    return row['id']


def claim_for_tenant(conn, tenant_id, raw_phone):
    """The phone typed on the Pay page: receipts for this demo tenant go there."""
    _ensure(conn)
    phone = normalize_to_e164(raw_phone)
    if phone and not phone.startswith(FAKE_PREFIX):
        _claim(conn, tenant_id, phone)
    return phone


def allowed(conn, numbers):
    """Split E.164 numbers into (send, hold) and count what is sent against the caps."""
    _ensure(conn)
    today = date.today().isoformat()
    day = conn.execute("SELECT count FROM demo_sms_days WHERE day = ?", (today,)).fetchone()
    sent_today = day['count'] if day else 0
    send, hold = [], []
    for num in numbers:
        row = conn.execute("SELECT * FROM demo_phones WHERE phone = ?", (num,)).fetchone()
        mine = (row['sms_count'] or 0) if row and row['sms_day'] == today else 0
        if not row:
            hold.append((num, 'demo: this number was not entered in the demo'))
        elif sent_today >= DAILY_CAP or mine >= PER_PHONE_CAP:
            hold.append((num, 'demo: daily SMS limit reached'))
        else:
            send.append(num)
            sent_today += 1
            conn.execute("UPDATE demo_phones SET sms_day = ?, sms_count = ? WHERE phone = ?", (today, mine + 1, num))
    conn.execute("""INSERT INTO demo_sms_days (day, count) VALUES (?, ?)
                    ON CONFLICT(day) DO UPDATE SET count = excluded.count""", (today, sent_today))
    return send, hold

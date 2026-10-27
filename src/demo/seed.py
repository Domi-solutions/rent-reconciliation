"""Demo data: a made-up building, managed by a made-up law firm, for a made-up owner.

Built through the same functions the real app uses (lease intake, allocation,
conversations, caretaker chat, expenses), so every screen behaves exactly as it
would for a real property. Dates are relative to today, so the demo never goes
stale. Nothing here is a real person or a real phone number in use (all SMS is
off on the demo site anyway).
"""

from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from src.database.db import allocate_payment, generate_id
from src.utils.due_dates import charge_due_date

ORG_NAME = 'Wakili & Associates Advocates'
PROPERTY_NAME = 'kijani court'
LOGINS = {
    'admin': {'email': 'admin@demo.domi.co.ke', 'password': 'Demo2026!', 'name': 'Grace Wanjiru'},
    'owner': {'email': 'owner@demo.domi.co.ke', 'password': 'Demo2026!', 'name': 'Mary Akinyi'},
    'caretaker': {'name': 'Juma', 'password': '1234'},
    'tenant_new': {'phone': '0700000103', 'name': 'Amina Wanjiku', 'unit': 'A3'},
    'tenant': {'phone': '0700000101', 'name': 'Brian Otieno', 'unit': 'A1', 'pin': '1234'},
}

# unit, rent, size, tenant, phone, pattern
#   pays      — pays every month on time
#   late1     — a month behind
#   late2     — two months behind
#   late3     — stopped paying three months ago
#   credit    — paid ahead
#   new       — moved in last month
UNITS = [
    ('A1', 18000, '2 Bedroom', 'Brian Otieno', '0700000101', 'pays'),
    ('A2', 18000, '2 Bedroom', 'Faith Chebet', '0700000102', 'pays'),
    ('A3', 15000, '1 Bedroom', 'Amina Wanjiku', '0700000103', 'late1'),   # owes this month: pays it in the demo
    ('A4', 15000, '1 Bedroom', 'Kevin Mutua', '0700000104', 'late1'),
    ('A5', 15000, '1 Bedroom', 'Lucy Njeri', '0700000105', 'pays'),
    ('A6', 22000, '3 Bedroom', 'Peter Kamau', '0700000106', 'late3'),
    ('B1', 18000, '2 Bedroom', 'Sarah Achieng', '0700000107', 'pays'),
    ('B2', 18000, '2 Bedroom', 'David Kiprop', '0700000108', 'late2'),
    ('B3', 15000, '1 Bedroom', 'Esther Mwende', '0700000109', 'credit'),
    ('B4', 15000, '1 Bedroom', 'John Odhiambo', '0700000110', 'pays'),
    ('B5', 22000, '3 Bedroom', 'Ruth Wambui', '0700000111', 'new'),
    ('B6', 15000, '1 Bedroom', None, None, None),      # vacant
]
WATER = {'A1': 650, 'A2': 900, 'A3': 400, 'A4': 750, 'A5': 520, 'A6': 1200, 'B1': 800, 'B2': 980,
         'B3': 450, 'B4': 610, 'B5': 1100}
SERVICE = 1000


def _months_back(today, n):
    """Period keys for the last n months up to and including this one, oldest first."""
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def _ref(i):
    return 'TJ' + format(0x5A1B3C + i * 7919, 'X')[-8:].rjust(8, 'K')


def seed(conn, today=None):
    from src.leases import intake
    from src.messaging import staff_chat, threads
    from src.money import expenses as ex

    today = today or date.today()
    org_id, pid = generate_id('ORG'), generate_id('PROP')
    conn.execute("INSERT INTO organizations (id, name, slug, contact_email, is_active) VALUES (?, ?, ?, ?, 1)",
                 (org_id, ORG_NAME, 'wakili-demo', 'office@demo.domi.co.ke'))
    a = LOGINS['admin']
    conn.execute("""INSERT INTO admin_users (id, org_id, name, email, password_hash, role, created_by)
                    VALUES (?, ?, ?, ?, ?, 'lead', 'demo seed')""",
                 (generate_id('ADM'), org_id, a['name'], a['email'], generate_password_hash(a['password'])))
    conn.execute("""INSERT INTO properties (id, name, address, organization_id, rent_due_day, status,
                                            pay_paybill, pay_account, pay_account_name)
                    VALUES (?, ?, ?, ?, 5, 'active', ?, ?, ?)""",
                 (pid, PROPERTY_NAME, 'Mlolongo, Machakos', org_id, '400999', '{unit}',
                  'Wakili & Associates for Kijani Court'))
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (pid,)).fetchone()

    # Owner (portfolio sign-in) and caretaker
    o = LOGINS['owner']
    owner_id, person_id = generate_id('OWNER'), generate_id('PERSON')
    conn.execute("INSERT INTO persons (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                 (person_id, o['name'], o['email'], generate_password_hash(o['password'])))
    conn.execute("INSERT INTO owners (id, name, phone, email, org_id, person_id) VALUES (?, ?, ?, ?, ?, ?)",
                 (owner_id, 'Kijani Holdings Ltd', '+254700000201', o['email'], org_id, person_id))
    conn.execute("INSERT INTO property_owners (property_id, owner_id, is_primary) VALUES (?, ?, 1)", (pid, owner_id))
    c = LOGINS['caretaker']
    ct_id = generate_id('CTKR')
    conn.execute("INSERT INTO caretakers (id, property_id, name, phone, password_hash) VALUES (?, ?, ?, ?, ?)",
                 (ct_id, pid, c['name'], '+254700000301', generate_password_hash(c['password'])))

    # Units, then a lease for every occupied one (the real intake path)
    months = _months_back(today, 4)                   # e.g. Jul, Aug, Sep, Oct
    first_month = date.fromisoformat(months[0] + '-01')
    tenants = {}
    for num, rent, size, name, phone, pattern in UNITS:
        uid = f'{pid}-{num}'
        conn.execute("""INSERT INTO units (id, property_id, unit_number, monthly_rent, service_charge, apartment_size,
                                           status, status_changed_at) VALUES (?, ?, ?, ?, ?, ?, 'vacant', CURRENT_TIMESTAMP)""",
                     (uid, pid, num, rent, SERVICE, size))
        if not name:
            continue
        start = (date.fromisoformat(months[-2] + '-01') if pattern == 'new'
                 else first_month - timedelta(days=200)).replace(day=1)
        lease_id, tid = intake.record_lease(conn, prop, {
            'unit_id': uid, 'who': '', 'name': name, 'phone': phone, 'national_id': f'2{len(tenants):02d}45{len(tenants):03d}',
            'start_date': start.isoformat(), 'monthly_rent': str(rent), 'service_charge': str(SERVICE),
            'deposit_required': str(rent), 'deposit_held': str(rent),
            'signed': '' if num in ('B4', 'B5') else '1', 'signed_on': start.isoformat(),
            'brought_forward': '0'}, 'demo seed')
        tenants[num] = {'id': tid, 'unit_id': uid, 'rent': rent, 'pattern': pattern, 'name': name, 'phone': phone,
                        'start': start}

    # Charges: rent + service every month, water from the second month (billed in arrears)
    for i, period in enumerate(months):
        if period == today.strftime('%Y-%m') and today.day < 1:
            continue
        due = charge_due_date(period, 5)
        for num, t in tenants.items():
            if period < t['start'].strftime('%Y-%m'):
                continue
            for ctype, amount in (('rent', t['rent']), ('service', SERVICE)) + (
                    (('water', WATER[num]),) if i > 0 else ()):
                conn.execute("""INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount, due_date)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""", (generate_id('CHG'), pid, t['unit_id'], period, ctype, amount, due))

    # Payments, by pattern (M-Pesa, booked like the paybill would book them)
    n = 0
    for i, period in enumerate(months):
        y, m = map(int, period.split('-'))
        for num, t in tenants.items():
            if period < t['start'].strftime('%Y-%m'):
                continue
            pattern, monthly = t['pattern'], t['rent'] + SERVICE + (WATER[num] if i > 0 else 0)
            pay_day = date(y, m, min(2 + (n % 4), 28))
            if pay_day > today:
                continue
            amount = monthly
            if pattern == 'late1' and i == len(months) - 1:
                continue                                   # skipped this month
            if pattern == 'late2' and i >= len(months) - 2:
                continue
            if pattern == 'late3' and i >= len(months) - 3:
                continue
            if pattern == 'credit' and i == len(months) - 1:
                amount = monthly + 5000
            pay_id = generate_id('PAY')
            ref = _ref(n)
            conn.execute("""INSERT INTO payments (id, property_id, unit_id, amount, payment_date, assignment_type,
                                                  assignment_reason, assigned_by, source)
                            VALUES (?, ?, ?, ?, ?, 'daraja', ?, 'M-Pesa', 'daraja')""",
                         (pay_id, pid, t['unit_id'], amount, pay_day.isoformat(), ref))
            allocate_payment(conn, pay_id, t['unit_id'], amount)
            n += 1

    # Two bank lines not yet matched — to show matching
    stmt = generate_id('STMT')
    conn.execute("""INSERT INTO bank_statements (id, org_id, filename, file_path, period_start, period_end, bank_format, status)
                    VALUES (?, ?, 'Demo bank statement.pdf', 'demo', ?, ?, 'family_bank', 'active')""",
                 (stmt, org_id, months[-1] + '-01', today.isoformat()))
    for sender, amount, ref in (('KEVIN MUTUA NZIOKA', 8000, 'TJ9XK2LMQ1'), ('ALEX WAFULA', 3000, 'TJ9XK2LMQ2')):
        conn.execute("""INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date, raw_text)
                        VALUES (?, ?, ?, ?, 'PAYBILL_CREDIT', ?, ?, ?)""",
                     (generate_id('TXN'), stmt, ref, amount, sender, (today - timedelta(days=2)).isoformat(),
                      f'Paybill Credit {ref} {sender}'))

    # Conversations: a repair the caretaker handled, a money question waiting for management
    t2 = tenants['A2']
    unit2 = conn.execute("SELECT * FROM units WHERE id = ?", (t2['unit_id'],)).fetchone()
    tenant2 = conn.execute("SELECT * FROM tenants WHERE id = ?", (t2['id'],)).fetchone()
    conv, _, _ = threads.start_conversation(conn, dict(tenant2), unit2, prop, 'repair', 'Kitchen sink leaking',
                                            'The pipe under the kitchen sink is dripping since yesterday.',
                                            category='plumbing')
    threads.add_message(conn, conv, 'caretaker', c['name'], 'I will come at 4pm today with the plumber.', account_id=ct_id)
    t6 = tenants['B1']
    unit6 = conn.execute("SELECT * FROM units WHERE id = ?", (t6['unit_id'],)).fetchone()
    tenant6 = conn.execute("SELECT * FROM tenants WHERE id = ?", (t6['id'],)).fetchone()
    threads.start_conversation(conn, dict(tenant6), unit6, prop, 'money', 'Water bill higher this month',
                               'Hello, my water bill went up. Can you confirm the meter reading?')

    # A notice to everyone
    batch = generate_id('BATCH')
    for t in tenants.values():
        conn.execute("""INSERT INTO messages (id, property_id, tenant_id, batch_id, subject, body, template_body,
                                              message_type, delivery_channel, delivery_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'broadcast', 'sms', 'delivered')""",
                     (generate_id('MSG'), pid, t['id'], batch, 'Water tank cleaning',
                      'Water will be off on Saturday from 9am to 1pm for tank cleaning. Thank you.',
                      'Water will be off on Saturday from 9am to 1pm for tank cleaning. Thank you.'))

    # Caretaker ↔ management
    staff_chat.post(conn, pid, ct_id, 'caretaker', c['name'],
                    staff_chat.REPORT_OUTLINE.replace('- Water:', '- Water: all meters read, B2 meter glass cracked')
                    .replace('- Repairs / maintenance:', '- Repairs / maintenance: A2 sink fixed')
                    .replace('- Tenants to follow up:', '- Tenants to follow up: A6, B2'), kind='report', account_id=ct_id)
    staff_chat.post(conn, pid, ct_id, 'admin', a['name'], 'Thanks Juma. Please get a quote for the B2 meter.')

    # Money out
    ago = lambda d: (today - timedelta(days=d)).isoformat()
    for cat, amt, day, desc, to, method in (
            ('security', 12000, 40, 'Night guard — last month', 'Salama Guards', 'bank'),
            ('wages', 15000, 30, 'Caretaker salary', 'Juma', 'mpesa'),
            ('water', 9800, 20, 'County water bill', 'Mavoko Water', 'bank'),
            ('garbage', 2500, 18, 'Garbage collection', 'Clean Kenya', 'mpesa'),
            ('owner_payout', 180000, 25, 'Rent to owner, last month', 'Kijani Holdings Ltd', 'bank')):
        ex.record(conn, prop, {'category': cat, 'amount': str(amt), 'expense_date': ago(day), 'description': desc,
                               'paid_to': to, 'already_paid': '1', 'paid_method': method,
                               'paid_reference': 'REF' + str(amt)}, 'admin:' + a['name'])
    actor_ct = f"caretaker:{c['name']}"
    e1, _, _ = ex.record(conn, prop, {'category': 'repairs', 'amount': '2300', 'expense_date': ago(6),
                                      'description': 'Sink pipe and fittings for A2', 'paid_to': 'Athi Hardware',
                                      'paid_myself': '1'}, actor_ct, by='caretaker', caretaker_id=ct_id)
    ex.decide(conn, ex.get(conn, e1, pid), True, 'Thanks — paid back today', 'admin:' + a['name'])
    ex.mark_paid(conn, ex.get(conn, e1, pid), 'mpesa', 'TJ8PAYBACK1', 'admin:' + a['name'])
    ex.record(conn, prop, {'category': 'repairs', 'amount': '650', 'expense_date': ago(1),
                           'description': '3 bulbs for the stairway', 'paid_to': 'Athi Hardware',
                           'paid_myself': '1'}, actor_ct, by='caretaker', caretaker_id=ct_id)

    # The returning tenant can sign in straight away (phone + PIN, and a payment PIN)
    from src.utils.tenant_auth import person_for_phone, set_pin, tenancies_for_phone
    rt = LOGINS['tenant']
    person = person_for_phone(conn, rt['phone'], tenancies_for_phone(conn, rt['phone']))
    set_pin(conn, person, rt['pin'])
    conn.execute("UPDATE tenants SET portal_pin_hash = ? WHERE id = ?",
                 (generate_password_hash(rt['pin']), tenants[rt['unit']]['id']))
    return pid

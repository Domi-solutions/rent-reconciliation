"""The payment receipt a tenant gets by SMS — one wording for every way a payment is recorded.

  "Mowin Apartments: payment of KES 18,000 confirmed (UJ81O9VVRZ, 9 Oct).
   Applied: Oct rent 17,000, Oct service 1,000. Balance: KES 4,500 owed."

What it settled comes from payment_allocations; the balance is the tenant's own
account today (tenant_balances), so the same figure the portal shows. The portal
link is added only once tenant outreach is approved (TENANT_OUTREACH_ENABLED):
a receipt must not double as a sign-in invitation during onboarding. A missing
portal token never grants access either way.
"""

import calendar

from src.messaging.delivery import plain_sms, sms_parts
from src.messaging.outreach import enabled as outreach_enabled
from src.utils.phone import normalize_to_e164

MAX_PARTS = 2                     # keep a receipt to two SMS at most (cost)
_TYPE = {'rent': 'rent', 'service': 'service', 'water': 'water', 'brought_forward': 'brought forward'}


def _period(period, paid_year):
    if period == 'ARREARS':
        return 'arrears'
    try:
        y, m = period.split('-')
        label = calendar.month_abbr[int(m)]
        return label if y == paid_year else f'{label} {y}'
    except (ValueError, IndexError, AttributeError):
        return period or ''


def _day(iso):
    try:
        y, m, d = iso[:10].split('-')
        return f'{int(d)} {calendar.month_abbr[int(m)]}'
    except (ValueError, AttributeError, IndexError):
        return ''


def receipt_sms(conn, payment_id, base_url=None, fallback_phone=None):
    """(phone, body) for this payment's receipt, or None if there is nobody to text.
    fallback_phone (the phone that paid by STK) is used only when the tenancy has none,
    and then without the portal link."""
    p = conn.execute("""
        SELECT p.*, bt.mpesa_ref AS bank_ref, pr.name AS property_name
        FROM payments p
        LEFT JOIN bank_transactions bt ON bt.id = p.bank_txn_id
        LEFT JOIN properties pr ON pr.id = p.property_id
        WHERE p.id = ?
    """, (payment_id,)).fetchone()
    if not p:
        return None
    tenant = None
    if p['tenant_id']:
        tenant = conn.execute("SELECT * FROM tenants WHERE id = ?", (p['tenant_id'],)).fetchone()
    if not tenant:
        tenant = conn.execute("SELECT * FROM tenants WHERE unit_id = ? AND status = 'active'",
                              (p['unit_id'],)).fetchone()
    phone = normalize_to_e164(tenant['phone']) if tenant else None
    payer_only = False
    if not phone and tenant:
        phone, payer_only = normalize_to_e164(fallback_phone), True
    if not phone:
        return None

    ref = p['bank_ref'] or (p['assignment_reason'] if p['source'] == 'daraja' else None)
    when = _day(p['payment_date'] or '')
    head = (f"{(p['property_name'] or '').title()}: payment of KES {float(p['amount']):,.0f} confirmed"
            + (f" ({', '.join(x for x in (ref, when) if x)})" if ref or when else '') + '.')

    allocs = conn.execute("""
        SELECT pa.amount, rc.charge_type, rc.period FROM payment_allocations pa
        JOIN rent_charges rc ON rc.id = pa.charge_id
        WHERE pa.payment_id = ?
        ORDER BY CASE WHEN rc.period = 'ARREARS' THEN '0000-00' ELSE rc.period END, rc.charge_type
    """, (payment_id,)).fetchall()
    year = (p['payment_date'] or '')[:4]
    applied = ', '.join(f"{_period(a['period'], year)} {_TYPE.get(a['charge_type'], a['charge_type'])} "
                        f"{float(a['amount']):,.0f}" for a in allocs)
    settled = f' Applied: {applied}.' if allocs else ' Held as credit for your next charges.'

    bal = conn.execute("SELECT balance FROM tenant_balances WHERE tenant_id = ?", (tenant['id'],)).fetchone()
    owed = round(float(bal['balance'] if bal else 0))
    balance = (f' Balance: KES {owed:,} owed.' if owed > 0
               else f' Balance: KES {-owed:,} in credit.' if owed < 0 else ' Balance: nothing owed.')

    link = ''
    if outreach_enabled() and not payer_only and base_url and tenant['short_code'] and tenant['access_token']:
        link = f" Receipt: {base_url.rstrip('/')}/t/{tenant['short_code']}"

    body = plain_sms(head + settled + balance + link)
    if sms_parts(body) > MAX_PARTS and allocs:
        # Many small charges: say how many rather than list them all.
        body = plain_sms(head + f' Applied to {len(allocs)} charges, oldest first.' + balance + link)
    return phone, body


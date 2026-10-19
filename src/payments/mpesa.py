"""
M-Pesa payments into the property's own paybill: record, check, book.

Two ways money arrives:
  STK Push — the tenant presses Pay in the portal; a payment_transactions row is
             written when the prompt is sent (we know the tenant and the amount),
             and Safaricom's callback fills in the result.
  C2B      — the tenant pays the paybill from their phone menu with an account
             number. Safaricom calls the confirmation URL; the account number says
             whose payment it is (the tenant's short code, or the unit number).

Neither callback is believed on its own:
  - both URLs carry a secret token (MPESA_CALLBACK_TOKEN) only Safaricom is given;
  - an STK result is confirmed with Safaricom's STK query API before booking,
    and the amount paid must equal the amount asked for;
  - one M-Pesa receipt number is booked once, whichever way it arrives first.

Booking creates a `payments` row for the tenant (source 'daraja'), allocates it
oldest-charge-first, and returns the confirmation SMS for the caller to send.
This module writes to the DB and returns — it never sends messages itself.
"""

import hmac
import json
import logging
import os
import re
import threading
from datetime import datetime

from src.database.db import allocate_payment, generate_id, get_connection
from src.payments import daraja

logger = logging.getLogger(__name__)

MAX_QUERY_ATTEMPTS = 10          # ~10 minutes at the worker's 60 s interval
_book_lock = threading.Lock()    # one booking at a time: the callback thread and the worker can overlap


# ── Callback authenticity ────────────────────────────────────────────────────

def callback_allowed(request):
    """The callback URL's secret token must match. In production a missing token
    setting refuses everything rather than accepting anything."""
    expected = os.environ.get('MPESA_CALLBACK_TOKEN', '')
    if not expected:
        return os.environ.get('DARAJA_ENV') != 'production'
    given = request.args.get('t', '')
    if not hmac.compare_digest(given, expected):
        return False
    allowed_ips = [ip.strip() for ip in os.environ.get('MPESA_ALLOWED_IPS', '').split(',') if ip.strip()]
    if allowed_ips:
        client = request.headers.get('Fly-Client-IP') or request.remote_addr or ''
        return client in allowed_ips
    return True


# ── Recording (called from the webhook routes; fast, no network) ─────────────

def _mpesa_time(raw):
    """'20261019143005' → '2026-10-19 14:30:05' (Safaricom sends Kenyan local time)."""
    raw = str(raw or '')
    if len(raw) >= 14 and raw[:14].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
    return None


def record_stk_callback(body):
    """Store an STK callback against the request it answers. Returns the
    payment_transactions id to process, or None."""
    parsed = daraja.parse_stk_callback(body)
    checkout_id = parsed.get('checkout_request_id')
    if not checkout_id:
        return None
    raw = json.dumps(body)
    with get_connection() as conn:
        row = conn.execute("SELECT id, processing_status FROM payment_transactions WHERE external_reference = ?",
                           (checkout_id,)).fetchone()
        if not row:
            # Every STK Push we send is recorded first, so this is not ours (or forged).
            conn.execute("""
                INSERT INTO payment_transactions (id, source, external_reference, amount, raw_callback,
                                                  processing_status, error_detail, processed_at)
                VALUES (?, 'daraja', ?, 0, ?, 'failed', 'callback for an unknown request', CURRENT_TIMESTAMP)
            """, (generate_id('PTXN'), checkout_id, raw))
            return None
        if row['processing_status'] != 'pending':
            return None                                   # already decided; Safaricom retried
        if parsed['result_code'] == 0:
            conn.execute("""
                UPDATE payment_transactions
                SET raw_callback = ?, callback_amount = ?, mpesa_ref = ?, paid_at = ?
                WHERE id = ?
            """, (raw, parsed['amount'], parsed['mpesa_ref'], _mpesa_time(parsed['txn_date']), row['id']))
            return row['id']
        conn.execute("""
            UPDATE payment_transactions
            SET raw_callback = ?, processing_status = 'failed', error_detail = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (raw, parsed['result_desc'] or 'Payment not completed', row['id']))
    return None


def _clean_ref(value):
    return re.sub(r'[\s\-/#]', '', str(value or '')).upper()


def resolve_account(conn, bill_ref):
    """Whose payment an account number names: the tenant's short code, else a unit
    number with one active tenant. Returns (tenant, unit) rows or (None, None)."""
    ref = _clean_ref(bill_ref)
    if not ref:
        return None, None
    t = conn.execute("""
        SELECT t.* FROM tenants t
        WHERE UPPER(t.short_code) = ? AND t.status IN ('active', 'departed') AND t.unit_id IS NOT NULL
    """, (ref,)).fetchone()
    if not t:
        units = conn.execute("""
            SELECT u.id FROM units u JOIN properties p ON p.id = u.property_id
            WHERE p.status = 'active' AND UPPER(REPLACE(REPLACE(u.unit_number, ' ', ''), '-', '')) = ?
        """, (ref,)).fetchall()
        if len(units) != 1:
            return None, None                              # unknown, or ambiguous across properties
        t = conn.execute("SELECT * FROM tenants WHERE unit_id = ? AND status = 'active'",
                         (units[0]['id'],)).fetchall()
        if len(t) != 1:
            return None, None
        t = t[0]
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (t['unit_id'],)).fetchone()
    return t, unit


def validate_c2b(body):
    """Safaricom asks before accepting a paybill payment (only if external
    validation is switched on for the shortcode). Unknown account numbers are
    turned away, so the money never lands without a name on it."""
    with get_connection() as conn:
        tenant, _ = resolve_account(conn, body.get('BillRefNumber'))
    if tenant:
        return {'ResultCode': '0', 'ResultDesc': 'Accepted'}
    return {'ResultCode': 'C2B00012', 'ResultDesc': 'Rejected'}       # invalid account number


def record_c2b_confirmation(body):
    """Store a paybill payment. Returns the payment_transactions id to process, or None."""
    trans_id = str(body.get('TransID') or '').strip()
    if not trans_id:
        return None
    try:
        amount = float(body.get('TransAmount') or 0)
    except (TypeError, ValueError):
        amount = 0.0
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM payment_transactions WHERE external_reference = ?",
                        (f'C2B:{trans_id}',)).fetchone():
            return None                                    # Safaricom retried
        tenant, unit = resolve_account(conn, body.get('BillRefNumber'))
        txn_id = generate_id('PTXN')
        conn.execute("""
            INSERT INTO payment_transactions
                (id, property_id, unit_id, tenant_id, source, external_reference, phone, amount,
                 raw_callback, processing_status, error_detail, mpesa_ref, account_ref, paid_at, callback_amount)
            VALUES (?, ?, ?, ?, 'c2b', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id, unit['property_id'] if unit else None, unit['id'] if unit else None,
              tenant['id'] if tenant else None, f'C2B:{trans_id}', str(body.get('MSISDN') or ''), amount,
              json.dumps(body), 'pending' if tenant else 'unmatched',
              None if tenant else f"account number '{body.get('BillRefNumber') or ''}' not recognised",
              trans_id, str(body.get('BillRefNumber') or ''), _mpesa_time(body.get('TransTime')), amount))
    return txn_id if tenant else None


# ── Booking (worker + instant thread) ────────────────────────────────────────

def _fail(conn, txn_id, why):
    conn.execute("""
        UPDATE payment_transactions SET processing_status = 'failed', error_detail = ?, processed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (why, txn_id))


def _confirm_with_safaricom(conn, txn):
    """'go' to book, 'wait' to ask again later, or 'stop' (failed)."""
    if txn['source'] != 'daraja':
        return 'go'                                         # C2B: the token-protected confirmation is the record
    if txn['callback_amount'] is not None and round(float(txn['callback_amount'])) != round(float(txn['amount'])):
        _fail(conn, txn['id'], f"amount mismatch: asked {txn['amount']:,.0f}, callback says {txn['callback_amount']:,.0f}")
        return 'stop'
    if not daraja.configured():
        if os.environ.get('DARAJA_ENV') == 'production':
            _fail(conn, txn['id'], 'Daraja not configured — cannot confirm with Safaricom')
            return 'stop'
        conn.execute("UPDATE payment_transactions SET verified_via = 'callback only (sandbox)' WHERE id = ?", (txn['id'],))
        return 'go'
    result = daraja.stk_query(txn['external_reference'])
    if result['state'] == 'success':
        conn.execute("UPDATE payment_transactions SET verified_via = 'stk query' WHERE id = ?", (txn['id'],))
        return 'go'
    if result['state'] == 'failed':
        _fail(conn, txn['id'], f"Safaricom says not paid: {result['detail']}")
        return 'stop'
    attempts = (txn['attempts'] or 0) + 1
    if attempts >= MAX_QUERY_ATTEMPTS:
        _fail(conn, txn['id'], f"could not confirm with Safaricom after {attempts} tries: {result['detail']}")
        return 'stop'
    conn.execute("UPDATE payment_transactions SET attempts = ? WHERE id = ?", (attempts, txn['id']))
    return 'wait'


def _format_allocations(allocations):
    """'KES 5,000 to Oct service, KES 5,000 to Nov rent'."""
    parts = []
    for a in allocations:
        period = a.get('period', '')
        label = datetime.strptime(period, '%Y-%m').strftime('%b %Y') if re.fullmatch(r'\d{4}-\d{2}', period) \
            else ('arrears' if period == 'ARREARS' else period)
        parts.append(f"KES {a.get('allocated', 0):,.0f} to {label} {a.get('charge_type', '')}".rstrip())
    return ', '.join(parts)


def book(txn_id, base_url=None):
    """Book one received M-Pesa payment. Returns an SMS dict {'phone', 'body'}
    for the caller to send, or None (nothing to send, or not ready yet)."""
    with _book_lock, get_connection() as conn:
        txn = conn.execute("SELECT * FROM payment_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not txn or txn['processing_status'] != 'pending' or not txn['raw_callback']:
            return None
        decision = _confirm_with_safaricom(conn, txn)
        if decision != 'go':
            return None

        # One receipt, one payment — an STK payment can also arrive as a C2B confirmation.
        if txn['mpesa_ref']:
            dup = conn.execute("""
                SELECT payment_id FROM payment_transactions
                WHERE mpesa_ref = ? AND processing_status = 'completed' AND id != ?
            """, (txn['mpesa_ref'], txn['id'])).fetchone()
            if dup:
                conn.execute("""
                    UPDATE payment_transactions SET processing_status = 'duplicate', payment_id = ?,
                           error_detail = 'same M-Pesa receipt already booked', processed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (dup['payment_id'], txn['id']))
                return None

        tenant = conn.execute("SELECT * FROM tenants WHERE id = ?", (txn['tenant_id'],)).fetchone() \
            if txn['tenant_id'] else None
        if not tenant or not txn['unit_id']:
            conn.execute("""
                UPDATE payment_transactions SET processing_status = 'unmatched', error_detail = 'no tenant for this payment'
                WHERE id = ?
            """, (txn['id'],))
            return None

        amount = float(txn['callback_amount'] if txn['callback_amount'] is not None else txn['amount'])
        paid_on = (txn['paid_at'] or datetime.utcnow().isoformat())[:10]
        payment_id = generate_id('PAY')
        conn.execute("""
            INSERT INTO payments
                (id, property_id, unit_id, tenant_id, claim_id, bank_txn_id, statement_id, amount, payment_date,
                 assignment_type, assignment_reason, assigned_by, source, created_at)
            VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, 'mpesa', 'daraja', CURRENT_TIMESTAMP)
        """, (payment_id, txn['property_id'], txn['unit_id'], tenant['id'], amount, paid_on,
              'mpesa_stk' if txn['source'] == 'daraja' else 'mpesa_paybill', txn['mpesa_ref'] or txn['external_reference']))
        allocations = allocate_payment(conn, payment_id, txn['unit_id'], amount)
        conn.execute("""
            UPDATE payment_transactions SET processing_status = 'completed', processed_at = CURRENT_TIMESTAMP,
                   payment_id = ?, amount = ? WHERE id = ?
        """, (payment_id, amount, txn['id']))

        # A tenant who already pasted this M-Pesa message: their claim is now answered.
        if txn['mpesa_ref']:
            conn.execute("""
                UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP
                WHERE mpesa_ref = ? AND status IN ('pending', 'flagged')
            """, (txn['mpesa_ref'],))
            conn.execute("UPDATE payments SET claim_id = (SELECT id FROM payment_claims WHERE mpesa_ref = ? LIMIT 1) "
                         "WHERE id = ? AND EXISTS (SELECT 1 FROM payment_claims WHERE mpesa_ref = ?)",
                         (txn['mpesa_ref'], payment_id, txn['mpesa_ref']))
            if not conn.execute("SELECT 1 FROM payment_claims WHERE tenant_id = ? AND status = 'flagged'",
                                (tenant['id'],)).fetchone():
                conn.execute("UPDATE tenants SET flagged = 0 WHERE id = ?", (tenant['id'],))

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('mpesa_payment_booked', 'payment', payment_id,
             f"{txn['source']} | KES {amount:,.0f} | ref {txn['mpesa_ref'] or '-'} | tenant {tenant['name']}", 'system'))

        prop = conn.execute("SELECT name FROM properties WHERE id = ?", (txn['property_id'],)).fetchone()
        phone = tenant['phone'] or (txn['phone'] if txn['source'] == 'daraja' else None)
        breakdown = _format_allocations(allocations)
        body = (f"{(prop['name'] if prop else '').title()}: KES {amount:,.0f} payment confirmed"
                + (f", M-Pesa {txn['mpesa_ref']}" if txn['mpesa_ref'] else '') + '.'
                + (f" Applied: {breakdown}." if breakdown else ' Held as credit for your next charges.'))
        base = base_url or os.environ.get('APP_BASE_URL')
        if base and tenant['short_code']:
            body += f" Receipt: {base.rstrip('/')}/t/{tenant['short_code']}"
        return {'phone': phone, 'body': body, 'property_id': txn['property_id']} if phone else None


def book_pending(base_url=None):
    """Worker pass: everything received and not yet booked. Returns SMS dicts to send."""
    with get_connection() as conn:
        ids = [r['id'] for r in conn.execute("""
            SELECT id FROM payment_transactions
            WHERE processing_status = 'pending' AND raw_callback IS NOT NULL
            ORDER BY received_at LIMIT 50
        """).fetchall()]
    out = []
    for txn_id in ids:
        try:
            sms = book(txn_id, base_url)
            if sms:
                out.append(sms)
        except Exception as e:
            logger.exception("Booking M-Pesa payment %s failed", txn_id)
            with get_connection() as conn:
                _fail(conn, txn_id, str(e)[:500])
    return out


def assign_unmatched(conn, txn_id, tenant_id):
    """Admin names the tenant for a paybill payment whose account number was not
    recognised; it is then booked like any other. Returns True if re-queued."""
    txn = conn.execute("SELECT * FROM payment_transactions WHERE id = ? AND processing_status = 'unmatched'",
                       (txn_id,)).fetchone()
    tenant = conn.execute("SELECT * FROM tenants WHERE id = ? AND unit_id IS NOT NULL", (tenant_id,)).fetchone()
    if not txn or not tenant:
        return False
    unit = conn.execute("SELECT property_id FROM units WHERE id = ?", (tenant['unit_id'],)).fetchone()
    conn.execute("""
        UPDATE payment_transactions SET tenant_id = ?, unit_id = ?, property_id = ?, processing_status = 'pending',
               error_detail = NULL WHERE id = ?
    """, (tenant['id'], tenant['unit_id'], unit['property_id'], txn_id))
    return True


def paid_through_mpesa(conn, mpesa_ref):
    """The booked payment for an M-Pesa receipt, if this paybill received it."""
    return conn.execute("""
        SELECT payment_id FROM payment_transactions WHERE mpesa_ref = ? AND processing_status = 'completed'
    """, (mpesa_ref,)).fetchone()

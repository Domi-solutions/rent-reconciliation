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
from decimal import Decimal, InvalidOperation
from datetime import datetime

from src.database.db import allocate_payment, generate_id, get_connection
from src.utils.runtime import is_production
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
        return not is_production() and os.environ.get('DARAJA_ENV') != 'production'
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
            # Safaricom can call back before the initiating request has returned
            # its checkout ID. Keep the result until pay_stk links that request.
            conn.execute("""
                INSERT INTO payment_transactions (id, source, external_reference, amount, raw_callback,
                                                  processing_status, error_detail, callback_amount,
                                                  mpesa_ref, paid_at)
                VALUES (?, 'daraja', ?, 0, ?, 'awaiting_request', ?, ?, ?, ?)
            """, (generate_id('PTXN'), checkout_id, raw,
                  None if parsed['result_code'] == 0 else (parsed['result_desc'] or 'Payment not completed'),
                  parsed['amount'], parsed['mpesa_ref'], _mpesa_time(parsed['txn_date'])))
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


def link_stk_request(conn, checkout_id, org_id, property_id, unit_id, tenant_id, phone, amount):
    """Link an initiated push to its tenant, including an early callback."""
    row = conn.execute("""SELECT id, processing_status FROM payment_transactions
                          WHERE external_reference = ?""", (checkout_id,)).fetchone()
    if row:
        if row['processing_status'] != 'awaiting_request':
            return False
        conn.execute("""
            UPDATE payment_transactions SET org_id = ?, property_id = ?, unit_id = ?, tenant_id = ?,
                phone = ?, amount = ?, processing_status = CASE
                    WHEN error_detail IS NULL THEN 'pending' ELSE 'failed' END,
                processed_at = CASE WHEN error_detail IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END
            WHERE id = ?
        """, (org_id, property_id, unit_id, tenant_id, phone, float(amount), row['id']))
        return True
    conn.execute("""
        INSERT INTO payment_transactions
            (id, org_id, property_id, unit_id, tenant_id, source,
             external_reference, phone, amount, processing_status)
        VALUES (?, ?, ?, ?, ?, 'daraja', ?, ?, ?, 'pending')
    """, (generate_id('PTXN'), org_id, property_id, unit_id, tenant_id,
          checkout_id, phone, float(amount)))
    return True


def _clean_ref(value):
    return re.sub(r'[\s\-/#]', '', str(value or '')).upper()


def collection_org(conn, shortcode):
    """Return the sole organization configured for this receiving paybill."""
    rows = conn.execute("""
        SELECT DISTINCT organization_id FROM properties
        WHERE pay_paybill = ? AND organization_id IS NOT NULL
    """, (str(shortcode or '').strip(),)).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def resolve_account(conn, bill_ref, org_id):
    """Whose payment an account number names: the tenant's short code, else a unit
    number with one active tenant. Returns (tenant, unit) rows or (None, None)."""
    ref = _clean_ref(bill_ref)
    if not ref or not org_id:
        return None, None
    t = conn.execute("""
        SELECT t.* FROM tenants t JOIN properties p ON p.id = t.property_id
        WHERE UPPER(t.short_code) = ? AND p.organization_id = ?
          AND t.status IN ('active', 'departed') AND t.unit_id IS NOT NULL
    """, (ref, org_id)).fetchone()
    if not t:
        units = conn.execute("""
            SELECT u.id FROM units u JOIN properties p ON p.id = u.property_id
            WHERE p.status = 'active' AND p.organization_id = ?
              AND UPPER(REPLACE(REPLACE(u.unit_number, ' ', ''), '-', '')) = ?
        """, (org_id, ref)).fetchall()
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
        org_id = collection_org(conn, body.get('BusinessShortCode'))
        tenant, _ = resolve_account(conn, body.get('BillRefNumber'), org_id)
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
        org_id = collection_org(conn, body.get('BusinessShortCode'))
        tenant, unit = resolve_account(conn, body.get('BillRefNumber'), org_id)
        txn_id = generate_id('PTXN')
        conn.execute("""
            INSERT INTO payment_transactions
                (id, org_id, property_id, unit_id, tenant_id, source, external_reference, phone, amount,
                 raw_callback, processing_status, error_detail, mpesa_ref, account_ref, paid_at, callback_amount)
            VALUES (?, ?, ?, ?, ?, 'c2b', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id, org_id, unit['property_id'] if unit else None, unit['id'] if unit else None,
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
    try:
        asked = Decimal(str(txn['amount']))
        received = Decimal(str(txn['callback_amount']))
        amounts_match = asked.is_finite() and received.is_finite() and asked > 0 and received == asked
    except (InvalidOperation, TypeError, ValueError):
        amounts_match = False
    if not amounts_match:
        _fail(conn, txn['id'], 'STK callback amount missing, invalid, or different from the request')
        return 'stop'
    if not txn['mpesa_ref']:
        _fail(conn, txn['id'], 'STK callback has no M-Pesa receipt number')
        return 'stop'
    from src.utils.runtime import is_demo
    if is_demo() and str(txn['external_reference'] or '').startswith('DEMO-'):
        # Demo site only: the simulated prompt has nobody at Safaricom to confirm it.
        conn.execute("UPDATE payment_transactions SET verified_via = 'demo (simulated)' WHERE id = ?", (txn['id'],))
        return 'go'
    if not daraja.configured():
        if is_production() or os.environ.get('DARAJA_ENV') == 'production':
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

        try:
            received_amount = Decimal(str(txn['callback_amount'] if txn['callback_amount'] is not None
                                          else txn['amount']))
            valid_amount = received_amount.is_finite() and received_amount > 0
        except (InvalidOperation, TypeError, ValueError):
            valid_amount = False
        if not valid_amount:
            _fail(conn, txn['id'], 'payment amount missing or invalid')
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

        amount = float(received_amount)
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
                WHERE property_id = ? AND tenant_id = ? AND mpesa_ref = ?
                  AND status IN ('pending', 'flagged')
            """, (txn['property_id'], tenant['id'], txn['mpesa_ref']))
            claim = conn.execute("""
                SELECT id FROM payment_claims
                WHERE property_id = ? AND tenant_id = ? AND mpesa_ref = ? LIMIT 1
            """, (txn['property_id'], tenant['id'], txn['mpesa_ref'])).fetchone()
            if claim:
                conn.execute("UPDATE payments SET claim_id = ? WHERE id = ?", (claim['id'], payment_id))
            if not conn.execute("SELECT 1 FROM payment_claims WHERE tenant_id = ? AND status = 'flagged'",
                                (tenant['id'],)).fetchone():
                conn.execute("UPDATE tenants SET flagged = 0 WHERE id = ?", (tenant['id'],))

        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
            ('mpesa_payment_booked', 'payment', payment_id,
             f"{txn['source']} | KES {amount:,.0f} | ref {txn['mpesa_ref'] or '-'} | tenant {tenant['name']}", 'system'))

        # Same receipt wording as every other way a payment is recorded (what it settled,
        # balance today; portal link only once outreach is approved).
        from src.messaging.tenant_payment import receipt_sms
        # No phone on the tenancy: the phone that paid by STK gets the receipt — without
        # being saved to the tenancy (tenants sign in with that phone; a payer is not the tenant).
        receipt = receipt_sms(conn, payment_id, base_url or os.environ.get('APP_BASE_URL'),
                              fallback_phone=txn['phone'] if txn['source'] == 'daraja' else None)
        return ({'phone': receipt[0], 'body': receipt[1], 'property_id': txn['property_id']}
                if receipt else None)


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


def assign_unmatched(conn, txn_id, tenant_id, org_id):
    """Admin names the tenant for a paybill payment whose account number was not
    recognised; it is then booked like any other. Returns True if re-queued."""
    txn = conn.execute("""SELECT * FROM payment_transactions
                          WHERE id = ? AND org_id = ? AND processing_status = 'unmatched'""",
                       (txn_id, org_id)).fetchone()
    tenant = conn.execute("""SELECT t.* FROM tenants t JOIN properties p ON p.id = t.property_id
                             WHERE t.id = ? AND t.unit_id IS NOT NULL AND p.organization_id = ?""",
                          (tenant_id, org_id)).fetchone()
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

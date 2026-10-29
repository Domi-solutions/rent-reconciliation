"""
SMS delivery — one function, swappable provider.

Every send attempt is logged to platform_outbox regardless of outcome, so the
platform can inspect all outbound SMS in /platform/outbox.

Env vars:
  SMS_PROVIDER     — 'africastalking' (default) | 'textsms'
  SMS_SENDER_ID    — default approved sender name, e.g. 'DOMI' once registered (unset = provider
                     default, e.g. TextSMS's own). A property with its own registered name
                     (properties.sms_sender_id, set at /platform/sms-senders) sends under that.
  AT_SMS_ENABLED   — 'false' logs as simulated and sends nothing (any provider)
  Africa's Talking: AT_API_KEY, AT_USERNAME ('sandbox' for testing); AT_SENDER_ID still honoured
  TextSMS:          TEXTSMS_API_KEY, TEXTSMS_PARTNER_ID

Usage:
    from src.messaging.delivery import send_sms
    sent, failed, errors = send_sms(recipients, "Your message text")
    # recipients: list of dicts with 'phone' key, or list of phone strings
"""

import json
import math
import os
import threading
import urllib.error
import urllib.request

from src.utils.phone import normalize_to_e164 as _normalize_phone

# Characters outside the GSM alphabet switch the whole SMS to Unicode, which fits
# 70 characters per part instead of 160 — two to three times the cost. Swap the
# usual offenders (from copy-paste and templates) for plain equivalents.
_PLAIN = str.maketrans({'\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'", '\u201c': '"',
                        '\u201d': '"', '\u2026': '...', '\u00a0': ' ', '\u2022': '-', '\u00b7': '-'})
_GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
           "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà^{}\\[~]|€")


def plain_sms(text):
    return (text or '').translate(_PLAIN)


def sms_parts(text):
    """How many SMS parts a message is billed as."""
    text = plain_sms(text)
    if all(c in _GSM for c in text):
        n = sum(2 if c in '^{}\\[~]|€' else 1 for c in text)
        return 1 if n <= 160 else math.ceil(n / 153)
    return 1 if len(text) <= 70 else math.ceil(len(text) / 67)


def provider():
    return (os.environ.get('SMS_PROVIDER') or 'africastalking').strip().lower()


def configured():
    if provider() == 'textsms':
        return bool(os.environ.get('TEXTSMS_API_KEY') and os.environ.get('TEXTSMS_PARTNER_ID'))
    return bool(os.environ.get('AT_API_KEY', '').strip())


def _sender_id():
    return (os.environ.get('SMS_SENDER_ID') or os.environ.get('AT_SENDER_ID') or '').strip() or None


def sender_label(sender):
    return sender or ('TextSMS' if provider() == 'textsms' else "the provider's default")


def sender_for(message, property_id=None):
    """→ (sender, message, property_id) for one SMS.

    A property with its own registered sender name sends under it. Tenant SMS lead with the
    property name ("Kijani Court: …"), which identifies the property when the caller did not
    pass property_id. When the sender name is that property's alone, the leading name is
    dropped — the phone already shows who it is from. A sender shared by several properties
    (one agency brand) keeps it, so tenants know which building. Anything else goes under
    the default sender, unchanged."""
    try:
        from src.database.db import get_connection
        with get_connection() as conn:
            rows = conn.execute("""SELECT id, name, TRIM(sms_sender_id) AS sender FROM properties
                                    WHERE TRIM(COALESCE(sms_sender_id, '')) != ''""").fetchall()
    except Exception:                   # no column yet (very old DB) or no DB: default sender
        return _sender_id(), message, property_id
    prop = None
    if property_id:
        prop = next((r for r in rows if r['id'] == property_id), None)
    else:
        lead = message.split(':', 1)[0].strip().lower() if ':' in message[:80] else ''
        matches = [r for r in rows if (r['name'] or '').strip().lower() == lead]
        if matches and len({m['sender'].upper() for m in matches}) == 1:
            prop = matches[0]
    if not prop:
        return _sender_id(), message, property_id
    shared = sum(1 for r in rows if r['sender'].upper() == prop['sender'].upper()) > 1
    lead = (prop['name'] or '').strip() + ':'
    if not shared and len(lead) > 1 and message.lower().startswith(lead.lower()):
        rest = message[len(lead):].lstrip()
        message = rest[:1].upper() + rest[1:]
    return prop['sender'], message, prop['id']


def _log_sms(phone, body, status, error=None, sender=None, property_id=None):
    try:
        from src.messaging.outbox import log_outbox
        log_outbox(channel='sms', to_phone=phone, body=body, status=status, error=error,
                   subject=f'From {sender_label(sender)}', property_id=property_id, message_type='sms')
    except Exception:
        pass  # Never let outbox logging break SMS delivery


def _send_africastalking(numbers, message, sender=None):
    """→ list of (number, ok, error)."""
    import africastalking
    africastalking.initialize(os.environ.get('AT_USERNAME', 'sandbox').strip(), os.environ['AT_API_KEY'].strip())
    sender = sender or _sender_id()
    kwargs = {'sender_id': sender} if sender else {}
    response = africastalking.SMS.send(message, numbers, **kwargs)
    out = []
    for r in response.get('SMSMessageData', {}).get('Recipients', []):
        ok = r.get('statusCode') == 101
        out.append((r.get('number', '?'), ok, None if ok else r.get('status')))
    return out


def _send_textsms(numbers, message, sender=None):
    """TextSMS (textsms.co.ke) JSON API, one request per number."""
    sender = sender or _sender_id()
    out = []
    for num in numbers:
        payload = {'apikey': os.environ['TEXTSMS_API_KEY'], 'partnerID': os.environ['TEXTSMS_PARTNER_ID'],
                   'message': message, 'shortcode': sender or 'TextSMS', 'mobile': num.lstrip('+')}
        try:
            req = urllib.request.Request('https://sms.textsms.co.ke/api/services/sendsms/',
                                         data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read() or b'{}')
            first = (data.get('responses') or [{}])[0]
            code = first.get('response-code', first.get('respose-code'))   # their API spells it both ways
            ok = str(code) == '200'
            out.append((num, ok, None if ok else str(first.get('response-description') or data)[:200]))
        except urllib.error.HTTPError as e:
            # Their reason is in the body ("invalid sender id", "insufficient balance", …).
            try:
                detail = e.read().decode('utf-8', 'replace').strip()
            except Exception:
                detail = ''
            out.append((num, False, f'HTTP {e.code}: {detail or e.reason}'[:300]))
        except Exception as e:
            out.append((num, False, str(e)[:200]))
    return out


def send_sms(recipients, message, property_id=None):
    """
    Send an SMS to one or more recipients through the configured provider.

    recipients — list of dicts with a 'phone' key, or list of phone strings
    message    — plain text body (multi-part SMS handled by the provider)

    property_id — optional; otherwise the property is read from the message's leading name
                  (see sender_for) to pick its own sender name.

    Returns (sent_count, failed_count, errors_list)
    """
    sender, message, property_id = sender_for(plain_sms(message), property_id)
    log = lambda phone, status, error=None: _log_sms(phone, message, status, error, sender, property_id)
    if not configured() or os.environ.get('AT_SMS_ENABLED', 'true').lower() == 'false':
        why = 'SMS disabled (AT_SMS_ENABLED=false)' if configured() else f'{provider()} not configured'
        for r in recipients:
            raw = r.get('phone') if isinstance(r, dict) else r
            log(str(raw or ''), 'simulated', why)
        return 0, len(recipients), [why]

    numbers, skipped = [], []
    for r in recipients:
        raw = r.get('phone') if isinstance(r, dict) else r
        normalized = _normalize_phone(raw)
        if normalized:
            numbers.append(normalized)
        else:
            skipped.append(str(raw or 'empty'))

    if not numbers:
        for s in skipped:
            log(s, 'failed', 'Invalid phone number')
        return 0, len(recipients), ['No valid phone numbers to send to'] + skipped

    from src.utils.runtime import is_demo
    if is_demo():
        # Demo site: real SMS only to phones someone typed into the demo, within daily caps.
        from src.database.db import get_connection
        from src.demo.phones import allowed
        try:
            with get_connection() as conn:
                numbers, held = allowed(conn, numbers)
        except Exception as e:                       # never fail silently: log it like any send
            for num in numbers:
                log(num, 'failed', f'demo SMS check failed: {e}')
            return 0, len(recipients), [str(e)]
        for num, why in held:
            log(num, 'simulated', why)
        if not numbers:
            return 0, len(recipients), [why for _, why in held]

    try:
        results = (_send_textsms(numbers, message, sender) if provider() == 'textsms'
                   else _send_africastalking(numbers, message, sender))
    except Exception as e:
        for num in numbers:
            log(num, 'failed', str(e))
        return 0, len(numbers) + len(skipped), [str(e)]

    errors = []
    for num, ok, err in results:
        log(num, 'sent' if ok else 'failed', err)
        if not ok:
            errors.append(f"{num}: {err or '?'}")
    success = sum(1 for _, ok, _ in results if ok)
    if skipped:
        errors.append(f"Skipped (no valid phone): {', '.join(skipped)}")
    return success, len(numbers) - success + len(skipped), errors


def send_many_async(items):
    """Send many texts in one background thread, one after another — for broadcasts and reminders.

    items: list of (recipients, message) or (recipients, message, property_id). The request (or
    job) returns at once and never holds the database while a provider call is in flight; each
    send is logged to platform_outbox as usual."""
    items = [tuple(i) for i in items if i]
    if not items:
        return None

    def run():
        for item in items:
            try:
                send_sms(*item)
            except Exception:
                pass                     # one bad number never stops the rest
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def send_sms_async(recipients, message, property_id=None):
    """Fire-and-forget SMS — returns immediately, sends in a daemon thread.

    Use this for confirmation messages in request handlers where the send
    result does not affect the HTTP response. The send is still logged to
    platform_outbox regardless of outcome.
    """
    t = threading.Thread(target=send_sms, args=(recipients, message, property_id), daemon=True)
    t.start()

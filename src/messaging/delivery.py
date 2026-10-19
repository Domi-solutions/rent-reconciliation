"""
SMS delivery — one function, swappable provider.

Every send attempt is logged to platform_outbox regardless of outcome, so the
platform can inspect all outbound SMS in /platform/outbox.

Env vars:
  SMS_PROVIDER     — 'africastalking' (default) | 'textsms'
  SMS_SENDER_ID    — approved sender name, e.g. 'MOWIN-APTS' (unset = provider default)
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


def _log_sms(phone, body, status, error=None):
    try:
        from src.messaging.outbox import log_outbox
        log_outbox(channel='sms', to_phone=phone, body=body, status=status, error=error,
                   message_type='sms')
    except Exception:
        pass  # Never let outbox logging break SMS delivery


def _send_africastalking(numbers, message):
    """→ list of (number, ok, error)."""
    import africastalking
    africastalking.initialize(os.environ.get('AT_USERNAME', 'sandbox').strip(), os.environ['AT_API_KEY'].strip())
    kwargs = {'sender_id': _sender_id()} if _sender_id() else {}
    response = africastalking.SMS.send(message, numbers, **kwargs)
    out = []
    for r in response.get('SMSMessageData', {}).get('Recipients', []):
        ok = r.get('statusCode') == 101
        out.append((r.get('number', '?'), ok, None if ok else r.get('status')))
    return out


def _send_textsms(numbers, message):
    """TextSMS (textsms.co.ke) JSON API, one request per number."""
    out = []
    for num in numbers:
        payload = {'apikey': os.environ['TEXTSMS_API_KEY'], 'partnerID': os.environ['TEXTSMS_PARTNER_ID'],
                   'message': message, 'shortcode': _sender_id() or 'TextSMS', 'mobile': num.lstrip('+')}
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
        except Exception as e:
            out.append((num, False, str(e)[:200]))
    return out


def send_sms(recipients, message):
    """
    Send an SMS to one or more recipients through the configured provider.

    recipients — list of dicts with a 'phone' key, or list of phone strings
    message    — plain text body (multi-part SMS handled by the provider)

    Returns (sent_count, failed_count, errors_list)
    """
    message = plain_sms(message)
    if not configured() or os.environ.get('AT_SMS_ENABLED', 'true').lower() == 'false':
        why = 'SMS disabled (AT_SMS_ENABLED=false)' if configured() else f'{provider()} not configured'
        for r in recipients:
            raw = r.get('phone') if isinstance(r, dict) else r
            _log_sms(str(raw or ''), message, 'simulated', why)
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
            _log_sms(s, message, 'failed', 'Invalid phone number')
        return 0, len(recipients), ['No valid phone numbers to send to'] + skipped

    try:
        results = _send_textsms(numbers, message) if provider() == 'textsms' else _send_africastalking(numbers, message)
    except Exception as e:
        for num in numbers:
            _log_sms(num, message, 'failed', str(e))
        return 0, len(numbers) + len(skipped), [str(e)]

    errors = []
    for num, ok, err in results:
        _log_sms(num, message, 'sent' if ok else 'failed', err)
        if not ok:
            errors.append(f"{num}: {err or '?'}")
    success = sum(1 for _, ok, _ in results if ok)
    if skipped:
        errors.append(f"Skipped (no valid phone): {', '.join(skipped)}")
    return success, len(numbers) - success + len(skipped), errors


def send_sms_async(recipients, message):
    """Fire-and-forget SMS — returns immediately, sends in a daemon thread.

    Use this for confirmation messages in request handlers where the send
    result does not affect the HTTP response. The send is still logged to
    platform_outbox regardless of outcome.
    """
    t = threading.Thread(target=send_sms, args=(recipients, message), daemon=True)
    t.start()

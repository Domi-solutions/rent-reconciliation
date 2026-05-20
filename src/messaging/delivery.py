"""
SMS delivery via Africa's Talking.

Every send attempt is also logged to platform_outbox regardless of outcome,
so the platform can inspect all outbound SMS in /platform/outbox.

Env vars:
  AT_API_KEY   — API key from AT Sandbox or Live dashboard
  AT_USERNAME  — 'sandbox' for testing, or your real AT username for live

Usage:
    from src.messaging.delivery import send_sms
    sent, failed, errors = send_sms(recipients, "Your message text")
    # recipients: list of dicts with 'phone' key, or list of phone strings
"""

import os
import threading

from src.utils.phone import normalize_to_e164 as _normalize_phone


def _log_sms(phone, body, status, error=None):
    try:
        from src.messaging.outbox import log_outbox
        log_outbox(channel='sms', to_phone=phone, body=body, status=status, error=error,
                   message_type='sms')
    except Exception:
        pass  # Never let outbox logging break SMS delivery


def send_sms(recipients, message):
    """
    Send an SMS to one or more recipients via Africa's Talking.

    recipients — list of dicts with a 'phone' key, or list of phone strings
    message    — plain text body (AT handles multi-part SMS automatically)

    Returns (sent_count, failed_count, errors_list)
    """
    api_key = os.environ.get('AT_API_KEY', '').strip()
    username = os.environ.get('AT_USERNAME', 'sandbox').strip()

    if not api_key:
        for r in recipients:
            raw = r.get('phone') if isinstance(r, dict) else r
            _log_sms(str(raw or ''), message, 'simulated', 'AT_API_KEY not configured')
        return 0, len(recipients), ['AT_API_KEY not configured']

    # Normalize phone numbers
    numbers = []
    skipped = []
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
        import africastalking
        africastalking.initialize(username, api_key)
        sms = africastalking.SMS
        response = sms.send(message, numbers)

        result_recipients = response.get('SMSMessageData', {}).get('Recipients', [])
        success = sum(1 for r in result_recipients if r.get('statusCode') == 101)
        failed_sms = len(numbers) - success
        errors = []
        for r in result_recipients:
            phone_num = r.get('number', '?')
            ok = r.get('statusCode') == 101
            _log_sms(phone_num, message, 'sent' if ok else 'failed',
                     None if ok else r.get('status'))
            if not ok:
                errors.append(f"{phone_num}: {r.get('status', '?')}")
        if skipped:
            errors.append(f"Skipped (no valid phone): {', '.join(skipped)}")
        return success, failed_sms + len(skipped), errors

    except Exception as e:
        for num in numbers:
            _log_sms(num, message, 'failed', str(e))
        return 0, len(numbers) + len(skipped), [str(e)]


def send_sms_async(recipients, message):
    """Fire-and-forget SMS — returns immediately, sends in a daemon thread.

    Use this for confirmation messages in request handlers where the send
    result does not affect the HTTP response. The send is still logged to
    platform_outbox regardless of outcome.
    """
    t = threading.Thread(target=send_sms, args=(recipients, message), daemon=True)
    t.start()

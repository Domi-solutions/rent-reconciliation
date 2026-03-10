"""
SMS delivery via Africa's Talking.

Env vars:
  AT_API_KEY   — API key from AT Sandbox or Live dashboard
  AT_USERNAME  — 'sandbox' for testing, or your real AT username for live

Usage:
    from src.messaging.delivery import send_sms
    sent, failed, errors = send_sms(recipients, "Your message text")
    # recipients: list of dicts with 'phone' key, or list of phone strings
"""

import os
import re


def _normalize_phone(raw):
    """
    Normalize a Kenyan phone number to E.164 format (+254XXXXXXXXX).
    Handles: 07XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX
    Returns None if the number can't be recognized.
    """
    if not raw:
        return None
    phone = re.sub(r'[\s\-\(\)]', '', str(raw).strip())
    if phone.startswith('+254') and len(phone) == 13:
        return phone
    if phone.startswith('254') and len(phone) == 12:
        return '+' + phone
    if phone.startswith('0') and len(phone) == 10:
        return '+254' + phone[1:]
    return None


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
        return 0, len(recipients), ['No valid phone numbers to send to'] + skipped

    try:
        import africastalking
        africastalking.initialize(username, api_key)
        sms = africastalking.SMS
        response = sms.send(message, numbers)

        result_recipients = response.get('SMSMessageData', {}).get('Recipients', [])
        success = sum(1 for r in result_recipients if r.get('statusCode') == 101)
        failed_sms = len(numbers) - success
        errors = [
            f"{r.get('number', '?')}: {r.get('status', '?')}"
            for r in result_recipients
            if r.get('statusCode') != 101
        ]
        if skipped:
            errors.append(f"Skipped (no valid phone): {', '.join(skipped)}")
        return success, failed_sms + len(skipped), errors

    except Exception as e:
        return 0, len(numbers) + len(skipped), [str(e)]

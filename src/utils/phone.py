"""
Kenyan phone number normalization utilities.
Single source of truth — import from here, never define locally in route files.
"""
import re


def normalize_to_e164(raw):
    """
    Normalize any Kenyan phone number to E.164 (+254XXXXXXXXX).
    Handles: 07XXXXXXXX, 01XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX
    Returns None if the number cannot be recognized.
    """
    if not raw:
        return None
    phone = re.sub(r'[\s\-\(\)]', '', str(raw).strip())
    # Digits only after the optional "+": "07XXXXXXXX" (a placeholder) is not a number.
    m = re.fullmatch(r'(?:\+?254|0)(\d{9})', phone)
    return '+254' + m.group(1) if m else None


def normalize_to_daraja(raw):
    """
    Normalize to Daraja API format: 254XXXXXXXXX (no leading +).
    Safaricom's STK Push requires this format.
    Returns None if the number cannot be recognized.
    """
    e164 = normalize_to_e164(raw)
    return e164[1:] if e164 else None

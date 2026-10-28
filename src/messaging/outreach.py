"""Release gate for bulk/proactive tenant contact during onboarding."""

import os


def enabled():
    """Only an explicit opt-in permits invitations, reminders, and check-ins."""
    return os.environ.get('TENANT_OUTREACH_ENABLED', '').strip().lower() == 'true'


def automatic_alerts_enabled():
    """Alerts the system would send on its own — the weekly owner digest, and
    "not found in bank records" / "claim still unmatched" texts to tenants and
    caretakers. Off unless explicitly opted in: people act on the lists in the
    portal instead. The flags, platform alerts and audit rows are still written.
    Payment confirmations (receipts) are not covered — those always send."""
    return os.environ.get('AUTOMATIC_ALERTS_ENABLED', '').strip().lower() == 'true'


def portal_footer(text, base_url, short_code):
    """Every SMS to a tenant ends with their portal link — once sign-in links are approved
    (a link is a sign-in invitation). Not added twice when the text already carries it."""
    if not (enabled() and short_code and base_url):
        return text
    link = f"{base_url.rstrip('/')}/t/{short_code}"
    return text if link in text else f"{text}\n\nOpen Domi: {link}"

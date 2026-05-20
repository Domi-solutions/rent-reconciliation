"""Platform outbox — log every outbound message to platform_outbox.

All channels (email, SMS, WhatsApp, portal) call log_outbox() before or after
attempting delivery. The outbox is visible only in /platform/outbox and acts as
a full delivery simulator for channels that aren't configured yet.

channel values: 'email' | 'sms' | 'whatsapp' | 'portal'
status values:  'sent' | 'failed' | 'simulated' (no API configured)
"""

from src.database.db import generate_id, get_connection


def log_outbox(
    channel,
    body,
    to_name=None,
    to_email=None,
    to_phone=None,
    subject=None,
    status='simulated',
    error=None,
    org_id=None,
    property_id=None,
    message_type=None,
):
    """Write one outbound message record to platform_outbox."""
    entry_id = generate_id('OBX')
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO platform_outbox
               (id, to_name, to_email, to_phone, channel, subject, body,
                status, error, org_id, property_id, message_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id, to_name, to_email, to_phone, channel, subject, body,
                status, error, org_id, property_id, message_type,
            ),
        )

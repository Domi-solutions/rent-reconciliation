"""Send SMS notifications to all owners of a property and store in portal inbox."""


def notify_property_owners(conn, property_id, message,
                           portal_subject=None, portal_body=None,
                           template_body=None, channel=None, recipient_count=None,
                           message_type='notification', sent_by=None):
    """Send SMS to all owners of a property and store message in their portal inbox.

    message         — plain text sent via SMS
    portal_subject  — heading shown in portal (defaults to first line of message)
    portal_body     — body shown in portal (defaults to message)
    template_body   — original unsubstituted template (for broadcasts)
    channel         — e.g. 'sms', 'portal'
    recipient_count — number of tenants the broadcast went to
    message_type    — 'notification' | 'broadcast'

    Returns (sent_count, failed_count, errors_list).
    """
    from src.messaging.delivery import send_sms
    from src.database.db import generate_id

    owners = conn.execute("""
        SELECT o.id, o.name, o.phone
        FROM owners o
        JOIN property_owners po ON po.owner_id = o.id
        WHERE po.property_id = ?
    """, (property_id,)).fetchall()

    if not owners:
        return 0, 0, []

    subject = (portal_subject or message.split('\n')[0].strip())[:100]
    body = portal_body or message

    for owner in owners:
        msg_id = generate_id('MSG')
        conn.execute("""
            INSERT INTO owner_messages
                (id, property_id, owner_id, subject, body, template_body,
                 message_type, channel, recipient_count, sent_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (msg_id, property_id, owner['id'], subject, body,
              template_body, message_type, channel, recipient_count, sent_by))

    owners_with_phone = [o for o in owners if o['phone']]
    if not owners_with_phone:
        return 0, 0, []

    recipients = [{'phone': o['phone']} for o in owners_with_phone]
    return send_sms(recipients, message)


def get_owner_portal_url(conn, property_id, base_url):
    """Return the portal URL for the first owner of a property who has a token.
    Returns None if no owner has a token.
    """
    row = conn.execute("""
        SELECT o.access_token
        FROM owners o
        JOIN property_owners po ON po.owner_id = o.id
        WHERE po.property_id = ? AND o.access_token IS NOT NULL
        LIMIT 1
    """, (property_id,)).fetchone()
    if row and row['access_token']:
        return f"{base_url.rstrip('/')}/view/o/{row['access_token']}"
    return None

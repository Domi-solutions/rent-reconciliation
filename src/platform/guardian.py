"""Platform guardian — shadow logging, alerts, and owner change notifications.

These functions are called alongside agency actions so the platform has an
agency-uneditable record of sensitive events and can notify affected parties
directly, bypassing the agency.
"""

from src.database.db import generate_id
from src.messaging.owner_notify import notify_property_owners


def platform_log(conn, action, entity_type=None, entity_id=None, details=None,
                 org_id=None, property_id=None, actor='agency'):
    """Write a shadow audit entry the agency cannot edit or delete."""
    entry_id = generate_id('PLOG')
    conn.execute(
        """INSERT INTO platform_shadow_log
               (id, org_id, property_id, action, entity_type, entity_id, details, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry_id, org_id, property_id, action, entity_type, entity_id, details, actor),
    )


def raise_alert(conn, alert_type, details, org_id=None, property_id=None, severity='warning'):
    """Create a platform alert visible only to the platform team."""
    alert_id = generate_id('PALERT')
    conn.execute(
        """INSERT INTO platform_alerts
               (id, org_id, property_id, alert_type, details, severity)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (alert_id, org_id, property_id, alert_type, details, severity),
    )


def notify_owner_change(conn, property_id, subject, body):
    """Notify all owners of a property of a change, bypassing agency delivery.

    Writes to owner_messages portal inbox and sends SMS directly.
    Also logs each outbound SMS to platform_outbox.
    """
    notify_property_owners(
        conn,
        property_id=property_id,
        message=body,
        portal_subject=subject,
        portal_body=body,
        message_type='notification',
        sent_by='Domi Platform',
    )

    # Log portal notifications to outbox for platform visibility
    try:
        from src.messaging.outbox import log_outbox
        owners = conn.execute(
            """SELECT o.id, p.name, p.email, p.phone
               FROM owners o
               JOIN property_owners po ON po.owner_id = o.id
               JOIN persons p ON p.id = o.person_id
               WHERE po.property_id = ?""",
            (property_id,),
        ).fetchall()
        for owner in owners:
            if owner['email']:
                log_outbox(
                    channel='portal',
                    to_name=owner['name'],
                    to_email=owner['email'],
                    to_phone=owner['phone'],
                    subject=subject,
                    body=body,
                    status='sent',
                    property_id=property_id,
                    message_type='owner_notification',
                )
    except Exception:
        pass  # Never let logging break notification delivery

"""Delivery router abstraction for agent-originated messages."""

import os

from src.database.db import generate_id, get_connection
from src.messaging.delivery import send_sms


def _store_portal_message(conn, msg):
    role = (msg.get("recipient_role") or "").lower()
    property_id = msg.get("property_id")
    subject = msg.get("subject") or "Domi update"
    body = msg.get("body") or ""
    message_type = msg.get("message_type") or "notification"

    if role == "owner":
        owner = conn.execute(
            """
            SELECT o.id
            FROM owners o
            JOIN property_owners po ON po.owner_id = o.id
            WHERE po.property_id = ?
              AND o.phone = ?
            LIMIT 1
            """,
            (property_id, msg.get("recipient_phone")),
        ).fetchone()
        if owner:
            conn.execute(
                """
                INSERT INTO owner_messages
                (id, property_id, owner_id, subject, body, message_type, channel, sent_by)
                VALUES (?, ?, ?, ?, ?, ?, 'portal', 'System')
                """,
                (generate_id("OMSG"), property_id, owner["id"], subject, body, message_type),
            )
    elif role == "tenant":
        tenant = conn.execute(
            """
            SELECT id
            FROM tenants
            WHERE property_id = ?
              AND phone = ?
              AND status = 'active'
            LIMIT 1
            """,
            (property_id, msg.get("recipient_phone")),
        ).fetchone()
        if tenant:
            conn.execute(
                """
                INSERT INTO messages
                (id, property_id, tenant_id, subject, body, message_type, delivery_channel, delivery_status)
                VALUES (?, ?, ?, ?, ?, ?, 'portal', 'delivered')
                """,
                (generate_id("MSG"), property_id, tenant["id"], subject, body, message_type),
            )


def _send_sms(phone, body):
    if not phone or not body:
        return
    send_sms([{"phone": phone}], body)


def _send_whatsapp(phone, body):
    # Credentials and template mapping are wired in a later phase.
    print(f"WhatsApp stub: would send to {phone}: {body}")


def route_message(msg):
    """
    Route a message to portal and selected direct channel.

    Expected keys:
    recipient_phone, recipient_role, property_id, message_type, body, subject (optional)
    """
    if not isinstance(msg, dict):
        return

    with get_connection() as conn:
        _store_portal_message(conn, msg)

    channel = (msg.get("channel") or "").lower()
    phone = msg.get("recipient_phone")
    body = msg.get("body")

    if channel == "whatsapp":
        _send_whatsapp(phone, body)
        return
    if channel == "sms":
        _send_sms(phone, body)
        return

    # Default fallback: SMS unless explicitly disabled.
    if os.environ.get("AT_SMS_ENABLED", "true").lower() == "true":
        _send_sms(phone, body)

